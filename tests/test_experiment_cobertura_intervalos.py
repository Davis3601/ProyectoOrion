"""Tests de las funciones puras del experimento D-EXP-4.

El script es borrable sin residuo; estos tests cubren SOLO su logica pura
(rollings de intervalo anti-leakage, cobertura con IC, corte titular/banca,
agregacion por subgrupos). No tocan red, GCS ni BigQuery.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "experiment_cobertura_intervalos",
    Path(__file__).resolve().parents[1] / "scripts" / "experiment_cobertura_intervalos.py",
)
exp = importlib.util.module_from_spec(_SPEC)
sys.modules["experiment_cobertura_intervalos"] = exp
_SPEC.loader.exec_module(exp)


def _serie(pts: list[float], minutos: float = 20.0) -> pd.DataFrame:
    n = len(pts)
    return pd.DataFrame({
        "player_id": [7] * n,
        "game_date": [f"2023-11-{d:02d}" for d in range(1, n + 1)],
        "minutes": [minutos] * n,
        "pts": pts,
        "reb": [0.0] * n, "ast": [0.0] * n, "fg3m": [0.0] * n,
    })


# --------------------------------------------------------------------------
# add_interval_rollings — anti-leakage y limites
# --------------------------------------------------------------------------


def test_intervalos_exigen_diez_previos():
    out = exp.add_interval_rollings(_serie([10.0] * 12), stats=("pts",))
    assert out["min_max_lo_pts"].iloc[:10].isna().all()
    assert not out["min_max_lo_pts"].iloc[10:].isna().any()


def test_min_max_toma_los_extremos_de_la_ventana_previa():
    pts = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 999.0]
    out = exp.add_interval_rollings(_serie(pts), stats=("pts",))
    fila = out.iloc[10]
    assert fila["min_max_lo_pts"] == 1.0
    assert fila["min_max_hi_pts"] == 10.0   # el 999 del propio partido NO entra


def test_mediana_de_la_ventana_previa():
    pts = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 0.0]
    out = exp.add_interval_rollings(_serie(pts), stats=("pts",))
    assert out["med_pts"].iloc[10] == pytest.approx(5.5)


def test_intervalos_anidados_por_construccion():
    """[p25,p75] dentro de [p10,p90] dentro de [min,max], siempre."""
    pts = [3.0, 9.0, 1.0, 7.0, 5.0, 2.0, 8.0, 4.0, 6.0, 10.0, 0.0]
    f = exp.add_interval_rollings(_serie(pts), stats=("pts",)).iloc[10]
    assert f["min_max_lo_pts"] <= f["p10_p90_lo_pts"] <= f["p25_p75_lo_pts"]
    assert f["p25_p75_hi_pts"] <= f["p10_p90_hi_pts"] <= f["min_max_hi_pts"]


def test_cuantiles_interpolan_y_no_son_valores_de_orden():
    """Con n=10 pandas interpola: p10 cae entre el 1er y el 2do valor.

    Importa para leer la cobertura: el intervalo sale mas estrecho que el del
    cuantil de orden, y por eso se espera que cubra bajo su nominal.
    """
    pts = [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 5.0]
    f = exp.add_interval_rollings(_serie(pts), stats=("pts",)).iloc[10]
    assert f["p10_p90_lo_pts"] == pytest.approx(9.0)    # no 0.0 ni 10.0
    assert f["p10_p90_hi_pts"] == pytest.approx(81.0)   # no 90.0


def test_intervalos_separan_jugadores():
    a = _serie([10.0] * 11)
    b = _serie([50.0] * 11)
    b["player_id"] = 8
    out = exp.add_interval_rollings(pd.concat([a, b], ignore_index=True), stats=("pts",))
    assert out[out["player_id"] == 7]["min_max_hi_pts"].iloc[10] == 10.0
    assert out[out["player_id"] == 8]["min_max_hi_pts"].iloc[10] == 50.0


# --------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------


def test_coverage_cuenta_los_limites_como_dentro():
    """El mensaje dira 'entre X e Y'; un valor igual al limite esta dentro."""
    r = exp.coverage([1.0, 5.0], [1.0, 1.0], [5.0, 5.0])
    assert r["cobertura"] == 1.0


def test_coverage_fuera_por_arriba_y_por_abajo():
    r = exp.coverage([0.0, 3.0, 9.0], [1.0, 1.0, 1.0], [5.0, 5.0, 5.0])
    assert r["cobertura"] == pytest.approx(1 / 3, abs=1e-5)
    assert r["n"] == 3


def test_coverage_reporta_ancho_mediano_y_medio():
    r = exp.coverage([1.0, 1.0, 1.0], [0.0, 0.0, 0.0], [2.0, 4.0, 10.0])
    assert r["ancho_mediano"] == pytest.approx(4.0)
    assert r["ancho_medio"] == pytest.approx(16 / 3, abs=1e-4)


def test_coverage_ic_se_estrecha_con_n():
    chico = exp.coverage([1.0] * 100, [0.0] * 100, [2.0] * 100)
    # cobertura 1.0 -> se = 0; usar una mezcla para tener varianza
    real = [1.0] * 50 + [9.0] * 50
    grande_real = [1.0] * 5000 + [9.0] * 5000
    a = exp.coverage(real, [0.0] * 100, [2.0] * 100)
    b = exp.coverage(grande_real, [0.0] * 10000, [2.0] * 10000)
    assert chico["cobertura"] == 1.0
    assert b["semiancho_ic_pp"] < a["semiancho_ic_pp"]


def test_coverage_universo_vacio_no_revienta():
    r = exp.coverage([], [], [])
    assert r["n"] == 0 and r["cobertura"] is None


# --------------------------------------------------------------------------
# split_titular
# --------------------------------------------------------------------------


def test_split_titular_usa_el_corte_pre_registrado():
    etiquetas = exp.split_titular([27.9, 28.0, 35.0])
    assert list(etiquetas) == ["banca", "titular", "titular"]


# --------------------------------------------------------------------------
# summarize_coverage
# --------------------------------------------------------------------------


def _df_cobertura() -> pd.DataFrame:
    """Dos temporadas, dos roles, intervalos fijos y conocidos."""
    filas = []
    for season, rol, real in [
        ("2020-21", "titular", 5.0), ("2020-21", "banca", 99.0),
        ("2021-22", "titular", 5.0), ("2021-22", "banca", 5.0),
    ]:
        f = {"season": season, "rol": rol, "pts": real}
        for nombre, _a, _b, _c in exp.INTERVALOS:
            f[f"{nombre}_lo_pts"] = 0.0
            f[f"{nombre}_hi_pts"] = 10.0
        filas.append(f)
    return pd.DataFrame(filas)


def test_summarize_coverage_global_y_por_subgrupos():
    res = exp.summarize_coverage(_df_cobertura(), stats=("pts",))
    celda = res["pts"]["p10_p90"]
    assert celda["global"]["cobertura"] == pytest.approx(0.75)   # 3 de 4
    assert celda["por_rol"]["titular"]["cobertura"] == 1.0
    assert celda["por_rol"]["banca"]["cobertura"] == pytest.approx(0.5)
    assert celda["por_temporada"]["2020-21"]["cobertura"] == pytest.approx(0.5)
    assert celda["por_temporada"]["2021-22"]["cobertura"] == 1.0
    assert celda["rango_temporadas_pp"] == pytest.approx(50.0)


def test_summarize_coverage_conserva_el_nominal_y_lo_deja_nulo_en_minmax():
    res = exp.summarize_coverage(_df_cobertura(), stats=("pts",))
    assert res["pts"]["p10_p90"]["nominal"] == 0.80
    assert res["pts"]["p25_p75"]["nominal"] == 0.50
    assert res["pts"]["min_max"]["nominal"] is None   # indefinido para n=10


# --------------------------------------------------------------------------
# median_vs_mean_mae
# --------------------------------------------------------------------------


def test_median_vs_mean_mae_compara_ambos_centros():
    df = pd.DataFrame({"pts": [10.0, 10.0], "med_pts": [10.0, 10.0],
                       "b0_pts": [12.0, 8.0]})
    r = exp.median_vs_mean_mae(df, stats=("pts",))["pts"]
    assert r["mae_mediana"] == 0.0
    assert r["mae_media_b0"] == pytest.approx(2.0)
    assert r["diferencia"] == pytest.approx(-2.0)


# --------------------------------------------------------------------------
# render_table — el script mide, no decide
# --------------------------------------------------------------------------


def test_render_table_no_emite_recomendacion():
    res = exp.summarize_coverage(_df_cobertura(), stats=("pts",))
    centro = {"pts": {"mae_mediana": 1.0, "mae_media_b0": 1.0, "diferencia": 0.0}}
    txt = exp.render_table(res, centro).lower()
    assert "pts" in txt and "p10_p90" in txt
    for palabra in ("recomend", "sugier", "elegir", "deberia"):
        assert palabra not in txt


def test_universo_esperado_es_el_publicado_por_dexp3():
    """Guarda documental: si alguien cambia el conteo, el assert del CLI cae."""
    assert exp.N_UNIVERSO_DEXP3 == 148_484


def test_intervalos_declarados_en_orden_de_anidamiento():
    anchos = [hi - lo for _n, lo, hi, _nom in exp.INTERVALOS]
    assert anchos == sorted(anchos, reverse=True)
    assert np.isnan(exp.INTERVALOS[0][3])   # min_max sin nominal definido
