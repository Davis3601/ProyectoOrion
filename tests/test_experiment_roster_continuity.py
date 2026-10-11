"""Unit tests de D-EXP-6 (continuidad de roster) con fixtures sinteticos minimos.

El nucleo es el ANTI-LEAKAGE, con el patron del test reina de Fase 2: plantar un
valor extremo en el partido K y exigir que la continuidad EN K no lo vea y la de
K+1 si. Se añade una version por grupo mas fuerte: perturbar arbitrariamente el
partido K y TODOS los posteriores no puede cambiar la continuidad de ningun
partido <= K. Si una feature mirara al presente o al futuro, este es el test que
se pone rojo — un leakage aqui produciria una tabla de aspecto perfectamente sano.
"""
from __future__ import annotations

import importlib.util
import math
import random
import sys
from pathlib import Path

import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "experiment_roster_continuity",
    Path(__file__).resolve().parents[1] / "scripts" / "experiment_roster_continuity.py",
)
exp = importlib.util.module_from_spec(_SPEC)
sys.modules["experiment_roster_continuity"] = exp
_SPEC.loader.exec_module(exp)

TG = exp.TeamGame


def _g(i, starters, minutes=None):
    """Partido i con titulares dados; por defecto juegan los titulares 30 min."""
    mins = minutes if minutes is not None else {p: 30.0 for p in starters}
    return TG(game_id=f"g{i:03d}", game_date=f"2020-01-{i + 1:02d}",
              starters=frozenset(starters), minutes=dict(mins))


def _estable(n, quinteto=(1, 2, 3, 4, 5)):
    return [_g(i, quinteto) for i in range(n)]


# --------------------------------------------------------------------------
# C_a — quintetas
# --------------------------------------------------------------------------


def test_c_a_es_uno_con_quinteto_identico():
    seq = _estable(10)
    assert exp.continuity_a(seq, 8) == pytest.approx(1.0)


def test_c_a_es_cero_si_el_quinteto_cambia_entero_cada_partido():
    seq = [_g(i, range(10 * i, 10 * i + 5)) for i in range(10)]
    assert exp.continuity_a(seq, 8) == pytest.approx(0.0)


def test_c_a_promedia_los_solapes_de_la_ventana():
    """Un solo cambio de un titular en la ventana: un solape de 4/5 y cuatro de 5/5."""
    seq = _estable(10)
    seq[6] = _g(6, (1, 2, 3, 4, 99))           # cambia 1 titular en el partido 6
    seq[7] = _g(7, (1, 2, 3, 4, 99))           # y se mantiene en el 7
    # ventana de k=8: j=3..7 -> solapes 5,5,5,4,5 -> media 24/25
    assert exp.continuity_a(seq, 8) == pytest.approx(24 / 25)


def test_c_a_esta_en_rango_unitario():
    rng = random.Random(1)
    seq = [_g(i, rng.sample(range(12), 5)) for i in range(40)]
    for k in range(6, 40):
        assert 0.0 <= exp.continuity_a(seq, k) <= 1.0


def test_c_a_sin_historia_suficiente_es_nan():
    seq = _estable(10)
    assert math.isnan(exp.continuity_a(seq, 5))   # necesita k-6 >= 0
    assert not math.isnan(exp.continuity_a(seq, 6))


# --------------------------------------------------------------------------
# C_b — rotacion por minutos
# --------------------------------------------------------------------------


def test_c_b_es_uno_si_la_rotacion_no_cambia():
    seq = _estable(15)
    assert exp.continuity_b(seq, 12) == pytest.approx(1.0)


def test_c_b_mide_la_fraccion_de_minutos_de_los_presentes():
    """El jugador 9 jugo 20 min en cada uno de los 10 partidos, pero no el ultimo."""
    seq = []
    for i in range(12):
        mins = {1: 30.0, 2: 30.0, 9: 20.0}
        if i == 11:                              # partido k-1 = 11: el 9 no juega
            mins = {1: 30.0, 2: 30.0, 9: 0.0}
        seq.append(_g(i, (1, 2), mins))
    # ventana k=12 -> partidos 2..11. Minutos totales: 60*10 + 20*9 = 780.
    # De presentes en el 11 (jugadores 1 y 2): 600.
    assert exp.continuity_b(seq, 12) == pytest.approx(600 / 780)


