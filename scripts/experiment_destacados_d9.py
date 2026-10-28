"""D-EXP-9 — minutos proyectados con redistribución por bajas (OFFLINE).

PREGUNTA (pre-registro sellado en CLAUDE.md, 2026-10-28T01:44:23Z): ¿mejora el
valor central de los destacados si los minutos se proyectan condicionados a los
compañeros Out en el corte publish, en vez del rolling propio?

  M0 = rolling de 10 de minutos (roll_min de D-EXP-3).
  M1 = M0 + beta * min_perdidos           (OLS SIN intercepto, tope [0, 44]).
  M2 = M0 + b1 * mismo_grupo + b2 * otro  (ídem).
  centro = tasa por minuto (media exponencial de stat/minutos, vida media 5,
           promedio de razones) x minutos del brazo.

min_perdidos = suma, sobre compañeros Out en el PDF publish con rolling >= 25,
de rolling del compañero x fracción de los últimos 10 jugados del destacado en
que ese compañero jugó con su mismo equipo. Una baja ya absorbida pesa 0.

POR QUÉ UN SCRIPT APARTE Y NO UNA ETAPA DE D-EXP-7: d7 tiene funciones
protegidas (range_series, range_gate, conformal_q) y marcas de una sola corrida
por etapa; este experimento solo IMPORTA de d7 (load_frame, reconstruct_selector,
add_center_arms, season_start_flags) y de D-EXP-3 (paired_mae_diff, mde_from_sd),
igual que d7 importa de D-EXP-3. fit_ols de D-EXP-3 queda intacto: aquí vive
fit_ols_sin_intercepto.

Solo lectura: BigQuery con CloudDataStore EXPLÍCITO (vía d7.load_frame) y los
artefactos locales de D-EXP-1 y D-EXP-2b. Escribe únicamente en
data/experiment_destacados_d9/. Una sola corrida: aborta si results.json existe.

Uso:
  python scripts/experiment_destacados_d9.py
"""
from __future__ import annotations

import importlib.util
import json
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_D7_PATH = Path(__file__).resolve().parent / "experiment_destacados_d7.py"
_SPEC = importlib.util.spec_from_file_location("_d7_para_d9", _D7_PATH)
d7 = importlib.util.module_from_spec(_SPEC)
sys.modules["_d7_para_d9"] = d7
_SPEC.loader.exec_module(d7)
dexp3 = d7.dexp3

OUT_DIR = Path("data/experiment_destacados_d9")
INSTANCIAS = Path("data/experiment_pjuega/instancias.json")
NYS_PARQUET = Path("data/experiment_v2b/nys.parquet")
RESOLVED_V2B = Path("data/experiment_v2b/resolved.parquet")

GEM_SEASONS: tuple[str, ...] = ("2023-24", "2024-25", "2025-26")
VALID: tuple[str, ...] = ("2024-25", "2025-26")
STATS: tuple[str, ...] = d7.STATS
WINDOW = 10
TEAMMATE_MIN_GAMES = 3          # rolling del compañero: mínimo 3, como el selector
FUERTE_MIN = 25.0               # compañero "fuerte": rolling >= 25
MIN_CAP = (0.0, 44.0)
EWM_HALFLIFE = d7.EWM_HALFLIFE  # 5
SEL_TOP = d7.SEL_TOP            # 2
EXCLUIR_ESTATUS = frozenset({"Out", "Doubtful"})

# Puertas (sin lectura alternativa).
UMBRAL_M1 = 0.05
UMBRAL_M2 = 0.03
UMBRAL_PRODUCTO = 0.05
MDE_REL_UMBRAL = 0.03
N_MIN_FASE0 = 2000
AUDIT_SEED = 42
AUDIT_AZAR = 10
AUDIT_POR_FRONTERA = 5


# ---------------------------------------------------------------------------
# Funciones PURAS — sin red, unit-testeables
# ---------------------------------------------------------------------------


def fit_ols_sin_intercepto(X: Any, y: Any) -> np.ndarray:
    """OLS SIN intercepto: min ||y - X b||. Con una columna, b = sum(xy)/sum(x^2).

    Sin intercepto, una fila con tratamiento 0 recibe exactamente M0: el brazo no
    puede corregir un sesgo medio de minutos, solo redistribuir.
    """
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X[:, None]
    coef, *_ = np.linalg.lstsq(X, np.asarray(y, dtype=float), rcond=None)
    return coef


def clip_minutes(m: Any, lo: float = MIN_CAP[0], hi: float = MIN_CAP[1]) -> np.ndarray:
    return np.clip(np.asarray(m, dtype=float), lo, hi)


def fraccion_jugada(ventana: list[tuple[str, int]], pid: int,
                    jugaron: dict[tuple[str, int], set[int]]) -> float:
    """Fracción de los partidos de `ventana` ((game_id, team_id) del destacado) en
    que `pid` jugó (minutes > 0) CON ESE MISMO EQUIPO. Ventana vacía -> 0."""
    if not ventana:
        return 0.0
    return sum(1 for k in ventana if pid in jugaron.get(k, ())) / len(ventana)


