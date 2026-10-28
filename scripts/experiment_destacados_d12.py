"""D-EXP-12 — umbrales binarios con nivel declarado ("X+ (80%)" y línea 50/50).

PREGUNTA (pre-registro sellado en CLAUDE.md, 2026-10-28T21:38:50Z): ¿puede el
mensaje 2 publicar "20+ pts (80%)" y "28+ pts (50%)" con un acierto real igual
al nivel declarado?

  U80 / U50 / U70: umbral = B0 - q * dispersión_10 (respaldo absoluto con
  dispersión 0), redondeado hacia arriba y acotado en 0. q = cuantil del
  residuo con signo (centro - real) en el TRAIN DE LA PRIMARIA del fold.
  U70 es descriptivo, no elegible.

POR QUÉ UN SCRIPT APARTE Y NO UNA ETAPA DE D-EXP-7: la etapa "pisos" de d7 es
de un experimento cerrado, con una sola corrida y la calibración fijada sobre
el universo; añadirle un parámetro sería modificarla. Aquí solo se IMPORTAN
de d7 las funciones puras de D-EXP-11 (conformal_q_lower, floor_values,
floor_coverage, informativeness) y las de soporte (load_frame,
reconstruct_selector, add_center_arms, range_series, season_start_flags), y de
D-EXP-9 filtrar_selector. Lo nuevo vive aquí: la puerta de dos lados, la
calibración sobre la primaria y el cargador de estatus GemBox + legacy.

Solo lectura: BigQuery con CloudDataStore EXPLÍCITO (vía d7.load_frame) y los
artefactos locales de D-EXP-1, D-EXP-5 y D-EXP-2b. Escribe únicamente en
data/experiment_destacados_d12/. Una sola corrida: aborta si results.json
existe.

Uso:
  python scripts/experiment_destacados_d12.py
"""
from __future__ import annotations

import importlib.util
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_SCRIPTS = Path(__file__).resolve().parent


def _cargar(nombre: str, archivo: str) -> Any:
    spec = importlib.util.spec_from_file_location(nombre, _SCRIPTS / archivo)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = mod
    spec.loader.exec_module(mod)
    return mod


d7 = _cargar("_d7_para_d12", "experiment_destacados_d7.py")
d9 = _cargar("_d9_para_d12", "experiment_destacados_d9.py")

OUT_DIR = Path("data/experiment_destacados_d12")
INSTANCIAS_GEM = Path("data/experiment_pjuega/instancias.json")
INSTANCIAS_LEG = Path("data/experiment_pjuega_legacy/instancias.json")
NYS_PARQUET = Path("data/experiment_v2b/nys.parquet")
RESOLVED_V2B = Path("data/experiment_v2b/resolved.parquet")

STATS: tuple[str, ...] = d7.STATS
VALID: tuple[str, ...] = d7.VALID_SEASONS
NIVELES: dict[str, float] = {"U80": 0.80, "U50": 0.50, "U70": 0.70}
# Bandas de DOS lados: (agregada, por fold), inclusivas.
BANDAS: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {
    "U80": ((0.78, 0.82), (0.75, 0.85)),
    "U50": ((0.48, 0.52), (0.45, 0.55)),
}
INFORMATIVIDAD_MIN = 0.70          # solo U80
UMBRAL_CERO_MAX = 0.30             # más de esto con umbral 0 = "sin umbral útil"
DESCRIPTIVOS = frozenset({"U70"})  # no elegibles
SOLO_PUNTOS = frozenset({"U50"})   # la línea 50/50 solo se publica en puntos
SUBGRUPO_DESVIO = 0.05
AUDIT_SEED = 42
AUDIT_N = 30


# ---------------------------------------------------------------------------
# Funciones PURAS — sin red, unit-testeables
# ---------------------------------------------------------------------------