def test_c_b_no_cuenta_minutos_cero_ni_none():
    seq = [_g(i, (1,), {1: 30.0, 2: 0.0}) for i in range(12)]
    assert exp.continuity_b(seq, 11) == pytest.approx(1.0)


def test_c_b_sin_historia_suficiente_es_nan():
    seq = _estable(15)
    assert math.isnan(exp.continuity_b(seq, 9))
    assert not math.isnan(exp.continuity_b(seq, 10))


# --------------------------------------------------------------------------
# ANTI-LEAKAGE — patron del test reina de Fase 2
# --------------------------------------------------------------------------


class TestReinaNoLeakage:
    """Plantar un roster totalmente nuevo en el partido K.
    - La continuidad EN K no debe verlo (solo mira K-1, K-2, ...).
    - La continuidad EN K+1 si debe verlo."""

    K = 12

    def _plantado(self):
        seq = _estable(20)
        seq[self.K] = _g(self.K, (91, 92, 93, 94, 95))    # roster nuevo en K
        return seq

    def test_c_a_en_k_no_ve_el_roster_plantado(self):
        assert exp.continuity_a(self._plantado(), self.K) == pytest.approx(1.0), \
            "LEAKAGE: C_a en K refleja los titulares del propio partido K"

    def test_c_a_en_k_mas_uno_si_lo_ve(self):
        # ventana de K+1 incluye j=K: solape(K, K-1) = 0 -> media 4/5
        assert exp.continuity_a(self._plantado(), self.K + 1) == pytest.approx(4 / 5)

    def test_c_b_en_k_no_ve_el_roster_plantado(self):
        assert exp.continuity_b(self._plantado(), self.K) == pytest.approx(1.0), \
            "LEAKAGE: C_b en K usa minutos o presencia del propio partido K"

    def test_c_b_en_k_mas_uno_si_lo_ve(self):
        # presentes en K = roster nuevo; la ventana K-9..K trae 9 partidos del viejo
        assert exp.continuity_b(self._plantado(), self.K + 1) == pytest.approx(1 / 10)


class TestNoLeakagePorGrupo:
    """Version fuerte: perturbar el partido K y TODOS los posteriores no puede
    mover la continuidad de ningun partido <= K. Cubre cualquier forma de mirar
    al presente o al futuro, no solo la que el test reina planta."""

    @pytest.mark.parametrize("k", [10, 15, 22])
    def test_el_futuro_no_toca_el_pasado(self, k):
        rng = random.Random(7)
        base = [_g(i, rng.sample(range(12), 5),
                   {p: rng.uniform(5, 40) for p in rng.sample(range(12), 8)})
                for i in range(30)]
        antes = exp.team_continuity(base)
        perturbado = base[:k] + [
            _g(i, rng.sample(range(100, 120), 5), {p: 48.0 for p in range(100, 110)})
            for i in range(k, 30)]
        despues = exp.team_continuity(perturbado)
        for i in range(k + 1):       # <= k: incluido el propio K
            for c in ("c_a", "c_b"):
                a, b = antes[i][c], despues[i][c]
                assert (math.isnan(a) and math.isnan(b)) or a == pytest.approx(b), \
                    f"LEAKAGE en {c} del partido {i}: cambio al perturbar partidos >= {k}"


# --------------------------------------------------------------------------
# build_sequences — orden cronologico y titulares
# --------------------------------------------------------------------------


def test_build_sequences_ordena_por_fecha_y_junta_titulares():
    games = pd.DataFrame({"game_id": ["b", "a"], "game_date": ["2020-01-02", "2020-01-01"]})
    pgs = pd.DataFrame({
        "game_id": ["b", "b", "a", "a"], "team_id": [7, 7, 7, 7],
        "player_id": [1, 2, 1, 3], "started": [1, 0, 1, 1],
        "minutes": [30.0, None, 25.0, 20.0]})
    seq = exp.build_sequences(pgs, games)[7]
    assert [g.game_id for g in seq] == ["a", "b"]
    assert seq[0].starters == frozenset({1, 3})
    assert seq[1].starters == frozenset({1})
    assert seq[1].minutes[2] == 0.0          # None -> 0, jamas NaN que se propague