def min_perdidos(contribs: list[tuple[float, float]]) -> float:
    """Suma de rolling x fracción jugada. Baja absorbida (fracción 0) pesa 0."""
    return float(sum(r * f for r, f in contribs))


def filtrar_selector(sel_all: pd.DataFrame, estatus: dict[tuple[str, int], str],
                     top: int = SEL_TOP, excluir: frozenset = EXCLUIR_ESTATUS) -> pd.DataFrame:
    """Filtro de producción sobre el ranking completo del selector: Out y Doubtful
    del PDF publish fuera, el puesto pasa al siguiente por minutos (re-ranking)."""
    s = sel_all.sort_values(["game_id", "team_id", "sel_rank"], kind="stable")
    st = [estatus.get((g, int(p))) for g, p in zip(s["game_id"], s["player_id"])]
    s = s[[x not in excluir for x in st]].copy()
    s["rank_prod"] = s.groupby(["game_id", "team_id"]).cumcount() + 1
    return s[s["rank_prod"] <= top]


def rolling_asof(played: pd.DataFrame, queries: pd.DataFrame, col: str,
                 window: int = WINDOW, min_games: int = TEAMMATE_MIN_GAMES) -> pd.DataFrame:
    """Media de `col` en los últimos `window` jugados del jugador ESTRICTAMENTE
    antes de la fecha de la consulta (y cuántos hay, tope `window`). Con menos de
    `min_games`, NaN. `queries`: player_id, game_date. Devuelve las mismas filas
    con columnas `{col}_r` y `{col}_n`, en el orden de `queries`.
    """
    p = played[["player_id", "game_date", col]].copy()
    p["player_id"] = p["player_id"].astype("int64")
    p["game_date"] = pd.to_datetime(p["game_date"]).astype("datetime64[ns]")
    p = p.sort_values(["player_id", "game_date"], kind="stable")
    g = p.groupby("player_id", sort=False)[col]
    p["_r"] = g.transform(lambda s: s.rolling(window, min_periods=1).mean())
    p["_n"] = g.cumcount() + 1
    p["_n"] = p["_n"].clip(upper=window)
    src = p[["player_id", "game_date", "_r", "_n"]].sort_values("game_date", kind="stable")

    q = queries[["player_id", "game_date"]].copy()
    q["_orden"] = np.arange(len(q))
    q["player_id"] = q["player_id"].astype("int64")
    q["_d"] = (pd.to_datetime(q["game_date"]) - pd.Timedelta(seconds=1)).astype("datetime64[ns]")
    q = q.sort_values("_d", kind="stable")
    m = pd.merge_asof(q, src.rename(columns={"game_date": "_d"}), on="_d", by="player_id",
                      direction="backward")
    m = m.sort_values("_orden")
    r = m["_r"].where(m["_n"] >= min_games)
    out = queries.copy()
    out[f"{col}_r"] = r.to_numpy()
    out[f"{col}_n"] = m["_n"].fillna(0).astype(int).to_numpy()
    return out


def rate_ewm(played: pd.DataFrame, stats: tuple[str, ...] = STATS,
             halflife: float = EWM_HALFLIFE) -> pd.DataFrame:
    """Tasa por minuto suavizada: media exponencial (todos los jugados PREVIOS,
    shift 1) de stat/minutos por partido — promedio de razones, D-EXP-3."""
    out = played.sort_values(["player_id", "game_date"], kind="stable").copy()
    for s in stats:
        out[f"_rate_{s}"] = out[s] / out["minutes"]
        out[f"tasa_{s}"] = out.groupby("player_id", sort=False)[f"_rate_{s}"].transform(
            lambda x: x.shift(1).ewm(halflife=halflife, adjust=True).mean())
    return out.drop(columns=[f"_rate_{s}" for s in stats])


def ventanas_destacado(played: pd.DataFrame, window: int = WINDOW
                       ) -> dict[tuple[str, int], list[tuple[str, int]]]:
    """(game_id, player_id) -> sus `window` jugados PREVIOS como (game_id, team_id).
    El partido que se predice no está en su propia ventana."""
    p = played.sort_values(["player_id", "game_date"], kind="stable")
    out: dict[tuple[str, int], list[tuple[str, int]]] = {}
    for pid, d in p.groupby("player_id", sort=False):
        gids, tids = d["game_id"].tolist(), d["team_id"].astype(int).tolist()
        for i, gid in enumerate(gids):
            out[(gid, int(pid))] = list(zip(gids[max(0, i - window):i], tids[max(0, i - window):i]))
    return out