def unir_estatus(*fuentes: pd.DataFrame) -> pd.DataFrame:
    """Une instancias publish de varias fuentes en una fila por (game_id,
    player_id). Duplicados IDÉNTICOS en estatus se colapsan; dos estatus
    distintos para el mismo par es un error ruidoso, no una elección
    silenciosa de cuál creer."""
    cols = ["game_id", "team_id", "player_id", "status", "date", "suffix", "season"]
    u = pd.concat([f[cols] for f in fuentes], ignore_index=True)
    u["player_id"] = u["player_id"].astype(int)
    u["team_id"] = u["team_id"].astype(int)
    u = u.drop_duplicates(["game_id", "player_id", "status"])
    dup = u[u.duplicated(["game_id", "player_id"], keep=False)]
    if len(dup):
        ejemplo = dup.sort_values(["game_id", "player_id"]).head(4).to_dict("records")
        raise ValueError(f"estatus en conflicto para el mismo (game_id, player_id): {ejemplo}")
    return u.reset_index(drop=True)


def calibrar_q(y: Any, centro: Any, disp: Any, nivel: float) -> tuple[float, float]:
    """(q escalado, q absoluto) al `nivel`, con conformal_q_lower de D-EXP-11.
    Quien llama decide la POBLACIÓN pasando solo sus filas: aquí, la primaria
    sin arranque del train del fold."""
    return (d7.conformal_q_lower(y, centro, disp, nivel),
            d7.conformal_q_lower(y, centro, None, nivel))


def en_banda(x: float, banda: tuple[float, float]) -> bool:
    return bool(banda[0] <= x <= banda[1])


def puerta_par(nivel: str, stat: str, acierto_agg: float, acierto_folds: list[float],
               informatividad: float | None, frac_cero: float) -> dict[str, Any]:
    """Puerta de un par (stat, nivel), sin lectura alternativa.

    calibra: agregada y TODOS los folds dentro de su banda de dos lados.
    informa: solo U80 (>= 0.70); para los demás niveles no aplica (None).
    sin_umbral_util: umbral 0 en más del 30% (los dos niveles).
    publicable: cruza todo, el nivel no es descriptivo y la línea 50/50 solo
    en puntos."""
    if nivel in DESCRIPTIVOS:
        return {"calibra": None, "informa": None, "sin_umbral_util": None,
                "cruza": None, "publicable": False, "descriptivo": True}
    b_agg, b_fold = BANDAS[nivel]
    calibra = en_banda(acierto_agg, b_agg) and all(en_banda(a, b_fold) for a in acierto_folds)
    informa = (None if nivel != "U80"
               else bool(informatividad is not None and informatividad >= INFORMATIVIDAD_MIN))
    sin_util = bool(frac_cero > UMBRAL_CERO_MAX)
    cruza = bool(calibra and informa is not False and not sin_util)
    publicable = bool(cruza and (nivel not in SOLO_PUNTOS or stat == "pts"))
    return {"calibra": bool(calibra), "informa": informa, "sin_umbral_util": sin_util,
            "cruza": cruza, "publicable": publicable, "descriptivo": False}


def escalonada(p_u80_pts: dict[str, Any]) -> str:
    """Consecuencia escalonada pre-registrada, decidida por U80 en puntos:
    (a) calibra e informa; (b) calibra y no informa; (c) no calibra."""
    if not p_u80_pts["calibra"]:
        return "c"
    if p_u80_pts["informa"] and not p_u80_pts["sin_umbral_util"]:
        return "a"
    return "b"


def acierto(y: Any, umbral: Any) -> float:
    return d7.floor_coverage(y, umbral)


# ---------------------------------------------------------------------------
# Carga (solo lectura)
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _publish(path: Path) -> pd.DataFrame:
    d = pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))
    return d[d["cut"] == "publish"].copy()


