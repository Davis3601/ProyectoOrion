"""D-EXP-7 — funciones puras del experimento de valor central y rango.

El invariante que más importa: NINGÚN brazo ve el partido que predice (shift 1),
ni la temporada en curso entra en media_prev. Un leakage aquí produciría una
tabla de aspecto idéntico con mejoras falsas.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "_d7", Path(__file__).resolve().parent.parent / "scripts" / "experiment_destacados_d7.py")
d7 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(d7)


def _jugador(pts_a, pts_b, pid=1):
    """Un jugador con una temporada '2024-25' (pts_a) y '2025-26' (pts_b)."""
    filas = []
    for season, vals, inicio in (("2024-25", pts_a, "2024-11-01"),
                                 ("2025-26", pts_b, "2025-11-01")):
        for i, v in enumerate(vals):
            filas.append({"player_id": pid, "season": season,
                          "game_date": pd.Timestamp(inicio) + pd.Timedelta(days=i),
                          "game_id": f"{season}-{i}", "minutes": 30.0,
                          "pts": float(v), "reb": 5.0, "ast": 3.0, "fg3m": 1.0})
    return pd.DataFrame(filas)


def test_previous_season():
    assert d7.previous_season("2026-27") == "2025-26"
    assert d7.previous_season("2000-01") == "1999-00"


def test_b0_mediana_y_c1_media_de_los_10_previos():
    a = list(range(1, 13))                    # 12 partidos: 1..12
    out = d7.add_center_arms(_jugador(a, [50]))
    fila = out[(out["season"] == "2025-26")].iloc[0]   # previos: 3..12
    assert fila["B0_pts"] == pytest.approx(np.median(range(3, 13)))
    assert fila["C1_pts"] == pytest.approx(np.mean(range(3, 13)))


def test_ningun_brazo_ve_su_propio_partido():
    base = d7.add_center_arms(_jugador(list(range(1, 13)), [10, 11, 12, 13]))
    mod = _jugador(list(range(1, 13)), [10, 11, 12, 999])     # cambia SOLO el último
    out = d7.add_center_arms(mod)
    ult = out.iloc[-1]
    ref = base.iloc[-1]
    for c in ("B0", "C1", "C2", "C3", "C5"):
        assert ult[f"{c}_pts"] == pytest.approx(ref[f"{c}_pts"]), c


def test_c3_shrinkage_al_arranque():
    a = [10.0] * 12                            # media de la temporada anterior = 10
    out = d7.add_center_arms(_jugador(a, [20, 20, 20]))
    b = out[out["season"] == "2025-26"].reset_index(drop=True)
    # n_act = 0: solo la temporada anterior
    assert b.loc[0, "n_act"] == 0 and b.loc[0, "C3_pts"] == pytest.approx(10.0)
    # n_act = 2, media_act = 20: (2*20 + 5*10) / (2+5)
    assert b.loc[2, "C3_pts"] == pytest.approx((2 * 20 + 5 * 10) / 7)


def test_c3_con_10_o_mas_de_la_temporada_es_c1():
    out = d7.add_center_arms(_jugador([10.0] * 12, [float(i) for i in range(11)]))
    b = out[out["season"] == "2025-26"].iloc[-1]       # n_act = 10
    assert b["n_act"] == 10 and b["C3_pts"] == pytest.approx(b["C1_pts"])


def test_c5_ultimos_10_de_la_anterior_y_luego_media_expansiva():
    a = [0.0] * 5 + [10.0] * 10                # últimos 10 de la anterior = 10
    out = d7.add_center_arms(_jugador(a, [20, 30, 40, 50]))
    b = out[out["season"] == "2025-26"].reset_index(drop=True)
    assert b.loc[0, "C5_pts"] == pytest.approx(10.0)         # n_act 0 < 3
    assert b.loc[2, "C5_pts"] == pytest.approx(10.0)         # n_act 2 < 3
    assert b.loc[3, "C5_pts"] == pytest.approx(np.mean([20, 30, 40]))   # n_act 3


def test_media_prev_no_usa_la_temporada_en_curso():
    """Sin temporada anterior, C3 cae a C1 (fallback declarado), jamás a la actual."""
    out = d7.add_center_arms(_jugador([], [float(i) for i in range(15)]))
    fila = out.iloc[-1]
    assert fila["n_act"] == 14
    assert out.attrs["c3_c5_fallback_filas"] == 15


def test_c2_es_la_media_exponencial_de_los_previos():
    a = [float(i) for i in range(12)]
    out = d7.add_center_arms(_jugador(a, []))
    esperado = pd.Series(a).shift(1).ewm(halflife=5, adjust=True).mean().iloc[-1]
    assert out.iloc[-1]["C2_pts"] == pytest.approx(esperado)


# ── Selector de producción reconstruido ──

def _filas_sel(spec):
    """spec: lista de (player_id, team_id, día, minutos)."""
    return pd.DataFrame([{"player_id": p, "team_id": t,
                          "game_date": pd.Timestamp("2025-11-01") + pd.Timedelta(days=d),
                          "minutes": m} for p, t, d, m in spec])


def _juego(gid, dia, home, away):
    return {"game_id": gid, "game_date": pd.Timestamp("2025-11-01") + pd.Timedelta(days=dia),
            "home_team_id": home, "away_team_id": away}


def test_selector_top2_por_minutos_con_minimo_3():
    filas = _filas_sel([(p, 1, d, m) for d in range(3)
                        for p, m in ((10, 30.0), (11, 20.0), (12, 35.0))]
                       + [(13, 1, 2, 40.0)])                 # 13: solo 1 jugado
    juegos = pd.DataFrame([_juego("g", 5, 1, 2)])
    sel = d7.reconstruct_selector(filas, juegos)
    assert list(zip(sel["player_id"], sel["sel_rank"])) == [(12, 1), (10, 2)]


def test_selector_no_ve_el_propio_dia():
    filas = _filas_sel([(p, 1, d, 30.0) for p in (10, 11) for d in range(3)]
                       + [(14, 1, 5, 50.0)])                 # filas del MISMO día del juego
    juegos = pd.DataFrame([_juego("g", 5, 1, 2)])
    assert 14 not in set(d7.reconstruct_selector(filas, juegos)["player_id"])


def test_selector_traspasado_ya_no_es_del_equipo_viejo():
    filas = _filas_sel([(p, 1, d, 30.0) for p in (10, 11) for d in range(3)]
                       + [(10, 2, 4, 0.0)])                  # 10 activado con el equipo 2
    juegos = pd.DataFrame([_juego("g", 5, 1, 3)])
    sel = d7.reconstruct_selector(filas, juegos)
    assert set(sel["player_id"]) == {11}


def test_selector_recencia_250_dias():
    filas = _filas_sel([(10, 1, d, 30.0) for d in range(3)])
    juegos = pd.DataFrame([_juego("g", 260, 1, 2)])
    assert d7.reconstruct_selector(filas, juegos).empty


# ── Puertas ──

def _ev(media, ic_lo, rel, folds, n_folds=6):
    return {"agregado": {"media": media, "ic95": [ic_lo, ic_lo + 0.1]},
            "mejora_rel": rel, "folds_con_mejora": folds, "n_folds": n_folds}


def test_puerta_centro_cruza_y_desplegable():
    g = d7.center_gate(_ev(0.2, 0.05, 0.05, 6), mde_rel=0.001)
    assert g["cruza"] and g["desplegable"] and g["umbral"] == 0.03


def test_puerta_centro_umbral_es_max_de_3pct_y_mde():
    g = d7.center_gate(_ev(0.2, 0.05, 0.035, 6), mde_rel=0.04)
    assert g["umbral"] == 0.04 and not g["cruza"]


def test_puerta_centro_debil_no_desplegable():
    g = d7.center_gate(_ev(0.2, 0.05, 0.035, 5), mde_rel=0.001)   # < 3%+1pp y 5/6 folds
    assert g["cruza"] and g["debil_no_desplegable"] and not g["desplegable"]


def test_puerta_centro_falla_por_folds_o_ic():
    assert not d7.center_gate(_ev(0.2, 0.05, 0.10, 4), mde_rel=0.001)["cruza"]
    assert not d7.center_gate(_ev(0.2, -0.01, 0.10, 6), mde_rel=0.001)["cruza"]


def test_puerta_arranque():
    assert d7.arranque_gate(_ev(0.2, 0.05, 0.03, 4))["cruza"]
    assert not d7.arranque_gate(_ev(0.2, 0.05, 0.029, 6))["cruza"]
    assert not d7.arranque_gate(_ev(0.2, 0.05, 0.05, 3))["cruza"]


def test_puerta_rango():
    ok = d7.range_gate(0.80, [0.79, 0.81], [0.1, 0.3], ancho_r1=10, ancho_r0=16)
    assert ok["cruza"]
    assert not d7.range_gate(0.83, [0.80], [0.1, 0.3], 10, 16)["a_cobertura"]
    assert not d7.range_gate(0.80, [0.74, 0.81], [0.1, 0.3], 10, 16)["a_cobertura"]
    assert not d7.range_gate(0.80, [0.80], [-0.1, 0.3], 10, 16)["b_winkler"]
    assert not d7.range_gate(0.80, [0.80], [0.1, 0.3], 17, 16)["c_ancho"]


def test_winkler_y_conformal():
    assert d7.winkler([5], [0], [10])[0] == 10                      # dentro: solo ancho
    assert d7.winkler([12], [0], [10], alfa=0.2)[0] == 10 + 10 * 2  # fuera por 2
    y = np.arange(100, dtype=float)
    assert d7.conformal_q(y, np.zeros(100), None, 0.8) == pytest.approx(np.quantile(y, 0.8))
    esc = np.where(np.arange(100) % 2 == 0, 0.0, 2.0)               # escala 0 se descarta
    q = d7.conformal_q(y, np.zeros(100), esc, 0.8)
    assert q == pytest.approx(np.quantile(y[1::2] / 2, 0.8))


def test_mde_celda_toma_el_peor_candidato():
    rng = np.random.default_rng(0)
    n = 500
    df = pd.DataFrame({"pts": rng.normal(20, 5, n), "B0_pts": rng.normal(20, 5, n)})
    for c in d7.CANDIDATOS:
        df[f"{c}_pts"] = df["B0_pts"] + rng.normal(0, 0.1 if c != "C4" else 3.0, n)
    cell = d7.mde_cell(df, "pts", n_total=n)
    assert cell["mde_rel_celda"] == max(v["mde_rel"] for v in cell["por_candidato"].values())
    assert cell["por_candidato"]["C4"]["mde_rel"] > cell["por_candidato"]["C1"]["mde_rel"]


# ── R0 sobre la serie completa (corrección de la corrida inválida) ──

def test_r0_coincide_con_el_rango_que_publica_produccion():
    """min/max de range_series == pts_min/pts_max de player_window_stats (producción)
    para los últimos 10 JUGADOS previos, en cada partido con historia suficiente."""
    from nba_predictor.api.player_highlights import player_window_stats

    rng = np.random.default_rng(7)
    vals = rng.integers(0, 40, 25).astype(float)
    df = _jugador(list(vals[:15]), list(vals[15:]))
    rs = d7.range_series(df).set_index("game_id")
    orden = df.sort_values("game_date").reset_index(drop=True)
    for i in range(10, len(orden)):
        previos = orden.iloc[:i].assign(reb=0.0, ast=0.0)
        w = player_window_stats(previos, 10)
        gid = orden.loc[i, "game_id"]
        assert rs.loc[gid, "min_pts"] == w["pts_min"]
        assert rs.loc[gid, "max_pts"] == w["pts_max"]


def test_r0_sin_nan_con_10_previos_y_no_ve_su_partido():
    df = _jugador([float(i) for i in range(12)], [100.0])
    rs = d7.range_series(df).set_index("game_id")
    fila = rs.loc["2025-26-0"]                      # previos: 2..11
    assert (fila["min_pts"], fila["max_pts"]) == (2.0, 11.0)
    assert fila["disp_pts"] == pytest.approx(np.mean(np.abs(np.arange(2, 12) - 6.5)))


# ── D-EXP-11: pisos de un solo lado ──────────────────────────────────────────

_Y = np.array([10.0, 14.0, 20.0, 25.0, 31.0, 18.0, 22.0, 9.0, 27.0, 16.0])
_C = np.array([18.0, 18.0, 20.0, 21.0, 24.0, 19.0, 20.0, 15.0, 23.0, 17.0])
_E = np.array([4.0, 2.0, 0.0, 5.0, 3.0, 2.5, 4.0, 3.0, 6.0, 1.0])


def test_conformal_q_lower_contra_calculo_directo():
    ok = _E > 0
    for nivel in (0.80, 0.90):
        esperado = np.quantile((_C[ok] - _Y[ok]) / _E[ok], nivel)
        assert d7.conformal_q_lower(_Y, _C, _E, nivel) == pytest.approx(esperado)
        assert d7.conformal_q_lower(_Y, _C, None, nivel) == pytest.approx(
            np.quantile(_C - _Y, nivel))


def test_conformal_q_de_r1_sin_cambio():
    """conformal_q sigue siendo el cuantil del residuo ABSOLUTO (lo que calibró R1),
    con su valor fijado; el de un lado es otro número sobre los mismos datos."""
    ok = _E > 0
    q_r1 = d7.conformal_q(_Y, _C, _E)
    assert q_r1 == pytest.approx(np.quantile(np.abs(_Y[ok] - _C[ok]) / _E[ok], 0.80))
    assert q_r1 == pytest.approx(2.0)
    # Espejo del real respecto al centro: el residuo absoluto (R1) no cambia;
    # el de un lado sí. Si conformal_q perdiera el valor absoluto, esto caería.
    espejo = 2 * _C - _Y
    assert d7.conformal_q(espejo, _C, _E) == pytest.approx(q_r1)
    assert d7.conformal_q_lower(espejo, _C, _E, 0.80) != pytest.approx(q_r1)


def test_piso_redondea_hacia_arriba_y_acota_en_cero():
    # 20 - 1.0 * 2.7 = 17.3 -> 18 (hacia ARRIBA); 20 - 4 = 16 exacto -> 16
    assert list(d7.floor_values([20.0, 20.0], [2.7, 4.0], 1.0, 99.0)) == [18.0, 16.0]
    # dispersión 0 -> respaldo absoluto; negativo -> 0
    assert list(d7.floor_values([5.0, 1.0], [0.0, 3.0], 2.0, 2.5)) == [3.0, 0.0]
    # fg3m con diez ceros: centro 0 -> piso 0, cobertura 100% por construcción
    piso = d7.floor_values([0.0, 0.0], [0.0, 0.0], 1.0, 1.0)
    assert list(piso) == [0.0, 0.0]
    assert d7.floor_coverage([0.0, 3.0], piso) == 1.0


def test_redondear_hacia_arriba_no_cambia_la_cobertura_continua():
    """Con stats enteros, real >= ceil(lo) <=> real >= lo; floor(lo) cubriría más."""
    rng = np.random.default_rng(0)
    y = rng.integers(0, 40, 500).astype(float)
    lo = rng.uniform(-5, 35, 500)
    piso = d7.floor_values(lo, np.zeros(500), 0.0, 0.0)
    assert d7.floor_coverage(y, piso) == pytest.approx(np.mean(y >= np.maximum(lo, 0)))
    assert np.mean(y >= np.floor(np.maximum(lo, 0))) > d7.floor_coverage(y, piso)


def test_cobertura_es_mayor_o_igual():
    assert d7.floor_coverage([18.0, 17.0, 30.0], [18.0, 18.0, 18.0]) == pytest.approx(2 / 3)


@pytest.mark.parametrize("agg,folds,cruza", [
    (0.97, [0.96, 0.98], True),     # por encima de la banda: cruza (no hay techo)
    (0.88, [0.85, 0.90], True),     # en los bordes exactos
    (0.879, [0.90, 0.90], False),   # agregada bajo 88%
    (0.90, [0.849, 0.95], False),   # un fold bajo 85%
])
def test_banda_solo_inferior_p90(agg, folds, cruza):
    g = d7.floor_gate(agg, folds, d7.PISO_BANDAS["P90"], frac_cero=0.0)
    assert g["cruza"] is cruza
    assert g["poco_informativo_hallazgo"] is (agg > 0.95)


def test_regla_del_30_por_ciento():
    g = d7.floor_gate(0.92, [0.9], d7.PISO_BANDAS["P90"], frac_cero=0.31)
    assert g["cruza_cobertura"] and g["sin_piso_util"] and not g["cruza"]
    g = d7.floor_gate(0.92, [0.9], d7.PISO_BANDAS["P90"], frac_cero=0.30)
    assert not g["sin_piso_util"] and g["cruza"]


def test_informatividad_cociente_de_medias_con_centro_cero():
    assert d7.informativeness([0.0, 10.0], [0.0, 20.0]) == pytest.approx(0.5)
    assert d7.informativeness([0.0, 0.0], [0.0, 0.0]) is None