def ventanas_equipo(games: pd.DataFrame, window: int = WINDOW
                    ) -> dict[tuple[str, int], list[tuple[str, int]]]:
    """(game_id, team_id) -> los `window` partidos PREVIOS de ese equipo."""
    tg = pd.concat([
        games[["game_id", "game_date", "home_team_id"]].rename(columns={"home_team_id": "team_id"}),
        games[["game_id", "game_date", "away_team_id"]].rename(columns={"away_team_id": "team_id"}),
    ])
    tg["game_date"] = pd.to_datetime(tg["game_date"])
    tg = tg.sort_values(["team_id", "game_date", "game_id"], kind="stable")
    out: dict[tuple[str, int], list[tuple[str, int]]] = {}
    for tid, d in tg.groupby("team_id", sort=False):
        gids = d["game_id"].tolist()
        for i, gid in enumerate(gids):
            out[(gid, int(tid))] = [(g, int(tid)) for g in gids[max(0, i - window):i]]
    return out


def paired_eval(df: pd.DataFrame, y: str, a: str, b: str,
                seasons: tuple[str, ...] = VALID) -> dict[str, Any]:
    """Pareado a (referencia) vs b (candidato): positivo = b acierta más."""
    agg = dexp3.paired_mae_diff(df[y], df[a], df[b])
    mae_a = float(np.abs(df[y] - df[a]).mean()) if len(df) else float("nan")
    por_fold = {}
    for s in seasons:
        v = df[df["season"] == s]
        if v.empty:
            continue
        p = dexp3.paired_mae_diff(v[y], v[a], v[b])
        mae_s = float(np.abs(v[y] - v[a]).mean())
        por_fold[s] = {"n": p["n"], "mejora": p["media"], "ic95": p["ic95"],
                       "mae_ref": round(mae_s, 5),
                       "mejora_rel": round(p["media"] / mae_s, 5) if mae_s > 0 else None}
    return {"agregado": agg, "mae_ref": round(mae_a, 5),
            "mae_cand": round(float(np.abs(df[y] - df[b]).mean()), 5) if len(df) else None,
            "mejora_rel": round(agg["media"] / mae_a, 5) if mae_a > 0 else None,
            "folds_con_mejora": sum(1 for f in por_fold.values() if f["mejora"] > 0),
            "n_folds": len(por_fold), "por_fold": por_fold}


def puerta(ev: dict[str, Any], umbral_min: float, mde_rel: float,
           n_folds_req: int = len(VALID)) -> dict[str, Any]:
    """(a) IC95 agregado excluye cero a favor; (b) mejora en los `n_folds_req`
    folds; (c) mejora relativa >= max(umbral_min, MDE relativo)."""
    umbral = max(umbral_min, mde_rel)
    ic = ev["agregado"]["ic95"]
    a = bool(ic is not None and ic[0] > 0)
    b = ev["folds_con_mejora"] >= n_folds_req
    c = bool(ev["mejora_rel"] is not None and ev["mejora_rel"] >= umbral)
    return {"umbral": round(umbral, 5), "a_ic": a, "b_folds": b, "c_magnitud": c,
            "cruza": bool(a and b and c)}


# ---------------------------------------------------------------------------
# Corrida única
# ---------------------------------------------------------------------------


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def cargar_estatus() -> tuple[pd.DataFrame, set[tuple[str, int]], set[str], dict[str, Any]]:
    """Instancias publish OFICIALES de D-EXP-1 + NYS de D-EXP-2b, con assert de que
    ambos usan el mismo sufijo publish por fecha."""
    ins = pd.DataFrame(json.loads(INSTANCIAS.read_text(encoding="utf-8")))
    pub = ins[(ins["cut"] == "publish") & ins["season"].isin(GEM_SEASONS)].copy()
    ok = pub[pub["veredicto"] == "incluida"].copy()
    ok["player_id"] = ok["player_id"].astype(int)
    ok["team_id"] = ok["team_id"].astype(int)

    nys = pd.read_parquet(NYS_PARQUET)
    nys = nys[nys["date"].between("2023-10-01", "2026-06-30")]
    nys_pares = {(d, int(t)) for d, t in zip(nys["date"], nys["team_id"])}

    res = pd.read_parquet(RESOLVED_V2B)
    suf_v2b = res.groupby("date")["suffix"].first().to_dict()
    suf_d1 = pub.groupby("date")["suffix"].first().to_dict()
    comunes = sorted(set(suf_v2b) & set(suf_d1))
    distintos = [f for f in comunes if suf_v2b[f] != suf_d1[f]]
    assert not distintos, f"sufijo publish distinto entre D-EXP-1 y D-EXP-2b: {distintos[:5]}"

    fechas_pdf = set(pub["date"]) | {d for d, _ in nys_pares}
    info = {"instancias_publish": int(len(pub)), "incluidas": int(len(ok)),
            "por_veredicto": {k: int(v) for k, v in pub["veredicto"].value_counts().items()},
            "nys_pares": len(nys_pares), "fechas_con_pdf": len(fechas_pdf),
            "fechas_sufijo_comparado": len(comunes), "sufijos_distintos": 0}
    return ok, nys_pares, fechas_pdf, info


