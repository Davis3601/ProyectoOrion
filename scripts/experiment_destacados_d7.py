"""
D-EXP-7 — DESTACADOS MÁS CERCA DEL RESULTADO REAL: valor central y rango.

Pre-registro SELLADO en CLAUDE.md (2026-10-25T22:45:37Z) ANTES de escribir
este script. Lo que aquí se fija viene de allí; donde el texto sellado admite
más de una implementación, la elegida se DECLARA en el docstring de la función
y en results (nada se resuelve en silencio).

*** SOLO LECTURA DE LA NUBE. *** games, player_game_stats, team_game_stats y
teams de BigQuery con CloudDataStore construido EXPLÍCITAMENTE (convención de
scripts). Escribe ÚNICAMENTE en data/experiment_destacados_d7/. Cero
despliegue; B-limpia, features, endpoint y mensajes intactos.

DOS ETAPAS, como exige el pre-registro (ORDEN):
  centro : fase 0 de potencia + brazos B0, C1..C5 + puertas de CENTRO y de
           ARRANQUE. Se detiene ahí: la adjudicación la confirma Antonio.
  rango  : corre UNA sola vez, con el centro ya adjudicado por stat
           (--centro pts=B0,reb=C1,...). R0, R1, R2 + puerta de RANGO.

UNIVERSO: el de D-EXP-3/4, importado de su script (una reimplementación sería
una segunda verdad): filas JUGADAS (minutes > 0) con >= 10 jugados previos,
shift(1), cruzando temporadas; folds = temporadas de validación 2020-21..2025-26.

Uso:
    python scripts/experiment_destacados_d7.py centro
    python scripts/experiment_destacados_d7.py rango --centro pts=B0,reb=B0,ast=B0,fg3m=B0
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nba_predictor.config import ROLLING_WINDOW_GAMES  # noqa: E402

# ── Helpers de D-EXP-3 (misma verdad: universo, pareado, MDE) ──
_V3_PATH = Path(__file__).resolve().parent / "experiment_stats_jugador.py"
_SPEC = importlib.util.spec_from_file_location("_dexp3", _V3_PATH)
dexp3 = importlib.util.module_from_spec(_SPEC)
sys.modules["_dexp3"] = dexp3
_SPEC.loader.exec_module(dexp3)

OUT_DIR = Path("data/experiment_destacados_d7")
STATS: tuple[str, ...] = dexp3.STATS
VALID_SEASONS: tuple[str, ...] = dexp3.VALID_SEASONS
FASE0_SEASONS: tuple[str, ...] = VALID_SEASONS[:2]     # "los DOS primeros folds"
CANDIDATOS: tuple[str, ...] = ("C1", "C2", "C3", "C4", "C5")

# Parámetros FIJADOS en el pre-registro (sin tuning sobre validación).
WINDOW = ROLLING_WINDOW_GAMES                 # 10
MIN_PREV = dexp3.MIN_PREV_GAMES               # 10
EWM_HALFLIFE = 5
SHRINK_K = 5
C5_MIN_ACT = 3
CORTE_TITULAR_MIN = 28.0
ARRANQUE_DIAS = 21
COBERTURA_OBJETIVO = 0.80
ALFA_WINKLER = 0.20

# Selector de producción (D-PRE-6, sin señales: resolución 2 de Antonio).
SEL_TOP = 2
SEL_MIN_GAMES = 3
SEL_RECENCY_DAYS = 250

# Puertas.
MDE_REL_UMBRAL = 0.03
CENTRO_UMBRAL_MIN = 0.03
CENTRO_MIN_FOLDS = 5
DEBIL_MARGEN = 0.01
ARRANQUE_UMBRAL = 0.03
ARRANQUE_MIN_FOLDS = 4
RANGO_COB_AGREGADA = (0.78, 0.82)
RANGO_COB_FOLD = (0.75, 0.85)

AUDIT_N = 30
AUDIT_SEED = 42


# ---------------------------------------------------------------------------
# Funciones PURAS — sin red, unit-testeables
# ---------------------------------------------------------------------------


def previous_season(season: str) -> str:
    """'2026-27' -> '2025-26'."""
    a = int(season[:4]) - 1
    return f"{a}-{(a + 1) % 100:02d}"


def add_center_arms(
    played: pd.DataFrame,
    stats: tuple[str, ...] = STATS,
    window: int = WINDOW,
    min_prev: int = MIN_PREV,
    halflife: float = EWM_HALFLIFE,
    k: float = SHRINK_K,
    c5_min_act: int = C5_MIN_ACT,
) -> pd.DataFrame:
    """Brazos de valor central B0, C1, C2, C3, C5 por stat (C4 va aparte: necesita
    contexto de equipo y ajuste por fold).

    `played`: filas JUGADAS (minutes > 0) con player_id, season, game_date y los
    stats. TODO usa solo partidos previos (shift 1): el partido G jamás se ve.

      B0 = mediana de los últimos `window` (min_periods = min_prev).
      C1 = media de los últimos `window`.
      C2 = media exponencial (halflife en partidos) de TODOS los previos.
      C3 = n_act >= window: C1 (los últimos 10 ya son de la temporada en curso);
           n_act < window: (n_act * media_act + k * media_prev) / (n_act + k).
      C5 = n_act >= c5_min_act: media expansiva de la temporada en curso;
           si no, media de los últimos 10 de la temporada anterior.
    n_act = partidos jugados previos en la temporada en curso; media_prev =
    media del jugador en la temporada anterior.
    FALLBACK DECLARADO (el pre-registro no lo fija): si el jugador no jugó la
    temporada anterior, C3 usa C1, y C5 usa C1 mientras n_act < c5_min_act
    (con n_act >= c5_min_act C5 ya no necesita la temporada anterior). Filas sin
    temporada anterior contadas en attrs['c3_c5_fallback_filas'].
    También se añaden roll_min (minutos medios de los últimos 10, de D-EXP-3).
    """
    out = played.sort_values(["player_id", "game_date"], kind="stable").copy()
    g = out.groupby("player_id", sort=False)
    out["roll_min"] = g["minutes"].transform(
        lambda s: s.shift(1).rolling(window, min_periods=min_prev).mean())

    gs = out.groupby(["player_id", "season"], sort=False)
    out["n_act"] = gs.cumcount()

    # Medias de la temporada anterior por (jugador, temporada) -> mapeadas a la
    # temporada SIGUIENTE: nada de la temporada en curso entra aquí.
    out["_prev_season"] = out["season"].map(previous_season)
    por_temp = out.groupby(["player_id", "season"], sort=False)
    media_temp = por_temp[list(stats)].mean()
    ult10_temp = por_temp[list(stats)].apply(lambda d: d.tail(window).mean())

    sin_prev = 0
    for stat in stats:
        out[f"B0_{stat}"] = g[stat].transform(
            lambda s: s.shift(1).rolling(window, min_periods=min_prev).median())
        out[f"C1_{stat}"] = g[stat].transform(
            lambda s: s.shift(1).rolling(window, min_periods=min_prev).mean())
        out[f"C2_{stat}"] = g[stat].transform(
            lambda s: s.shift(1).ewm(halflife=halflife, adjust=True).mean())
        media_act = gs[stat].transform(lambda s: s.shift(1).expanding().mean())

        key = pd.MultiIndex.from_arrays([out["player_id"], out["_prev_season"]])
        media_prev = pd.Series(media_temp[stat].reindex(key).to_numpy(), index=out.index)
        ult10_prev = pd.Series(ult10_temp[stat].reindex(key).to_numpy(), index=out.index)

        n = out["n_act"].astype(float)
        shrink = (n * media_act.fillna(0.0) + k * media_prev) / (n + k)
        c3 = np.where(out["n_act"] >= window, out[f"C1_{stat}"], shrink)
        c3 = np.where(np.isnan(c3), out[f"C1_{stat}"], c3)
        out[f"C3_{stat}"] = c3

        c5 = np.where(out["n_act"] >= c5_min_act, media_act, ult10_prev)
        c5 = np.where(np.isnan(c5), out[f"C1_{stat}"], c5)
        out[f"C5_{stat}"] = c5
        if stat == stats[0]:
            sin_prev = int(media_prev.isna().sum())
    out.attrs["c3_c5_fallback_filas"] = sin_prev
    return out.drop(columns=["_prev_season"])


def team_context(tgs_rated: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Contexto por (game_id, team_id) para C4: rating defensivo rolling del RIVAL,
    ritmo rolling del RIVAL, local y back-to-back propio.

    tgs_rated = salida de features.ratings.compute_team_rolling_ratings (la MISMA
    fórmula de las features oficiales: ratio de medias, shift(1)). Ritmo =
    poss_rolling. b2b = el equipo jugó el día anterior.
    """
    base = tgs_rated[["game_id", "team_id", "is_home", "def_rating", "poss_rolling"]]
    opp = base.rename(columns={"team_id": "opp_team_id", "def_rating": "opp_def_rating",
                               "poss_rolling": "opp_pace", "is_home": "_x"})
    m = base.merge(opp, on="game_id")
    m = m[m["team_id"] != m["opp_team_id"]].drop(columns=["_x", "def_rating", "poss_rolling"])

    fechas = pd.concat([
        games[["game_id", "game_date", "home_team_id"]].rename(columns={"home_team_id": "team_id"}),
        games[["game_id", "game_date", "away_team_id"]].rename(columns={"away_team_id": "team_id"}),
    ])
    fechas["game_date"] = pd.to_datetime(fechas["game_date"])
    fechas = fechas.sort_values(["team_id", "game_date"])
    fechas["b2b"] = (fechas.groupby("team_id")["game_date"].diff() == pd.Timedelta(days=1)
                     ).astype(int)
    # is_home NO se devuelve: player_game_stats ya lo trae por fila de jugador, y
    # duplicarlo rompería el merge (is_home_x / is_home_y).
    m = m.drop(columns=["is_home"])
    return m.merge(fechas[["game_id", "team_id", "b2b"]], on=["game_id", "team_id"],
                   how="left")


