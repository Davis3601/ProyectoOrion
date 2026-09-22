"""
D-EXP-3 — FUNDACION DE JUGADOR: descomposicion minutos x tasa contra rolling
directo.

*** SOLO LECTURA DE LA NUBE. *** player_game_stats y games de BigQuery, PDFs
publish de GCS, con CloudDataStore construido EXPLICITAMENTE (convencion de
scripts). Escribe UNICAMENTE en data/experiment_jugador/. Cero despliegue, cero
ediciones al paquete: B-limpia, features_v1, registry, endpoint y market_odds
quedan intactos.

LA PREGUNTA FUNDACIONAL: ¿la descomposicion minutos x tasa le gana al rolling
directo del stat? Si no, el producto de stats es un rolling transparente y el
bottom-up de victoria pierde su motor.

  B0 = rolling(10, shift 1) del propio stat.
  C1 = rolling(10, shift 1) de minutos  x  rolling(10, shift 1) de la tasa/min.
  C2 = C1 + disponibilidad del RIVAL (PDF publish, pesos D-EXP-1), GemBox.

POR QUE LA TASA ES PROMEDIO-DE-RAZONES Y NO RAZON-DE-PROMEDIOS: la convencion
"ratios sobre promedios" de la Fase 2 aplica a las features de equipo. Aqui NO
puede aplicarse, y no por gusto: con razon-de-promedios,
    C1 = mean(min) * [mean(stat)/mean(min)] = mean(stat) = B0
identicamente, y el experimento no mediria nada. La unica lectura no degenerada
de "rolling de la tasa por minuto" es el promedio de las tasas por partido. La
eleccion esta forzada por el algebra, no elegida — y hay un test que lo fija.

CERO LEAKAGE: todo rolling lleva shift(1) y se calcula solo sobre partidos
JUGADOS previos, cruzando temporadas como el pipeline oficial. El corte publish
del PDF es anterior a todo tip-off de su fecha.

Uso:
    python scripts/experiment_stats_jugador.py
    python scripts/experiment_stats_jugador.py --skip-c2     # solo brazo primario
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nba_predictor.config import ROLLING_WINDOW_GAMES, TRAINING_SEASONS, settings
from nba_predictor.features.availability import _add_player_minutes_rolling
from nba_predictor.ingestion.injury_report import (
    NameIndex,
    load_player_names_from_raw_json,
    parse_pdf,
)
from nba_predictor.storage.cloud import CloudDataStore

log = logging.getLogger("dexp3")

OUT_DIR = Path("data/experiment_jugador")

# Folds del pipeline oficial: primer fold valida 2020-21 = TRAINING_SEASONS[4].
VALID_SEASONS: tuple[str, ...] = TRAINING_SEASONS[4:]
GEMBOX_SEASONS: tuple[str, ...] = ("2023-24", "2024-25", "2025-26")
# Walk-forward del brazo C2, restringido al corpus con parser auditado.
C2_FOLDS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("A", ("2023-24",), "2024-25"),
    ("B", ("2023-24", "2024-25"), "2025-26"),
)

STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
MIN_PREV_GAMES = 10          # "jugador con >= 10 partidos jugados previos"
MDE_REL_UMBRAL = 0.03        # gate de resolucion: 3% del MAE baseline
POTENCIA_Z = 2.8             # 80% de potencia, alfa 0.05 dos colas
AUDIT_N = 30
AUDIT_SEED = 42

# El signal de disponibilidad del rival se calcula con el MISMO codigo auditado
# de D-EXP-2 (no una segunda implementacion): la correccion ya esta establecida
# alli con sus unit tests. Dependencia deliberada entre dos scripts desechables.
_V2_PATH = Path(__file__).resolve().parent / "experiment_disponibilidad_v2.py"
_SPEC = importlib.util.spec_from_file_location("_dexp2", _V2_PATH)
dexp2 = importlib.util.module_from_spec(_SPEC)
sys.modules["_dexp2"] = dexp2
_SPEC.loader.exec_module(dexp2)


# ---------------------------------------------------------------------------
# Funciones PURAS — sin red, sin BigQuery, unit-testeables
# ---------------------------------------------------------------------------


def derive_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Añade pts y reb, que player_game_stats NO trae como columnas.

    pts = 2*fgm + fg3m + ftm — porque fgm YA incluye los triples:
    2*(fgm - fg3m) + 3*fg3m + ftm se simplifica a esto. reb = oreb + dreb.
    """
    out = df.copy()
    out["pts"] = 2 * out["fgm"] + out["fg3m"] + out["ftm"]
    out["reb"] = out["oreb"] + out["dreb"]
    return out