def construir(df: pd.DataFrame, games: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame,
                                                               dict[str, Any]]:
    """Universo GemBox con tratamiento, placebo, flags y selector filtrado.
    Devuelve (universo, pares compañero-baja en formato largo, contadores)."""
    ok, nys_pares, fechas_pdf, info = cargar_estatus()
    estatus = {(g, p): s for g, p, s in zip(ok["game_id"], ok["player_id"], ok["status"])}

    print("Selector con ranking completo (top alto)...", flush=True)
    sel_all = d7.reconstruct_selector(df, games, top=10**6)
    sel_all = sel_all.merge(games[["game_id", "season"]], on="game_id")
    sel_all = sel_all[sel_all["season"].isin(GEM_SEASONS)]
    sel_prod = filtrar_selector(sel_all, estatus)
    sel_d7 = sel_all[sel_all["sel_rank"] <= SEL_TOP]
    st_d7 = pd.Series([estatus.get((g, int(p))) for g, p in
                       zip(sel_d7["game_id"], sel_d7["player_id"])]).value_counts(dropna=False)
    info["selector_d7_estatus"] = {str(k): int(v) for k, v in st_d7.items()}

    played = df[df["minutes"] > 0].copy()
    played["reb_pm"] = played["reb"] / played["minutes"]
    arms = d7.add_center_arms(played)
    arms = rate_ewm(arms)
    cols = [f"B0_{s}" for s in STATS] + [f"C1_{s}" for s in STATS]
    u = arms.dropna(subset=cols)
    u = u[u["season"].isin(GEM_SEASONS)].copy()
    u = u.reset_index(drop=True)

    # Flags.
    keys_prod = set(zip(sel_prod["game_id"], sel_prod["player_id"].astype(int)))
    keys_d7 = set(zip(sel_d7["game_id"], sel_d7["player_id"].astype(int)))
    u["primaria"] = [(g, int(p)) in keys_prod for g, p in zip(u["game_id"], u["player_id"])]
    u["primaria_d7"] = [(g, int(p)) in keys_d7 for g, p in zip(u["game_id"], u["player_id"])]
    u["titular"] = u["roll_min"] >= d7.CORTE_TITULAR_MIN
    u["arranque"] = d7.season_start_flags(u, games).to_numpy()
    gm = games.set_index("game_id")[["home_team_id", "away_team_id"]]
    h = u["game_id"].map(gm["home_team_id"]).astype(int)
    a = u["game_id"].map(gm["away_team_id"]).astype(int)
    u["opp_team_id"] = np.where(u["team_id"].astype(int) == h, a, h)
    u["fecha"] = pd.to_datetime(u["game_date"]).dt.strftime("%Y-%m-%d")
    u["con_pdf"] = u["fecha"].isin(fechas_pdf)
    u["nys_propio"] = [(f, int(t)) in nys_pares for f, t in zip(u["fecha"], u["team_id"])]
    u["nys_rival"] = [(f, int(t)) in nys_pares for f, t in zip(u["fecha"], u["opp_team_id"])]
    u["excluida"] = np.select([~u["con_pdf"], u["nys_propio"] | u["nys_rival"]],
                              ["sin_pdf", "nys"], default="")

    # Bajas fuertes: Out del PDF publish con rolling (últimos 10, mínimo 3) >= 25.
    outs = ok[ok["status"] == "Out"][["game_id", "team_id", "player_id", "date"]].copy()
    outs["game_date"] = pd.to_datetime(outs["date"])
    outs = rolling_asof(played, outs, "minutes")
    outs = rolling_asof(played, outs, "reb_pm")
    fuertes = outs[outs["minutes_r"] >= FUERTE_MIN].copy()
    info["bajas_out_publish"] = int(len(outs))
    info["bajas_fuertes"] = int(len(fuertes))

    # Rebotes por minuto del destacado hasta la fecha (misma función).
    rq = rolling_asof(played, u[["player_id", "game_date"]], "reb_pm")
    u["reb_pm_r"] = rq["reb_pm_r"].to_numpy()

    jugaron: dict[tuple[str, int], set[int]] = defaultdict(set)
    for g, t, p in zip(played["game_id"], played["team_id"].astype(int),
                       played["player_id"].astype(int)):
        jugaron[(g, t)].add(p)
    vent_d = ventanas_destacado(played)
    vent_e = ventanas_equipo(games)
    por_equipo = defaultdict(list)
    for r in fuertes.itertuples(index=False):
        por_equipo[(r.game_id, int(r.team_id))].append(r)

    pares: list[dict] = []
    mp, mpr, nueva = np.zeros(len(u)), np.zeros(len(u)), np.zeros(len(u), dtype=bool)
    for i, r in enumerate(u.itertuples(index=False)):
        pid, tid, opp, gid = int(r.player_id), int(r.team_id), int(r.opp_team_id), r.game_id
        ventana = vent_d.get((gid, pid), [])
        prev_team = vent_e.get((gid, tid), [])
        for b in por_equipo.get((gid, tid), ()):
            bp = int(b.player_id)
            if bp == pid:
                continue
            f = fraccion_jugada(ventana, bp, jugaron)
            jugo_ant = bool(prev_team) and bp in jugaron.get(prev_team[-1], ())
            nueva[i] |= jugo_ant
            mp[i] += b.minutes_r * f
            pares.append({"fila": i, "lado": "propio", "baja_id": bp, "rolling": b.minutes_r,
                          "n_ventana": int(b.minutes_n), "fraccion": f,
                          "reb_pm_baja": b.reb_pm_r, "jugo_partido_anterior": jugo_ant})
        vent_rival = vent_e.get((gid, opp), [])
        for b in por_equipo.get((gid, opp), ()):
            f = fraccion_jugada(vent_rival, int(b.player_id), jugaron)
            mpr[i] += b.minutes_r * f
            pares.append({"fila": i, "lado": "rival", "baja_id": int(b.player_id),
                          "rolling": b.minutes_r, "n_ventana": int(b.minutes_n),
                          "fraccion": f, "reb_pm_baja": b.reb_pm_r,
                          "jugo_partido_anterior": False})
    u["min_perdidos"] = mp
    u["min_perdidos_rival"] = mpr
    u["baja_nueva"] = nueva
    u["A"] = u["min_perdidos"] > 0
    # Destacado que cambió de equipo dentro de su ventana (columna de frontera).
    u["cambio_equipo"] = [len({t for _, t in vent_d.get((g, int(p)), [])} | {int(t)}) > 1
                          for g, p, t in zip(u["game_id"], u["player_id"], u["team_id"])]
    return u, pd.DataFrame(pares), info


