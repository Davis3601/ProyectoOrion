"""Tests de las funciones puras del experimento D-EXP-1.

El script es borrable sin residuo; estos tests cubren SOLO su logica pura
(clasificacion, filtrado multi-fecha, normalizacion de equipos, agregacion).
No tocan red, GCS ni BigQuery.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "experiment_pjuega",
    Path(__file__).resolve().parents[1] / "scripts" / "experiment_pjuega.py",
)
exp = importlib.util.module_from_spec(_SPEC)
sys.modules["experiment_pjuega"] = exp
_SPEC.loader.exec_module(exp)


@dataclass
class FakeRow:
    game_date: str
    team: str
    player_name: str
    status: str


# --------------------------------------------------------------------------
# season_prefix / to_mdy
# --------------------------------------------------------------------------


def test_season_prefix():
    assert exp.season_prefix("2023-24") == "00223"
    assert exp.season_prefix("2025-26") == "00225"


def test_to_mdy_usa_formato_del_pdf():
    assert exp.to_mdy("2023-11-24") == "11/24/2023"
    assert exp.to_mdy("2025-01-05") == "01/05/2025"


# --------------------------------------------------------------------------
# normalize_team
# --------------------------------------------------------------------------


def test_normalize_team_token_sin_espacios_coincide_con_catalogo():
    assert exp.normalize_team("BostonCeltics") == exp.normalize_team("Boston Celtics")


def test_normalize_team_conserva_digitos():
    """El normalizador de nombres de persona borra el '76'; este NO debe."""
    assert exp.normalize_team("Philadelphia 76ers") == "philadelphia76ers"
    assert exp.normalize_team("Philadelphia76ers") == "philadelphia76ers"


def test_normalize_team_alias_la():
    assert exp.normalize_team("LAClippers") == exp.normalize_team("Los Angeles Clippers")
    assert exp.normalize_team("LALakers") == exp.normalize_team("Los Angeles Lakers")


# --------------------------------------------------------------------------
# filter_rows_for_date — el PDF es multi-fecha (hallazgo 2026-08-22)
# --------------------------------------------------------------------------


def test_filter_rows_descarta_las_del_dia_siguiente():
    rows = [
        FakeRow("11/24/2023", "BostonCeltics", "Brown,Jaylen", "Out"),
        FakeRow("11/25/2023", "BostonCeltics", "Tatum,Jayson", "Questionable"),
    ]
    quedan = exp.filter_rows_for_date(rows, "2023-11-24")
    assert [r.player_name for r in quedan] == ["Brown,Jaylen"]


def test_filter_rows_sin_coincidencias_devuelve_vacio():
    rows = [FakeRow("11/25/2023", "MiamiHeat", "Adebayo,Bam", "Probable")]
    assert exp.filter_rows_for_date(rows, "2023-11-24") == []


# --------------------------------------------------------------------------
# game_id_for_team
# --------------------------------------------------------------------------


JUEGOS = [("0022300100", 1, 2), ("0022300101", 3, 4)]


def test_game_id_for_team_local_y_visitante():
    assert exp.game_id_for_team(JUEGOS, 1) == "0022300100"
    assert exp.game_id_for_team(JUEGOS, 2) == "0022300100"
    assert exp.game_id_for_team(JUEGOS, 4) == "0022300101"


def test_game_id_for_team_sin_partido_o_sin_equipo():
    assert exp.game_id_for_team(JUEGOS, 99) is None
    assert exp.game_id_for_team(JUEGOS, None) is None


# --------------------------------------------------------------------------
# classify_instance
# --------------------------------------------------------------------------


MINUTOS = {
    ("G1", 10): 31.5,   # jugo
    ("G1", 11): 0.0,    # activado pero sin minutos (DNP)
    ("G1", 12): None,   # activado, minutos nulos
}


def test_classify_jugo_primaria_y_secundaria():
    assert exp.classify_instance(1, 10, "G1", MINUTOS) == ("incluida", True, True)


def test_classify_dnp_activado_pero_no_jugo():
    """La divergencia primaria/secundaria es en si misma un dato."""
    assert exp.classify_instance(1, 11, "G1", MINUTOS) == ("incluida", False, True)
    assert exp.classify_instance(1, 12, "G1", MINUTOS) == ("incluida", False, True)


def test_classify_ausente_del_boxscore_es_no_jugo_no_exclusion():
    """No activado: el partido existe y el jugador estaba en el reporte."""
    assert exp.classify_instance(1, 99, "G1", MINUTOS) == ("incluida", False, False)


@pytest.mark.parametrize(
    "tid,pid,gid,esperado",
    [
        (None, 10, "G1", "sin_match_equipo"),
        (1, None, "G1", "sin_match_nombre"),
        (1, 10, None, "sin_partido"),
    ],
)
def test_classify_exclusiones_en_orden(tid, pid, gid, esperado):
    veredicto, p1, p2 = exp.classify_instance(tid, pid, gid, MINUTOS)
    assert veredicto == esperado
    assert (p1, p2) == (False, False)


# --------------------------------------------------------------------------
# summarize
# --------------------------------------------------------------------------


def _inst(status, veredicto="incluida", p1=False, p2=False, cut="publish"):
    return {
        "season": "2023-24", "cut": cut, "status": status,
        "veredicto": veredicto, "jugo_primaria": p1, "jugo_secundaria": p2,
    }


def test_summarize_calcula_probabilidades():
    res = exp.summarize([
        _inst("Questionable", p1=True, p2=True),
        _inst("Questionable", p1=False, p2=True),
        _inst("Out"),
    ])
    q = res["por_temporada"]["2023-24"]["publish"]["Questionable"]
    assert q["instancias"] == 2
    assert q["p_juega_primaria"] == 0.5
    assert q["p_juega_secundaria"] == 1.0
    assert res["por_temporada"]["2023-24"]["publish"]["Out"]["p_juega_primaria"] == 0.0


def test_summarize_excluidas_no_entran_al_denominador():
    res = exp.summarize([
        _inst("Questionable", p1=True, p2=True),
        _inst("Questionable", veredicto="sin_match_nombre"),
        _inst("Questionable", veredicto="sin_partido"),
    ])
    q = res["por_temporada"]["2023-24"]["publish"]["Questionable"]
    assert q["instancias"] == 1
    assert q["p_juega_primaria"] == 1.0
    assert res["exclusiones"]["2023-24"]["publish"] == {
        "sin_match_nombre": 1, "sin_partido": 1,
    }


def test_summarize_separa_cortes():
    res = exp.summarize([
        _inst("Probable", p1=True, p2=True, cut="publish"),
        _inst("Probable", p1=False, p2=False, cut="late"),
    ])
    temporada = res["por_temporada"]["2023-24"]
    assert temporada["publish"]["Probable"]["p_juega_primaria"] == 1.0
    assert temporada["late"]["Probable"]["p_juega_primaria"] == 0.0


def test_render_table_no_revienta_con_resumen_real():
    res = exp.summarize([_inst("Out"), _inst("Probable", veredicto="sin_partido")])
    tabla = exp.render_table(res)
    assert "2023-24" in tabla and "Out" in tabla and "sin_partido=1" in tabla


# --------------------------------------------------------------------------
# render_audit — el listado que lee Antonio
# --------------------------------------------------------------------------


def _inst_full(**kw):
    base = {
        "season": "2023-24", "cut": "publish", "date": "2023-11-24",
        "suffix": "01PM", "team": "BostonCeltics", "player_name": "Brown,Jaylen",
        "status": "Questionable", "reason": "Left knee soreness", "team_id": 1,
        "player_id": 10, "game_id": "G1", "minutes": 31.5,
        "veredicto": "incluida", "jugo_primaria": True, "jugo_secundaria": True,
    }
    base.update(kw)
    return base


def test_render_audit_lista_cada_instancia_de_la_fecha():
    txt = exp.render_audit(
        [
            _inst_full(),
            _inst_full(player_name="Tatum,Jayson", status="Out", minutes=None,
                       jugo_primaria=False, jugo_secundaria=False),
            _inst_full(date="2023-11-25", player_name="Otro,Dia"),
        ],
        "2023-11-24",
    )
    assert "Brown,Jaylen" in txt
    assert "Tatum,Jayson" in txt
    assert "Otro,Dia" not in txt          # otra fecha, no entra
    assert "Left knee soreness" in txt    # la razon del PDF viaja al listado
    assert "2023-11-24_01PM.pdf" in txt   # el documento fuente queda nombrado


def test_render_audit_declara_corte_sin_pdf():
    txt = exp.render_audit([_inst_full()], "2023-11-24")
    assert "corte late: sin PDF archivado" in txt