C4_FEATURES: tuple[str, ...] = ("opp_def_rating", "opp_pace", "is_home", "b2b")


def fit_c4_residual(train: pd.DataFrame, stat: str) -> np.ndarray:
    """OLS sobre el RESIDUO (real - C1), ajustada SOLO en el train del fold."""
    t = train.dropna(subset=list(C4_FEATURES) + [f"C1_{stat}"])
    X = t[list(C4_FEATURES)].to_numpy(dtype=float)
    y = (t[stat] - t[f"C1_{stat}"]).to_numpy(dtype=float)
    return dexp3.fit_ols(X, y)


def apply_c4(df: pd.DataFrame, stat: str, coef: np.ndarray) -> np.ndarray:
    """C4 = C1 + residuo ajustado; sin contexto (NaN) cae a C1 (declarado)."""
    X = df[list(C4_FEATURES)].to_numpy(dtype=float)
    ajuste = dexp3.apply_ols(coef, np.nan_to_num(X))
    sin_ctx = np.isnan(X).any(axis=1)
    c1 = df[f"C1_{stat}"].to_numpy(dtype=float)
    return np.where(sin_ctx, c1, c1 + ajuste)


def reconstruct_selector(
    rows: pd.DataFrame,
    games: pd.DataFrame,
    top: int = SEL_TOP,
    min_games: int = SEL_MIN_GAMES,
    window: int = WINDOW,
    recency_days: int = SEL_RECENCY_DAYS,
) -> pd.DataFrame:
    """Top `top` por minutos rolling por equipo y partido, con la regla de
    producción de D-PRE-6 SIN señales de plantilla (resolución 2 de Antonio):

      equipo actual = equipo de la fila MÁS RECIENTE del jugador en la liga
                      (cualquier fila, también de 0 minutos);
      recencia      = esa fila dentro de `recency_days`;
      mínimo        = >= `min_games` jugados en su ventana de `window`;
      orden         = media de minutos de sus últimos `window` jugados (desc),
                      desempate por player_id.

    Estado SOLO con filas de fechas ESTRICTAMENTE anteriores a la del partido
    (anti-leakage, igual que producción). El filtro de estatus Out/Doubtful no
    es reconstruible en histórico (declarado en el pre-registro).

    `rows`: player_id, team_id, game_date, minutes (todas las filas, 0 incluidas).
    Devuelve (game_id, team_id, player_id, sel_rank, sel_games_in_window).
    """
    r = rows[["player_id", "team_id", "game_date", "minutes"]].copy()
    r["game_date"] = pd.to_datetime(r["game_date"])
    g = games[["game_id", "game_date", "home_team_id", "away_team_id"]].copy()
    g["game_date"] = pd.to_datetime(g["game_date"])

    last_team: dict[int, int] = {}
    last_date: dict[int, pd.Timestamp] = {}
    members: dict[int, set[int]] = {}
    mins: dict[int, deque] = {}
    out: list[tuple] = []

    filas_por_dia = {d: df for d, df in r.groupby("game_date", sort=True)}
    juegos_por_dia = {d: df for d, df in g.groupby("game_date", sort=True)}
    for d in sorted(set(filas_por_dia) | set(juegos_por_dia)):
        corte = d - timedelta(days=recency_days)
        for jg in juegos_por_dia.get(d, pd.DataFrame()).itertuples(index=False):
            for tid in (int(jg.home_team_id), int(jg.away_team_id)):
                cands = []
                for pid in members.get(tid, ()):
                    if last_date[pid] < corte:
                        continue
                    dq = mins.get(pid)
                    if dq is None or len(dq) < min_games:
                        continue
                    cands.append((-float(np.mean(dq)), pid, len(dq)))
                for rank, (_, pid, n) in enumerate(sorted(cands)[:top], start=1):
                    out.append((jg.game_id, tid, pid, rank, n))
        for f in filas_por_dia.get(d, pd.DataFrame()).itertuples(index=False):
            pid, tid = int(f.player_id), int(f.team_id)
            prev = last_team.get(pid)
            if prev is not None and prev != tid:
                members[prev].discard(pid)
            members.setdefault(tid, set()).add(pid)
            last_team[pid], last_date[pid] = tid, d
            if f.minutes == f.minutes and f.minutes and f.minutes > 0:
                mins.setdefault(pid, deque(maxlen=window)).append(float(f.minutes))
    return pd.DataFrame(out, columns=["game_id", "team_id", "player_id", "sel_rank",
                                      "sel_games_in_window"])