def congelar_auditoria(gate_a: pd.DataFrame, pares: pd.DataFrame) -> list[dict]:
    """30 filas de A en la población de la puerta, ANTES de ajustar nada: 10 al
    azar y 20 de frontera a propósito (regla de D-RES-3e)."""
    rng = random.Random(AUDIT_SEED)
    prop = pares[pares["lado"] == "propio"]
    estratos = {
        "fraccion_parcial": set(prop[(prop["fraccion"] > 0) & (prop["fraccion"] < 1)]["fila"]),
        "rolling_25_27": set(prop[(prop["rolling"] >= 25) & (prop["rolling"] < 27)
                                  & (prop["fraccion"] > 0)]["fila"]),
        "baja_3a5_jugados": set(prop[prop["n_ventana"].between(3, 5)
                                     & (prop["fraccion"] > 0)]["fila"]),
        "destacado_cambio_equipo": set(gate_a[gate_a["cambio_equipo"]].index),
    }
    elegibles = set(gate_a.index)
    elegidas: list[dict] = []
    usadas: set[int] = set()
    for nombre, cand in estratos.items():
        pool = sorted((cand & elegibles) - usadas)
        toma = rng.sample(pool, min(AUDIT_POR_FRONTERA, len(pool)))
        usadas |= set(toma)
        elegidas += [{"fila": int(f), "estrato": nombre} for f in toma]
    pool = sorted(elegibles - usadas)
    elegidas += [{"fila": int(f), "estrato": "azar"} for f in
                 rng.sample(pool, min(AUDIT_AZAR, len(pool)))]
    return elegidas