def add_rollings(
    played: pd.DataFrame,
    stats: tuple[str, ...] = STATS,
    window: int = ROLLING_WINDOW_GAMES,
    min_prev: int = MIN_PREV_GAMES,
) -> pd.DataFrame:
    """Añade B0 y C1 por stat sobre filas de partidos JUGADOS, por jugador.

    `played` debe venir filtrado a minutes > 0 y ordenado por jugador y fecha.
    Todo rolling es shift(1) + rolling(window, min_periods=min_prev): el partido
    G jamas se ve a si mismo, y sin `min_prev` partidos previos el valor queda
    NaN (fila excluida del universo y contada).
    """
    out = played.sort_values(["player_id", "game_date"], kind="stable").copy()
    g = out.groupby("player_id", sort=False)

    out["roll_min"] = g["minutes"].transform(
        lambda s: s.shift(1).rolling(window, min_periods=min_prev).mean()
    )
    for stat in stats:
        out[f"b0_{stat}"] = g[stat].transform(
            lambda s: s.shift(1).rolling(window, min_periods=min_prev).mean()
        )
        # PROMEDIO DE RAZONES (ver docstring del modulo): razon-de-promedios
        # colapsaria C1 en B0 identicamente.
        out[f"rate_{stat}"] = out[stat] / out["minutes"]
        out[f"roll_rate_{stat}"] = out.groupby("player_id", sort=False)[
            f"rate_{stat}"
        ].transform(lambda s: s.shift(1).rolling(window, min_periods=min_prev).mean())
        out[f"c1_{stat}"] = out["roll_min"] * out[f"roll_rate_{stat}"]
    return out


def paired_mae_diff(y: Any, pred_a: Any, pred_b: Any) -> dict:
    """Diferencia PAREADA de error absoluto: d_i = |e_A| - |e_B|.

    Positivo = B (el candidato) acierta mas en ESE jugador-partido. mean(d) es
    exactamente la diferencia de MAE, con el ruido entre jugadores cancelado al
    restar sobre la misma fila.
    """
    y = np.asarray(y, dtype=float)
    d = np.abs(y - np.asarray(pred_a, dtype=float)) - np.abs(
        y - np.asarray(pred_b, dtype=float))
    n = int(len(d))
    media = float(d.mean())
    sd = float(d.std(ddof=1)) if n > 1 else float("nan")
    se = sd / np.sqrt(n) if n > 1 and sd > 0 else float("nan")
    return {
        "n": n,
        "media": round(media, 6),
        "sd": round(sd, 6),
        "se": round(float(se), 6) if se == se else None,
        "t": round(media / se, 3) if se == se and se > 0 else None,
        "ic95": [round(media - 1.96 * se, 6), round(media + 1.96 * se, 6)]
        if se == se else None,
        "gana_b_en_filas": int((d > 0).sum()),
    }


def mde_from_sd(sd: float, n_proyectado: int, z: float = POTENCIA_Z) -> float:
    """Efecto minimo detectable: z * sd / sqrt(n), con z=2.8 (80% potencia)."""
    return float(z * sd / np.sqrt(n_proyectado))


def resolution_gate(
    sd_fold1: float,
    n_proyectado: int,
    mae_baseline: float,
    umbral_rel: float = MDE_REL_UMBRAL,
) -> dict:
    """Gate de resolucion de D-EXP-2, ahora ejecutable.

    Si el MDE supera `umbral_rel` del MAE baseline, el stat se declara SIN
    RESOLUCION: sus numeros descriptivos se emiten etiquetados, pero su
    comparacion inferencial NO se corre. Un experimento que no puede distinguir
    su propia expectativa de cero no debe pronunciarse.
    """
    mde = mde_from_sd(sd_fold1, n_proyectado)
    rel = mde / mae_baseline if mae_baseline > 0 else float("inf")
    return {
        "sd_fold1": round(sd_fold1, 6),
        "n_proyectado": int(n_proyectado),
        "mae_baseline": round(mae_baseline, 5),
        "mde_absoluto": round(mde, 6),
        "mde_relativo": round(rel, 5),
        "umbral_relativo": umbral_rel,
        "con_resolucion": bool(rel <= umbral_rel),
    }


