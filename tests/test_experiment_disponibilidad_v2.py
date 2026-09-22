"""Tests de las funciones puras del experimento D-EXP-2.

El script es borrable sin residuo; estos tests cubren SOLO su logica pura
(tabla de pesos, disponibilidad ponderada, construccion del mapa de pesos desde
las filas del PDF, NYS por fecha). No tocan red, GCS ni BigQuery.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "experiment_disponibilidad_v2",
    Path(__file__).resolve().parents[1] / "scripts" / "experiment_disponibilidad_v2.py",
)
exp = importlib.util.module_from_spec(_SPEC)
sys.modules["experiment_disponibilidad_v2"] = exp
_SPEC.loader.exec_module(exp)


@dataclass
class FakeRow:
    game_date: str
    team: str
    player_name: str
    status: str
    reason: str | None = None


@dataclass
class FakeNys:
    game_date: str
    team: str


TEAMS = {"bostonceltics": 1, "miamiheat": 2, "losangelesclippers": 3}


class FakeIndex:
    """NameIndex mínimo: resuelve por diccionario, None si no está."""

    def __init__(self, mapping: dict[str, int]):
        self._m = mapping

    def match(self, pdf_name: str) -> int | None:
        return self._m.get(pdf_name)


# --------------------------------------------------------------------------
# weight_v2 — la tabla oficial de D-EXP-1
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,esperado",
    [("Out", 0.00), ("Doubtful", 0.02), ("Questionable", 0.48), ("Probable", 0.91)],
)
def test_weight_v2_pesos_oficiales(status, esperado):
    assert exp.weight_v2(status, "Injury/Illness") == esperado


def test_weight_v2_available_se_descompone_por_razon():
    """Adjudicación 2 de D-EXP-1: 'Available' mezcla poblaciones."""
    assert exp.weight_v2("Available", "G League - Two-Way") == exp.W_AVAILABLE_GLEAGUE
    assert exp.weight_v2("Available", "GLeague-OnAssignment") == exp.W_AVAILABLE_GLEAGUE
    assert exp.weight_v2("Available", "Injury/Illness-LeftKnee") == exp.W_AVAILABLE_OTRO
    assert exp.weight_v2("Available", None) == exp.W_AVAILABLE_OTRO


def test_weight_v2_estatus_desconocido_no_resta():
    assert exp.weight_v2("Inventado", "x") == exp.W_NO_LISTADO


def test_weight_binary_out_solo_out_resta():
    """Decisión 2 del feed: en v1 solo Out cuenta como ausencia."""
    assert exp.weight_binary_out("Out", "x") == 0.0
    for s in ("Doubtful", "Questionable", "Probable", "Available"):
        assert exp.weight_binary_out(s, "x") == 1.0


# --------------------------------------------------------------------------
# team_availability
# --------------------------------------------------------------------------


DENOM = {10: 30.0, 11: 20.0, 12: 50.0}   # 100 minutos de rotación


def test_team_availability_sin_pesos_es_uno():
    """Equipo sin nadie en el reporte: disponibilidad plena."""
    assert exp.team_availability(DENOM, {}) == 1.0


def test_team_availability_resta_proporcional_a_los_minutos():
    """Un Out de 30 de 100 minutos deja 0.70."""
    assert exp.team_availability(DENOM, {10: 0.0}) == pytest.approx(0.70)


def test_team_availability_pondera_en_vez_de_binarizar():
    """El caso que motiva v2: Questionable a 0.48 en vez de 1.0."""
    assert exp.team_availability(DENOM, {12: 0.48}) == pytest.approx(0.74)


def test_team_availability_jugador_fuera_de_la_rotacion_no_afecta():
    """Listado pero sin minutos recientes: no entra al numerador ni al denominador."""
    assert exp.team_availability(DENOM, {99: 0.0}) == 1.0


def test_team_availability_denominador_cero_es_nan():
    """Equipo sin historia: NaN, como el pipeline oficial."""
    import math
    assert math.isnan(exp.team_availability({}, {}))
    assert math.isnan(exp.team_availability({10: 0.0}, {}))


# --------------------------------------------------------------------------
# build_weight_maps
# --------------------------------------------------------------------------


def test_build_weight_maps_solo_la_fecha_objetivo():
    """El PDF es multi-fecha; las filas de mañana describen otro partido."""
    rows = [
        FakeRow("11/24/2023", "BostonCeltics", "Brown,Jaylen", "Out", "Injury"),
        FakeRow("11/25/2023", "BostonCeltics", "Tatum,Jayson", "Out", "Injury"),
    ]
    idx = FakeIndex({"Brown,Jaylen": 10, "Tatum,Jayson": 11})
    w2, wb, cont = exp.build_weight_maps(rows, "2023-11-24", TEAMS, idx)
    assert w2 == {1: {10: 0.0}}
    assert wb == {1: {10: 0.0}}
    assert cont["filas"] == 1


def test_build_weight_maps_separa_v2_de_binario():
    rows = [FakeRow("11/24/2023", "MiamiHeat", "Adebayo,Bam", "Questionable", "Injury")]
    idx = FakeIndex({"Adebayo,Bam": 20})
    w2, wb, _ = exp.build_weight_maps(rows, "2023-11-24", TEAMS, idx)
    assert w2[2][20] == 0.48
    assert wb[2][20] == 1.0


def test_build_weight_maps_sin_match_de_nombre_no_pondera_y_cuenta():
    """Mismo trato que v1 da a lo que no ve: peso 1.0, pero contado."""
    rows = [FakeRow("11/24/2023", "MiamiHeat", "Fantasma,Juan", "Out", "Injury")]
    w2, wb, cont = exp.build_weight_maps(rows, "2023-11-24", TEAMS, FakeIndex({}))
    assert w2 == {} and wb == {}
    assert cont["sin_match_nombre"] == 1


def test_build_weight_maps_sin_match_de_equipo_cuenta():
    rows = [FakeRow("11/24/2023", "EquipoInexistente", "Brown,Jaylen", "Out", None)]
    idx = FakeIndex({"Brown,Jaylen": 10})
    w2, _, cont = exp.build_weight_maps(rows, "2023-11-24", TEAMS, idx)
    assert w2 == {}
    assert cont["sin_match_equipo"] == 1


def test_build_weight_maps_token_sin_espacios_del_pdf():
    """El PDF escribe 'LAClippers'; el catálogo, 'Los Angeles Clippers'."""
    rows = [FakeRow("11/24/2023", "LAClippers", "Leonard,Kawhi", "Out", None)]
    idx = FakeIndex({"Leonard,Kawhi": 30})
    w2, _, _ = exp.build_weight_maps(rows, "2023-11-24", TEAMS, idx)
    assert w2 == {3: {30: 0.0}}


# --------------------------------------------------------------------------
# nys_team_ids — réplica de 13e-2.5 caso 3
# --------------------------------------------------------------------------


def test_nys_team_ids_filtra_por_fecha():
    """Un equipo puede tener reporte entregado hoy y NYS para mañana."""
    entradas = [FakeNys("11/24/2023", "MiamiHeat"), FakeNys("11/25/2023", "BostonCeltics")]
    assert exp.nys_team_ids(entradas, "2023-11-24", TEAMS) == {2}


def test_nys_team_ids_ignora_equipo_desconocido():
    assert exp.nys_team_ids([FakeNys("11/24/2023", "Xxx")], "2023-11-24", TEAMS) == set()


# --------------------------------------------------------------------------
# to_mdy / normalize_team
# --------------------------------------------------------------------------


def test_to_mdy():
    assert exp.to_mdy("2023-11-24") == "11/24/2023"


def test_normalize_team_conserva_digitos_y_aplica_alias():
    assert exp.normalize_team("Philadelphia76ers") == "philadelphia76ers"
    assert exp.normalize_team("LALakers") == exp.normalize_team("Los Angeles Lakers")


# --------------------------------------------------------------------------
# player_denominators — la adaptación de _compute_denominator
# --------------------------------------------------------------------------


def test_player_denominators_conserva_el_vector_y_suma_al_denominador_de_v1():
    """Invariante estructural: sum(vector) == denominador de availability.py."""
    import pandas as pd

    from nba_predictor.features.availability import _compute_denominator

    stats = pd.DataFrame([
        # equipo 1, tres partidos consecutivos, dos jugadores
        {"game_id": "G1", "team_id": 1, "player_id": 10, "game_date": "2023-11-01",
         "minutes_rolling": 30.0},
        {"game_id": "G1", "team_id": 1, "player_id": 11, "game_date": "2023-11-01",
         "minutes_rolling": 20.0},
        {"game_id": "G2", "team_id": 1, "player_id": 10, "game_date": "2023-11-03",
         "minutes_rolling": 31.0},
        {"game_id": "G2", "team_id": 1, "player_id": 11, "game_date": "2023-11-03",
         "minutes_rolling": 22.0},
        {"game_id": "G3", "team_id": 1, "player_id": 10, "game_date": "2023-11-05",
         "minutes_rolling": 33.0},
    ])
    vectores = exp.player_denominators(stats, window=10)
    oficial = _compute_denominator(stats, window=10).set_index(["game_id", "team_id"])

    for (gid, tid), vec in vectores.items():
        assert sum(vec.values()) == pytest.approx(
            float(oficial.loc[(gid, tid), "denominator"])
        )
    # G1 es el primer partido del equipo: nadie tiene historia previa.
    assert vectores[("G1", 1)] == {}
    # G3 arrastra los valores de G2 vía shift(1).
    assert vectores[("G3", 1)] == {10: 31.0, 11: 22.0}


# --------------------------------------------------------------------------
# paired_diff
# --------------------------------------------------------------------------


def test_paired_diff_signo_positivo_cuando_b_es_mejor():
    """d = LL(A) - LL(B): positivo significa que B predice mejor."""
    y = [1, 1, 0, 0]
    peor = [0.5, 0.5, 0.5, 0.5]
    mejor = [0.9, 0.9, 0.1, 0.1]
    r = exp.paired_diff(y, peor, mejor)
    assert r["media"] > 0
    assert r["gana_b_en_partidos"] == 4
    assert r["n"] == 4


def test_paired_diff_identicos_da_cero_exacto():
    p = [0.6, 0.4, 0.7]
    r = exp.paired_diff([1, 0, 1], p, p)
    assert r["media"] == 0.0
    assert r["gana_b_en_partidos"] == 0


def test_paired_diff_ic_contiene_la_media():
    import random
    random.seed(7)
    y = [random.randint(0, 1) for _ in range(200)]
    a = [random.uniform(0.2, 0.8) for _ in range(200)]
    b = [random.uniform(0.2, 0.8) for _ in range(200)]
    r = exp.paired_diff(y, a, b)
    assert r["ic95"][0] < r["media"] < r["ic95"][1]