def ajustar_y_aplicar(u: pd.DataFrame, pares: pd.DataFrame) -> dict[str, Any]:
    """Beta por fold en la primaria sin arranque del train; brazos en las filas de
    la temporada validada. Escribe M1, M2, placebo y los centros en `u`."""
    for c in ("M1", "M2", "MP"):
        u[c] = np.nan
    coefs: dict[str, Any] = {}
    prop = pares[pares["lado"] == "propio"]
    for s in VALID:
        train = u[(u["season"] < s) & u["primaria"] & ~u["arranque"] & (u["excluida"] == "")]
        x = train["min_perdidos"].to_numpy(float)
        y = (train["minutes"] - train["roll_min"]).to_numpy(float)
        b1 = fit_ols_sin_intercepto(x, y)

        # Grupo por rebotes por minuto contra la mediana de la liga en el train.
        med = float(np.nanmedian(u.loc[u["season"] < s, "reb_pm_r"]))
        p = prop.copy()
        int_d = (u.loc[p["fila"], "reb_pm_r"].to_numpy() > med)
        int_b = (p["reb_pm_baja"].to_numpy() > med)
        p["contrib"] = p["rolling"] * p["fraccion"]
        p["mismo"] = int_d == int_b
        g = p.groupby(["fila", "mismo"])["contrib"].sum().unstack(fill_value=0.0)
        mismo = pd.Series(0.0, index=u.index).add(g.get(True, pd.Series(dtype=float)),
                                                  fill_value=0.0)
        otro = pd.Series(0.0, index=u.index).add(g.get(False, pd.Series(dtype=float)),
                                                 fill_value=0.0)
        X2 = np.column_stack([mismo.loc[train.index], otro.loc[train.index]])
        b2 = fit_ols_sin_intercepto(X2, y)

        m = u["season"] == s
        u.loc[m, "M1"] = clip_minutes(u.loc[m, "roll_min"] + b1[0] * u.loc[m, "min_perdidos"])
        u.loc[m, "M2"] = clip_minutes(u.loc[m, "roll_min"] + b2[0] * mismo[m] + b2[1] * otro[m])
        u.loc[m, "MP"] = clip_minutes(u.loc[m, "roll_min"]
                                      + b1[0] * u.loc[m, "min_perdidos_rival"])
        u.loc[m, "mismo_grupo"] = mismo[m]
        u.loc[m, "otro_grupo"] = otro[m]
        coefs[s] = {"beta_M1": round(float(b1[0]), 6),
                    "beta_M2_mismo": round(float(b2[0]), 6),
                    "beta_M2_otro": round(float(b2[1]), 6),
                    "mediana_reb_pm_train": round(med, 6), "n_train": int(len(train)),
                    "n_train_A": int((train["min_perdidos"] > 0).sum())}
    u["M0"] = u["roll_min"]
    for st in STATS:
        for c in ("M0", "M1", "M2", "MP"):
            u[f"{c}c_{st}"] = u[f"tasa_{st}"] * u[c]
    coefs["tope_44_M1"] = int((u["M1"] >= MIN_CAP[1]).sum())
    coefs["tope_44_M2"] = int((u["M2"] >= MIN_CAP[1]).sum())
    return coefs