def fit_ols(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """OLS con intercepto por minimos cuadrados. Devuelve los coeficientes.

    C2 necesita una REGLA DE COMBINACION (C1 es un producto desnudo; la señal
    del rival es otra escala). Para que la comparacion aisle la señal añadida y
    no el hecho de estar ajustado, AMBOS brazos se ajustan igual: C1 tambien
    pasa por OLS. Nota declarada: OLS minimiza error cuadratico y la metrica
    primaria es MAE — es la asimetria estandar, no un sesgo a favor de nadie.
    """
    A = np.column_stack([np.ones(len(X)), X])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    return coef


def apply_ols(coef: np.ndarray, X: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(len(X)), X]) @ coef


def freeze_audit_sample(ids: list[tuple[str, int]], n: int = AUDIT_N,
                        seed: int = AUDIT_SEED) -> list[tuple[str, int]]:
    """Muestra de auditoria humana, congelada con seed fijo.

    Se elige del universo elegible ANTES de computar metrica alguna: ninguna
    fila puede entrar por lucir bien.
    """
    rng = random.Random(seed)
    return sorted(rng.sample(ids, min(n, len(ids))))


# ---------------------------------------------------------------------------
# Lectura (GCS / BigQuery)
# ---------------------------------------------------------------------------


def make_store() -> CloudDataStore:
    """CloudDataStore EXPLICITO. Nunca get_datastore(): el modo no se hereda."""
    return CloudDataStore(
        project_id=settings.gcp_project_id,
        dataset=settings.bq_dataset,
        bucket_name=settings.gcs_bucket,
    )