def cargar_estatus_ampliado() -> tuple[pd.DataFrame, set[tuple[str, int]], set[str],
                                       dict[str, Any]]:
    """El cargador de D-EXP-9 extendido al corpus legacy: instancias publish
    incluidas de D-EXP-1 (GemBox) y D-EXP-5 (legacy), NYS de D-EXP-2b, con assert
    de que ambos corpus usan el mismo sufijo publish que D-EXP-2b por fecha."""
    gem, leg = _publish(INSTANCIAS_GEM), _publish(INSTANCIAS_LEG)
    ok = unir_estatus(gem[gem["veredicto"] == "incluida"], leg[leg["veredicto"] == "incluida"])

    nys = pd.read_parquet(NYS_PARQUET)
    nys_pares = {(d, int(t)) for d, t in zip(nys["date"], nys["team_id"])}

    suf_v2b = pd.read_parquet(RESOLVED_V2B).groupby("date")["suffix"].first().to_dict()
    info: dict[str, Any] = {}
    for nombre, pub in (("gembox", gem), ("legacy", leg)):
        suf = pub.groupby("date")["suffix"].first().to_dict()
        comunes = sorted(set(suf) & set(suf_v2b))
        distintos = [f for f in comunes if suf[f] != suf_v2b[f]]
        assert not distintos, f"sufijo publish distinto ({nombre}) vs D-EXP-2b: {distintos[:5]}"
        info[nombre] = {"instancias_publish": int(len(pub)),
                        "incluidas": int((pub["veredicto"] == "incluida").sum()),
                        "fechas": int(pub["date"].nunique()),
                        "fechas_sufijo_comparado": len(comunes)}
    fechas_pdf = set(gem["date"]) | set(leg["date"]) | {d for d, _ in nys_pares}
    info.update({"estatus_unidos": int(len(ok)), "nys_pares": len(nys_pares),
                 "fechas_con_pdf": len(fechas_pdf),
                 "primera_fecha_pdf": min(fechas_pdf), "ultima_fecha_pdf": max(fechas_pdf)})
    return ok, nys_pares, fechas_pdf, info