def evaluar(u: pd.DataFrame, fase0: dict[str, Any]) -> dict[str, Any]:
    base = u[u["season"].isin(VALID) & (u["excluida"] == "")]
    gate = base[base["primaria"] & ~base["arranque"]]
    A, B = gate[gate["A"]], gate[~gate["A"]]
    plc = gate[gate["min_perdidos_rival"] > 0]
    mde_rel = fase0["pts"]["mde_rel"]
    res: dict[str, Any] = {"n": {"gate": len(gate), "A": len(A), "B": len(B),
                                 "placebo": len(plc)}, "por_stat": {}}
    for st in STATS:
        y = st
        e_m1 = paired_eval(A, y, f"M0c_{st}", f"M1c_{st}")
        e_m2 = paired_eval(A, y, f"M1c_{st}", f"M2c_{st}")
        e_pl = paired_eval(plc, y, f"M0c_{st}", f"MPc_{st}")
        e_pr = paired_eval(A, y, f"B0_{st}", f"M1c_{st}")
        r = {"M1_vs_M0_A": e_m1, "M2_vs_M1_A": e_m2, "placebo_vs_M0": e_pl,
             "producto_M1_vs_B0_A": e_pr,
             "desc": {
                 "M0_vs_B0_A": paired_eval(A, y, f"B0_{st}", f"M0c_{st}"),
                 "M0_vs_B0_B": paired_eval(B, y, f"B0_{st}", f"M0c_{st}"),
                 "M1_vs_B0_gate_agregado": paired_eval(gate, y, f"B0_{st}", f"M1c_{st}"),
                 "M1_vs_M0_A_arranque": paired_eval(
                     base[base["primaria"] & base["arranque"] & base["A"]], y,
                     f"M0c_{st}", f"M1c_{st}"),
                 "M1_vs_M0_A_nueva": paired_eval(A[A["baja_nueva"]], y,
                                                 f"M0c_{st}", f"M1c_{st}"),
                 "M1_vs_M0_A_titulares": paired_eval(
                     base[base["titular"] & ~base["arranque"] & base["A"]], y,
                     f"M0c_{st}", f"M1c_{st}"),
                 "M1_vs_M0_A_universo": paired_eval(
                     base[~base["arranque"] & base["A"]], y, f"M0c_{st}", f"M1c_{st}"),
             }}
        if st == "pts":
            r["puertas"] = {
                "M1_vs_M0": puerta(e_m1, UMBRAL_M1, mde_rel),
                "M2_vs_M1": puerta(e_m2, UMBRAL_M2, mde_rel),
                "producto": puerta(e_pr, UMBRAL_PRODUCTO, mde_rel),
                "placebo_mejora_sobre_mde": bool(
                    e_pl["agregado"]["media"] > fase0["placebo_pts"]["mde_abs"]),
            }
        res["por_stat"][st] = r
    # Minutos (no es puerta: muestra el mecanismo).
    res["minutos_A"] = {
        "M1_vs_M0": paired_eval(A, "minutes", "M0", "M1"),
        "M2_vs_M0": paired_eval(A, "minutes", "M0", "M2"),
        "residuo_medio_A": round(float((A["minutes"] - A["M0"]).mean()), 4),
        "residuo_medio_B": round(float((B["minutes"] - B["M0"]).mean()), 4),
        "min_perdidos_medio_A": round(float(A["min_perdidos"].mean()), 4),
    }
    return res


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if (OUT_DIR / "results.json").exists():
        sys.exit("results.json ya existe: el pre-registro permite UNA corrida.")
    t0 = _ts()
    df, games, _, names = d7.load_frame()
    u, pares, info = construir(df, games)

    base = u[u["season"].isin(VALID) & (u["excluida"] == "")]
    gate = base[base["primaria"] & ~base["arranque"]]
    gate_a = gate[gate["A"]]

    def _d(s: pd.Series) -> dict:
        return {str(k): int(v) for k, v in s.items()}

    excl = u[u["primaria"] & (u["excluida"] != "")]
    conteos = {
        "universo_gem": int(len(u)),
        "primaria_por_temporada": _d(u[u["primaria"]].groupby("season").size()),
        "primaria_d7_por_temporada": _d(u[u["primaria_d7"]].groupby("season").size()),
        "excluidas_primaria": _d(excl.groupby(["season", "excluida"]).size()),
        "excluidas_primaria_2025_02_13": int((u["primaria"] & (u["fecha"] == "2025-02-13")).sum()),
        "gate_por_temporada": _d(gate.groupby("season").size()),
        "A_gate_por_temporada": _d(gate_a.groupby("season").size()),
        "A_nueva_gate_por_temporada": _d(gate_a[gate_a["baja_nueva"]].groupby("season").size()),
        "placebo_gate": int((gate["min_perdidos_rival"] > 0).sum()),
    }

    # 1) Muestra de auditoría, congelada ANTES de ajustar nada.
    muestra = congelar_auditoria(gate_a, pares)
    (OUT_DIR / "audit_sample.json").write_text(json.dumps(
        {"sellado_utc": _ts(), "seed": AUDIT_SEED, "filas": muestra,
         "claves": [{"game_id": u.at[m["fila"], "game_id"],
                     "player_id": int(u.at[m["fila"], "player_id"])} for m in muestra]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Muestra de auditoría congelada: {len(muestra)} filas", flush=True)

    # 2) Ajuste por fold (solo train) y aplicación.
    coefs = ajustar_y_aplicar(u, pares)
    base = u[u["season"].isin(VALID) & (u["excluida"] == "")]
    gate = base[base["primaria"] & ~base["arranque"]]
    gate_a = gate[gate["A"]]

    # 3) FASE 0, escrita ANTES de cualquier métrica.
    f1 = gate_a[gate_a["season"] == VALID[0]]
    n_tot = int(len(gate_a))
    fase0: dict[str, Any] = {"sellado_utc": _ts(), "n_A_gate": n_tot,
                             "n_A_fold1": int(len(f1)), "n_min": N_MIN_FASE0}
    for st in STATS:
        sd = dexp3.paired_mae_diff(f1[st], f1[f"M0c_{st}"], f1[f"M1c_{st}"])["sd"]
        mde = dexp3.mde_from_sd(sd, n_tot)
        mae = float(np.abs(f1[st] - f1[f"M0c_{st}"]).mean())
        fase0[st] = {"sd_fold1": sd, "mde_abs": round(mde, 6), "mae_M0_fold1": round(mae, 5),
                     "mde_rel": round(mde / mae, 5),
                     "con_resolucion": bool(n_tot >= N_MIN_FASE0
                                            and mde / mae <= MDE_REL_UMBRAL)}
    plc = gate[gate["min_perdidos_rival"] > 0]
    p1 = plc[plc["season"] == VALID[0]]
    sdp = dexp3.paired_mae_diff(p1["pts"], p1["M0c_pts"], p1["MPc_pts"])["sd"]
    mdep = dexp3.mde_from_sd(sdp, len(plc))
    fase0["placebo_pts"] = {"n": int(len(plc)), "n_fold1": int(len(p1)), "sd_fold1": sdp,
                            "mde_abs": round(mdep, 6)}
    (OUT_DIR / "fase0.json").write_text(json.dumps(fase0, indent=2), encoding="utf-8")
    print(json.dumps(fase0, indent=2), flush=True)

    # 4) Métricas.
    res = evaluar(u, fase0)
    res.update({"inicio_utc": t0, "fin_utc": _ts(), "info_estatus": info, "conteos": conteos,
                "coeficientes": coefs})
    (OUT_DIR / "results.json").write_text(
        json.dumps(res, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (OUT_DIR / "tabla.txt").write_text(render(res, fase0), encoding="utf-8")
    (OUT_DIR / "auditoria_muestra.txt").write_text(
        render_auditoria(u, pares, muestra, names), encoding="utf-8")
    print(render(res, fase0), flush=True)


def _fila_ev(nombre: str, ev: dict[str, Any]) -> str:
    a = ev["agregado"]
    folds = " ".join(f"{s}:{v['mejora']:+.4f}({v['mejora_rel'] * 100:+.2f}%)"
                     for s, v in ev["por_fold"].items() if v["mejora_rel"] is not None)
    ic = a["ic95"] or [float("nan"), float("nan")]
    rel = ev["mejora_rel"] * 100 if ev["mejora_rel"] is not None else float("nan")
    cand = ev["mae_cand"] if ev["mae_cand"] is not None else float("nan")
    return (f"  {nombre:<26} n={a['n']:>5}  ref {ev['mae_ref']:.4f} cand {cand:.4f}"
            f"  mejora {a['media']:+.4f} ({rel:+.2f}%)  t={a['t']}"
            f"  IC95 [{ic[0]:+.4f},{ic[1]:+.4f}]  | {folds}")


def render(res: dict[str, Any], fase0: dict[str, Any]) -> str:
    lin = ["D-EXP-9 — minutos con redistribución por bajas (positivo = el candidato acierta más)",
           f"n puerta={res['n']['gate']}  A={res['n']['A']}  B={res['n']['B']}  "
           f"placebo={res['n']['placebo']}",
           f"FASE 0 pts: MDE rel {fase0['pts']['mde_rel'] * 100:.2f}%  con_resolucion="
           f"{fase0['pts']['con_resolucion']}"]
    for st, r in res["por_stat"].items():
        lin.append(f"\n[{st}]")
        lin.append(_fila_ev("M1 vs M0 (A)", r["M1_vs_M0_A"]))
        lin.append(_fila_ev("M2 vs M1 (A)", r["M2_vs_M1_A"]))
        lin.append(_fila_ev("placebo vs M0", r["placebo_vs_M0"]))
        lin.append(_fila_ev("tasa x M1 vs B0 (A)", r["producto_M1_vs_B0_A"]))
        for k, ev in r["desc"].items():
            if ev["agregado"]["n"] > 1:
                lin.append(_fila_ev(f"desc {k}", ev))
        if "puertas" in r:
            lin.append(f"  PUERTAS: {json.dumps(r['puertas'])}")
    lin.append(f"\nMinutos en A: {json.dumps(res['minutos_A']['residuo_medio_A'])} residuo medio;"
               f" M1 vs M0 en minutos: {res['minutos_A']['M1_vs_M0']['agregado']['media']:+.4f}")
    lin.append(f"Coeficientes: {json.dumps(res['coeficientes'])}")
    return "\n".join(lin) + "\n"


def render_auditoria(u: pd.DataFrame, pares: pd.DataFrame, muestra: list[dict],
                     names: dict[int, str]) -> str:
    lin = ["D-EXP-9 — muestra de auditoría (30 filas de A, congelada antes del ajuste).",
           "Cotejar contra el PDF publish del día y los boxscores: compañeros Out, su rolling",
           "(últimos 10 jugados, mín. 3), fracción de los últimos 10 del destacado que jugaron",
           "con él, min_perdidos = suma rolling x fracción; M0/M1/M2 en minutos; real.", ""]
    for m in muestra:
        i = m["fila"]
        r = u.loc[i]
        nom = names.get(int(r["player_id"]), f"#{int(r['player_id'])}")
        lin.append(f"[{m['estrato']}] {r['fecha']} {r['game_id']}  {nom} (team {int(r['team_id'])})"
                   f"  temporada {r['season']}  cambio_equipo={bool(r['cambio_equipo'])}")
        lin.append(f"    M0 {r['M0']:.2f}  M1 {r['M1']:.2f}  M2 {r['M2']:.2f}  real {r['minutes']:.2f}"
                   f"  | pts real {r['pts']:.0f}  B0 {r['B0_pts']:.1f}  tasa*M0 {r['M0c_pts']:.2f}"
                   f"  tasa*M1 {r['M1c_pts']:.2f}  | min_perdidos {r['min_perdidos']:.2f}"
                   f"  rival {r['min_perdidos_rival']:.2f}")
        for p in pares[pares["fila"] == i].itertuples(index=False):
            nb = names.get(int(p.baja_id), f"#{int(p.baja_id)}")
            lin.append(f"      {p.lado:<6} Out {nb:<28} rolling {p.rolling:5.2f} (n={p.n_ventana})"
                       f"  fraccion {p.fraccion:.1f}  aporte {p.rolling * p.fraccion:5.2f}"
                       f"  jugo_partido_anterior={p.jugo_partido_anterior}")
        lin.append("")
    return "\n".join(lin)


if __name__ == "__main__":
    main()