def season_start_flags(df: pd.DataFrame, games: pd.DataFrame, days: int = ARRANQUE_DIAS) -> Any:
    """True si el partido cae en los primeros `days` días de su temporada regular."""
    g = games[["season", "game_date"]].copy()
    g["game_date"] = pd.to_datetime(g["game_date"])
    inicio = g.groupby("season")["game_date"].min()
    fecha = pd.to_datetime(df["game_date"])
    return (fecha - df["season"].map(inicio)).dt.days < days


def evaluate_candidate(df: pd.DataFrame, stat: str, cand: str,
                       seasons: tuple[str, ...] = VALID_SEASONS) -> dict[str, Any]:
    """B0 vs candidato: pareado agregado + por fold, en una población dada."""
    y, b0, c = (df[stat].to_numpy(float), df[f"B0_{stat}"].to_numpy(float),
                df[f"{cand}_{stat}"].to_numpy(float))
    agg = dexp3.paired_mae_diff(y, b0, c)
    mae_b0 = float(np.abs(y - b0).mean()) if len(y) else float("nan")
    por_fold = {}
    for s in seasons:
        v = df[df["season"] == s]
        if v.empty:
            continue
        p = dexp3.paired_mae_diff(v[stat], v[f"B0_{stat}"], v[f"{cand}_{stat}"])
        por_fold[s] = {"n": p["n"], "mejora": p["media"],
                       "mae_b0": round(float(np.abs(v[stat] - v[f"B0_{stat}"]).mean()), 5)}
    return {"agregado": agg, "mae_b0": round(mae_b0, 5),
            "mae_cand": round(float(np.abs(y - c).mean()), 5) if len(y) else None,
            "mejora_rel": round(agg["media"] / mae_b0, 5) if mae_b0 > 0 else None,
            "folds_con_mejora": sum(1 for f in por_fold.values() if f["mejora"] > 0),
            "n_folds": len(por_fold), "por_fold": por_fold}


