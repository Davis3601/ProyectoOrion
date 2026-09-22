"""Tests de las funciones puras del experimento D-EXP-3.

El script es borrable sin residuo; estos tests cubren SOLO su logica pura
(derivacion de stats, rollings anti-leakage, diferencia pareada de MAE, gate de
resolucion, OLS y congelado de la muestra). No tocan red, GCS ni BigQuery.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "experiment_stats_jugador",
    Path(__file__).resolve().parents[1] / "scripts" / "experiment_stats_jugador.py",
)
exp = importlib.util.module_from_spec(_SPEC)
sys.modules["experiment_stats_jugador"] = exp
_SPEC.loader.exec_module(exp)


# --------------------------------------------------------------------------
# derive_stats — player_game_stats NO trae pts ni reb
# --------------------------------------------------------------------------


def test_derive_stats_pts_cuenta_los_triples_una_sola_vez():
    """fgm YA incluye los triples: 2*fgm + fg3m + ftm, no 2*fgm + 3*fg3m."""
    df = pd.DataFrame([{"fgm": 10, "fg3m": 4, "ftm": 3, "oreb": 2, "dreb": 5}])
    out = exp.derive_stats(df)
    # 6 dobles (12) + 4 triples (12) + 3 libres = 27
    assert out.loc[0, "pts"] == 27
    assert out.loc[0, "reb"] == 7


def test_derive_stats_no_muta_la_entrada():
    df = pd.DataFrame([{"fgm": 1, "fg3m": 0, "ftm": 0, "oreb": 0, "dreb": 0}])
    exp.derive_stats(df)
    assert "pts" not in df.columns


# --------------------------------------------------------------------------
# add_rollings — anti-leakage y la elección forzada de la tasa
# --------------------------------------------------------------------------


def _serie(n: int, pts_por_partido: list[float], minutos: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "player_id": [7] * n,
        "game_date": [f"2023-11-{d:02d}" for d in range(1, n + 1)],
        "minutes": minutos,
        "pts": pts_por_partido,
        "reb": [0.0] * n, "ast": [0.0] * n, "fg3m": [0.0] * n,
    })


def test_add_rollings_exige_min_previos_y_deja_nan_antes():
    """Con 10 previos requeridos, las 10 primeras filas quedan NaN."""
    df = _serie(12, [10.0] * 12, [20.0] * 12)
    out = exp.add_rollings(df, stats=("pts",))
    assert out["b0_pts"].iloc[:10].isna().all()
    assert not out["b0_pts"].iloc[10:].isna().any()


def test_add_rollings_no_ve_el_partido_actual():
    """shift(1): un pico en el último partido no puede entrar en su propia predicción."""
    pts = [10.0] * 10 + [999.0, 10.0]
    df = _serie(12, pts, [20.0] * 12)
    out = exp.add_rollings(df, stats=("pts",))
    # Fila 10 (el pico) predice con los 10 anteriores, todos de 10 puntos.
    assert out["b0_pts"].iloc[10] == pytest.approx(10.0)
    # Fila 11 sí absorbe el pico.
    assert out["b0_pts"].iloc[11] > 10.0


def test_c1_coincide_con_b0_cuando_los_minutos_son_constantes():
    """Con minutos fijos, promedio-de-razones x minutos == rolling directo."""
    df = _serie(12, [10.0, 12.0, 8.0, 14.0, 9.0, 11.0, 13.0, 7.0, 10.0, 12.0, 10.0, 10.0],
                [20.0] * 12)
    out = exp.add_rollings(df, stats=("pts",))
    fila = out.iloc[10]
    assert fila["c1_pts"] == pytest.approx(fila["b0_pts"])


def test_c1_difiere_de_b0_cuando_los_minutos_varian():
    """El experimento sólo tiene contenido si la varianza de minutos los separa.

    Con razón-de-promedios C1 sería idénticamente B0 y no habría nada que medir;
    este test fija que la implementación usa promedio-de-razones.
    """
    minutos = [10.0, 30.0] * 6
    pts = [5.0, 25.0] * 6
    out = exp.add_rollings(_serie(12, pts, minutos), stats=("pts",))
    fila = out.iloc[10]
    assert fila["c1_pts"] != pytest.approx(fila["b0_pts"])


def test_add_rollings_separa_jugadores():
    """El rolling de un jugador jamás toma partidos de otro."""
    a = _serie(11, [10.0] * 11, [20.0] * 11)
    b = _serie(11, [30.0] * 11, [20.0] * 11)
    b["player_id"] = 8
    out = exp.add_rollings(pd.concat([a, b], ignore_index=True), stats=("pts",))
    assert out[out["player_id"] == 7]["b0_pts"].iloc[10] == pytest.approx(10.0)
    assert out[out["player_id"] == 8]["b0_pts"].iloc[10] == pytest.approx(30.0)


# --------------------------------------------------------------------------
# paired_mae_diff
# --------------------------------------------------------------------------


def test_paired_mae_diff_media_es_la_diferencia_de_mae():
    y = [10.0, 20.0, 30.0]
    a = [12.0, 24.0, 33.0]   # errores 2, 4, 3 -> MAE 3
    b = [11.0, 21.0, 31.0]   # errores 1, 1, 1 -> MAE 1
    r = exp.paired_mae_diff(y, a, b)
    assert r["media"] == pytest.approx(2.0)
    assert r["gana_b_en_filas"] == 3
    assert r["n"] == 3


def test_paired_mae_diff_signo_negativo_si_a_es_mejor():
    r = exp.paired_mae_diff([10.0, 10.0], [10.0, 10.0], [13.0, 13.0])
    assert r["media"] == pytest.approx(-3.0)
    assert r["gana_b_en_filas"] == 0


def test_paired_mae_diff_identicos_da_cero_y_sd_cero():
    p = [1.0, 2.0, 3.0]
    r = exp.paired_mae_diff([1.5, 2.5, 3.5], p, p)
    assert r["media"] == 0.0
    assert r["t"] is None   # sd == 0 -> no hay estadístico que reportar


# --------------------------------------------------------------------------
# mde_from_sd / resolution_gate — el gate ejecutable de D-EXP-2
# --------------------------------------------------------------------------


def test_mde_baja_con_la_raiz_de_n():
    """Cuadruplicar la muestra corta el MDE a la mitad."""
    assert exp.mde_from_sd(1.0, 400) == pytest.approx(exp.mde_from_sd(1.0, 100) / 2)


def test_resolution_gate_declara_con_resolucion_bajo_el_umbral():
    g = exp.resolution_gate(sd_fold1=1.0, n_proyectado=1_000_000, mae_baseline=5.0)
    assert g["con_resolucion"] is True
    assert g["mde_relativo"] < 0.03


def test_resolution_gate_declara_sin_resolucion_con_muestra_chica():
    """Es el caso que mató a D-EXP-2: ruido grande, muestra insuficiente."""
    g = exp.resolution_gate(sd_fold1=5.0, n_proyectado=100, mae_baseline=5.0)
    assert g["con_resolucion"] is False
    assert g["mde_relativo"] > 0.03


def test_resolution_gate_umbral_exacto_cuenta_como_con_resolucion():
    # sd elegido para que MDE == 3% exacto del MAE baseline
    n, mae = 10_000, 5.0
    sd = 0.03 * mae * (n ** 0.5) / exp.POTENCIA_Z
    g = exp.resolution_gate(sd, n, mae)
    assert g["mde_relativo"] == pytest.approx(0.03, abs=1e-6)
    assert g["con_resolucion"] is True


# --------------------------------------------------------------------------
# fit_ols / apply_ols
# --------------------------------------------------------------------------


def test_ols_recupera_una_relacion_lineal_exacta():
    X = np.array([[1.0], [2.0], [3.0], [4.0]])
    y = 3.0 + 2.0 * X[:, 0]
    coef = exp.fit_ols(X, y)
    assert coef[0] == pytest.approx(3.0)
    assert coef[1] == pytest.approx(2.0)
    assert exp.apply_ols(coef, X) == pytest.approx(y)


def test_ols_con_segunda_columna_usa_ambas():
    X = np.array([[1.0, 0.0], [2.0, 1.0], [3.0, 0.0], [4.0, 1.0]])
    y = 1.0 + 2.0 * X[:, 0] + 5.0 * X[:, 1]
    coef = exp.fit_ols(X, y)
    assert exp.apply_ols(coef, X) == pytest.approx(y)


# --------------------------------------------------------------------------
# freeze_audit_sample
# --------------------------------------------------------------------------


def test_freeze_audit_sample_es_determinista():
    ids = [(f"G{i}", i) for i in range(500)]
    assert exp.freeze_audit_sample(ids) == exp.freeze_audit_sample(ids)


def test_freeze_audit_sample_respeta_el_tamano_y_no_repite():
    ids = [(f"G{i}", i) for i in range(500)]
    m = exp.freeze_audit_sample(ids)
    assert len(m) == exp.AUDIT_N
    assert len(set(m)) == exp.AUDIT_N


def test_freeze_audit_sample_no_revienta_con_universo_menor():
    ids = [("G1", 1), ("G2", 2)]
    assert len(exp.freeze_audit_sample(ids)) == 2
