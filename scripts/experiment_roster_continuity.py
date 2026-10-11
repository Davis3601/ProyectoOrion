"""
D-EXP-6 — CONTINUIDAD DE ROSTER DESDE DATOS ESTRUCTURADOS.

*** OFFLINE, SOLO LECTURA DE LA NUBE. *** B-limpia, features_v1, registry,
endpoint, market_odds y n8n intactos; nada se despliega. CloudDataStore
construido EXPLICITAMENTE (convencion de scripts). Sin PDFs, sin LLM, sin
red de la NBA. Nada se escribe fuera de data/experiment_roster/.

QUE MIDE: si la continuidad del roster — cuanto se parece el equipo de hoy al
de los partidos recientes — añade señal a la B-limpia. Es la ultima feature
pendiente del Camino 5 y la UNICA superviviente del documento de ingesta no
estructurada (CANDIDATO DIFERIDO, 2026-10-08): la misma pregunta, contestada
con datos que ya estan en el boxscore en vez de con noticias.

DOS DEFINICIONES, ambas con shift(1) — solo partidos ANTERIORES:
  C_a (quintetas)  media, sobre los ultimos 5 partidos jugados, de
                   |titulares(j) ∩ titulares(j-1)| / 5. Rango [0,1].
  C_b (rotacion)   fraccion de los minutos del equipo en sus ultimos 10
                   partidos que pertenecen a jugadores que jugaron su partido
                   mas reciente. Rango [0,1].
La feature del modelo es la diferencia LOCAL - VISITANTE, como todas.

POR QUE DOS: miden cosas distintas. C_a ve el QUINTETO titular, que cambia
por decision del entrenador; C_b ve la ROTACION entera ponderada por
minutos, que cambia por lesiones, traspasos y descansos. Si las dos dan el
mismo signo, la señal es del roster y no del instrumento.

CONTRASTE: CAND vs CTL de MISMA construccion (CTL = las 11 features oficiales;
CAND = las mismas + una diferencia de continuidad). Sus predicciones estan muy
correlacionadas, asi que la diferencia pareada tiene sd baja: la leccion de
diseño de D-EXP-2b, aplicada desde el inicio.

LA HISTORIA CRUZA TEMPORADAS desde el warmup (2014-15), igual que el rolling
oficial: la continuidad entre el final de una temporada y el inicio de la
siguiente ES la señal que interesa (traspasos del verano). Las filas del
universo excluyen los primeros 15 partidos de cada equipo por temporada (regla
de AMBOS de features_v1), asi que sus ventanas siempre estan completas.

Uso:
    python scripts/experiment_roster_continuity.py --freeze-audit   # primero
    python scripts/experiment_roster_continuity.py
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import math
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from nba_predictor.config import FIRST_VAL_IDX, TRAINING_SEASONS
from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS

# Metricas por fold y diferencia pareada: las de D-EXP-2 por IMPORT. fold_metrics
# entrena con _fit_fold del pipeline oficial (scaler ajustado SOLO con train,
# logistica L2 con LOGREG_C): es el mismo entrenamiento que produjo la B-limpia.
_D2 = Path(__file__).resolve().parent / "experiment_disponibilidad_v2.py"
_S2 = importlib.util.spec_from_file_location("_dexp2", _D2)
dexp2 = importlib.util.module_from_spec(_S2)
sys.modules["_dexp2"] = dexp2
_S2.loader.exec_module(dexp2)

log = logging.getLogger("dexp6")

OUT_DIR = Path("data/experiment_roster")

# Folds walk-forward OFICIALES, construidos como los construye models/logistic.py
# (range(FIRST_VAL_IDX, len(TRAINING_SEASONS))): train acumulado desde 2016-17,
# valida la siguiente. Seis folds, 2020-21 .. 2025-26.
FOLDS: tuple[tuple[str, tuple[str, ...], str], ...] = tuple(
    (f"F{i - FIRST_VAL_IDX}", tuple(TRAINING_SEASONS[:i]), TRAINING_SEASONS[i])
    for i in range(FIRST_VAL_IDX, len(TRAINING_SEASONS))
)

WINDOW_A = 5     # partidos sobre los que se promedia el solape de quintetas
WINDOW_B = 10    # ventana de minutos de la rotacion, = ROLLING_WINDOW_GAMES
QUINTETO = 5

# Gate de resolucion y puerta numerica: TAL COMO SE PRE-REGISTRARON.
MDE_TECHO = 0.005
_Z_MDE = 2.8016          # z(0.975) + z(0.80)
PUERTA_FOLDS_POSITIVOS = 4
PUERTA_FOLDS_IC = 2
UMBRAL_ACCIONABLE = 0.005
UMBRAL_LEAKAGE = 0.015

AUDIT_SEED = 42
AUDIT_N = 30

CANDIDATOS = ("c_a_diff", "c_b_diff")


# ---------------------------------------------------------------------------
# Funciones PURAS — sin red, sin BigQuery, unit-testeables
# ---------------------------------------------------------------------------


@dataclass
class TeamGame:
    """Un partido de UN equipo: quien salio de titular y cuantos minutos jugo cada uno."""

    game_id: str
    game_date: str
    starters: frozenset[int]
    minutes: dict[int, float] = field(default_factory=dict)

    @property
    def played(self) -> frozenset[int]:
        """Jugadores con minutos > 0 en este partido."""
        return frozenset(p for p, m in self.minutes.items() if m and m > 0)


def continuity_a(seq: list[TeamGame], k: int, window: int = WINDOW_A) -> float:
    """C_a del partido k: solape medio de quintetas en los `window` partidos ANTERIORES.

    Usa los partidos k-window .. k-1 y, para cada uno, su predecesor: necesita
    por tanto k-window-1 .. k-1. El partido k NO entra — ni sus titulares ni nada
    suyo. Si no hay historia suficiente devuelve NaN: una ventana parcial
    produciria un valor de aspecto sano calculado sobre menos evidencia, y en el
    universo eso no debe ocurrir (se comprueba por assert).
    """
    if k - window - 1 < 0:
        return float("nan")
    solapes = [
        len(seq[j].starters & seq[j - 1].starters) / QUINTETO
        for j in range(k - window, k)
    ]
    return sum(solapes) / len(solapes)


def continuity_b(seq: list[TeamGame], k: int, window: int = WINDOW_B) -> float:
    """C_b del partido k: fraccion de los minutos de k-window..k-1 que son de
    jugadores que jugaron el partido k-1.

    El partido k no entra. Mide cuanto del tiempo de juego reciente del equipo
    esta en manos de la rotacion vigente: un traspaso o una lesion larga sacan
    minutos de la ventana que ya no estan en el ultimo partido.
    """
    if k - window < 0:
        return float("nan")
    ventana = seq[k - window: k]
    presentes = seq[k - 1].played
    total = 0.0
    de_presentes = 0.0
    for g in ventana:
        for p, m in g.minutes.items():
            if not m or m <= 0:
                continue
            total += m
            if p in presentes:
                de_presentes += m
    if total <= 0:
        return float("nan")
    return de_presentes / total


def team_continuity(seq: list[TeamGame]) -> list[dict[str, Any]]:
    """C_a y C_b de cada partido de la secuencia (orden cronologico)."""
    return [
        {"game_id": g.game_id, "c_a": continuity_a(seq, k), "c_b": continuity_b(seq, k)}
        for k, g in enumerate(seq)
    ]


def build_sequences(pgs: pd.DataFrame, games: pd.DataFrame) -> dict[int, list[TeamGame]]:
    """team_id -> partidos del equipo en orden cronologico, con titulares y minutos.

    Orden por (game_date, game_id): el game_id desempata dentro de una fecha y es
    monotono en la temporada, asi que el orden es total y reproducible.
    """
    fechas = games.set_index("game_id")["game_date"].astype(str).to_dict()
    por_eq: dict[tuple[int, str], TeamGame] = {}
    for r in pgs.itertuples(index=False):
        clave = (int(r.team_id), str(r.game_id))
        tg = por_eq.get(clave)
        if tg is None:
            tg = TeamGame(game_id=str(r.game_id), game_date=fechas.get(str(r.game_id), ""),
                          starters=frozenset())
            por_eq[clave] = tg
        if int(r.started) == 1:
            tg.starters = tg.starters | {int(r.player_id)}
        m = r.minutes
        tg.minutes[int(r.player_id)] = float(m) if m is not None and m == m else 0.0
    out: dict[int, list[TeamGame]] = {}
    for (tid, _gid), tg in por_eq.items():
        out.setdefault(tid, []).append(tg)
    for tid in out:
        out[tid].sort(key=lambda g: (g.game_date, g.game_id))
    return out


def mde_pareado(sd: float, n: int) -> float:
    """MDE de un contraste pareado a 80% de potencia, alfa 0.05 dos colas."""
    if n <= 1 or sd <= 0:
        return float("nan")
    return _Z_MDE * sd / math.sqrt(n)


def resolution_verdict(mde: float, techo: float = MDE_TECHO) -> str:
    if not (mde == mde):
        return "SIN_RESOLUCION"
    return "CUBRE" if mde <= techo else "SIN_RESOLUCION"


def gate_verdict(agregado: dict, por_fold: list[dict]) -> dict:
    """Puerta numerica pre-registrada, evaluada SIN lectura alternativa.

    "Excluye cero" y "fuera de cero" se evaluan en la direccion de la MEJORA
    (IC enteramente positivo): la puerta pregunta si el candidato GANA. Para que
    ninguna lectura quede escondida — la ambigüedad direccional fue el error de
    diseño de D-EXP-2b — se reportan tambien los conteos a dos colas, y si las dos
    lecturas discrepan se marca explicitamente.
    """
    lo, hi = agregado["ic95"]
    pos = sum(1 for f in por_fold if f["media"] > 0)
    ic_pos = sum(1 for f in por_fold if f["ic95"][0] > 0)
    ic_dos_colas = sum(1 for f in por_fold if f["ic95"][0] > 0 or f["ic95"][1] < 0)
    i_ = lo > 0
    ii = pos >= PUERTA_FOLDS_POSITIVOS
    iii = ic_pos >= PUERTA_FOLDS_IC
    cruza = i_ and ii and iii
    lectura_literal = (lo > 0 or hi < 0) and ii and (ic_dos_colas >= PUERTA_FOLDS_IC)
    if not cruza:
        salida = "NO_CRUZA: cierre con resolucion"
    elif agregado["media"] >= UMBRAL_ACCIONABLE:
        salida = "CRUZA >= 0.005: candidato a v2 post-enero (no sombra)"
    else:
        salida = "CRUZA < 0.005: real pero no accionable sola"
    return {
        "i_ic_agregado_positivo": i_, "ii_folds_positivos": pos,
        "iii_folds_ic_positivo": ic_pos, "folds_ic_fuera_de_cero_dos_colas": ic_dos_colas,
        "cruza": cruza, "salida": salida,
        "lectura_literal_dos_colas_cruzaria": lectura_literal,
        "lecturas_discrepan": cruza != lectura_literal,
        "auditar_leakage": agregado["media"] > UMBRAL_LEAKAGE,
    }


def freeze_audit_sample(pares: list[tuple[str, int]], seed: int = AUDIT_SEED,
                        n: int = AUDIT_N) -> dict:
    """30 equipo-partido del universo, sorteados con seed fijo ANTES de medir."""
    rng = random.Random(seed)
    elegidos = sorted(rng.sample(sorted(pares), min(n, len(pares))))
    return {"seed": seed,
            "congelado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_universo": len(pares),
            "pares": [{"game_id": g, "team_id": t} for g, t in elegidos]}


def describe(serie: pd.Series) -> dict:
    """Sanity de una feature: que no sea degenerada y tenga varianza util."""
    s = serie.dropna()
    return {"n": int(len(s)), "media": round(float(s.mean()), 5),
            "sd": round(float(s.std()), 5), "min": round(float(s.min()), 5),
            "p05": round(float(s.quantile(0.05)), 5), "p50": round(float(s.median()), 5),
            "p95": round(float(s.quantile(0.95)), 5), "max": round(float(s.max()), 5),
            "frac_valor_modal": round(float(s.round(6).value_counts(normalize=True).iloc[0]), 4),
            "distintos": int(s.round(6).nunique())}


# ---------------------------------------------------------------------------
# Lectura y CLI
# ---------------------------------------------------------------------------


def _load(store):
    features = store.load_features()
    games = store.load_games()
    pgs = store.load_player_game_stats()
    return features, games, pgs


def _assemble(features, games, seqs) -> pd.DataFrame:
    """features_v1 + diferencias de continuidad. Paridad con features_v1 por assert."""
    filas = []
    for tid, seq in seqs.items():
        for r in team_continuity(seq):
            filas.append({"team_id": tid, **r})
    cont = pd.DataFrame(filas)
    meta = games[["game_id", "home_team_id", "away_team_id"]]
    df = features.merge(meta, on="game_id", how="left", validate="one_to_one")
    h = cont.rename(columns={"team_id": "home_team_id", "c_a": "c_a_home", "c_b": "c_b_home"})
    a = cont.rename(columns={"team_id": "away_team_id", "c_a": "c_a_away", "c_b": "c_b_away"})
    df = df.merge(h, on=["game_id", "home_team_id"], how="left", validate="one_to_one")
    df = df.merge(a, on=["game_id", "away_team_id"], how="left", validate="one_to_one")
    df["c_a_diff"] = df["c_a_home"] - df["c_a_away"]
    df["c_b_diff"] = df["c_b_home"] - df["c_b_away"]
    assert len(df) == len(features), "paridad de filas con features_v1 rota"
    faltan = int(df[["c_a_diff", "c_b_diff"]].isna().any(axis=1).sum())
    assert faltan == 0, f"{faltan} filas del universo sin continuidad: ventana incompleta"
    return df


def main() -> None:  # noqa: C901 — CLI de orquestacion
    ap = argparse.ArgumentParser(description="D-EXP-6: continuidad de roster")
    ap.add_argument("--freeze-audit", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    store = dexp2.make_store()
    features, games, pgs = _load(store)
    print(f"features {features.shape} | games {games.shape} | pgs {pgs.shape}", flush=True)

    if args.freeze_audit:
        destino = OUT_DIR / "audit_sample.json"
        if destino.exists():
            print(f"YA CONGELADA: {destino}")
            return
        meta = games.set_index("game_id")[["home_team_id", "away_team_id"]]
        valid = {v for _, _, v in FOLDS}
        gids = features.loc[features["season"].isin(valid), "game_id"]
        pares = [(g, int(meta.at[g, side])) for g in gids
                 for side in ("home_team_id", "away_team_id")]
        muestra = freeze_audit_sample(pares)
        destino.write_text(json.dumps(muestra, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Muestra congelada: {destino} ({muestra['n_universo']} pares candidatos)")
        return

    seqs = build_sequences(pgs, games)
    anomalos = sum(1 for s in seqs.values() for g in s if len(g.starters) != QUINTETO)
    print(f"{len(seqs)} equipos | quintetas != 5: {anomalos}", flush=True)
    df = _assemble(features, games, seqs)
    print(f"Universo: {len(df)} filas, paridad con features_v1 OK", flush=True)

    cols = {"CTL": list(OFFICIAL_LOGISTIC_COLS),
            "c_a_diff": list(OFFICIAL_LOGISTIC_COLS) + ["c_a_diff"],
            "c_b_diff": list(OFFICIAL_LOGISTIC_COLS) + ["c_b_diff"]}

    # --- FASE 0: MDE desde el PRIMER fold, extrapolado al n total ------------
    n_por_fold = {v: int((df["season"] == v).sum()) for _, _, v in FOLDS}
    n_total = sum(n_por_fold.values())
    f0, train0, valid0 = FOLDS[0]
    ctl0 = dexp2.fold_metrics(df, cols["CTL"], train0, valid0)
    fase0: dict[str, Any] = {
        "computado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fold_base": f0, "valid": valid0, "n_valid_por_fold": n_por_fold,
        "n_total": n_total, "n_total_pre_registrado": "~7000",
        "techo": MDE_TECHO, "expectativa": [0.0015, 0.0025], "candidatos": {}}
    for c in CANDIDATOS:
        m = dexp2.fold_metrics(df, cols[c], train0, valid0)
        assert m["n_valid"] == ctl0["n_valid"], "paridad rota en fase 0"
        p = dexp2.paired_diff(ctl0["_y"], ctl0["_p"], m["_p"])
        mde = mde_pareado(p["sd"], n_total)
        fase0["candidatos"][c] = {"sd": p["sd"], "n_fold_base": p["n"],
                                  "mde_fold_base": round(mde_pareado(p["sd"], p["n"]), 6),
                                  "mde_agregado": round(mde, 6),
                                  "veredicto": resolution_verdict(mde)}
    (OUT_DIR / "mde_fase0.json").write_text(
        json.dumps(fase0, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n=== FASE 0 ===\n" + json.dumps(fase0, indent=2, ensure_ascii=False), flush=True)

    con_resolucion = [c for c in CANDIDATOS
                      if fase0["candidatos"][c]["veredicto"] == "CUBRE"]
    if not con_resolucion:
        (OUT_DIR / "results.json").write_text(json.dumps(
            {"veredicto": "SIN_RESOLUCION", "fase0": fase0}, indent=2), encoding="utf-8")
        print("GATE: ningun candidato cubre el techo. SIN RESOLUCION, se detiene.")
        return

    # --- EJECUCION -------------------------------------------------------
    res: dict[str, Any] = {"fase0": fase0, "folds": {}, "agregado": {}, "puerta": {},
                           "candidatos_con_resolucion": con_resolucion,
                           "quintetas_anomalas": anomalos}
    acum: dict[str, list] = {"y": [], "CTL": [], **{c: [] for c in CANDIDATOS}}
    por_fold_pareado: dict[str, list[dict]] = {c: [] for c in CANDIDATOS}
    for nombre, train, valid in FOLDS:
        ctl = dexp2.fold_metrics(df, cols["CTL"], train, valid)
        fila: dict[str, Any] = {"train": [train[0], train[-1]], "valid": valid,
                                "n_train": ctl["n_train"], "n_valid": ctl["n_valid"],
                                "CTL": {k: ctl[k] for k in ("log_loss", "brier", "accuracy")}}
        acum["y"] += list(ctl["_y"])
        acum["CTL"] += list(ctl["_p"])
        for c in CANDIDATOS:
            m = dexp2.fold_metrics(df, cols[c], train, valid)
            assert m["n_valid"] == ctl["n_valid"], f"paridad rota en {nombre}/{c}"
            p = dexp2.paired_diff(ctl["_y"], ctl["_p"], m["_p"])
            por_fold_pareado[c].append(p)
            acum[c] += list(m["_p"])
            fila[c] = {"log_loss": m["log_loss"], "brier": m["brier"],
                       "accuracy": m["accuracy"], "pareado_vs_ctl": p,
                       "coef": dict(m["coefs"])[c]}
        res["folds"][nombre] = fila
        print(f"  {nombre} {valid} n={ctl['n_valid']}  " + "  ".join(
            f"{c}: {fila[c]['pareado_vs_ctl']['media']:+.6f} (t={fila[c]['pareado_vs_ctl']['t']})"
            for c in CANDIDATOS), flush=True)

    for c in CANDIDATOS:
        agg = dexp2.paired_diff(acum["y"], acum["CTL"], acum[c])
        res["agregado"][c] = agg
        res["puerta"][c] = gate_verdict(agg, por_fold_pareado[c])
        if c not in con_resolucion:
            res["puerta"][c]["salida"] = "DESCRIPTIVO: su fase 0 no cubrio el techo"
    res["distribucion"] = {k: describe(df[k]) for k in
                           ("c_a_home", "c_b_home", "c_a_diff", "c_b_diff")}
    res["correlaciones"] = {
        "c_a_diff~c_b_diff": round(float(df["c_a_diff"].corr(df["c_b_diff"])), 4),
        "c_a_diff~availability_diff": round(float(df["c_a_diff"].corr(df["availability_diff"])), 4),
        "c_b_diff~availability_diff": round(float(df["c_b_diff"].corr(df["availability_diff"])), 4),
    }

    # listado de auditoria sobre la muestra CONGELADA
    audit_file = OUT_DIR / "audit_sample.json"
    if audit_file.exists():
        nombres = store.load_player_names()
        tri = {int(r["team_id"]): str(r["abbreviation"])
               for _, r in store.load_teams().iterrows()}
        _write_audit(json.loads(audit_file.read_text(encoding="utf-8")),
                     seqs, nombres, tri)

    (OUT_DIR / "results.json").write_text(
        json.dumps(res, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("\n" + json.dumps({c: {"agregado": {k: res["agregado"][c][k]
                                                 for k in ("media", "t", "ic95")},
                                    "puerta": res["puerta"][c]} for c in CANDIDATOS},
                            indent=2, ensure_ascii=False))
    print(f"\nEscrito en {OUT_DIR}/")


def _write_audit(muestra: dict, seqs, nombres: dict[int, str], tri: dict[int, str]) -> None:
    """Para cada equipo-partido: las quintetas de los ultimos 5 (y su predecesor),
    el solape de cada uno, C_a, los presentes del ultimo partido y C_b. En NOMBRES,
    no ids: los ids esconden lo que los nombres delatan (D-PROD-1e+1f)."""
    out = [f"AUDITORIA D-EXP-6 — muestra congelada seed {muestra['seed']} "
           f"({muestra['congelado_utc']}), {len(muestra['pares'])} equipo-partido",
           "Cotejar quintetas y minutos contra los boxscores de los partidos ANTERIORES.",
           "=" * 78]
    nm = lambda p: nombres.get(p, f"#{p}")  # noqa: E731
    for par in muestra["pares"]:
        gid, tid = par["game_id"], int(par["team_id"])
        seq = seqs[tid]
        k = next(i for i, g in enumerate(seq) if g.game_id == gid)
        out.append(f"\n{tri.get(tid, tid)}  partido {gid} ({seq[k].game_date})  "
                   f"C_a={continuity_a(seq, k):.3f}  C_b={continuity_b(seq, k):.4f}")
        for j in range(k - WINDOW_A, k):
            sol = len(seq[j].starters & seq[j - 1].starters)
            out.append(f"   {seq[j].game_date} {seq[j].game_id}  solape {sol}/5  "
                       + ", ".join(sorted(nm(p) for p in seq[j].starters)))
        pres = sorted(nm(p) for p in seq[k - 1].played)
        out.append(f"   presentes en el partido anterior ({len(pres)}): {', '.join(pres)}")
    (OUT_DIR / "auditoria_muestra.txt").write_text("\n".join(out), encoding="utf-8")
    print(f"Listado de auditoria: {OUT_DIR / 'auditoria_muestra.txt'}")


if __name__ == "__main__":
    main()