def center_gate(ev: dict[str, Any], mde_rel: float) -> dict[str, Any]:
    """Puerta de CENTRO del pre-registro, sin lectura alternativa.

    (a) IC95 agregado excluye cero a favor del candidato; (b) mejora en >= 5
    folds; (c) mejora relativa >= max(3%, MDE relativo del stat).
    "Débil, no desplegable" (multiplicidad): cruza, con mejora en
    [umbral, umbral + 1 pp) y SIN consistencia por fold. INTERPRETACIÓN
    DECLARADA de "sin consistencia por fold": mejora en menos de TODOS los folds.
    """
    umbral = max(CENTRO_UMBRAL_MIN, mde_rel)
    ic = ev["agregado"]["ic95"]
    a = bool(ic is not None and ic[0] > 0)
    b = ev["folds_con_mejora"] >= CENTRO_MIN_FOLDS
    c = ev["mejora_rel"] is not None and ev["mejora_rel"] >= umbral
    cruza = a and b and c
    debil = bool(cruza and ev["mejora_rel"] < umbral + DEBIL_MARGEN
                 and ev["folds_con_mejora"] < ev["n_folds"])
    return {"umbral": round(umbral, 5), "a_ic": a, "b_folds": b, "c_magnitud": bool(c),
            "cruza": cruza, "debil_no_desplegable": debil,
            "desplegable": bool(cruza and not debil)}


def arranque_gate(ev: dict[str, Any]) -> dict[str, Any]:
    """Puerta de ARRANQUE (solo C3): >= 3% del MAE de B0, IC95 excluye cero y
    mejora en >= 4 de 6 temporadas, en el subconjunto de los primeros 21 días."""
    ic = ev["agregado"]["ic95"]
    a = bool(ic is not None and ic[0] > 0)
    b = ev["folds_con_mejora"] >= ARRANQUE_MIN_FOLDS
    c = ev["mejora_rel"] is not None and ev["mejora_rel"] >= ARRANQUE_UMBRAL
    return {"a_ic": a, "b_folds": b, "c_magnitud": bool(c), "cruza": a and b and c}


def mde_cell(fase0_df: pd.DataFrame, stat: str, n_total: int) -> dict[str, Any]:
    """MDE de la celda (stat, población): sd pareada de los DOS primeros folds
    contra B0, extrapolada al n total. INTERPRETACIÓN DECLARADA: la celda toma el
    MAYOR MDE entre los candidatos (conservador: si un candidato no tiene
    resolución, la celda no la tiene)."""
    y = fase0_df[stat].to_numpy(float)
    b0 = fase0_df[f"B0_{stat}"].to_numpy(float)
    mae_b0 = float(np.abs(y - b0).mean()) if len(y) else float("nan")
    por_cand = {}
    for c in CANDIDATOS:
        col = f"{c}_{stat}"
        if col not in fase0_df:
            continue
        sd = dexp3.paired_mae_diff(y, b0, fase0_df[col].to_numpy(float))["sd"]
        mde = dexp3.mde_from_sd(sd, n_total)
        por_cand[c] = {"sd": round(sd, 6), "mde_abs": round(mde, 6),
                       "mde_rel": round(mde / mae_b0, 5) if mae_b0 > 0 else None}
    peor = max((v["mde_rel"] for v in por_cand.values() if v["mde_rel"] is not None),
               default=float("inf"))
    return {"n_fase0": int(len(y)), "n_total": int(n_total), "mae_b0": round(mae_b0, 5),
            "por_candidato": por_cand, "mde_rel_celda": round(peor, 5),
            "con_resolucion": bool(peor <= MDE_REL_UMBRAL)}


def freeze_audit(df: pd.DataFrame, n: int = AUDIT_N, seed: int = AUDIT_SEED) -> list[int]:
    """Índices de la muestra de auditoría, sorteados ANTES de computar métricas."""
    idx = sorted(df.index.tolist())
    return sorted(random.Random(seed).sample(idx, min(n, len(idx))))


