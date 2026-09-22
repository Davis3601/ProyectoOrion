"""
D-EXP-2 — CANDIDATO disponibilidad-v2, OFFLINE.

*** SOLO LECTURA DE LA NUBE. *** PDFs publish de
gs://{bucket}/raw/injury_reports/, features_v1 del parquet canonico de GCS y
player_game_stats/games de BigQuery, con CloudDataStore construido
EXPLICITAMENTE (convencion de scripts). Escribe UNICAMENTE en
data/experiment_v2/. No despliega nada: el candidato vive y muere aqui.
features_v1.parquet, B-limpia, el registry y el endpoint quedan intactos.

QUE MIDE: si ponderar availability_diff por P(juega | estatus) (pesos oficiales
de D-EXP-1) mejora el log loss frente a la binaria de v1, en folds restringidos
con control re-entrenado en los MISMOS datos.

POR QUE LA CONSTRUCCION ES LA DE PRODUCCION, NO LA DEL PARQUET: la ruta en vivo
(live_lookup, modo v0) calcula
    numerator = denominator - suma de minutos rolling de los ausentes declarados
sobre la ROTACION RECIENTE del equipo. Eso es exactamente una ponderacion
binaria (0 para el ausente, 1 para el resto) del mismo denominador. v2 sustituye
ese 0/1 por peso[estatus]. El parquet, en cambio, arma el numerador con los
jugadores ACTIVADOS del boxscore (interpretacion B) — informacion posterior al
tip-off que la prediccion en vivo no tiene.

CERO LEAKAGE POR CONSTRUCCION: el corte publish es el ultimo PDF creado antes de
las 13:00 CDMX, anterior a todo tip-off de la fecha; los minutos rolling llevan
shift(1) del pipeline oficial. Ningun dato posterior al corte entra a la feature.

Uso:
    python scripts/experiment_disponibilidad_v2.py
    python scripts/experiment_disponibilidad_v2.py --limit-dates 10   # humo
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from nba_predictor.config import ROLLING_WINDOW_GAMES, LOGREG_C, settings
from nba_predictor.features.availability import _add_player_minutes_rolling
from nba_predictor.ingestion.injury_report import (
    InjuryStatus,
    NameIndex,
    load_player_names_from_raw_json,
    parse_pdf,
)
from nba_predictor.models.evaluation import _accuracy, _brier, _log_loss
from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS, _extract_coefs, _fit_fold
from nba_predictor.storage.cloud import CloudDataStore, _gcs_injury_report_path

log = logging.getLogger("dexp2")

OUT_DIR = Path("data/experiment_v2")
BACKFILL_DIR = Path("data/backfill_injury")
# Cache de PDFs de D-EXP-1: mismos objetos de GCS, ya en disco. Solo lectura.
SHARED_CACHE = Path("data/experiment_pjuega/cache")
CACHE_DIR = OUT_DIR / "cache"

# SOLO GemBox: el parser auditado de 13e-1. iTextSharp prohibido hasta D-RES-3.
SEASONS: tuple[str, ...] = ("2023-24", "2024-25", "2025-26")
# Temporadas cuyas filas alimentan el pivote de rotacion (>= 10 partidos de
# holgura sobre la ventana; los minutos rolling se calculan con TODA la historia).
PIVOT_FROM_SEASON = "2022-23"

FOLDS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("A", ("2023-24",), "2024-25"),
    ("B", ("2023-24", "2024-25"), "2025-26"),
)

# ---------------------------------------------------------------------------
# PESOS OFICIALES (D-EXP-1, congelados en CLAUDE.md antes de computar nada)
# ---------------------------------------------------------------------------
W_OUT = 0.00
W_DOUBTFUL = 0.02
W_QUESTIONABLE = 0.48
W_PROBABLE = 0.91
W_AVAILABLE_GLEAGUE = 0.45   # descomposicion de la adjudicacion 2
W_AVAILABLE_OTRO = 0.77
W_NO_LISTADO = 1.0

_GLEAGUE_RE = re.compile(r"g\s*league|two\s*-?\s*way", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Funciones PURAS — sin red, sin BigQuery, unit-testeables
# ---------------------------------------------------------------------------


def to_mdy(fecha_iso: str) -> str:
    """'2023-11-24' -> '11/24/2023', el formato de game_date del PDF."""
    y, m, d = fecha_iso.split("-")
    return f"{m}/{d}/{y}"


def normalize_team(nombre: str) -> str:
    """Alfanumerico en minusculas + alias LA. Conserva digitos ('76ers')."""
    clave = re.sub(r"[^a-z0-9]", "", nombre.lower())
    return {"laclippers": "losangelesclippers",
            "lalakers": "losangeleslakers"}.get(clave, clave)


def weight_v2(status: str, reason: str | None) -> float:
    """Peso P(juega | estatus) de una fila del PDF.

    'Available' NO tiene peso unico (adjudicacion 2 de D-EXP-1: la etiqueta
    mezcla poblaciones). Se descompone por razon: G-League/Two-Way frente al
    resto, con los valores medidos.
    """
    if status == InjuryStatus.OUT.value:
        return W_OUT
    if status == InjuryStatus.DOUBTFUL.value:
        return W_DOUBTFUL
    if status == InjuryStatus.QUESTIONABLE.value:
        return W_QUESTIONABLE
    if status == InjuryStatus.PROBABLE.value:
        return W_PROBABLE
    if status == InjuryStatus.AVAILABLE.value:
        return W_AVAILABLE_GLEAGUE if _GLEAGUE_RE.search(reason or "") else W_AVAILABLE_OTRO
    return W_NO_LISTADO


def weight_binary_out(status: str, reason: str | None) -> float:
    """Peso binario de la ruta de produccion v1: solo Out resta disponibilidad.

    Decision 2 del feed ('Solo status==Out cuenta como ausencia en v1').
    Es el BRAZO DIAGNOSTICO que aisla el efecto de la ponderacion: comparte
    construccion y escala con v2 y solo cambia la tabla de pesos.
    """
    return 0.0 if status == InjuryStatus.OUT.value else 1.0


def team_availability(
    player_denom: dict[int, float],
    weights: dict[int, float],
) -> float:
    """Disponibilidad de UN equipo: sum(peso_p * minutos_p) / sum(minutos_p).

    player_denom: minutos rolling de cada jugador de la rotacion reciente
                  (identico al denominador de v1 y de live_lookup).
    weights     : peso por player_id; ausente del dict = 1.0 (no listado).

    Denominador 0 (equipo sin historia) -> NaN, como en el pipeline oficial.
    """
    denominador = sum(player_denom.values())
    if denominador <= 0:
        return float("nan")
    numerador = sum(weights.get(pid, W_NO_LISTADO) * mins for pid, mins in player_denom.items())
    return numerador / denominador


def build_weight_maps(
    rows: list[Any],
    fecha_iso: str,
    team_index: dict[str, int],
    name_index: NameIndex,
) -> tuple[dict[int, dict[int, float]], dict[int, dict[int, float]], dict[str, int]]:
    """Pesos por (team_id -> player_id -> peso) para v2 y para el binario.

    Solo filas cuyo game_date ES la fecha objetivo: el PDF es multi-fecha y las
    del dia siguiente describen otro partido.

    Devuelve tambien contadores: filas usadas, sin match de nombre (se tratan
    como no listadas, peso 1.0 — mismo trato que v1 da a lo que no ve) y sin
    match de equipo.
    """
    objetivo = to_mdy(fecha_iso)
    w2: dict[int, dict[int, float]] = defaultdict(dict)
    wb: dict[int, dict[int, float]] = defaultdict(dict)
    cont = {"filas": 0, "sin_match_nombre": 0, "sin_match_equipo": 0}

    for r in rows:
        if r.game_date != objetivo:
            continue
        cont["filas"] += 1
        tid = team_index.get(normalize_team(r.team))
        if tid is None:
            cont["sin_match_equipo"] += 1
            continue
        pid = name_index.match(r.player_name)
        if pid is None:
            cont["sin_match_nombre"] += 1
            continue
        status = r.status.value if isinstance(r.status, InjuryStatus) else str(r.status)
        w2[tid][pid] = weight_v2(status, r.reason)
        wb[tid][pid] = weight_binary_out(status, r.reason)
    return dict(w2), dict(wb), cont


def nys_team_ids(nys: list[Any], fecha_iso: str, team_index: dict[str, int]) -> set[int]:
    """team_ids con reporte NO ENTREGADO para la fecha objetivo.

    Replica de la condicion de produccion (13e-2.5 caso 3): sin reporte no hay
    ausencias que aplicar, asi que el equipo va SIN AJUSTE (todos los pesos 1.0,
    disponibilidad 1.0). Se cuenta aparte porque es degradacion declarada, no
    informacion de que el equipo este sano.
    """
    objetivo = to_mdy(fecha_iso)
    out: set[int] = set()
    for e in nys:
        if getattr(e, "game_date", None) != objetivo:
            continue
        tid = team_index.get(normalize_team(e.team))
        if tid is not None:
            out.add(tid)
    return out


def fold_metrics(
    df: pd.DataFrame,
    cols: list[str],
    train_seasons: tuple[str, ...],
    valid_season: str,
) -> dict:
    """Entrena la logistica en train_seasons y evalua en valid_season.

    Misma maquinaria del pipeline oficial por IMPORT (_fit_fold: StandardScaler
    ajustado SOLO con train, LogisticRegression L2 con LOGREG_C). El scaler
    jamas ve validacion.
    """
    tr = df[df["season"].isin(train_seasons)]
    va = df[df["season"] == valid_season]
    scaler, lr = _fit_fold(tr[cols].to_numpy(), tr["home_won"].to_numpy(), C=LOGREG_C)
    p = lr.predict_proba(scaler.transform(va[cols].to_numpy()))[:, 1]
    y = va["home_won"].to_numpy()
    return {
        "n_train": int(len(tr)),
        "n_valid": int(len(va)),
        "log_loss": float(_log_loss(y, p)),
        "brier": float(_brier(y, p)),
        "accuracy": float(_accuracy(y, p)),
        "coefs": [(c, round(v, 5)) for c, v in _extract_coefs(lr, cols)],
        "intercept": float(lr.intercept_[0]),
        "_p": p,
        "_y": y,
    }


def paired_diff(y: Any, p_a: Any, p_b: Any) -> dict:
    """Diferencia PAREADA de log loss por partido entre dos arreglos.

    d_i = LL_i(A) - LL_i(B): positivo = B (el candidato) predice mejor ESE
    partido. Parear es obligatorio aqui — ambos arreglos predicen exactamente
    los mismos partidos, y la varianza entre partidos (decenas de veces mayor
    que la diferencia entre modelos) se cancela al restar.

    El estadistico t sobre d responde la pregunta del pre-registro ("fuera del
    ruido"). No sustituye a la puerta de decision: la adjudica Antonio.
    """
    import numpy as np

    eps = 1e-15
    y = np.asarray(y, dtype=float)
    a = np.clip(np.asarray(p_a, dtype=float), eps, 1 - eps)
    b = np.clip(np.asarray(p_b, dtype=float), eps, 1 - eps)
    ll_a = -(y * np.log(a) + (1 - y) * np.log(1 - a))
    ll_b = -(y * np.log(b) + (1 - y) * np.log(1 - b))
    d = ll_a - ll_b
    n = len(d)
    media = float(d.mean())
    sd = float(d.std(ddof=1))
    se = sd / (n ** 0.5) if n > 1 and sd > 0 else float("nan")
    return {
        "n": n,
        "media": round(media, 6),
        "sd": round(sd, 6),
        "se": round(se, 6),
        "t": round(media / se, 3) if se == se and se > 0 else None,
        "ic95": [round(media - 1.96 * se, 6), round(media + 1.96 * se, 6)],
        "gana_b_en_partidos": int((d > 0).sum()),
    }


def distribution_report(df: pd.DataFrame) -> dict:
    """Cuantas filas cambian de valor entre v1 y v2, y cuanto.

    Si hay mejora, su mecanismo tiene que ser visible aqui: sin filas movidas
    no hay nada que explicar una diferencia de log loss.
    """
    d = df["availability_diff_v2"] - df["availability_diff"]
    dv = df["availability_diff_v2"] - df["availability_diff_bin"]
    return {
        "n": int(len(df)),
        "v1": {"mean": float(df["availability_diff"].mean()),
               "std": float(df["availability_diff"].std()),
               "min": float(df["availability_diff"].min()),
               "max": float(df["availability_diff"].max())},
        "v2": {"mean": float(df["availability_diff_v2"].mean()),
               "std": float(df["availability_diff_v2"].std()),
               "min": float(df["availability_diff_v2"].min()),
               "max": float(df["availability_diff_v2"].max())},
        "binario": {"mean": float(df["availability_diff_bin"].mean()),
                    "std": float(df["availability_diff_bin"].std())},
        "v2_vs_v1": {"filas_distintas": int((d.abs() > 1e-12).sum()),
                     "abs_media": float(d.abs().mean()),
                     "abs_p95": float(d.abs().quantile(0.95)),
                     "abs_max": float(d.abs().max())},
        "v2_vs_binario": {"filas_distintas": int((dv.abs() > 1e-12).sum()),
                          "abs_media": float(dv.abs().mean()),
                          "abs_p95": float(dv.abs().quantile(0.95)),
                          "abs_max": float(dv.abs().max())},
    }


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


def fetch_pdf(store: CloudDataStore, fecha: str, sufijo: str) -> bytes:
    """PDF de GCS. Reutiliza el cache de D-EXP-1 si ya lo bajo (solo lectura)."""
    compartido = SHARED_CACHE / f"{fecha}_{sufijo}.pdf"
    if compartido.exists():
        return compartido.read_bytes()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local = CACHE_DIR / f"{fecha}_{sufijo}.pdf"
    if local.exists():
        return local.read_bytes()
    raw = store._gcs.bucket(store.bucket_name).blob(
        store._gcs_path(_gcs_injury_report_path(fecha, sufijo))
    ).download_as_bytes()
    local.write_bytes(raw)
    return raw


def publish_suffixes(season: str) -> dict[str, str | None]:
    """fecha -> sufijo del corte publish archivado por el backfill D-RES-2."""
    data = json.loads((BACKFILL_DIR / f"{season}.json").read_text(encoding="utf-8"))
    return {r["date"]: r["cuts"].get("publish", {}).get("suffix") for r in data["records"]}


def player_denominators(
    stats: pd.DataFrame,
    window: int,
) -> dict[tuple[str, int], dict[int, float]]:
    """(game_id, team_id) -> {player_id: minutos rolling} de la rotacion reciente.

    ADAPTACION de _compute_denominator de availability.py (no edicion del
    paquete): identica aritmetica — pivote rank x player, shift(1),
    ffill(limit=window-1), fillna(0) — pero conserva el VECTOR por jugador en
    vez de sumarlo, que es lo que la ponderacion necesita. La suma de este
    vector es, por construccion, el denominador exacto de v1.
    """
    team_games = (
        stats[["team_id", "game_id", "game_date"]]
        .drop_duplicates()
        .sort_values(["team_id", "game_date"], kind="stable")
    )
    team_games["team_game_rank"] = team_games.groupby("team_id").cumcount()
    stats_r = stats.merge(team_games[["game_id", "team_id", "team_game_rank"]],
                          on=["game_id", "team_id"])

    salida: dict[tuple[str, int], dict[int, float]] = {}
    for team_id, tg in stats_r.groupby("team_id"):
        pivot = tg.pivot_table(index="team_game_rank", columns="player_id",
                               values="minutes_rolling", aggfunc="last")
        pivot = pivot.reindex(sorted(tg["team_game_rank"].unique()))
        shifted = pivot.shift(1)
        if window > 1:
            shifted = shifted.ffill(limit=window - 1)
        matriz = shifted.fillna(0.0)

        rank_to_game = (
            tg[["team_game_rank", "game_id"]].drop_duplicates()
            .set_index("team_game_rank")["game_id"]
        )
        for rank, fila in matriz.iterrows():
            vivos = fila[fila > 0]
            salida[(rank_to_game[rank], int(team_id))] = {
                int(p): float(v) for p, v in vivos.items()
            }
    return salida


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="D-EXP-2: candidato disponibilidad-v2 offline")
    ap.add_argument("--limit-dates", type=int, default=None, help="N fechas por temporada (humo)")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    store = make_store()

    print("Cargando fuentes...", flush=True)
    features = store.load_features()
    games = store.load_games()
    pgs = store.load_player_game_stats()
    teams = {normalize_team(str(r["name"])): int(r["team_id"])
             for _, r in store.load_teams().iterrows()}
    print(f"  features {features.shape} | games {games.shape} | pgs {pgs.shape} "
          f"| teams {len(teams)}", flush=True)

    # Minutos rolling: sobre la historia COMPLETA (el rolling cruza temporadas).
    stats = pgs.merge(games[["game_id", "season", "game_date"]], on="game_id",
                      validate="many_to_one")
    stats = _add_player_minutes_rolling(stats, ROLLING_WINDOW_GAMES)

    pivot_seasons = sorted({s for s in stats["season"].unique() if s >= PIVOT_FROM_SEASON})
    print(f"Pivote de rotacion sobre {pivot_seasons}", flush=True)
    denoms = player_denominators(
        stats[stats["season"].isin(pivot_seasons)], ROLLING_WINDOW_GAMES)
    print(f"  {len(denoms)} vectores (game_id, team_id)", flush=True)

    meta = games.set_index("game_id")[["game_date", "home_team_id", "away_team_id", "season"]]
    juegos_por_fecha: dict[str, list[str]] = defaultdict(list)
    objetivo = features[features["season"].isin(SEASONS)]["game_id"].tolist()
    for gid in objetivo:
        juegos_por_fecha[str(meta.at[gid, "game_date"])].append(gid)

    filas: list[dict] = []
    cont_glob = {"filas_pdf": 0, "sin_match_nombre": 0, "sin_match_equipo": 0,
                 "fechas_sin_pdf": 0, "equipos_nys": 0, "sin_rotacion": 0,
                 "equipos_sin_ajuste": 0}
    nys_ids_vistos: set[int] = set()

    for season in SEASONS:
        sufijos = publish_suffixes(season)
        nombres = load_player_names_from_raw_json(
            settings.raw_dir, glob_pattern=f"002{season[2:4]}*.json")
        idx = NameIndex.from_player_map(nombres)
        fechas = sorted(f for f in juegos_por_fecha if f in sufijos)
        if args.limit_dates:
            fechas = fechas[: args.limit_dates]
        print(f"\n=== {season}: {len(fechas)} fechas con partidos en features ===", flush=True)

        for n, fecha in enumerate(fechas, 1):
            sufijo = sufijos.get(fecha)
            if sufijo:
                try:
                    rows, nys = parse_pdf(fetch_pdf(store, fecha, sufijo))
                except Exception as exc:  # noqa: BLE001 — se declara y se sigue
                    log.error("PDF %s ilegible: %r", fecha, exc)
                    rows, nys = [], []
            else:
                cont_glob["fechas_sin_pdf"] += 1
                rows, nys = [], []

            w2, wb, cont = build_weight_maps(rows, fecha, teams, idx)
            for k in ("filas", "sin_match_nombre", "sin_match_equipo"):
                cont_glob["filas_pdf" if k == "filas" else k] += cont[k]
            nys_tids = nys_team_ids(nys, fecha, teams)
            nys_ids_vistos |= nys_tids

            for gid in juegos_por_fecha[fecha]:
                home = int(meta.at[gid, "home_team_id"])
                away = int(meta.at[gid, "away_team_id"])
                dh = denoms.get((gid, home))
                da = denoms.get((gid, away))
                if not dh or not da:
                    cont_glob["sin_rotacion"] += 1
                    continue
                for tid in (home, away):
                    if tid in nys_tids:
                        cont_glob["equipos_nys"] += 1
                    if tid in nys_tids or tid not in w2:
                        cont_glob["equipos_sin_ajuste"] += 1
                # NYS = sin ajuste (pesos 1.0): replica de 13e-2.5 caso 3.
                h2 = {} if home in nys_tids else w2.get(home, {})
                a2 = {} if away in nys_tids else w2.get(away, {})
                hb = {} if home in nys_tids else wb.get(home, {})
                ab = {} if away in nys_tids else wb.get(away, {})
                filas.append({
                    "game_id": gid,
                    "availability_diff_v2": team_availability(dh, h2) - team_availability(da, a2),
                    "availability_diff_bin": team_availability(dh, hb) - team_availability(da, ab),
                })
            if n % 40 == 0:
                print(f"  {n}/{len(fechas)} fechas", flush=True)

    nuevas = pd.DataFrame(filas)
    df = features.merge(nuevas, on="game_id", how="inner")
    antes = len(df)
    df = df.dropna(subset=["availability_diff_v2", "availability_diff_bin"])
    print(f"\nUniverso: {antes} filas cruzadas, {len(df)} tras dropna "
          f"({antes - len(df)} con disponibilidad NaN)")

    cols_v2 = [c if c != "availability_diff" else "availability_diff_v2"
               for c in OFFICIAL_LOGISTIC_COLS]
    cols_bin = [c if c != "availability_diff" else "availability_diff_bin"
                for c in OFFICIAL_LOGISTIC_COLS]

    resultados: dict[str, Any] = {"folds": {}, "contadores": cont_glob,
                                  "equipos_nys_distintos": len(nys_ids_vistos),
                                  "pesos": {"Out": W_OUT, "Doubtful": W_DOUBTFUL,
                                            "Questionable": W_QUESTIONABLE,
                                            "Probable": W_PROBABLE,
                                            "Available_gleague": W_AVAILABLE_GLEAGUE,
                                            "Available_otro": W_AVAILABLE_OTRO}}
    lineas: list[str] = []
    for nombre, train, valid in FOLDS:
        ctrl = fold_metrics(df, OFFICIAL_LOGISTIC_COLS, train, valid)
        cand = fold_metrics(df, cols_v2, train, valid)
        diag = fold_metrics(df, cols_bin, train, valid)
        assert ctrl["n_valid"] == cand["n_valid"] == diag["n_valid"], "paridad de universo rota"
        resultados["folds"][nombre] = {
            "train": list(train), "valid": valid,
            "n_train": ctrl["n_train"], "n_valid": ctrl["n_valid"],
            "control": {k: v for k, v in ctrl.items() if not k.startswith("_")},
            "candidato_v2": {k: v for k, v in cand.items() if not k.startswith("_")},
            "diagnostico_binario": {k: v for k, v in diag.items() if not k.startswith("_")},
            "ganancia_ll_v2_vs_control": round(ctrl["log_loss"] - cand["log_loss"], 5),
            "ganancia_ll_v2_vs_binario": round(diag["log_loss"] - cand["log_loss"], 5),
            "ganancia_ll_binario_vs_control": round(ctrl["log_loss"] - diag["log_loss"], 5),
            "pareado_v2_vs_control": paired_diff(ctrl["_y"], ctrl["_p"], cand["_p"]),
            "pareado_v2_vs_binario": paired_diff(ctrl["_y"], diag["_p"], cand["_p"]),
            "pareado_binario_vs_control": paired_diff(ctrl["_y"], ctrl["_p"], diag["_p"]),
        }
        pv = resultados["folds"][nombre]["pareado_v2_vs_control"]
        pb = resultados["folds"][nombre]["pareado_v2_vs_binario"]
        lineas.append(
            f"\nFOLD {nombre}  train={'+'.join(train)} ({ctrl['n_train']})  "
            f"valid={valid} ({ctrl['n_valid']})\n"
            f"  {'arreglo':<22}{'log loss':>10}{'Brier':>10}{'acc':>9}{'vs control':>12}\n"
            f"  {'control (v1)':<22}{ctrl['log_loss']:>10.5f}{ctrl['brier']:>10.5f}"
            f"{ctrl['accuracy']:>9.3f}{'—':>12}\n"
            f"  {'candidato v2':<22}{cand['log_loss']:>10.5f}{cand['brier']:>10.5f}"
            f"{cand['accuracy']:>9.3f}{ctrl['log_loss'] - cand['log_loss']:>+12.5f}\n"
            f"  {'diagnostico binario':<22}{diag['log_loss']:>10.5f}{diag['brier']:>10.5f}"
            f"{diag['accuracy']:>9.3f}{ctrl['log_loss'] - diag['log_loss']:>+12.5f}\n"
            f"  pareado v2 vs control : media {pv['media']:+.5f}  t={pv['t']}  "
            f"IC95 [{pv['ic95'][0]:+.5f}, {pv['ic95'][1]:+.5f}]\n"
            f"  pareado v2 vs binario : media {pb['media']:+.5f}  t={pb['t']}  "
            f"IC95 [{pb['ic95'][0]:+.5f}, {pb['ic95'][1]:+.5f}]"
        )
        for etiqueta, res, col in (("control", ctrl, "availability_diff"),
                                   ("candidato", cand, "availability_diff_v2"),
                                   ("binario", diag, "availability_diff_bin")):
            coef = dict(res["coefs"])[col]
            lineas.append(f"  coef {col} ({etiqueta}): {coef:+.5f}")

    resultados["distribucion"] = distribution_report(df)

    (OUT_DIR / "results.json").write_text(
        json.dumps(resultados, indent=2, ensure_ascii=False), encoding="utf-8")
    df[["game_id", "season", "game_date", "availability_diff",
        "availability_diff_v2", "availability_diff_bin", "home_won"]].to_csv(
        OUT_DIR / "availability_comparado.csv", index=False)

    tabla = "\n".join(lineas)
    (OUT_DIR / "tabla.txt").write_text(tabla, encoding="utf-8")
    print(tabla)
    print("\nDISTRIBUCION:", json.dumps(resultados["distribucion"], indent=2))
    print("CONTADORES:", json.dumps(cont_glob, indent=2))
    print(f"\nEscrito en {OUT_DIR}/: results.json, tabla.txt, availability_comparado.csv")


if __name__ == "__main__":
    main()