def opponent_availability(
    store: CloudDataStore,
    games: pd.DataFrame,
    stats_roll: pd.DataFrame,
    teams: dict[str, int],
) -> dict[tuple[str, int], float]:
    """(game_id, team_id) -> disponibilidad ponderada del equipo, corpus GemBox.

    Reutiliza integramente la maquinaria auditada de D-EXP-2: vector de minutos
    de la rotacion reciente, pesos P(juega|estatus) del PDF publish, y NYS =
    sin ajuste (replica de 13e-2.5 caso 3).
    """
    pivot_seasons = [s for s in stats_roll["season"].unique() if s >= "2022-23"]
    denoms = dexp2.player_denominators(
        stats_roll[stats_roll["season"].isin(pivot_seasons)][
            ["game_id", "team_id", "player_id", "game_date", "minutes_rolling"]
        ],
        ROLLING_WINDOW_GAMES,
    )

    meta = games.set_index("game_id")[["game_date", "home_team_id", "away_team_id"]]
    por_fecha: dict[str, list[str]] = defaultdict(list)
    gem = games[games["season"].isin(GEMBOX_SEASONS)]
    for gid, fecha in zip(gem["game_id"], gem["game_date"].astype(str)):
        por_fecha[fecha].append(gid)

    salida: dict[tuple[str, int], float] = {}
    for season in GEMBOX_SEASONS:
        sufijos = dexp2.publish_suffixes(season)
        nombres = load_player_names_from_raw_json(
            settings.raw_dir, glob_pattern=f"002{season[2:4]}*.json")
        idx = NameIndex.from_player_map(nombres)
        fechas = sorted(f for f in por_fecha if f in sufijos)
        print(f"  C2 {season}: {len(fechas)} fechas", flush=True)
        for fecha in fechas:
            sufijo = sufijos.get(fecha)
            try:
                rows, nys = parse_pdf(dexp2.fetch_pdf(store, fecha, sufijo)) \
                    if sufijo else ([], [])
            except Exception as exc:  # noqa: BLE001 — se declara y se sigue
                log.error("PDF %s ilegible: %r", fecha, exc)
                rows, nys = [], []
            w2, _wb, _c = dexp2.build_weight_maps(rows, fecha, teams, idx)
            nys_tids = dexp2.nys_team_ids(nys, fecha, teams)
            for gid in por_fecha[fecha]:
                for tid in (int(meta.at[gid, "home_team_id"]),
                            int(meta.at[gid, "away_team_id"])):
                    vec = denoms.get((gid, tid))
                    if not vec:
                        continue
                    pesos = {} if tid in nys_tids else w2.get(tid, {})
                    salida[(gid, tid)] = dexp2.team_availability(vec, pesos)
    return salida


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="D-EXP-3: minutos x tasa vs rolling directo")
    ap.add_argument("--skip-c2", action="store_true", help="solo el brazo primario")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    store = make_store()

    print("Cargando fuentes...", flush=True)
    games = store.load_games()
    pgs = store.load_player_game_stats()
    teams = {dexp2.normalize_team(str(r["name"])): int(r["team_id"])
             for _, r in store.load_teams().iterrows()}
    print(f"  games {games.shape} | pgs {pgs.shape} | teams {len(teams)}", flush=True)

    df = pgs.merge(games[["game_id", "season", "game_date", "home_team_id", "away_team_id"]],
                   on="game_id", validate="many_to_one")
    df = derive_stats(df)
    df["game_date"] = df["game_date"].astype(str)

    n_total = len(df)
    played = df[df["minutes"] > 0].copy()
    n_dnp = n_total - len(played)
    roll = add_rollings(played)

    cols_needed = [f"b0_{s}" for s in STATS] + [f"c1_{s}" for s in STATS]
    universo = roll.dropna(subset=cols_needed)
    n_sin_historia = len(roll) - len(universo)
    universo = universo[universo["season"].isin(VALID_SEASONS)]
    print(f"\nUniverso: {n_total} filas totales | {n_dnp} DNP (minutes=0/NaN) "
          f"| {n_sin_historia} sin {MIN_PREV_GAMES} previos "
          f"| {len(universo)} en temporadas de validacion", flush=True)

    # ── Sanity de no-negatividad, ANTES de cualquier metrica ──
    for s in STATS:
        assert (universo[f"b0_{s}"] >= 0).all(), f"B0 negativo en {s}"
        assert (universo[f"c1_{s}"] >= 0).all(), f"C1 negativo en {s}"

    # ── Muestra de auditoria: congelada ANTES de computar metricas ──
    ids = list(zip(universo["game_id"], universo["player_id"].astype(int)))
    muestra = freeze_audit_sample(ids)
    idx_m = universo.set_index(["game_id", "player_id"])
    filas_audit = []
    for gid, pid in muestra:
        r = idx_m.loc[(gid, pid)]
        filas_audit.append({
            "game_id": gid, "player_id": int(pid), "game_date": str(r["game_date"]),
            "season": r["season"], "minutes": round(float(r["minutes"]), 2),
            **{f"real_{s}": float(r[s]) for s in STATS},
            **{f"b0_{s}": round(float(r[f"b0_{s}"]), 3) for s in STATS},
            **{f"c1_{s}": round(float(r[f"c1_{s}"]), 3) for s in STATS},
        })
    (OUT_DIR / "audit_sample.json").write_text(
        json.dumps({"seed": AUDIT_SEED, "n": len(filas_audit),
                    "congelada_antes_de_computar_metricas": True,
                    "filas": filas_audit}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    print(f"Muestra de auditoria congelada: {len(filas_audit)} filas "
          f"-> {OUT_DIR / 'audit_sample.json'}", flush=True)

    # ── FASE 0 DE POTENCIA (se escribe ANTES que results.json) ──
    fold1 = universo[universo["season"] == VALID_SEASONS[0]]
    fase0: dict[str, Any] = {
        "generado_utc": datetime.now(timezone.utc).isoformat(),
        "regla": "stat con MDE relativo > 3% del MAE baseline = SIN RESOLUCION",
        "n_fold1": int(len(fold1)), "n_proyectado_total": int(len(universo)),
        "primario_c1_vs_b0": {},
    }
    for s in STATS:
        p = paired_mae_diff(fold1[s], fold1[f"b0_{s}"], fold1[f"c1_{s}"])
        mae_b0 = float(np.abs(fold1[s] - fold1[f"b0_{s}"]).mean())
        fase0["primario_c1_vs_b0"][s] = resolution_gate(p["sd"], len(universo), mae_b0)
    (OUT_DIR / "mde_fase0.json").write_text(
        json.dumps(fase0, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFASE 0 escrita -> {OUT_DIR / 'mde_fase0.json'}")
    print(f"  {'stat':<6}{'MAE B0':>9}{'MDE abs':>10}{'MDE rel':>10}  resolucion")
    for s in STATS:
        g = fase0["primario_c1_vs_b0"][s]
        print(f"  {s:<6}{g['mae_baseline']:>9.4f}{g['mde_absoluto']:>10.5f}"
              f"{g['mde_relativo']:>10.4f}  {'SI' if g['con_resolucion'] else 'SIN RESOLUCION'}")

    con_res = [s for s in STATS if fase0["primario_c1_vs_b0"][s]["con_resolucion"]]
    if not con_res:
        print("\nLOS CUATRO STATS SIN RESOLUCION -> DETENERSE tras fase 0 "
              "(resultado entregable por la regla pre-registrada).")
        return

    # ── Brazo primario: C1 vs B0 por stat x fold ──
    resultados: dict[str, Any] = {
        "generado_utc": datetime.now(timezone.utc).isoformat(),
        "stats_con_resolucion": con_res,
        "exclusiones": {"filas_totales": n_total, "dnp_o_cero_minutos": n_dnp,
                        "sin_historia_suficiente": n_sin_historia,
                        "universo_validacion": int(len(universo))},
        "primario": {}, "secundario_c2": {},
    }
    lineas: list[str] = ["BRAZO PRIMARIO — C1 (minutos x tasa) vs B0 (rolling directo)"]
    for s in STATS:
        etiqueta = "" if s in con_res else "   [SIN RESOLUCION: descriptivo, no inferencial]"
        lineas.append(f"\n  {s.upper()}{etiqueta}")
        lineas.append(f"    {'fold':<10}{'n':>7}{'MAE B0':>10}{'MAE C1':>10}"
                      f"{'RMSE B0':>10}{'RMSE C1':>10}{'dif':>10}{'t':>8}")
        por_fold = {}
        for season in VALID_SEASONS:
            v = universo[universo["season"] == season]
            if v.empty:
                continue
            y = v[s].to_numpy(dtype=float)
            b0 = v[f"b0_{s}"].to_numpy(dtype=float)
            c1 = v[f"c1_{s}"].to_numpy(dtype=float)
            assert len(b0) == len(c1) == len(y), "paridad de universo rota"
            p = paired_mae_diff(y, b0, c1)
            fila = {
                "n": p["n"],
                "mae_b0": round(float(np.abs(y - b0).mean()), 5),
                "mae_c1": round(float(np.abs(y - c1).mean()), 5),
                "rmse_b0": round(float(np.sqrt(((y - b0) ** 2).mean())), 5),
                "rmse_c1": round(float(np.sqrt(((y - c1) ** 2).mean())), 5),
                "pareado": p,
            }
            por_fold[season] = fila
            t = p["t"] if s in con_res else None
            lineas.append(
                f"    {season:<10}{p['n']:>7}{fila['mae_b0']:>10.4f}{fila['mae_c1']:>10.4f}"
                f"{fila['rmse_b0']:>10.4f}{fila['rmse_c1']:>10.4f}"
                f"{p['media']:>+10.4f}{(f'{t:.2f}' if t is not None else '—'):>8}")
        # Agregado sobre todas las temporadas de validacion
        y = universo[s].to_numpy(dtype=float)
        pa = paired_mae_diff(y, universo[f"b0_{s}"].to_numpy(dtype=float),
                             universo[f"c1_{s}"].to_numpy(dtype=float))
        mae_b0 = float(np.abs(y - universo[f"b0_{s}"].to_numpy(dtype=float)).mean())
        resultados["primario"][s] = {
            "con_resolucion": s in con_res, "por_fold": por_fold,
            "agregado": pa, "mae_b0_agregado": round(mae_b0, 5),
            "mejora_relativa": round(pa["media"] / mae_b0, 5) if mae_b0 else None,
            "folds_con_ganancia": sum(1 for f in por_fold.values()
                                      if f["pareado"]["media"] > 0),
            "folds_totales": len(por_fold),
        }
        ic = pa["ic95"]
        lineas.append(
            f"    {'AGREGADO':<10}{pa['n']:>7}{mae_b0:>10.4f}"
            f"{mae_b0 - pa['media']:>10.4f}{'':>10}{'':>10}"
            f"{pa['media']:>+10.4f}{pa['t']:>8.2f}\n"
            f"      mejora relativa {pa['media'] / mae_b0:+.2%}  "
            f"IC95 [{ic[0]:+.4f}, {ic[1]:+.4f}]  "
            f"gana C1 en {pa['gana_b_en_filas']}/{pa['n']} filas")

    # ── Brazo secundario C2 (GemBox) ──
    if not args.skip_c2:
        print("\nCalculando disponibilidad del rival (corpus GemBox)...", flush=True)
        # La rotacion se arma con TODAS las filas (los DNP pertenecen a la
        # plantilla y su minutes_rolling se propaga por ffill), igual que en
        # D-EXP-2 y en availability.py. Alimentarla solo con filas jugadas
        # encogeria el denominador y produciria otra feature.
        stats_all = _add_player_minutes_rolling(df, ROLLING_WINDOW_GAMES)
        avail = opponent_availability(store, games, stats_all, teams)
        gem = universo[universo["season"].isin(GEMBOX_SEASONS)].copy()
        gem["opp_team_id"] = np.where(
            gem["team_id"] == gem["home_team_id"], gem["away_team_id"], gem["home_team_id"])
        gem["opp_avail"] = [
            avail.get((g, int(t)), np.nan)
            for g, t in zip(gem["game_id"], gem["opp_team_id"])]
        n_sin_avail = int(gem["opp_avail"].isna().sum())
        gem = gem.dropna(subset=["opp_avail"])
        print(f"  {len(gem)} filas GemBox con señal del rival "
              f"({n_sin_avail} sin señal, excluidas)")

        lineas.append("\n\nBRAZO SECUNDARIO — C2 (C1 + disponibilidad del rival) vs C1")
        lineas.append("  ambos brazos ajustados por OLS sobre el train del fold: "
                      "la comparacion aisla la señal añadida, no el ajuste")
        resultados["exclusiones"]["c2_sin_señal_rival"] = n_sin_avail
        for s in STATS:
            lineas.append(f"\n  {s.upper()}")
            lineas.append(f"    {'fold':<8}{'n_tr':>8}{'n_va':>8}{'MAE C1':>10}"
                          f"{'MAE C2':>10}{'dif':>10}{'t':>8}   MDE rel")
            resultados["secundario_c2"][s] = {}
            for nombre, tr_s, va_s in C2_FOLDS:
                tr = gem[gem["season"].isin(tr_s)]
                va = gem[gem["season"] == va_s]
                if tr.empty or va.empty:
                    continue
                x1_tr = tr[[f"c1_{s}"]].to_numpy(dtype=float)
                x2_tr = tr[[f"c1_{s}", "opp_avail"]].to_numpy(dtype=float)
                y_tr = tr[s].to_numpy(dtype=float)
                c1_hat = apply_ols(fit_ols(x1_tr, y_tr),
                                   va[[f"c1_{s}"]].to_numpy(dtype=float))
                c2_hat = apply_ols(fit_ols(x2_tr, y_tr),
                                   va[[f"c1_{s}", "opp_avail"]].to_numpy(dtype=float))
                y_va = va[s].to_numpy(dtype=float)
                assert len(c1_hat) == len(c2_hat) == len(y_va), "paridad de universo rota"
                p = paired_mae_diff(y_va, c1_hat, c2_hat)
                mae_c1 = float(np.abs(y_va - c1_hat).mean())
                gate = resolution_gate(p["sd"], len(va), mae_c1)
                resultados["secundario_c2"][s][nombre] = {
                    "train": list(tr_s), "valid": va_s,
                    "n_train": int(len(tr)), "n_valid": int(len(va)),
                    "mae_c1": round(mae_c1, 5),
                    "mae_c2": round(float(np.abs(y_va - c2_hat).mean()), 5),
                    "pareado": p, "gate": gate,
                }
                lineas.append(
                    f"    {nombre:<8}{len(tr):>8}{len(va):>8}{mae_c1:>10.4f}"
                    f"{float(np.abs(y_va - c2_hat).mean()):>10.4f}"
                    f"{p['media']:>+10.4f}{p['t']:>8.2f}   "
                    f"{gate['mde_relativo']:.4f} "
                    f"{'OK' if gate['con_resolucion'] else 'SIN RESOLUCION'}")

    (OUT_DIR / "results.json").write_text(
        json.dumps(resultados, indent=2, ensure_ascii=False), encoding="utf-8")
    tabla = "\n".join(lineas)
    (OUT_DIR / "tabla.txt").write_text(tabla, encoding="utf-8")
    print("\n" + tabla)
    print(f"\nEscrito en {OUT_DIR}/: mde_fase0.json, results.json, tabla.txt, "
          f"audit_sample.json")


if __name__ == "__main__":
    main()
