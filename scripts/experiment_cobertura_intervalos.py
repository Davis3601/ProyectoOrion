"""
D-EXP-4 — COBERTURA DE INTERVALOS EMPIRICOS POR JUGADOR.

*** SOLO LECTURA DE LA NUBE. *** player_game_stats y games de BigQuery con
CloudDataStore construido EXPLICITAMENTE (convencion de scripts). Escribe
UNICAMENTE en data/experiment_cobertura/. Cero despliegue, cero ediciones al
paquete: nada publicado se toca.

QUE MIDE: la cobertura REAL de los intervalos empiricos que saldrian del
rolling de 10 partidos. D-EXP-3 ya adjudico que el modelo es el rolling
directo; aqui no hay hipotesis de prediccion que probar — hay CALIBRACION del
intervalo que se publicaria, para que las palabras del mensaje digan lo que el
dato sostiene y no la etiqueta nominal.

EL SCRIPT MIDE, NO DECIDE: no emite recomendacion de que intervalo publicar.
Esa eleccion es adjudicacion humana con la tabla en mano.

UNIVERSO IDENTICO A D-EXP-3, y no por convencion: se construye llamando a las
MISMAS funciones de aquel script (import, no copia) y se verifica por assert
contra su conteo exacto. Un universo distinto haria incomparables las dos
mediciones sobre las que se disena el mismo producto.

CERO LEAKAGE: todo intervalo sale de shift(1).rolling(10) sobre partidos
JUGADOS previos. El partido evaluado jamas entra en su propio intervalo.

Uso:
    python scripts/experiment_cobertura_intervalos.py
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nba_predictor.config import ROLLING_WINDOW_GAMES, settings
from nba_predictor.storage.cloud import CloudDataStore

log = logging.getLogger("dexp4")

OUT_DIR = Path("data/experiment_cobertura")

# El universo se construye con el codigo de D-EXP-3, no con una segunda
# implementacion: la paridad es el requisito, y copiar es como se pierde.
_V3_PATH = Path(__file__).resolve().parent / "experiment_stats_jugador.py"
_SPEC = importlib.util.spec_from_file_location("_dexp3", _V3_PATH)
dexp3 = importlib.util.module_from_spec(_SPEC)
sys.modules["_dexp3"] = dexp3
_SPEC.loader.exec_module(dexp3)

STATS: tuple[str, ...] = dexp3.STATS
VALID_SEASONS: tuple[str, ...] = dexp3.VALID_SEASONS
# Conteo publicado en el RESULTADO de D-EXP-3 (CLAUDE.md 2026-09-22).
N_UNIVERSO_DEXP3 = 148_484

# Intervalos candidatos: (nombre, cuantil_bajo, cuantil_alto, cobertura nominal).
# min/max se expresan como los cuantiles 0 y 1 — misma maquinaria, sin casos
# especiales.
INTERVALOS: tuple[tuple[str, float, float, float], ...] = (
    ("min_max", 0.00, 1.00, float("nan")),   # nominal indefinido para n=10
    ("p10_p90", 0.10, 0.90, 0.80),
    ("p25_p75", 0.25, 0.75, 0.50),
)

# Titular vs banca: corte pre-registrado sobre la mediana de minutos rolling.
CORTE_TITULAR_MIN = 28.0
AUDIT_N = 20
AUDIT_SEED = 42


# ---------------------------------------------------------------------------
# Funciones PURAS — sin red, sin BigQuery, unit-testeables
# ---------------------------------------------------------------------------


def add_interval_rollings(
    played: pd.DataFrame,
    stats: tuple[str, ...] = STATS,
    window: int = ROLLING_WINDOW_GAMES,
    min_prev: int = dexp3.MIN_PREV_GAMES,
    intervalos: tuple[tuple[str, float, float, float], ...] = INTERVALOS,
) -> pd.DataFrame:
    """Añade mediana y los limites de cada intervalo candidato, por jugador.

    Todo con shift(1).rolling(window, min_periods=min_prev): el partido que se
    evalua no puede estar dentro del intervalo que lo evalua.

    NOTA DE METODO: pandas interpola linealmente los cuantiles. Con n=10, p10
    NO es "el segundo valor mas bajo" sino una interpolacion entre el primero y
    el segundo. Importa para leer la cobertura: el intervalo es mas estrecho
    que el que daria el cuantil de orden, y por eso se espera que cubra por
    debajo de su nominal.
    """
    out = played.sort_values(["player_id", "game_date"], kind="stable").copy()

    for stat in stats:
        g = out.groupby("player_id", sort=False)[stat]
        out[f"med_{stat}"] = g.transform(
            lambda s: s.shift(1).rolling(window, min_periods=min_prev).median()
        )
        for nombre, q_lo, q_hi, _nom in intervalos:
            out[f"{nombre}_lo_{stat}"] = g.transform(
                lambda s, q=q_lo: s.shift(1)
                .rolling(window, min_periods=min_prev)
                .quantile(q)
            )
            out[f"{nombre}_hi_{stat}"] = g.transform(
                lambda s, q=q_hi: s.shift(1)
                .rolling(window, min_periods=min_prev)
                .quantile(q)
            )
    return out


def coverage(real: Any, lo: Any, hi: Any) -> dict:
    """Cobertura empirica de un intervalo, con IC95 de proporcion.

    Dentro = lo <= real <= hi, inclusivo en ambos extremos: el intervalo
    publicado se leera como "entre X e Y", y un valor exactamente igual al
    limite esta dentro de esa frase.
    """
    real = np.asarray(real, dtype=float)
    lo = np.asarray(lo, dtype=float)
    hi = np.asarray(hi, dtype=float)
    dentro = (real >= lo) & (real <= hi)
    n = int(len(dentro))
    if n == 0:
        return {"n": 0, "cobertura": None, "ic95": None, "ancho_mediano": None}
    p = float(dentro.mean())
    se = float(np.sqrt(p * (1 - p) / n))
    return {
        "n": n,
        "cobertura": round(p, 5),
        "ic95": [round(p - 1.96 * se, 5), round(p + 1.96 * se, 5)],
        "semiancho_ic_pp": round(1.96 * se * 100, 3),
        "ancho_mediano": round(float(np.median(hi - lo)), 4),
        "ancho_medio": round(float(np.mean(hi - lo)), 4),
    }


def split_titular(roll_min: Any, corte: float = CORTE_TITULAR_MIN) -> Any:
    """Etiqueta titular/banca por mediana de minutos rolling (corte pre-registrado)."""
    return np.where(np.asarray(roll_min, dtype=float) >= corte, "titular", "banca")


def summarize_coverage(
    df: pd.DataFrame,
    stats: tuple[str, ...] = STATS,
    intervalos: tuple[tuple[str, float, float, float], ...] = INTERVALOS,
) -> dict:
    """Cobertura por stat x intervalo, global y por subgrupos pre-registrados."""
    salida: dict[str, Any] = {}
    for stat in stats:
        salida[stat] = {}
        for nombre, _lo, _hi, nominal in intervalos:
            lo = df[f"{nombre}_lo_{stat}"]
            hi = df[f"{nombre}_hi_{stat}"]
            celda: dict[str, Any] = {
                "nominal": None if nominal != nominal else nominal,
                "global": coverage(df[stat], lo, hi),
                "por_rol": {}, "por_temporada": {},
            }
            for rol in ("titular", "banca"):
                m = df["rol"] == rol
                celda["por_rol"][rol] = coverage(df.loc[m, stat], lo[m], hi[m])
            for season in sorted(df["season"].unique()):
                m = df["season"] == season
                celda["por_temporada"][season] = coverage(df.loc[m, stat], lo[m], hi[m])
            cobs = [v["cobertura"] for v in celda["por_temporada"].values()
                    if v["cobertura"] is not None]
            celda["rango_temporadas_pp"] = (
                round((max(cobs) - min(cobs)) * 100, 3) if cobs else None)
            salida[stat][nombre] = celda
    return salida


def median_vs_mean_mae(df: pd.DataFrame, stats: tuple[str, ...] = STATS) -> dict:
    """MAE de la mediana rolling frente a la media rolling (B0). REFERENCIA.

    No es un contraste inferencial: D-EXP-3 ya cerro la pregunta de que estima
    mejor el centro. Se reporta porque el producto publicaria la mediana como
    centro del intervalo y conviene saber cuanto cuesta esa eleccion.
    """
    out = {}
    for stat in stats:
        y = df[stat].to_numpy(dtype=float)
        mae_med = float(np.abs(y - df[f"med_{stat}"].to_numpy(dtype=float)).mean())
        mae_b0 = float(np.abs(y - df[f"b0_{stat}"].to_numpy(dtype=float)).mean())
        out[stat] = {
            "mae_mediana": round(mae_med, 5),
            "mae_media_b0": round(mae_b0, 5),
            "diferencia": round(mae_med - mae_b0, 5),
        }
    return out


def render_table(cob: dict, centro: dict) -> str:
    """Tabla legible. Sin recomendacion: el script mide, no decide."""
    out: list[str] = ["COBERTURA DE INTERVALOS EMPIRICOS (ventana 10, shift 1)"]
    for stat, porint in cob.items():
        out.append(f"\n  {stat.upper()}")
        out.append(f"    {'intervalo':<10}{'nominal':>9}{'cobertura':>11}{'IC95 +-pp':>11}"
                   f"{'ancho med':>11}{'titular':>10}{'banca':>10}{'rango temp pp':>15}")
        for nombre, celda in porint.items():
            g = celda["global"]
            nom = "—" if celda["nominal"] is None else f"{celda['nominal']:.0%}"
            out.append(
                f"    {nombre:<10}{nom:>9}{g['cobertura']:>11.4f}"
                f"{g['semiancho_ic_pp']:>11.3f}{g['ancho_mediano']:>11.2f}"
                f"{celda['por_rol']['titular']['cobertura']:>10.4f}"
                f"{celda['por_rol']['banca']['cobertura']:>10.4f}"
                f"{celda['rango_temporadas_pp']:>15.2f}")
    out.append("\n\nCENTRO — MAE de la mediana rolling vs la media rolling (referencia)")
    out.append(f"  {'stat':<6}{'MAE mediana':>13}{'MAE media B0':>14}{'dif':>10}")
    for stat, v in centro.items():
        out.append(f"  {stat:<6}{v['mae_mediana']:>13.4f}{v['mae_media_b0']:>14.4f}"
                   f"{v['diferencia']:>+10.4f}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def make_store() -> CloudDataStore:
    """CloudDataStore EXPLICITO. Nunca get_datastore(): el modo no se hereda."""
    return CloudDataStore(
        project_id=settings.gcp_project_id,
        dataset=settings.bq_dataset,
        bucket_name=settings.gcs_bucket,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="D-EXP-4: cobertura de intervalos empiricos")
    ap.add_argument("--allow-parity-drift", action="store_true",
                    help="continuar aunque el universo no coincida con D-EXP-3")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    store = make_store()

    print("Cargando fuentes...", flush=True)
    games = store.load_games()
    pgs = store.load_player_game_stats()
    df = pgs.merge(games[["game_id", "season", "game_date"]], on="game_id",
                   validate="many_to_one")
    df = dexp3.derive_stats(df)
    df["game_date"] = df["game_date"].astype(str)

    played = df[df["minutes"] > 0].copy()
    roll = dexp3.add_rollings(played)          # B0/C1 de D-EXP-3, por import
    roll = add_interval_rollings(roll)         # intervalos de este experimento
    print(f"  {len(played)} filas jugadas | rollings listos", flush=True)

    cols_needed = ([f"b0_{s}" for s in STATS] + [f"c1_{s}" for s in STATS]
                   + [f"med_{s}" for s in STATS]
                   + [f"{n}_{lado}_{s}" for n, _a, _b, _c in INTERVALOS
                      for lado in ("lo", "hi") for s in STATS])
    universo = roll.dropna(subset=cols_needed)
    universo = universo[universo["season"].isin(VALID_SEASONS)].copy()

    # ── PARIDAD DE UNIVERSO contra D-EXP-3, por assert y no por narracion ──
    print(f"\nParidad de universo: {len(universo)} filas "
          f"(D-EXP-3 publico {N_UNIVERSO_DEXP3})")
    if not args.allow_parity_drift:
        assert len(universo) == N_UNIVERSO_DEXP3, (
            f"universo {len(universo)} != {N_UNIVERSO_DEXP3} de D-EXP-3: "
            "las dos mediciones dejarian de ser comparables")

    universo["rol"] = split_titular(universo["roll_min"])
    print(f"  titulares {int((universo['rol'] == 'titular').sum())} | "
          f"banca {int((universo['rol'] == 'banca').sum())}", flush=True)

    # ── Muestra de auditoria: congelada ANTES de computar cobertura ──
    ids = list(zip(universo["game_id"], universo["player_id"].astype(int)))
    muestra = dexp3.freeze_audit_sample(ids, n=AUDIT_N, seed=AUDIT_SEED)
    idx_m = universo.set_index(["game_id", "player_id"])
    filas_audit = []
    for gid, pid in muestra:
        r = idx_m.loc[(gid, pid)]
        fila = {"game_id": gid, "player_id": int(pid), "game_date": str(r["game_date"]),
                "season": r["season"], "rol": r["rol"],
                "minutes": round(float(r["minutes"]), 2),
                "roll_min": round(float(r["roll_min"]), 2)}
        for s in STATS:
            fila[s] = {
                "real": float(r[s]), "mediana": round(float(r[f"med_{s}"]), 3),
                **{n: [round(float(r[f"{n}_lo_{s}"]), 3),
                       round(float(r[f"{n}_hi_{s}"]), 3)]
                   for n, _a, _b, _c in INTERVALOS},
            }
        filas_audit.append(fila)
    (OUT_DIR / "audit_sample.json").write_text(
        json.dumps({"seed": AUDIT_SEED, "n": len(filas_audit),
                    "congelada_antes_de_computar_cobertura": True,
                    "generado_utc": datetime.now(timezone.utc).isoformat(),
                    "filas": filas_audit}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    print(f"Muestra de auditoria congelada: {len(filas_audit)} filas -> "
          f"{OUT_DIR / 'audit_sample.json'}", flush=True)

    # ── Cobertura ──
    cob = summarize_coverage(universo)
    centro = median_vs_mean_mae(universo)
    resultados = {
        "generado_utc": datetime.now(timezone.utc).isoformat(),
        "universo": {"n": int(len(universo)), "n_dexp3": N_UNIVERSO_DEXP3,
                     "paridad": bool(len(universo) == N_UNIVERSO_DEXP3),
                     "titulares": int((universo["rol"] == "titular").sum()),
                     "banca": int((universo["rol"] == "banca").sum()),
                     "corte_titular_min": CORTE_TITULAR_MIN},
        "cobertura": cob,
        "centro_mediana_vs_media": centro,
        "nota": "El script mide; la eleccion del intervalo publicable es "
                "adjudicacion humana con esta tabla.",
    }
    (OUT_DIR / "results.json").write_text(
        json.dumps(resultados, indent=2, ensure_ascii=False), encoding="utf-8")

    tabla = render_table(cob, centro)
    (OUT_DIR / "tabla.txt").write_text(tabla, encoding="utf-8")
    print("\n" + tabla)
    print(f"\nEscrito en {OUT_DIR}/: results.json, tabla.txt, audit_sample.json")


if __name__ == "__main__":
    main()