def construir(df: pd.DataFrame, games: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    ok, nys_pares, fechas_pdf, info = cargar_estatus_ampliado()
    estatus = {(g, int(p)): s for g, p, s in zip(ok["game_id"], ok["player_id"], ok["status"])}

    print("Selector con ranking completo (top alto)...", flush=True)
    sel_all = d7.reconstruct_selector(df, games, top=10**6)
    sel_prod = d9.filtrar_selector(sel_all, estatus)
    sel_d7 = sel_all[sel_all["sel_rank"] <= d7.SEL_TOP]

    played = df[df["minutes"] > 0].copy()
    arms = d7.add_center_arms(played)
    cols = [f"B0_{s}" for s in STATS] + [f"C1_{s}" for s in STATS]
    u = arms.dropna(subset=cols).copy()
    u = u.merge(d7.range_series(played), on=["game_id", "player_id"], how="left",
                validate="one_to_one")
    for s in STATS:
        assert u[f"disp_{s}"].notna().all(), f"dispersión NaN en {s}"
    assert "is_home" in u, "falta is_home para el control local/visitante"

    rank = {(g, int(p)): int(r) for g, p, r in
            zip(sel_prod["game_id"], sel_prod["player_id"], sel_prod["rank_prod"])}
    keys_d7 = set(zip(sel_d7["game_id"], sel_d7["player_id"].astype(int)))
    u["rank_prod"] = [rank.get((g, int(p))) for g, p in zip(u["game_id"], u["player_id"])]
    u["primaria"] = u["rank_prod"].notna()
    u["seleccionado_d7"] = [(g, int(p)) in keys_d7 for g, p in zip(u["game_id"], u["player_id"])]
    u["arranque"] = d7.season_start_flags(u, games).to_numpy()
    u["titular"] = u["roll_min"] >= d7.CORTE_TITULAR_MIN
    u["local"] = u["is_home"].astype(bool)
    gm = games.set_index("game_id")[["home_team_id", "away_team_id"]]
    h = u["game_id"].map(gm["home_team_id"]).astype(int)
    a = u["game_id"].map(gm["away_team_id"]).astype(int)
    u["opp_team_id"] = np.where(u["team_id"].astype(int) == h, a, h)
    u["fecha"] = pd.to_datetime(u["game_date"]).dt.strftime("%Y-%m-%d")
    u["con_pdf"] = u["fecha"].isin(fechas_pdf)
    u["nys"] = [((f, int(t)) in nys_pares) or ((f, int(o)) in nys_pares)
                for f, t, o in zip(u["fecha"], u["team_id"], u["opp_team_id"])]
    info["selector_d7_top2_con_estatus"] = {
        str(k): int(v) for k, v in pd.Series(
            [estatus.get((g, int(p))) for g, p in zip(sel_d7["game_id"], sel_d7["player_id"])]
        ).value_counts(dropna=False).items()}
    return u, info


# ---------------------------------------------------------------------------
# Corrida única
# ---------------------------------------------------------------------------

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    marca = OUT_DIR / "results.json"
    if marca.exists():
        sys.exit("results.json ya existe: el pre-registro permite UNA corrida.")
    t0 = _ts()
    df, games, _, names = d7.load_frame()
    u, info = construir(df, games)

    base = u[u["primaria"] & ~u["arranque"] & ~u["nys"]]
    v_all = u[u["season"].isin(VALID)]
    puerta = v_all["primaria"] & ~v_all["arranque"] & ~v_all["nys"]
    arranque_prim = v_all["primaria"] & v_all["arranque"] & ~v_all["nys"]
    pobl_sec = {"titulares_sin_arranque": v_all["titular"] & ~v_all["arranque"],
                "universo_sin_arranque": ~v_all["arranque"]}
    subgrupos = {"titulares": puerta & v_all["titular"], "banca": puerta & ~v_all["titular"],
                 "local": puerta & v_all["local"], "visitante": puerta & ~v_all["local"],
                 "arranque": arranque_prim}

    # Umbrales por fold, calibrados en la primaria sin arranque del train.
    q_por: dict[str, dict[str, dict[str, Any]]] = {}
    umbrales: dict[tuple[str, str], pd.Series] = {}
    for s in STATS:
        q_por[s] = {}
        for nivel, p in NIVELES.items():
            serie = pd.Series(np.nan, index=v_all.index)
            q_por[s][nivel] = {}
            for season in VALID:
                tr = base[base["season"] < season]
                q_sc, q_ab = calibrar_q(tr[s], tr[f"B0_{s}"], tr[f"disp_{s}"], p)
                m = v_all["season"] == season
                serie[m] = d7.floor_values(v_all.loc[m, f"B0_{s}"],
                                           v_all.loc[m, f"disp_{s}"], q_sc, q_ab)
                q_por[s][nivel][season] = {"q_escalado": round(q_sc, 6), "q_abs": round(q_ab, 6),
                                           "n_train": int(len(tr))}
            assert serie.notna().all(), f"umbral NaN en {s} {nivel}"
            umbrales[(s, nivel)] = serie

    # ── FASE 0 (antes de cualquier métrica) ──
    n_p = int(puerta.sum())
    excl_nys = v_all["primaria"] & ~v_all["arranque"] & v_all["nys"]
    fase0: dict[str, Any] = {
        "sellado_utc": _ts(), "pre_registro": "2026-10-28T21:38:50Z",
        "n_puerta": n_p,
        "n_por_fold": {s: int((puerta & (v_all["season"] == s)).sum()) for s in VALID},
        "n_train_por_fold": {s: int((base["season"] < s).sum()) for s in VALID},
        "sin_filtro_por_falta_de_pdf": {
            "puerta": int((puerta & ~v_all["con_pdf"]).sum()),
            "train_por_fold": {s: int(((base["season"] < s) & ~base["con_pdf"]).sum())
                               for s in VALID}},
        "excluidas_nys_validacion": int(excl_nys.sum()),
        "excluidas_nys_total_primaria_sin_arranque": int(
            (u["primaria"] & ~u["arranque"] & u["nys"]).sum()),
        "control_d7_sin_filtro": {
            "primaria": int(v_all["seleccionado_d7"].sum()),
            "puerta": int((v_all["seleccionado_d7"] & ~v_all["arranque"]).sum()),
            "esperado_d7": {"primaria": 22745, "puerta": 19793}},
        "info_estatus": info,
        "nota": "error estándar binomial = COTA OPTIMISTA: las filas de un mismo "
                "jugador no son independientes",
        "niveles": {}}
    for nivel, p in NIVELES.items():
        se = float(np.sqrt(p * (1 - p) / n_p))
        fase0["niveles"][nivel] = {
            "se_agregada_pp": round(100 * se, 3),
            "se_por_fold_pp": {s: round(100 * float(np.sqrt(p * (1 - p) / max(n, 1))), 3)
                               for s, n in fase0["n_por_fold"].items()}}
    (OUT_DIR / "fase0.json").write_text(json.dumps(fase0, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print(json.dumps({k: fase0[k] for k in ("n_puerta", "n_por_fold", "control_d7_sin_filtro",
                                            "sin_filtro_por_falta_de_pdf",
                                            "excluidas_nys_validacion")}, indent=1), flush=True)

    # ── Muestra de auditoría (antes de cualquier métrica) ──
    idx = sorted(v_all.index[puerta])
    elegidas = random.Random(AUDIT_SEED).sample(idx, AUDIT_N)
    muestra = []
    for i in elegidas:
        r = v_all.loc[i]
        fila = {"fila": int(i), "fecha": r["fecha"], "game_id": r["game_id"],
                "player_id": int(r["player_id"]),
                "jugador": names.get(int(r["player_id"]), f"#{int(r['player_id'])}"),
                "team_id": int(r["team_id"]), "rank": int(r["rank_prod"]),
                "con_pdf": bool(r["con_pdf"]), "season": r["season"]}
        for s in STATS:
            fila[s] = {"centro_B0": float(r[f"B0_{s}"]), "disp": float(r[f"disp_{s}"]),
                       "U80": float(umbrales[(s, "U80")][i]),
                       "U50": float(umbrales[(s, "U50")][i]), "real": float(r[s])}
        muestra.append(fila)
    (OUT_DIR / "audit_sample.json").write_text(json.dumps(
        {"sellado_utc": _ts(), "seed": AUDIT_SEED, "filas": muestra}, ensure_ascii=False,
        indent=1), encoding="utf-8")
    (OUT_DIR / "auditoria_muestra.txt").write_text(render_auditoria(muestra), encoding="utf-8")
    print(f"Muestra de auditoría congelada: {len(muestra)} filas", flush=True)

    # ── Métricas ──
    def resumen(mask: pd.Series, s: str, nivel: str) -> dict[str, Any]:
        y, c, f = v_all.loc[mask, s], v_all.loc[mask, f"B0_{s}"], umbrales[(s, nivel)][mask]
        return {"n": int(mask.sum()), "acierto": acierto(y, f),
                "umbral_medio": float(f.mean()), "centro_medio": float(c.mean()),
                "informatividad": d7.informativeness(f, c),
                "frac_umbral_cero": float((f == 0).mean())}

    res: dict[str, Any] = {"inicio_utc": t0, "pre_registro": "2026-10-28T21:38:50Z",
                           "centro": "B0", "por_stat": {}}
    for s in STATS:
        res["por_stat"][s] = {}
        for nivel in NIVELES:
            g = resumen(puerta, s, nivel)
            folds = {season: resumen(puerta & (v_all["season"] == season), s, nivel)["acierto"]
                     for season in VALID}
            gate = puerta_par(nivel, s, g["acierto"], list(folds.values()),
                              g["informatividad"], g["frac_umbral_cero"])
            sub = {}
            for k, m in subgrupos.items():
                r = resumen(m, s, nivel)
                r["desvio_pp"] = round(100 * (r["acierto"] - g["acierto"]), 3)
                r["hallazgo"] = bool(abs(r["acierto"] - g["acierto"]) > SUBGRUPO_DESVIO)
                sub[k] = r
            res["por_stat"][s][nivel] = {
                "q_por_fold": q_por[s][nivel], "puerta_poblacion": g,
                "acierto_por_fold": folds, "puerta": gate, "subgrupos": sub,
                "secundarias": {k: resumen(m, s, nivel) for k, m in pobl_sec.items()}}
    res["escalonada_pts"] = escalonada(res["por_stat"]["pts"]["U80"]["puerta"])
    res["fin_utc"] = _ts()
    marca.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    texto = render(res, fase0)
    (OUT_DIR / "tabla.txt").write_text(texto, encoding="utf-8")
    print(texto)


def render_auditoria(muestra: list[dict]) -> str:
    lin = ["D-EXP-12 — muestra de auditoría (30 filas de la población de la puerta, seed 42),",
           "congelada ANTES de cualquier métrica. Umbral publicado = B0 - q * dispersión,",
           "hacia arriba y acotado en 0; acierto = real >= umbral.", ""]
    for n, f in enumerate(muestra, start=1):
        lin.append(f"{n:>2}. {f['fecha']} {f['game_id']}  {f['jugador']} (team {f['team_id']},"
                   f" rank {f['rank']})  {f['season']}  con_pdf={f['con_pdf']}")
        for s in STATS:
            x = f[s]
            lin.append(f"      {s:<5} centro {x['centro_B0']:5.1f}  disp {x['disp']:5.2f}"
                       f"  U80 {x['U80']:>4.0f}+  U50 {x['U50']:>4.0f}+  real {x['real']:>4.0f}"
                       f"  U80 {'sí' if x['real'] >= x['U80'] else 'no'}"
                       f"  U50 {'sí' if x['real'] >= x['U50'] else 'no'}")
        lin.append("")
    return "\n".join(lin)


def render(res: dict[str, Any], fase0: dict[str, Any]) -> str:
    L = ["D-EXP-12 — umbrales binarios (centro B0; puerta = primaria sin arranque, filtro de "
         "estatus donde hay PDF, sin NYS)",
         f"n puerta {fase0['n_puerta']}  por fold {fase0['n_por_fold']}", ""]
    for s in STATS:
        L.append(f"[{s}]")
        for nivel in NIVELES:
            r = res["por_stat"][s][nivel]
            g, p = r["puerta_poblacion"], r["puerta"]
            folds = " ".join(f"{100 * a:.1f}" for a in r["acierto_por_fold"].values())
            inf = g["informatividad"]
            estado = ("descriptivo" if p["descriptivo"] else
                      f"calibra={p['calibra']} informa={p['informa']} "
                      f"sin_util={p['sin_umbral_util']} cruza={p['cruza']} "
                      f"publicable={p['publicable']}")
            L.append(f"  {nivel}  acierto {100 * g['acierto']:5.1f}%  folds {folds}  umbral "
                     f"{g['umbral_medio']:5.2f} centro {g['centro_medio']:5.2f} inform "
                     f"{inf if inf is None else round(inf, 3)}  umbral0 "
                     f"{100 * g['frac_umbral_cero']:.1f}%  | {estado}")
            hall = [f"{k} {v['desvio_pp']:+.1f}pp (n={v['n']})"
                    for k, v in r["subgrupos"].items() if v["hallazgo"]]
            L.append("        subgrupos: " + ", ".join(
                f"{k} {100 * v['acierto']:.1f}" for k, v in r["subgrupos"].items())
                + (f"  | HALLAZGO: {'; '.join(hall)}" if hall else ""))
            L.append("        secundarias: " + ", ".join(
                f"{k} {100 * v['acierto']:.1f}% inf {v['informatividad']:.3f}"
                for k, v in r["secundarias"].items()))
        L.append("")
    L.append(f"ESCALONADA (U80 en pts): ({res['escalonada_pts']})")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