# ---------------------------------------------------------------------------
# Carga (solo lectura)
# ---------------------------------------------------------------------------


def load_frame() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[int, str]]:
    from nba_predictor.features._shared import add_opponent_stats
    from nba_predictor.features.ratings import _STAT_COLS, compute_team_rolling_ratings

    store = dexp3.make_store()
    print("Cargando fuentes (solo lectura)...", flush=True)
    games = store.load_games()
    pgs = store.load_player_game_stats()
    tgs = store.load_team_game_stats()
    names = store.load_player_names()
    print(f"  games {games.shape} | pgs {pgs.shape} | tgs {tgs.shape}", flush=True)

    gcols = ["game_id", "season", "game_date", "home_team_id", "away_team_id"]
    tg = tgs.merge(games[gcols[:3]], on="game_id", validate="many_to_one")
    tg = add_opponent_stats(tg, _STAT_COLS)
    rated = compute_team_rolling_ratings(tg)
    ctx = team_context(rated, games)

    df = pgs.merge(games[gcols], on="game_id", validate="many_to_one")
    df = dexp3.derive_stats(df)
    df = df.merge(ctx, on=["game_id", "team_id"], how="left", validate="many_to_one")
    df["game_date"] = pd.to_datetime(df["game_date"])
    return df, games, rated, names


# ---------------------------------------------------------------------------
# Etapa CENTRO
# ---------------------------------------------------------------------------