# --------------------------------------------------------------------------
# Gate de resolucion y puerta numerica
# --------------------------------------------------------------------------


def test_mde_pareado():
    assert exp.mde_pareado(0.05, 5823) == pytest.approx(2.8016 * 0.05 / math.sqrt(5823))


def test_techo_pre_registrado():
    assert exp.MDE_TECHO == 0.005
    assert exp.resolution_verdict(0.005) == "CUBRE"
    assert exp.resolution_verdict(0.00501) == "SIN_RESOLUCION"
    assert exp.resolution_verdict(float("nan")) == "SIN_RESOLUCION"


def _f(media, lo, hi):
    return {"media": media, "ic95": [lo, hi]}


def test_puerta_cruza_con_las_tres_condiciones():
    agg = {"media": 0.002, "ic95": [0.0005, 0.0035]}
    folds = [_f(0.003, 0.001, 0.005), _f(0.002, 0.0002, 0.004), _f(0.001, -0.001, 0.003),
             _f(0.001, -0.001, 0.003), _f(-0.001, -0.003, 0.001), _f(0.002, -0.001, 0.005)]
    v = exp.gate_verdict(agg, folds)
    assert v["cruza"] and v["ii_folds_positivos"] == 5 and v["iii_folds_ic_positivo"] == 2
    assert v["salida"].startswith("CRUZA < 0.005")


def test_puerta_falla_sin_dos_folds_con_ic_positivo():
    """El caso exacto de D-EXP-2b: agregado significativo, un solo fold que lo carga."""
    agg = {"media": 0.0013, "ic95": [0.00006, 0.0026]}
    folds = [_f(0.005, 0.0014, 0.0086)] + [_f(0.0005, -0.003, 0.004)] * 4 + \
            [_f(-0.0005, -0.004, 0.003)]
    v = exp.gate_verdict(agg, folds)
    assert not v["cruza"]
    assert v["iii_folds_ic_positivo"] == 1


def test_puerta_cruza_accionable_por_encima_de_0005():
    agg = {"media": 0.006, "ic95": [0.004, 0.008]}
    folds = [_f(0.006, 0.002, 0.010)] * 6
    assert exp.gate_verdict(agg, folds)["salida"].startswith("CRUZA >= 0.005")


def test_puerta_evalua_en_direccion_de_mejora_y_declara_la_otra_lectura():
    """Dos folds con IC fuera de cero pero NEGATIVOS: la lectura a dos colas
    contaria 'fuera de cero' y la de mejora no. Las dos se reportan."""
    agg = {"media": 0.001, "ic95": [0.0001, 0.002]}
    folds = [_f(-0.003, -0.005, -0.001)] * 2 + [_f(0.002, -0.001, 0.005)] * 4
    v = exp.gate_verdict(agg, folds)
    assert v["iii_folds_ic_positivo"] == 0
    assert v["folds_ic_fuera_de_cero_dos_colas"] == 2
    assert not v["cruza"]
    assert v["lectura_literal_dos_colas_cruzaria"] and v["lecturas_discrepan"]


def test_puerta_marca_leakage_por_encima_de_0015():
    agg = {"media": 0.02, "ic95": [0.015, 0.025]}
    assert exp.gate_verdict(agg, [_f(0.02, 0.01, 0.03)] * 6)["auditar_leakage"]


# --------------------------------------------------------------------------
# Folds y muestra
# --------------------------------------------------------------------------


def test_seis_folds_oficiales_2020_21_a_2025_26():
    assert [v for _, _, v in exp.FOLDS] == [
        "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]


def test_ningun_fold_valida_una_temporada_de_su_train():
    for _n, train, valid in exp.FOLDS:
        assert valid not in train
        assert train[0] == "2016-17"


def test_muestra_de_auditoria_determinista_y_del_tamano_pre_registrado():
    pares = [(f"g{i}", t) for i in range(200) for t in (1, 2)]
    a, b = exp.freeze_audit_sample(pares), exp.freeze_audit_sample(pares)
    assert a["pares"] == b["pares"] and len(a["pares"]) == 30


def test_describe_detecta_feature_degenerada():
    d = exp.describe(pd.Series([0.0] * 99 + [0.1]))
    assert d["frac_valor_modal"] == pytest.approx(0.99)
    assert d["distintos"] == 2
