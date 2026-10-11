"""
D-EXP-2b — RE-MEDICION de disponibilidad-v2 CON CORPUS AMPLIADO.

*** OFFLINE, SOLO LECTURA DE LA NUBE. *** B-limpia intacta,
features_v1.parquet intacto, nada se despliega. CloudDataStore construido
EXPLICITAMENTE (convencion de scripts). Nada se escribe fuera de
data/experiment_v2b/.

QUE RESUELVE: D-EXP-2 murio de falta de RESOLUCION — su MDE era 0.0096/0.0121
contra un rango esperado de 0.000-0.008, asi que no podia distinguir su propia
expectativa de cero. Con el parser legacy auditado (D-RES-3 cuerpo, D-RES-3e
fronteras) y los pesos demostrados PORTABLES entre eras (D-EXP-5), el corpus
pasa de 2 folds a 7 y la pregunta se vuelve contestable.

LOS CUATRO REQUISITOS registrados al cerrar D-RES-3e:
  1. MDE computado en FASE 0, como gate ejecutable (regla de D-EXP-2).
  2. V2 vs BIN como contraste PRIMARIO: misma construccion en ambos brazos, y
     es la hipotesis exacta que sobrevivio al brazo diagnostico de D-EXP-2
     ("ponderar ESTABILIZA frente al binario de misma construccion").
  3. CONFOUND DE CONSTRUCCION declarado desde el diseño: CTL (B-limpia
     re-entrenada) usa el numerador de ACTIVADOS del boxscore; BIN y V2 usan
     rotacion PRONOSTICADA desde el PDF. CTL se reporta, no decide.
  4. PESOS POR FOLD desde el TRAIN de cada fold. Walk-forward puro: pesos
     globales medidos sobre todo el corpus se filtrarian al fold de
     validacion — leakage por la puerta de atras, invisible en la tabla.

DE DONDE SALEN LOS PESOS (decision declarada): de los ficheros de instancias
que YA produjeron D-EXP-1 (GemBox, 2023-24..2025-26) y D-EXP-5 (legacy,
2018-19..2022-23), agregados POR TEMPORADA y pooleados sobre las temporadas de
train de cada fold. Es la MISMA medicion auditada, no una re-derivacion: volver
a medirla aqui seria una segunda verdad de un numero que ya paso auditoria
humana. La funcion de pooleo se importa de D-EXP-5 y la particion de Available
del paquete (clasificador portable de D-RES-3e).

Uso:
    python scripts/experiment_disponibilidad_v2b.py --stage resolve   # parsea
    python scripts/experiment_disponibilidad_v2b.py --stage measure   # mide
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from nba_predictor.config import ROLLING_WINDOW_GAMES
from nba_predictor.features.availability import _add_player_minutes_rolling
from nba_predictor.ingestion.injury_classify import available_bucket
from nba_predictor.ingestion.injury_report import InjuryStatus, NameIndex
from nba_predictor.ingestion.injury_report_legacy import (
    ParserCoverageError,
    UnknownLayoutError,
    parse_pdf_any,
)
from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS

# Los scripts de D-EXP-2 y D-EXP-5 se IMPORTAN: aritmetica de disponibilidad,
# metricas por fold, diferencia pareada y pooleo por estatus vienen de donde ya
# fueron auditados. Una reimplementacion seria una segunda verdad.
_D2 = Path(__file__).resolve().parent / "experiment_disponibilidad_v2.py"
_S2 = importlib.util.spec_from_file_location("_dexp2", _D2)
dexp2 = importlib.util.module_from_spec(_S2)
sys.modules["_dexp2"] = dexp2
_S2.loader.exec_module(dexp2)

_D5 = Path(__file__).resolve().parent / "experiment_pjuega_legacy.py"
_S5 = importlib.util.spec_from_file_location("_dexp5", _D5)
dexp5 = importlib.util.module_from_spec(_S5)
sys.modules["_dexp5"] = dexp5
_S5.loader.exec_module(dexp5)

log = logging.getLogger("dexp2b")

OUT_DIR = Path("data/experiment_v2b")
BACKFILL_DIR = Path("data/backfill_injury")
# Caches de PDFs ya en disco (solo lectura, no se re-descarga nada).
CACHES = (Path("data/experiment_pjuega/cache"), Path("data/experiment_pjuega_legacy/cache"))
CACHE_DIR = OUT_DIR / "cache"
INSTANCIAS = (Path("data/experiment_pjuega/instancias.json"),
              Path("data/experiment_pjuega_legacy/instancias.json"))

# Corpus: desde el borde de retencion (primer archivo REGULAR) hasta hoy.
CORPUS_START = "2018-12-18"
SEASONS: tuple[str, ...] = ("2018-19", "2019-20", "2020-21", "2021-22",
                            "2022-23", "2023-24", "2024-25", "2025-26")

# Folds walk-forward: train ACUMULADO, valida la siguiente. 2018-19 entra
# PARCIAL (su archivo empieza el 2018-12-18) y solo como train — no se valida
# sobre una temporada cuyo corpus esta truncado.
FOLDS: tuple[tuple[str, tuple[str, ...], str], ...] = tuple(
    (f"F{i}", SEASONS[: i + 1], SEASONS[i + 1]) for i in range(len(SEASONS) - 1)
)

STATUSES: tuple[str, ...] = ("Out", "Doubtful", "Questionable", "Probable", "Available")

# Pesos OFICIALES de D-EXP-1, solo como VARA de comparacion del diagnostico de
# estabilidad. Los que se usan en cada fold se miden del train de ese fold.
PESOS_OFICIALES = {"Out": 0.00, "Doubtful": 0.02, "Questionable": 0.48,
                   "Probable": 0.91, "Available::gleague_o_twoway": 0.45,
                   "Available::resto": 0.77}

# Gate de resolucion: techo del rango esperado del pre-registro.
MDE_TECHO = 0.008
# z(0.975) + z(0.80): el multiplicador del MDE de un contraste pareado a 80% de
# potencia con alfa 0.05 a dos colas.
_Z_MDE = 2.8016

AUDIT_SEED = 42
AUDIT_N = 30


# ---------------------------------------------------------------------------
# Funciones PURAS — sin red, sin BigQuery, unit-testeables
# ---------------------------------------------------------------------------


def status_key(status: str, category: str | None, reason: str | None) -> str:
    """Clave de peso de una fila: el estatus, y Available partido por razon.

    Available NO admite peso unico (adjudicacion 2 de D-EXP-1: mezcla
    poblaciones), y la particion la decide el clasificador PORTABLE del paquete
    — category cuando el layout la trae, razon cuando no (D-RES-3e).
    """
    if status == "Available":
        return f"Available::{available_bucket(category, reason)}"
    return status


def season_counts(instancias: list[dict]) -> dict[str, dict[str, dict[str, int]]]:
    """temporada -> clave de peso -> {n, jugo}, del corte publish e incluidas.

    Los agregados salen de la medicion ya auditada (D-EXP-1 y D-EXP-5), no de
    una re-derivacion: el numero que alimenta el peso es el mismo que Antonio
    adjudico contra el PDF y el boxscore.
    """
    out: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"n": 0, "jugo": 0}))
    for i in instancias:
        if i.get("cut") != "publish" or i.get("veredicto") != "incluida":
            continue
        if i["status"] not in STATUSES:
            continue
        celda = out[i["season"]][status_key(i["status"], i.get("category"), i.get("reason"))]
        celda["n"] += 1
        celda["jugo"] += int(i["jugo_primaria"])
    return {s: dict(v) for s, v in out.items()}


def weights_from_train(
    counts: dict[str, dict[str, dict[str, int]]],
    train_seasons: tuple[str, ...],
) -> tuple[dict[str, float], dict[str, int]]:
    """Pesos del fold: P(juega | clave) pooleado SOLO sobre el train.

    Se poolea, no se promedia: las temporadas tienen n muy distintos (2018-19
    entra parcial) y un promedio simple le daria el mismo voto a una celda de
    140 y a una de 8 600.

    SIN REDONDEO, a diferencia de los pesos oficiales de D-EXP-1 (2 decimales,
    eleccion presentacional): redondear inyectaria un escalon artificial entre
    folds que la comparacion entre folds leeria como señal.

    Una clave sin instancias en el train no recibe peso: cae a 1.0 (no listado)
    por la misma via que cualquier jugador que el PDF no menciona. Se devuelve
    el n por clave para poder declarar cual quedo vacia.
    """
    agg: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "jugo": 0})
    for s in train_seasons:
        for clave, v in counts.get(s, {}).items():
            agg[clave]["n"] += v["n"]
            agg[clave]["jugo"] += v["jugo"]
    pesos = {k: v["jugo"] / v["n"] for k, v in agg.items() if v["n"] > 0}
    ns = {k: v["n"] for k, v in agg.items()}
    return pesos, ns


def weight_of(clave: str, pesos: dict[str, float]) -> float:
    """Peso de una fila del PDF bajo la tabla de pesos de un fold.

    Ausente = 1.0: es el mismo trato que v1 da a quien no aparece en el
    reporte, y mantiene la escala comparable entre brazos.
    """
    return pesos.get(clave, dexp2.W_NO_LISTADO)


def weight_binary(clave: str) -> float:
    """Peso del brazo BIN: solo Out resta disponibilidad (Decision 2 del feed).

    NO depende del fold — por eso se computa una vez. Es el brazo de MISMA
    CONSTRUCCION que V2 (rotacion pronosticada desde el PDF), lo que lo hace el
    contraste limpio del efecto de ponderar.
    """
    return 0.0 if clave == "Out" else 1.0


def mde_pareado(sd: float, n: int) -> float:
    """MDE de un contraste pareado a 80% de potencia, alfa 0.05 dos colas.

    (z_{1-a/2} + z_{power}) * sd / sqrt(n). Es el gate ejecutable que D-EXP-2
    pago con un null que no podia interpretarse: si el MDE no cubre el rango
    esperado, el experimento no puede responder su propia pregunta y decirlo
    ANTES es el entregable.
    """
    if n <= 1 or sd <= 0:
        return float("nan")
    return _Z_MDE * sd / math.sqrt(n)


def resolution_verdict(mde: float, techo: float = MDE_TECHO) -> str:
    """'CUBRE' si el MDE cabe bajo el techo del rango esperado; 'SIN_RESOLUCION' si no."""
    if not (mde == mde):  # NaN
        return "SIN_RESOLUCION"
    return "CUBRE" if mde <= techo else "SIN_RESOLUCION"


def weight_stability(pesos_por_fold: dict[str, dict[str, float]],
                     oficiales: dict[str, float] = PESOS_OFICIALES,
                     tol_pp: float = 5.0) -> dict:
    """Desviacion de los pesos de cada fold respecto a los oficiales, en pp.

    D-EXP-5 predice que se queden dentro de +-5pp; una salida fuera es hallazgo
    a adjudicar, no a aceptar.
    """
    filas = []
    fuera = []
    for fold, pesos in pesos_por_fold.items():
        for clave, oficial in oficiales.items():
            if clave not in pesos:
                continue
            dif_pp = (pesos[clave] - oficial) * 100
            filas.append({"fold": fold, "clave": clave, "peso": round(pesos[clave], 4),
                          "oficial": oficial, "dif_pp": round(dif_pp, 2)})
            if abs(dif_pp) > tol_pp:
                fuera.append({"fold": fold, "clave": clave, "dif_pp": round(dif_pp, 2)})
    return {"filas": filas, "fuera_de_tolerancia": fuera, "tolerancia_pp": tol_pp}


def freeze_audit_games(game_ids: list[str], seed: int = AUDIT_SEED,
                       n: int = AUDIT_N) -> dict:
    """Muestra de partidos para spot-check humano, congelada ANTES de medir."""
    rng = random.Random(seed)
    elegidos = sorted(rng.sample(sorted(game_ids), min(n, len(game_ids))))
    return {
        "seed": seed,
        "congelado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_universo": len(game_ids),
        "game_ids": elegidos,
    }


def render_folds_table(res: dict) -> str:
    """Tabla por fold: los tres brazos y el contraste primario."""
    out = ["", "TABLA POR FOLD — contraste PRIMARIO V2 vs BIN (misma construccion)",
           "CTL = B-limpia re-entrenada; se reporta, NO decide (confound declarado)", ""]
    out.append(f"  {'fold':<5}{'valid':<9}{'n_val':>7}{'LL CTL':>10}{'LL BIN':>10}"
               f"{'LL V2':>10}{'V2-BIN':>10}{'t':>8}{'IC95 (pp de LL)':>24}")
    for fold, f in res["folds"].items():
        p = f["pareado_v2_vs_bin"]
        ic = f"[{p['ic95'][0]:+.5f},{p['ic95'][1]:+.5f}]"
        out.append(
            f"  {fold:<5}{f['valid']:<9}{f['n_valid']:>7}"
            f"{f['ctl']['log_loss']:>10.5f}{f['bin']['log_loss']:>10.5f}"
            f"{f['v2']['log_loss']:>10.5f}{f['ganancia_v2_vs_bin']:>+10.5f}"
            f"{(p['t'] if p['t'] is not None else float('nan')):>8.2f}{ic:>24}"
        )
    a = res["agregado"]["pareado_v2_vs_bin"]
    out += ["", f"  AGREGADO n={a['n']}  media {a['media']:+.6f}  t={a['t']}  "
                f"IC95 [{a['ic95'][0]:+.6f}, {a['ic95'][1]:+.6f}]  "
                f"gana V2 en {a['gana_b_en_partidos']} partidos"]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# ETAPA 1 — parseo (una sola vez; los pesos se aplican despues)
# ---------------------------------------------------------------------------


def fetch_pdf(store, fecha: str, sufijo: str) -> bytes:
    """PDF de los caches en disco; si no esta, de GCS (solo lectura)."""
    for c in CACHES:
        p = c / f"{fecha}_{sufijo}.pdf"
        if p.exists():
            return p.read_bytes()
    from nba_predictor.storage.cloud import _gcs_injury_report_path
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
    data = json.loads((BACKFILL_DIR / f"{season}.json").read_text(encoding="utf-8"))
    return {r["date"]: r["cuts"].get("publish", {}).get("suffix") for r in data["records"]}


def stage_resolve(args) -> None:
    """Parsea los PDFs publish del corpus y persiste (fecha, equipo, jugador, clave).

    Se separa de la medicion porque el parseo es lo caro (~1 700 PDFs) y los
    pesos cambian por fold: parsear una vez y ponderar muchas es la unica forma
    de que el walk-forward puro no cueste siete pasadas.
    """
    store = dexp2.make_store()
    teams = {dexp2.normalize_team(str(r["name"])): int(r["team_id"])
             for _, r in store.load_teams().iterrows()}
    nombres = store.load_player_names()
    idx = NameIndex.from_player_map(nombres)
    print(f"Catalogo: {len(teams)} equipos | {len(nombres)} jugadores", flush=True)

    filas: list[dict] = []
    nys: list[dict] = []
    parseadas: list[str] = []
    cont = {"fechas_parseadas": 0, "fechas_sin_pdf": 0, "layout_desconocido": 0,
            "ilegible": 0, "cobertura_insuficiente": 0, "filas_pdf": 0,
            "sin_match_nombre": 0, "sin_match_equipo": 0, "fuera_de_corpus": 0}
    excluidas: dict[str, list[str]] = defaultdict(list)

    for season in SEASONS:
        sufijos = publish_suffixes(season)
        fechas = sorted(f for f, s in sufijos.items() if s and f >= CORPUS_START)
        cont["fuera_de_corpus"] += sum(1 for f, s in sufijos.items()
                                      if s and f < CORPUS_START)
        cont["fechas_sin_pdf"] += sum(1 for s in sufijos.values() if not s)
        if args.limit_dates:
            fechas = fechas[: args.limit_dates]
        print(f"\n=== {season}: {len(fechas)} fechas publish en corpus ===", flush=True)

        for n, fecha in enumerate(fechas, 1):
            try:
                rep = parse_pdf_any(fetch_pdf(store, fecha, sufijos[fecha]))
            except UnknownLayoutError:
                # Las 32 fechas de D-RES-3d. JAMAS se fuerza un layout.
                cont["layout_desconocido"] += 1
                excluidas["layout_desconocido"].append(fecha)
                continue
            except ParserCoverageError as exc:
                cont["cobertura_insuficiente"] += 1
                excluidas["cobertura_insuficiente"].append(f"{fecha}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 — se declara y se sigue
                cont["ilegible"] += 1
                excluidas["ilegible"].append(f"{fecha}: {exc!r}")
                continue
            cont["fechas_parseadas"] += 1
            parseadas.append(fecha)

            objetivo = dexp2.to_mdy(fecha)
            for row in rep.rows:
                if row.game_date != objetivo:
                    continue
                st = row.status.value if isinstance(row.status, InjuryStatus) else str(row.status)
                if st not in STATUSES:
                    continue
                cont["filas_pdf"] += 1
                tid = teams.get(dexp2.normalize_team(row.team))
                if tid is None:
                    cont["sin_match_equipo"] += 1
                    continue
                pid = idx.match(row.player_name)
                if pid is None:
                    cont["sin_match_nombre"] += 1
                    continue
                filas.append({
                    "date": fecha, "season": season, "team_id": tid, "player_id": pid,
                    "clave": status_key(st, getattr(row, "category", None), row.reason),
                    "player_name": row.player_name, "status": st,
                    "suffix": sufijos[fecha],
                })
            for e in rep.nys:
                if getattr(e, "game_date", None) != objetivo:
                    continue
                tid = teams.get(dexp2.normalize_team(e.team))
                if tid is not None:
                    nys.append({"date": fecha, "team_id": tid})
            if n % 40 == 0:
                print(f"  {n}/{len(fechas)} fechas", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(filas).to_parquet(OUT_DIR / "resolved.parquet", index=False)
    pd.DataFrame(nys).drop_duplicates().to_parquet(OUT_DIR / "nys.parquet", index=False)
    (OUT_DIR / "resolve_contadores.json").write_text(
        json.dumps({"contadores": cont,
                    "fechas_parseadas_lista": sorted(parseadas),
                    "excluidas": {k: v for k, v in excluidas.items()},
                    "excluidas_conteo": {k: len(v) for k, v in excluidas.items()}},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFilas resueltas: {len(filas)} | NYS: {len(nys)}")
    print(f"Contadores: {json.dumps(cont, ensure_ascii=False)}")
    print(f"Escrito en {OUT_DIR}/resolved.parquet")


# ---------------------------------------------------------------------------
# ETAPA 2 — medicion (fase 0 de potencia + ejecucion condicionada)
# ---------------------------------------------------------------------------


def _build_universe(store) -> tuple[pd.DataFrame, dict, dict]:
    """features + denominadores de rotacion + metadatos de partido del corpus."""
    features = store.load_features()
    games = store.load_games()
    pgs = store.load_player_game_stats()

    stats = pgs.merge(games[["game_id", "season", "game_date"]], on="game_id",
                      validate="many_to_one")
    stats = _add_player_minutes_rolling(stats, ROLLING_WINDOW_GAMES)
    # El rolling cruza temporadas: se alimenta con holgura desde la anterior al
    # corpus para que el primer partido de 2018-19 no arranque en frio.
    pivot = sorted({s for s in stats["season"].unique() if s >= "2017-18"})
    denoms = dexp2.player_denominators(stats[stats["season"].isin(pivot)],
                                       ROLLING_WINDOW_GAMES)
    meta = games.set_index("game_id")[["game_date", "home_team_id",
                                       "away_team_id", "season"]]
    return features, denoms, meta


def stage_measure(args) -> None:  # noqa: C901 — CLI de orquestacion
    store = dexp2.make_store()
    resolved = pd.read_parquet(OUT_DIR / "resolved.parquet")
    nys_df = pd.read_parquet(OUT_DIR / "nys.parquet")
    print(f"Resueltas {len(resolved)} filas | NYS {len(nys_df)}", flush=True)

    # --- pesos por temporada, de la medicion YA auditada --------------------
    instancias: list[dict] = []
    for p in INSTANCIAS:
        if not p.exists():
            raise SystemExit(f"Falta {p}: los pesos salen de D-EXP-1/D-EXP-5, no se re-miden.")
        instancias += json.loads(p.read_text(encoding="utf-8"))
    counts = season_counts(instancias)
    print(f"Conteos de pesos por temporada: {sorted(counts)}", flush=True)

    features, denoms, meta = _build_universe(store)
    print(f"features {features.shape} | {len(denoms)} vectores de rotacion", flush=True)

    # --- universo de partidos: corpus x features x rotacion x PDF ----------
    # Fechas PARSEADAS, no fechas con filas: un PDF valido puede no traer
    # ninguna fila de su dia objetivo (todos los equipos NYS, o nadie lesionado),
    # y sus partidos siguen siendo universo legitimo con disponibilidad 1.0.
    cont_resolve = json.loads(
        (OUT_DIR / "resolve_contadores.json").read_text(encoding="utf-8"))
    fechas_ok = set(cont_resolve["fechas_parseadas_lista"])
    nys_por_fecha: dict[str, set[int]] = defaultdict(set)
    for _, r in nys_df.iterrows():
        nys_por_fecha[r["date"]].add(int(r["team_id"]))

    cont = {"sin_rotacion": 0, "fecha_sin_parseo": 0, "equipos_nys": 0}
    base = features[features["season"].isin(SEASONS)].copy()
    filas: list[dict] = []
    por_fecha_equipo: dict[tuple[str, int], dict[str, str]] = defaultdict(dict)
    for _, r in resolved.iterrows():
        por_fecha_equipo[(r["date"], int(r["team_id"]))][int(r["player_id"])] = r["clave"]

    for gid in base["game_id"]:
        fecha = str(meta.at[gid, "game_date"])
        if fecha not in fechas_ok:
            cont["fecha_sin_parseo"] += 1
            continue
        home, away = int(meta.at[gid, "home_team_id"]), int(meta.at[gid, "away_team_id"])
        dh, da = denoms.get((gid, home)), denoms.get((gid, away))
        if not dh or not da:
            cont["sin_rotacion"] += 1
            continue
        nys_tids = nys_por_fecha.get(fecha, set())
        for tid in (home, away):
            if tid in nys_tids:
                cont["equipos_nys"] += 1
        filas.append({
            "game_id": gid, "date": fecha,
            "_dh": dh, "_da": da,
            # NYS = sin ajuste (replica de 13e-2.5 caso 3)
            "_kh": {} if home in nys_tids else por_fecha_equipo.get((fecha, home), {}),
            "_ka": {} if away in nys_tids else por_fecha_equipo.get((fecha, away), {}),
        })
    print(f"Universo: {len(filas)} partidos | {json.dumps(cont)}", flush=True)

    # BIN no depende del fold: se computa UNA vez.
    for f in filas:
        wb_h = {p: weight_binary(k) for p, k in f["_kh"].items()}
        wb_a = {p: weight_binary(k) for p, k in f["_ka"].items()}
        f["availability_diff_bin"] = (dexp2.team_availability(f["_dh"], wb_h)
                                      - dexp2.team_availability(f["_da"], wb_a))

    universo = pd.DataFrame([{k: v for k, v in f.items() if not k.startswith("_")}
                             for f in filas])
    df = features.merge(universo, on="game_id", how="inner")
    antes = len(df)
    df = df[df["availability_diff_bin"].notna()].reset_index(drop=True)
    print(f"Tras dropna de disponibilidad: {len(df)} de {antes}", flush=True)
    claves_por_gid = {f["game_id"]: f for f in filas}

    # --- muestra de auditoria CONGELADA antes de medir ----------------------
    audit_file = OUT_DIR / "audit_games.json"
    if not audit_file.exists():
        audit_file.write_text(json.dumps(
            freeze_audit_games(df["game_id"].tolist()), indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"Muestra de auditoria congelada: {audit_file}", flush=True)

    cols_v2 = [c if c != "availability_diff" else "availability_diff_v2"
               for c in OFFICIAL_LOGISTIC_COLS]
    cols_bin = [c if c != "availability_diff" else "availability_diff_bin"
                for c in OFFICIAL_LOGISTIC_COLS]

    def v2_column(pesos: dict[str, float]) -> pd.Series:
        """availability_diff ponderada con la tabla de pesos de UN fold."""
        vals = []
        for gid in df["game_id"]:
            f = claves_por_gid[gid]
            wh = {p: weight_of(k, pesos) for p, k in f["_kh"].items()}
            wa = {p: weight_of(k, pesos) for p, k in f["_ka"].items()}
            vals.append(dexp2.team_availability(f["_dh"], wh)
                        - dexp2.team_availability(f["_da"], wa))
        return pd.Series(vals, index=df.index)

    pesos_por_fold: dict[str, dict[str, float]] = {}
    ns_por_fold: dict[str, dict[str, int]] = {}
    for nombre, train, _valid in FOLDS:
        pesos_por_fold[nombre], ns_por_fold[nombre] = weights_from_train(counts, train)

    # --- FASE 0: MDE desde el PRIMER fold, extrapolado al n total ----------
    n_por_fold = {v: int((df["season"] == v).sum()) for _, _, v in FOLDS}
    n_total = sum(n_por_fold.values())
    f0, train0, valid0 = FOLDS[0]
    df_f0 = df.copy()
    df_f0["availability_diff_v2"] = v2_column(pesos_por_fold[f0])
    m_bin0 = dexp2.fold_metrics(df_f0, cols_bin, train0, valid0)
    m_v20 = dexp2.fold_metrics(df_f0, cols_v2, train0, valid0)
    assert m_bin0["n_valid"] == m_v20["n_valid"], "paridad de universo rota en fase 0"
    p0 = dexp2.paired_diff(m_bin0["_y"], m_bin0["_p"], m_v20["_p"])
    mde_fold0 = mde_pareado(p0["sd"], p0["n"])
    mde_total = mde_pareado(p0["sd"], n_total)
    veredicto = resolution_verdict(mde_total)

    fase0 = {
        "computado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fold_base": f0, "valid": valid0, "n_fold_base": p0["n"],
        "sd_diferencia_pareada": p0["sd"],
        "n_valid_por_fold": n_por_fold, "n_total_proyectado": n_total,
        "mde_fold_base": round(mde_fold0, 6),
        "mde_agregado": round(mde_total, 6),
        "techo_pre_registrado": MDE_TECHO,
        "expectativa_pre_registrada": [0.004, 0.006],
        "veredicto": veredicto,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "mde_fase0.json").write_text(
        json.dumps(fase0, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n=== FASE 0 DE POTENCIA ===")
    print(json.dumps(fase0, indent=2, ensure_ascii=False))

    if veredicto == "SIN_RESOLUCION":
        print("\nGATE: MDE agregado por encima del techo pre-registrado. "
              "SIN RESOLUCION: no se corre inferencia. Se emiten descriptivos.")
        (OUT_DIR / "results.json").write_text(json.dumps(
            {"veredicto": "SIN_RESOLUCION", "fase0": fase0,
             "descriptivos": {"n_por_fold": n_por_fold,
                              "pesos_por_fold": pesos_por_fold,
                              "distribucion": None}},
            indent=2, ensure_ascii=False), encoding="utf-8")
        return

    # --- EJECUCION -------------------------------------------------------
    res: dict[str, Any] = {"folds": {}, "contadores": cont,
                           "pesos_por_fold": {k: {kk: round(vv, 5) for kk, vv in v.items()}
                                              for k, v in pesos_por_fold.items()},
                           "n_pesos_por_fold": ns_por_fold,
                           "fase0": fase0}
    acum = {"y": [], "bin": [], "v2": [], "ctl": []}
    dist_por_fold = {}

    for nombre, train, valid in FOLDS:
        d = df.copy()
        d["availability_diff_v2"] = v2_column(pesos_por_fold[nombre])
        ctl = dexp2.fold_metrics(d, OFFICIAL_LOGISTIC_COLS, train, valid)
        bn = dexp2.fold_metrics(d, cols_bin, train, valid)
        v2 = dexp2.fold_metrics(d, cols_v2, train, valid)
        assert ctl["n_valid"] == bn["n_valid"] == v2["n_valid"], \
            f"paridad de universo rota en {nombre}"
        res["folds"][nombre] = {
            "train": list(train), "valid": valid,
            "n_train": ctl["n_train"], "n_valid": ctl["n_valid"],
            "ctl": {k: v for k, v in ctl.items() if not k.startswith("_")},
            "bin": {k: v for k, v in bn.items() if not k.startswith("_")},
            "v2": {k: v for k, v in v2.items() if not k.startswith("_")},
            "ganancia_v2_vs_bin": round(bn["log_loss"] - v2["log_loss"], 6),
            "ganancia_v2_vs_ctl": round(ctl["log_loss"] - v2["log_loss"], 6),
            "ganancia_bin_vs_ctl": round(ctl["log_loss"] - bn["log_loss"], 6),
            "pareado_v2_vs_bin": dexp2.paired_diff(bn["_y"], bn["_p"], v2["_p"]),
            "pareado_v2_vs_ctl": dexp2.paired_diff(ctl["_y"], ctl["_p"], v2["_p"]),
            "pareado_bin_vs_ctl": dexp2.paired_diff(ctl["_y"], ctl["_p"], bn["_p"]),
            "coef_availability": {
                "ctl": dict(ctl["coefs"])["availability_diff"],
                "bin": dict(bn["coefs"])["availability_diff_bin"],
                "v2": dict(v2["coefs"])["availability_diff_v2"],
            },
        }
        acum["y"] += list(bn["_y"])
        acum["bin"] += list(bn["_p"])
        acum["v2"] += list(v2["_p"])
        acum["ctl"] += list(ctl["_p"])
        dv = d.loc[d["season"] == valid, "availability_diff_v2"]
        db = d.loc[d["season"] == valid, "availability_diff_bin"]
        dist_por_fold[nombre] = {
            "filas_distintas": int(((dv - db).abs() > 1e-12).sum()),
            "n": int(len(dv)),
            "abs_media": float((dv - db).abs().mean()),
            "abs_p95": float((dv - db).abs().quantile(0.95)),
            "abs_max": float((dv - db).abs().max()),
        }
        print(f"  {nombre} valid={valid} n={ctl['n_valid']} "
              f"V2-BIN={res['folds'][nombre]['ganancia_v2_vs_bin']:+.6f}", flush=True)

    res["agregado"] = {
        "n": len(acum["y"]),
        "pareado_v2_vs_bin": dexp2.paired_diff(acum["y"], acum["bin"], acum["v2"]),
        "pareado_v2_vs_ctl": dexp2.paired_diff(acum["y"], acum["ctl"], acum["v2"]),
        "pareado_bin_vs_ctl": dexp2.paired_diff(acum["y"], acum["ctl"], acum["bin"]),
        "folds_ganados_por_v2": sum(
            1 for f in res["folds"].values() if f["ganancia_v2_vs_bin"] > 0),
        "folds_totales": len(res["folds"]),
    }
    res["estabilidad_pesos"] = weight_stability(pesos_por_fold)
    res["distribucion_v2_vs_bin"] = dist_por_fold

    (OUT_DIR / "results.json").write_text(
        json.dumps(res, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tabla = render_folds_table(res)
    (OUT_DIR / "tabla.txt").write_text(tabla, encoding="utf-8")
    print(tabla)
    print(f"\nEstabilidad de pesos fuera de +-5pp: "
          f"{res['estabilidad_pesos']['fuera_de_tolerancia'] or 'ninguno'}")
    print(f"Escrito en {OUT_DIR}/")


def stage_audit(args) -> None:
    """Listado de la muestra congelada: ausencias aplicadas por cada brazo.

    Para el spot-check humano. Cada partido se describe con el PDF de origen y,
    por equipo, los jugadores que el reporte menciona con su clave de peso, el
    peso que le da BIN y el que le da V2 en el fold que VALIDA su temporada —
    ese es el peso con el que realmente se predijo ese partido.
    """
    store = dexp2.make_store()
    muestra = json.loads((OUT_DIR / "audit_games.json").read_text(encoding="utf-8"))
    resolved = pd.read_parquet(OUT_DIR / "resolved.parquet")
    nys_df = pd.read_parquet(OUT_DIR / "nys.parquet")
    res = json.loads((OUT_DIR / "results.json").read_text(encoding="utf-8"))

    games = store.load_games()
    tri = {int(r["team_id"]): str(r["abbreviation"])
           for _, r in store.load_teams().iterrows()}
    meta = games.set_index("game_id")[["game_date", "home_team_id",
                                       "away_team_id", "season"]]
    fold_de_valid = {d["valid"]: f for f, d in res["folds"].items()}
    nys_pares = {(r["date"], int(r["team_id"])) for _, r in nys_df.iterrows()}

    out = [f"AUDITORIA D-EXP-2b — muestra congelada seed {muestra['seed']} "
           f"({muestra['congelado_utc']})",
           f"{len(muestra['game_ids'])} partidos de un universo de {muestra['n_universo']}",
           "Para cada jugador: CLAVE de peso | peso BIN | peso V2 del fold que valida",
           "=" * 78]
    for gid in muestra["game_ids"]:
        if gid not in meta.index:
            out.append(f"\n{gid}: fuera de games")
            continue
        fecha = str(meta.at[gid, "game_date"])
        season = str(meta.at[gid, "season"])
        # 2018-19 nunca se valida (su corpus esta truncado), asi que sus partidos
        # son TRAIN en los siete folds. Mostrar "fold None" con V2=1.0 seria
        # engañoso — un Out con peso 1.0 no existe en ninguna corrida. Se
        # etiqueta el rol y se muestran los pesos del primer fold que lo usa.
        fold = fold_de_valid.get(season)
        rol = "valida" if fold else "TRAIN en los 7 folds; pesos de"
        if fold is None:
            fold = FOLDS[0][0]
        pesos = res["pesos_por_fold"].get(fold, {})
        home, away = int(meta.at[gid, "home_team_id"]), int(meta.at[gid, "away_team_id"])
        sub = resolved[resolved["date"] == fecha]
        suf = sub["suffix"].iloc[0] if len(sub) else "?"
        out.append(f"\n{gid}  {fecha}  {tri.get(away, away)}@{tri.get(home, home)}  "
                   f"temporada {season}  {rol} {fold}  PDF {fecha}_{suf}.pdf")
        for tid, lado in ((home, "LOCAL"), (away, "VISITA")):
            marca = "  [NYS -> SIN AJUSTE en ambos brazos]" if (fecha, tid) in nys_pares else ""
            filas = sub[sub["team_id"] == tid]
            out.append(f"  {lado} {tri.get(tid, tid)}: {len(filas)} filas del reporte{marca}")
            for _, f in filas.sort_values("player_name").iterrows():
                wv = pesos.get(f["clave"], 1.0)
                out.append(f"      {f['player_name']:<26}{f['clave']:<32}"
                           f"BIN {weight_binary(f['clave']):.2f}   V2 {wv:.4f}")
    destino = OUT_DIR / "auditoria_muestra.txt"
    destino.write_text("\n".join(out), encoding="utf-8")
    print(f"Listado de auditoria: {destino} ({len(out)} lineas)")


def main() -> None:
    ap = argparse.ArgumentParser(description="D-EXP-2b: re-medicion con corpus ampliado")
    ap.add_argument("--stage", choices=("resolve", "measure", "audit"), required=True)
    ap.add_argument("--limit-dates", type=int, default=None, help="N fechas/temporada (humo)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    {"resolve": stage_resolve, "measure": stage_measure,
     "audit": stage_audit}[args.stage](args)


if __name__ == "__main__":
    main()