def etapa_centro() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df, games, _, names = load_frame()

    print("Reconstruyendo el selector de producción (D-PRE-6, sin señales)...", flush=True)
    sel = reconstruct_selector(df, games)

    played = df[df["minutes"] > 0].copy()
    arms = add_center_arms(played)
    cols = [f"B0_{s}" for s in STATS] + [f"C1_{s}" for s in STATS]
    universo_todo = arms.dropna(subset=cols).copy()   # >= 10 previos, todas las temporadas

    # C4: ajuste por fold, solo con el train del fold (temporadas anteriores).
    for s in STATS:
        universo_todo[f"C4_{s}"] = np.nan
    coefs: dict[str, dict[str, list[float]]] = {}
    for season in VALID_SEASONS:
        train = universo_todo[universo_todo["season"] < season]
        mask = universo_todo["season"] == season
        coefs[season] = {}
        for s in STATS:
            coef = fit_c4_residual(train, s)
            coefs[season][s] = [round(float(x), 6) for x in coef]
            universo_todo.loc[mask, f"C4_{s}"] = apply_c4(universo_todo[mask], s, coef)

    universo = universo_todo[universo_todo["season"].isin(VALID_SEASONS)].copy()
    sel_keys = sel.set_index(["game_id", "player_id"])
    universo["seleccionado"] = pd.MultiIndex.from_arrays(
        [universo["game_id"], universo["player_id"]]).isin(sel_keys.index)
    universo["titular"] = universo["roll_min"] >= CORTE_TITULAR_MIN
    universo["arranque"] = season_start_flags(universo, games).to_numpy()

    pobl = {
        "primaria_selector": universo[universo["seleccionado"]],
        "titulares": universo[universo["titular"]],
        "universo": universo,
    }
    # Descriptivo (resolución 1): seleccionados con 3-9 jugados en su ventana.
    sel_valid = sel.merge(games[["game_id", "season"]], on="game_id")
    sel_valid = sel_valid[sel_valid["season"].isin(VALID_SEASONS)]
    corto = sel_valid[sel_valid["sel_games_in_window"] < MIN_PREV]

    for nombre, p in pobl.items():
        for s in STATS:
            for c in ("B0", "C1", "C2", "C3", "C4", "C5"):
                assert p[f"{c}_{s}"].notna().all(), f"NaN en {c}_{s} ({nombre})"
    print(f"  universo {len(universo)} | primaria {len(pobl['primaria_selector'])} | "
          f"titulares {len(pobl['titulares'])} | seleccionados con 3-9 jugados {len(corto)}",
          flush=True)

    # ── Muestra de auditoría: congelada ANTES de métricas ──
    prim = pobl["primaria_selector"]
    muestra = freeze_audit(prim)
    filas_audit = []
    for i in muestra:
        r = prim.loc[i]
        filas_audit.append({
            "fecha": str(r["game_date"].date()), "game_id": r["game_id"],
            "jugador": names.get(int(r["player_id"]), f"#{int(r['player_id'])}"),
            "player_id": int(r["player_id"]), "team_id": int(r["team_id"]),
            "n_act": int(r["n_act"]),
            **{f"real_{s}": float(r[s]) for s in STATS},
            **{f"{c}_{s}": round(float(r[f"{c}_{s}"]), 2)
               for s in STATS for c in ("B0", "C1", "C2", "C3", "C4", "C5")},
        })
    (OUT_DIR / "audit_sample_centro.json").write_text(json.dumps(
        {"seed": AUDIT_SEED, "congelada_antes_de_metricas": True,
         "generado_utc": datetime.now(timezone.utc).isoformat(), "filas": filas_audit},
        ensure_ascii=False, indent=1), encoding="utf-8")

    # ── FASE 0 (se escribe ANTES que los resultados) ──
    fase0: dict[str, Any] = {"generado_utc": datetime.now(timezone.utc).isoformat(),
                             "regla": "celda con MDE relativo > 3% = SIN RESOLUCIÓN",
                             "folds_fase0": list(FASE0_SEASONS), "celdas": {}}
    for nombre, p in {**pobl, "arranque_primaria": prim[prim["arranque"]]}.items():
        f0 = p[p["season"].isin(FASE0_SEASONS)]
        fase0["celdas"][nombre] = {s: mde_cell(f0, s, len(p)) for s in STATS}
    (OUT_DIR / "mde_fase0.json").write_text(json.dumps(fase0, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    print("FASE 0 escrita.", flush=True)

    # ── Resultados de CENTRO ──
    res: dict[str, Any] = {"generado_utc": datetime.now(timezone.utc).isoformat(),
                           "parametros": {"halflife": EWM_HALFLIFE, "K": SHRINK_K,
                                          "c5_min_act": C5_MIN_ACT, "titular": CORTE_TITULAR_MIN,
                                          "arranque_dias": ARRANQUE_DIAS},
                           "conteos": {"universo": len(universo),
                                       "primaria_selector": len(prim),
                                       "titulares": len(pobl["titulares"]),
                                       "seleccionados_3_9_jugados": len(corto),
                                       "c3_c5_fallback_filas": arms.attrs.get(
                                           "c3_c5_fallback_filas")},
                           "c4_coeficientes": coefs, "poblaciones": {}, "puertas": {},
                           "arranque": {}}
    for nombre, p in pobl.items():
        res["poblaciones"][nombre] = {
            s: {c: evaluate_candidate(p, s, c) for c in CANDIDATOS} for s in STATS}
    for s in STATS:
        mde_rel = fase0["celdas"]["primaria_selector"][s]["mde_rel_celda"]
        puertas = {c: center_gate(res["poblaciones"]["primaria_selector"][s][c], mde_rel)
                   for c in CANDIDATOS}
        desplegables = [c for c, g in puertas.items() if g["desplegable"]]
        ganador = max(desplegables,
                      key=lambda c: res["poblaciones"]["primaria_selector"][s][c]["mejora_rel"],
                      default="B0")
        res["puertas"][s] = {"por_candidato": puertas, "ganador": ganador,
                             "con_resolucion": fase0["celdas"]["primaria_selector"][s]
                             ["con_resolucion"]}
        ar = prim[prim["arranque"]]
        ev = evaluate_candidate(ar, s, "C3")
        res["arranque"][s] = {"C3": ev, "puerta": arranque_gate(ev),
                              "n": int(len(ar))}
    (OUT_DIR / "results_centro.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    texto = render_centro(res, fase0)
    (OUT_DIR / "tabla_centro.txt").write_text(texto, encoding="utf-8")
    print(texto)


def render_centro(res: dict[str, Any], fase0: dict[str, Any]) -> str:
    L = ["D-EXP-7 — CENTRO (población primaria: selector reconstruido, >= 10 previos)", ""]
    c = res["conteos"]
    L.append(f"universo {c['universo']} | primaria {c['primaria_selector']} | "
             f"titulares {c['titulares']} | seleccionados 3-9 jugados (descriptivo) "
             f"{c['seleccionados_3_9_jugados']} | fallback C3/C5 {c['c3_c5_fallback_filas']}")
    for nombre in ("primaria_selector", "titulares", "universo"):
        L.append("")
        L.append(f"[{nombre}]")
        L.append(f"  {'stat':<5}{'cand':<5}{'MAE B0':>8}{'MAE c':>8}{'mejora':>9}"
                 f"{'rel':>8}{'IC95':>22}{'folds':>7}  {'MDEcelda':>8}")
        for s in STATS:
            mde = fase0["celdas"][nombre][s]["mde_rel_celda"]
            for cand in CANDIDATOS:
                e = res["poblaciones"][nombre][s][cand]
                ic = e["agregado"]["ic95"]
                L.append(f"  {s:<5}{cand:<5}{e['mae_b0']:>8.3f}{e['mae_cand']:>8.3f}"
                         f"{e['agregado']['media']:>9.4f}{e['mejora_rel']:>8.2%}"
                         f"   [{ic[0]:+.4f},{ic[1]:+.4f}]"
                         f"{e['folds_con_mejora']:>4}/{e['n_folds']}  {mde:>8.4f}")
    L.append("")
    L.append("PUERTAS DE CENTRO (primaria): a=IC, b=folds>=5, c=magnitud>=max(3%,MDE)")
    for s in STATS:
        p = res["puertas"][s]
        marcas = " ".join(
            f"{cand}:{'S' if g['cruza'] else 'n'}{'(débil)' if g['debil_no_desplegable'] else ''}"
            for cand, g in p["por_candidato"].items())
        L.append(f"  {s:<5} {marcas}  -> ganador {p['ganador']}"
                 f"{'' if p['con_resolucion'] else '  [celda SIN RESOLUCIÓN]'}")
    L.append("")
    L.append("PUERTA DE ARRANQUE (C3, primeros 21 días, primaria):")
    for s in STATS:
        a = res["arranque"][s]
        e = a["C3"]
        L.append(f"  {s:<5} n={a['n']:<6} rel {e['mejora_rel']:+.2%}  folds "
                 f"{e['folds_con_mejora']}/{e['n_folds']}  -> "
                 f"{'CRUZA' if a['puerta']['cruza'] else 'no cruza'}")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Etapa RANGO (una sola vez, con el centro adjudicado)
# ---------------------------------------------------------------------------


def mean_abs_dev_rolling(s: pd.Series, window: int = WINDOW, min_prev: int = MIN_PREV) -> pd.Series:
    """Desviación absoluta media de los últimos `window` previos (shift 1)."""
    return s.shift(1).rolling(window, min_periods=min_prev).apply(
        lambda a: float(np.mean(np.abs(a - a.mean()))), raw=True)


def range_series(played: pd.DataFrame, stats: tuple[str, ...] = STATS,
                 window: int = WINDOW, min_prev: int = MIN_PREV) -> pd.DataFrame:
    """R0 ([mín, máx]) y dispersión de los últimos `window` JUGADOS, sobre la serie
    COMPLETA de jugados de cada jugador (shift 1). Devuelve una fila por
    (game_id, player_id) con min_{stat}, max_{stat} y disp_{stat}.

    CORRECCIÓN (2026-10-25, re-run adjudicado por Antonio): la primera corrida de
    rango calculaba mín y máx con un rolling sobre las filas del UNIVERSO ya
    filtrado, no sobre la serie completa, y salía NaN o con otra ventana que la
    publicada. Aquí la ventana es la misma de producción (player_window_stats:
    los últimos 10 partidos jugados), fijada por test.
    """
    out = played.sort_values(["player_id", "game_date"], kind="stable").copy()
    g = out.groupby("player_id", sort=False)
    cols = ["game_id", "player_id"]
    for stat in stats:
        out[f"min_{stat}"] = g[stat].transform(
            lambda x: x.shift(1).rolling(window, min_periods=min_prev).min())
        out[f"max_{stat}"] = g[stat].transform(
            lambda x: x.shift(1).rolling(window, min_periods=min_prev).max())
        out[f"disp_{stat}"] = g[stat].transform(
            lambda x: mean_abs_dev_rolling(x, window, min_prev))
        cols += [f"min_{stat}", f"max_{stat}", f"disp_{stat}"]
    return out[cols]


def winkler(y: Any, lo: Any, hi: Any, alfa: float = ALFA_WINKLER) -> np.ndarray:
    """Interval score de Winkler: ancho + (2/alfa) * distancia fuera del rango."""
    y, lo, hi = (np.asarray(v, dtype=float) for v in (y, lo, hi))
    return (hi - lo) + (2 / alfa) * (np.maximum(lo - y, 0) + np.maximum(y - hi, 0))


def conformal_q(y: Any, centro: Any, escala: Any | None, nivel: float = COBERTURA_OBJETIVO) -> float:
    """Cuantil `nivel` de |y - centro| (/ escala si se da) en el TRAIN."""
    r = np.abs(np.asarray(y, float) - np.asarray(centro, float))
    if escala is not None:
        e = np.asarray(escala, float)
        ok = e > 0
        r = r[ok] / e[ok]
    return float(np.quantile(r, nivel))


def range_gate(cob_agg: float, cob_folds: list[float], ws_ic: list[float] | None,
               ancho_r1: float, ancho_r0: float) -> dict[str, Any]:
    """Puerta de RANGO: (a) cobertura agregada en [78%, 82%] y cada fold en
    [75%, 85%]; (b) interval score mejor que R0 con IC95 que excluye cero;
    (c) ancho medio menor que R0."""
    a = (RANGO_COB_AGREGADA[0] <= cob_agg <= RANGO_COB_AGREGADA[1]
         and all(RANGO_COB_FOLD[0] <= c <= RANGO_COB_FOLD[1] for c in cob_folds))
    b = bool(ws_ic is not None and ws_ic[0] > 0)
    c = ancho_r1 < ancho_r0
    return {"a_cobertura": bool(a), "b_winkler": b, "c_ancho": bool(c),
            "cruza": bool(a and b and c)}


def etapa_rango(centro_por_stat: dict[str, str]) -> None:
    # La corrida inválida del 2026-10-25T23:16:52Z quedó como
    # results_rango_INVALIDA_R0.json; esta repetición la adjudicó Antonio.
    marca = OUT_DIR / "results_rango.json"
    if marca.exists():
        raise SystemExit("La etapa RANGO ya corrió: el pre-registro la permite UNA vez.")
    if not (OUT_DIR / "results_centro.json").exists():
        raise SystemExit("Falta la etapa CENTRO: el rango corre con el centro adjudicado.")
    df, games, _, _ = load_frame()
    sel = reconstruct_selector(df, games)
    played = df[df["minutes"] > 0].copy()
    arms = add_center_arms(played)
    cols = [f"B0_{s}" for s in STATS] + [f"C1_{s}" for s in STATS]
    u = arms.dropna(subset=cols).copy()
    # R0 y dispersión sobre la serie COMPLETA de jugados (ver range_series).
    u = u.merge(range_series(played), on=["game_id", "player_id"], how="left",
                validate="one_to_one")
    for s in STATS:
        assert u[f"min_{s}"].notna().all() and u[f"max_{s}"].notna().all(), f"R0 NaN en {s}"
    if any(v == "C4" for v in centro_por_stat.values()):
        raise SystemExit("C4 como centro adjudicado: requiere recalcular el ajuste por fold "
                         "(no implementado en esta etapa; reportar antes de correr).")
    sel_idx = pd.MultiIndex.from_frame(sel[["game_id", "player_id"]])
    u["seleccionado"] = pd.MultiIndex.from_arrays([u["game_id"], u["player_id"]]).isin(sel_idx)

    res: dict[str, Any] = {"generado_utc": datetime.now(timezone.utc).isoformat(),
                           "centro_adjudicado": centro_por_stat, "por_stat": {}}
    for s in STATS:
        por_fold = {}
        for season in VALID_SEASONS:
            train = u[u["season"] < season]
            q1 = conformal_q(train[s], train[f"{centro_por_stat[s]}_{s}"], train[f"disp_{s}"])
            q2 = conformal_q(train[s], train[f"{centro_por_stat[s]}_{s}"], None)
            v = u[(u["season"] == season) & u["seleccionado"]]
            c = v[f"{centro_por_stat[s]}_{s}"].to_numpy(float)
            d = v[f"disp_{s}"].to_numpy(float)
            r1_lo = np.where(d > 0, c - q1 * d, c - q2)
            r1_hi = np.where(d > 0, c + q1 * d, c + q2)
            por_fold[season] = {"q1": q1, "q2": q2, "v": v, "r1": (r1_lo, r1_hi),
                                "r2": (c - q2, c + q2)}
        y = np.concatenate([f["v"][s].to_numpy(float) for f in por_fold.values()])
        r0 = (np.concatenate([f["v"][f"min_{s}"].to_numpy(float) for f in por_fold.values()]),
              np.concatenate([f["v"][f"max_{s}"].to_numpy(float) for f in por_fold.values()]))
        r1 = tuple(np.concatenate([f["r1"][i] for f in por_fold.values()]) for i in (0, 1))
        r2 = tuple(np.concatenate([f["r2"][i] for f in por_fold.values()]) for i in (0, 1))

        def cob(lo, hi, yy=y):
            return float(np.mean((yy >= lo) & (yy <= hi)))

        w0, w1 = winkler(y, *r0), winkler(y, *r1)
        dw = w0 - w1
        se = float(dw.std(ddof=1) / np.sqrt(len(dw)))
        ws_ic = [float(dw.mean() - 1.96 * se), float(dw.mean() + 1.96 * se)]
        cob_folds = [cob(*f["r1"], yy=f["v"][s].to_numpy(float)) for f in por_fold.values()]
        res["por_stat"][s] = {
            "n": int(len(y)),
            "R0": {"cobertura": cob(*r0), "ancho": float(np.mean(r0[1] - r0[0])),
                   "winkler": float(w0.mean())},
            "R1": {"cobertura": cob(*r1), "ancho": float(np.mean(r1[1] - r1[0])),
                   "winkler": float(w1.mean()), "cobertura_por_fold": cob_folds,
                   "q_por_fold": {k: f["q1"] for k, f in por_fold.items()}},
            "R2": {"cobertura": cob(*r2), "ancho": float(np.mean(r2[1] - r2[0])),
                   "winkler": float(winkler(y, *r2).mean())},
            "winkler_R0_menos_R1": {"media": float(dw.mean()), "ic95": ws_ic},
            "puerta": range_gate(cob(*r1), cob_folds, ws_ic,
                                 float(np.mean(r1[1] - r1[0])), float(np.mean(r0[1] - r0[0]))),
        }
    marca.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str),
                     encoding="utf-8")
    for s, r in res["por_stat"].items():
        print(f"{s:<5} R0 cob {r['R0']['cobertura']:.1%} ancho {r['R0']['ancho']:.2f} | "
              f"R1 cob {r['R1']['cobertura']:.1%} ancho {r['R1']['ancho']:.2f} | "
              f"R2 cob {r['R2']['cobertura']:.1%} ancho {r['R2']['ancho']:.2f} | "
              f"puerta {'CRUZA' if r['puerta']['cruza'] else 'no cruza'}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="D-EXP-7")
    sub = ap.add_subparsers(dest="etapa", required=True)
    sub.add_parser("centro")
    pr = sub.add_parser("rango")
    pr.add_argument("--centro", required=True, help="pts=B0,reb=C1,ast=B0,fg3m=B0")
    args = ap.parse_args()
    if args.etapa == "centro":
        etapa_centro()
    else:
        centro = dict(kv.split("=") for kv in args.centro.split(","))
        assert set(centro) == set(STATS), "falta un stat en --centro"
        etapa_rango(centro)


if __name__ == "__main__":
    main()
