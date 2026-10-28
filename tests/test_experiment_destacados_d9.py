"""D-EXP-9 — funciones puras del experimento de minutos con redistribución.

Los invariantes que más importan: una baja ya absorbida pesa 0, el ajuste no
tiene intercepto (en B el brazo es M0 exacto), y NINGÚN brazo ve el partido que
predice. Un leakage aquí produciría una tabla de aspecto idéntico con mejoras
falsas.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "_d9", Path(__file__).resolve().parent.parent / "scripts" / "experiment_destacados_d9.py")
d9 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(d9)


# ── tratamiento ponderado ──────────────────────────────────────────────────

_VENT = [(f"g{i}", 1) for i in range(10)]          # 10 partidos del destacado con el equipo 1


def test_baja_absorbida_pesa_cero():
    jugaron = {k: {100} for k in _VENT}             # el compañero 200 no jugó ninguno
    f = d9.fraccion_jugada(_VENT, 200, jugaron)
    assert f == 0.0
    assert d9.min_perdidos([(31.5, f)]) == 0.0


def test_companero_que_jugo_10_de_10_aporta_su_rolling_completo():
    jugaron = {k: {100, 200} for k in _VENT}
    f = d9.fraccion_jugada(_VENT, 200, jugaron)
    assert f == 1.0
    assert d9.min_perdidos([(31.5, f)]) == pytest.approx(31.5)


def test_fraccion_parcial_y_suma_de_varios_companeros():
    jugaron = {k: ({100, 200} if i < 4 else {100}) for i, k in enumerate(_VENT)}
    f = d9.fraccion_jugada(_VENT, 200, jugaron)
    assert f == pytest.approx(0.4)
    assert d9.min_perdidos([(30.0, f), (26.0, 1.0)]) == pytest.approx(30.0 * 0.4 + 26.0)


def test_solo_cuenta_si_jugo_con_el_mismo_equipo_que_el_destacado():
    # El compañero (hoy en el equipo 1) jugó esos partidos, pero CONTRA el destacado
    # (equipo 2): no compartía minutos con él, no es absorción.
    jugaron = {(g, 2): {200} for g, _ in _VENT}
    assert d9.fraccion_jugada(_VENT, 200, jugaron) == 0.0


def test_ventana_vacia_da_cero():
    assert d9.fraccion_jugada([], 200, {}) == 0.0


# ── placebo: bajas del RIVAL, absorción sobre la ventana del equipo rival ──

def _games(n, equipos=((1, 2), (3, 1), (2, 3))):
    filas = []
    for i in range(n):
        h, a = equipos[i % len(equipos)]
        filas.append({"game_id": f"g{i:02d}", "game_date": pd.Timestamp("2025-01-01")
                      + pd.Timedelta(days=i), "home_team_id": h, "away_team_id": a})
    return pd.DataFrame(filas)


def test_ventana_de_equipo_son_sus_partidos_previos_sin_el_actual():
    g = _games(30)
    v = d9.ventanas_equipo(g)
    ult = g[(g.home_team_id == 2) | (g.away_team_id == 2)]["game_id"].tolist()
    actual = ult[-1]
    assert v[(actual, 2)] == [(x, 2) for x in ult[-11:-1]]
    assert (actual, 2) not in v[(actual, 2)]


def test_placebo_usa_la_ventana_del_rival():
    g = _games(30)
    v = d9.ventanas_equipo(g)
    rival = 2
    ult = g[(g.home_team_id == rival) | (g.away_team_id == rival)]["game_id"].tolist()
    gid = ult[-1]
    # La baja del rival (300) jugó 6 de los 10 previos de SU equipo.
    jugaron = {(x, rival): ({300} if i < 6 else set()) for i, x in enumerate(ult[-11:-1])}
    f = d9.fraccion_jugada(v[(gid, rival)], 300, jugaron)
    assert f == pytest.approx(0.6)


# ── OLS sin intercepto ──────────────────────────────────────────────────────

def test_fit_sin_intercepto_una_columna_contra_calculo_directo():
    rng = np.random.default_rng(0)
    x = rng.uniform(0, 30, 200)
    y = 0.37 * x + rng.normal(0, 2, 200) + 5.0      # un sesgo de +5 que NO debe absorber
    b = d9.fit_ols_sin_intercepto(x, y)
    assert b.shape == (1,)
    assert b[0] == pytest.approx(float((x * y).sum() / (x * x).sum()))


def test_fit_sin_intercepto_dos_columnas_contra_ecuaciones_normales():
    rng = np.random.default_rng(1)
    X = rng.uniform(0, 30, (300, 2))
    y = X @ np.array([0.2, 0.5]) + rng.normal(0, 1, 300)
    b = d9.fit_ols_sin_intercepto(X, y)
    np.testing.assert_allclose(b, np.linalg.solve(X.T @ X, X.T @ y), rtol=1e-10)


def test_sin_intercepto_tratamiento_cero_reproduce_m0():
    b = d9.fit_ols_sin_intercepto(np.array([0.0, 10.0, 20.0]), np.array([1.0, 4.0, 7.0]))
    m0 = np.array([31.0, 28.0])
    np.testing.assert_array_equal(d9.clip_minutes(m0 + b[0] * np.zeros(2)), m0)


def test_fit_ols_de_dexp3_sigue_con_intercepto():
    # La función existente queda INTACTA: devuelve intercepto + pendiente.
    x = np.array([[0.0], [1.0], [2.0], [3.0]])
    y = np.array([1.0, 3.0, 5.0, 7.0])
    coef = d9.dexp3.fit_ols(x, y)
    np.testing.assert_allclose(coef, [1.0, 2.0], atol=1e-12)


# ── filtro de producción en el selector ─────────────────────────────────────

def test_filtro_out_y_doubtful_y_el_puesto_pasa_al_siguiente():
    sel = pd.DataFrame({"game_id": ["g1"] * 5, "team_id": [1] * 5,
                        "player_id": [10, 11, 12, 13, 14], "sel_rank": [1, 2, 3, 4, 5],
                        "sel_games_in_window": [10] * 5})
    est = {("g1", 10): "Out", ("g1", 11): "Doubtful", ("g1", 12): "Questionable"}
    out = d9.filtrar_selector(sel, est)
    assert out["player_id"].tolist() == [12, 13]
    assert out["rank_prod"].tolist() == [1, 2]


def test_filtro_sin_estatus_no_cambia_el_top():
    sel = pd.DataFrame({"game_id": ["g1"] * 3, "team_id": [1] * 3, "player_id": [10, 11, 12],
                        "sel_rank": [1, 2, 3], "sel_games_in_window": [10] * 3})
    assert d9.filtrar_selector(sel, {})["player_id"].tolist() == [10, 11]


# ── tope [0, 44] ────────────────────────────────────────────────────────────

def test_tope_de_minutos():
    np.testing.assert_array_equal(d9.clip_minutes([-3.0, 12.5, 44.0, 51.2]),
                                  [0.0, 12.5, 44.0, 44.0])


# ── ningún brazo ve el partido que predice ──────────────────────────────────

def _played(n=14, pid=7):
    return pd.DataFrame({
        "player_id": [pid] * n, "team_id": [1] * n,
        "game_id": [f"g{i:02d}" for i in range(n)],
        "game_date": pd.date_range("2025-01-01", periods=n, freq="D"),
        "minutes": np.linspace(20, 33, n), "pts": np.arange(n, dtype=float) + 10,
        "reb": 5.0, "ast": 3.0, "fg3m": 1.0,
    })


def test_rolling_asof_es_estrictamente_anterior_a_la_fecha():
    p = _played()
    q = p[["player_id", "game_date"]].iloc[[12]].copy()
    r = d9.rolling_asof(p, q, "minutes")
    assert r["minutes_r"].iloc[0] == pytest.approx(p["minutes"].iloc[2:12].mean())
    p2 = p.copy()
    p2.loc[12, "minutes"] = 99.0                     # el partido que se predice
    assert d9.rolling_asof(p2, q, "minutes")["minutes_r"].iloc[0] == pytest.approx(
        r["minutes_r"].iloc[0])


def test_rolling_asof_minimo_tres():
    p = _played()
    q = p[["player_id", "game_date"]].iloc[[2, 3]].copy()
    r = d9.rolling_asof(p, q, "minutes")
    assert np.isnan(r["minutes_r"].iloc[0])          # solo 2 previos
    assert r["minutes_r"].iloc[1] == pytest.approx(p["minutes"].iloc[:3].mean())


def test_ventana_del_destacado_no_incluye_su_partido():
    p = _played()
    v = d9.ventanas_destacado(p)
    assert v[("g12", 7)] == [(f"g{i:02d}", 1) for i in range(2, 12)]


def test_tasa_ewm_no_ve_el_partido():
    p = _played()
    a = d9.rate_ewm(p)
    p2 = p.copy()
    p2.loc[13, "pts"] = 300.0
    b = d9.rate_ewm(p2)
    assert a["tasa_pts"].iloc[13] == pytest.approx(b["tasa_pts"].iloc[13])
    esperado = (p["pts"] / p["minutes"]).iloc[:13].ewm(halflife=5, adjust=True).mean().iloc[-1]
    assert a["tasa_pts"].iloc[13] == pytest.approx(esperado)


# ── puerta ──────────────────────────────────────────────────────────────────

def _ev(media, ic, folds, rel):
    return {"agregado": {"media": media, "ic95": ic}, "folds_con_mejora": folds,
            "mejora_rel": rel}


@pytest.mark.parametrize("ev, cruza", [
    (_ev(0.3, [0.1, 0.5], 2, 0.06), True),
    (_ev(0.3, [-0.1, 0.5], 2, 0.06), False),     # IC incluye cero
    (_ev(0.3, [0.1, 0.5], 1, 0.06), False),      # mejora en un solo fold
    (_ev(0.2, [0.1, 0.3], 2, 0.04), False),      # bajo el 5%
])
def test_puerta_m1(ev, cruza):
    assert d9.puerta(ev, 0.05, 0.01)["cruza"] is cruza


def test_puerta_usa_el_mde_si_es_mayor_que_el_umbral():
    assert d9.puerta(_ev(0.3, [0.1, 0.5], 2, 0.06), 0.05, 0.07)["cruza"] is False
