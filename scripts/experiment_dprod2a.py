"""D-PROD-2a — verificación histórica de la línea .5 de D-PROD-2.

PREGUNTA (pre-registro sellado en CLAUDE.md, 2026-10-29T02:30:58Z): ¿la línea
.5 (línea = ceil(mediana de 10) - 0.5, graduada como real >= ceil(mediana))
acierta el 50% en la población histórica de destacados?

Medición NUEVA, no segunda corrida de D-EXP-12: su results.json no se toca y
sus puertas no se re-evalúan. Población = la puerta de D-EXP-12, reconstruida
con construir() de d12, que solo se IMPORTA (igual que d12 importa d7 y d9).
El control de n se verifica ANTES de medir: si no reproduce los conteos
sellados, el script se detiene sin medir.

Solo lectura: BigQuery con CloudDataStore EXPLÍCITO (vía d7.load_frame) y los
artefactos locales que ya usa d12. Escribe únicamente en
data/experiment_dprod2a/. Una sola corrida: aborta si results.json existe.

Uso:
  python scripts/experiment_dprod2a.py
"""
from __future__ import annotations

import importlib.util
import json
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


d12 = _cargar("_d12_para_dprod2a", "experiment_destacados_d12.py")
d7 = d12.d7

OUT_DIR = Path("data/experiment_dprod2a")
D12_RESULTS = Path("data/experiment_destacados_d12/results.json")
STATS = d12.STATS
VALID = d12.VALID

# Control de instrumento sellado: la puerta de D-EXP-12, exacta.
N_ESPERADO = 20505
N_POR_FOLD_ESPERADO = {"2020-21": 2921, "2021-22": 3483, "2022-23": 3552,
                       "2023-24": 3564, "2024-25": 3448, "2025-26": 3537}
BANDA_AGG = (0.48, 0.52)
BANDA_FOLD = (0.45, 0.55)


def linea_publicada(mediana: Any) -> np.ndarray:
    """Línea .5 de D-PROD-2: ceil(mediana) - 0.5 (= entero publicado hoy - 0.5)."""
    return np.ceil(np.asarray(mediana, float)) - 0.5


def acierta(real: Any, mediana: Any) -> np.ndarray:
    """Evento graduado: real >= ceil(mediana), es decir real > línea .5."""
    return np.asarray(real, float) >= np.ceil(np.asarray(mediana, float))


def puerta(acierto_agg: float, acierto_folds: list[float]) -> bool:
    """Banda de dos lados: agregado en [48%, 52%] Y cada fold en [45%, 55%]."""
    return (BANDA_AGG[0] <= acierto_agg <= BANDA_AGG[1]
            and all(BANDA_FOLD[0] <= a <= BANDA_FOLD[1] for a in acierto_folds))


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    marca = OUT_DIR / "results.json"
    if marca.exists():
        sys.exit("results.json ya existe: el pre-registro permite UNA corrida.")
    t0 = _ts()
    df, games, _, _ = d7.load_frame()
    u, _ = d12.construir(df, games)

    v_all = u[u["season"].isin(VALID)]
    p = v_all[v_all["primaria"] & ~v_all["arranque"] & ~v_all["nys"]]

    # ── Control de instrumento, ANTES de medir ──
    n_fold = {s: int((p["season"] == s).sum()) for s in VALID}
    print("n puerta", len(p), n_fold, flush=True)
    if len(p) != N_ESPERADO or n_fold != N_POR_FOLD_ESPERADO:
        sys.exit(f"CONTROL FALLIDO: n={len(p)} {n_fold} != {N_ESPERADO} "
                 f"{N_POR_FOLD_ESPERADO}. Se detiene sin medir.")
    for s in STATS:
        assert p[f"B0_{s}"].notna().all(), f"B0 NaN en {s}: ventana incompleta"

    d12r = json.loads(D12_RESULTS.read_text(encoding="utf-8"))
    out: dict[str, Any] = {"inicio_utc": t0, "pre_registro": "2026-10-29T02:30:58Z",
                           "n": len(p), "n_por_fold": n_fold, "por_stat": {}}
    for s in STATS:
        hit = acierta(p[s], p[f"B0_{s}"])
        agg = float(hit.mean())
        folds = {se: float(hit[(p["season"] == se).to_numpy()].mean()) for se in VALID}
        u50_ref = d12r["por_stat"][s]["U50"]
        rec: dict[str, Any] = {
            "acierto": agg, "acierto_por_fold": folds,
            "cruza": puerta(agg, list(folds.values())),
            "u50_d12": {"acierto": u50_ref["puerta_poblacion"]["acierto"],
                        "acierto_por_fold": u50_ref["acierto_por_fold"]},
        }
        # Umbral U50 con los q SELLADOS por fold de D-EXP-12 (sin recalibrar).
        u50 = pd.Series(np.nan, index=p.index)
        for se in VALID:
            q = u50_ref["q_por_fold"][se]
            m = p["season"] == se
            u50[m] = d7.floor_values(p.loc[m, f"B0_{s}"], p.loc[m, f"disp_{s}"],
                                     q["q_escalado"], q["q_abs"])
        hit_u50 = p[s].to_numpy(float) >= u50.to_numpy()
        difiere = np.ceil(p[f"B0_{s}"].to_numpy(float)) != u50.to_numpy()
        rec["u50_recalculado_acierto"] = float(hit_u50.mean())
        rec["filas_linea_distinta_de_u50"] = {
            "n": int(difiere.sum()),
            "acierto_linea": float(hit[difiere].mean()) if difiere.any() else None,
            "acierto_u50": float(hit_u50[difiere].mean()) if difiere.any() else None}
        entera = (p[f"B0_{s}"] % 1 == 0).to_numpy()
        rec["por_mediana"] = {
            "entera": {"n": int(entera.sum()), "acierto": float(hit[entera].mean())},
            "x5": {"n": int((~entera).sum()),
                   "acierto": float(hit[~entera].mean()) if (~entera).any() else None}}
        out["por_stat"][s] = rec
    out["veredicto_pts"] = "CRUZA" if out["por_stat"]["pts"]["cruza"] else "NO CRUZA"
    out["fin_utc"] = _ts()
    marca.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    lineas = [f"D-PROD-2a — línea .5 (real >= ceil(mediana)), n = {len(p)}",
              "stat  acierto  por fold (" + " ".join(VALID) + ")  | U50 d12  | puerta"]
    for s in STATS:
        r = out["por_stat"][s]
        fol = " ".join(f"{100 * a:.1f}" for a in r["acierto_por_fold"].values())
        lineas.append(f"{s:<5} {100 * r['acierto']:.2f}%  {fol}  | "
                      f"{100 * r['u50_d12']['acierto']:.2f}%  | "
                      f"{'cruza' if r['cruza'] else 'no cruza'}")
    for s in STATS:
        r = out["por_stat"][s]
        lineas.append(f"{s}: mediana entera n={r['por_mediana']['entera']['n']} "
                      f"acierto={r['por_mediana']['entera']['acierto']} | x.5 "
                      f"n={r['por_mediana']['x5']['n']} acierto={r['por_mediana']['x5']['acierto']}"
                      f" | filas línea != U50: {r['filas_linea_distinta_de_u50']}")
    lineas.append(f"veredicto pts: {out['veredicto_pts']}")
    (OUT_DIR / "tabla.txt").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\n".join(lineas), flush=True)


if __name__ == "__main__":
    main()
