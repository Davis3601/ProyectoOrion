"""Unit tests de las funciones puras de D-EXP-2b (re-medicion disponibilidad-v2).

Guardas de REGRESION. Lo que fijan es lo que puede equivocarse en silencio y
producir una tabla verosimil y falsa: el pooleo de pesos por fold (si se colara
una temporada de validacion, el walk-forward estaria roto y la tabla se veria
igual de sana), la aritmetica del MDE, el gate de resolucion y la particion de
Available.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "experiment_disponibilidad_v2b",
    Path(__file__).resolve().parents[1] / "scripts" / "experiment_disponibilidad_v2b.py",
)
exp = importlib.util.module_from_spec(_SPEC)
sys.modules["experiment_disponibilidad_v2b"] = exp
_SPEC.loader.exec_module(exp)


# --------------------------------------------------------------------------
# FOLDS — walk-forward acumulado, 2018-19 solo como train
# --------------------------------------------------------------------------


def test_son_siete_folds_uno_por_temporada_validable():
    assert len(exp.FOLDS) == 7


def test_ninguna_temporada_de_validacion_aparece_en_su_propio_train():
    """El corazon del walk-forward. Si esto falla, todo lo demas es ruido."""
    for _nombre, train, valid in exp.FOLDS:
        assert valid not in train


def test_el_train_es_acumulado_y_contiguo_desde_2018_19():
    for _nombre, train, _valid in exp.FOLDS:
        assert train[0] == "2018-19"
        assert list(train) == list(exp.SEASONS[: len(train)])


def test_2018_19_nunca_se_valida():
    """Su corpus empieza el 2018-12-18: validar sobre una temporada truncada
    mediria el hueco del archivo, no el modelo."""
    assert "2018-19" not in [v for _n, _t, v in exp.FOLDS]


def test_el_ultimo_fold_valida_la_temporada_mas_reciente():
    assert exp.FOLDS[-1][2] == "2025-26"


# --------------------------------------------------------------------------
# status_key — particion de Available con el clasificador portable
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status, category, reason, esperado",
    [
        ("Out", None, "Left knee", "Out"),
        ("Doubtful", None, "-", "Doubtful"),
        ("Questionable", None, "Ankle", "Questionable"),
        ("Probable", None, "Rest", "Probable"),
        # Available se parte; la category manda cuando el layout la trae (V1)
        ("Available", "G League Team", "-", "Available::gleague_o_twoway"),
        ("Available", "Injury/Illness", "Left knee", "Available::resto"),
        # sin category (V2 y GemBox) decide la razon
        ("Available", None, "G League - On Assignment", "Available::gleague_o_twoway"),
        ("Available", None, "Left ankle", "Available::resto"),
    ],
)
def test_status_key(status, category, reason, esperado):
    assert exp.status_key(status, category, reason) == esperado


def test_solo_available_se_parte():
    """Los otros cuatro estatus tienen peso unico: partirlos inventaria celdas."""
    for st in ("Out", "Doubtful", "Questionable", "Probable"):
        assert "::" not in exp.status_key(st, "G League Team", "G League")


# --------------------------------------------------------------------------
# season_counts — de las instancias ya auditadas
# --------------------------------------------------------------------------


def _inst(season, status, jugo, cut="publish", veredicto="incluida",
          category=None, reason="-"):
    return {"season": season, "status": status, "jugo_primaria": jugo,
            "cut": cut, "veredicto": veredicto, "category": category, "reason": reason}


def test_season_counts_agrupa_por_temporada_y_clave():
    inst = [
        _inst("2018-19", "Out", False), _inst("2018-19", "Out", False),
        _inst("2018-19", "Questionable", True),
        _inst("2019-20", "Questionable", False),
    ]
    c = exp.season_counts(inst)
    assert c["2018-19"]["Out"] == {"n": 2, "jugo": 0}
    assert c["2018-19"]["Questionable"] == {"n": 1, "jugo": 1}
    assert c["2019-20"]["Questionable"] == {"n": 1, "jugo": 0}


def test_season_counts_ignora_el_corte_late():
    """El late quedo INVALIDADO como pronostico en D-EXP-1 (adjudicacion 3):
    es retrospectiva y contaria quien ya se sabia que jugo."""
    c = exp.season_counts([_inst("2018-19", "Out", True, cut="late")])
    assert c == {}


def test_season_counts_ignora_las_instancias_excluidas():
    c = exp.season_counts([_inst("2018-19", "Out", True, veredicto="sin_partido")])
    assert c == {}


def test_season_counts_ignora_estatus_desconocidos():
    c = exp.season_counts([_inst("2018-19", "Inventado", True)])
    assert c == {}


# --------------------------------------------------------------------------
# weights_from_train — walk-forward PURO (el cuarto requisito de D-RES-3e)
# --------------------------------------------------------------------------


def test_los_pesos_solo_miran_las_temporadas_de_train():
    """Si una temporada de validacion se colara, habria leakage invisible: la
    tabla se veria igual de sana y el resultado seria inservible."""
    counts = {
        "2018-19": {"Questionable": {"n": 100, "jugo": 50}},
        "2019-20": {"Questionable": {"n": 100, "jugo": 100}},   # NO debe entrar
    }
    pesos, _ns = exp.weights_from_train(counts, ("2018-19",))
    assert pesos["Questionable"] == pytest.approx(0.50)


def test_los_pesos_poolean_las_temporadas_de_train_no_las_promedian():
    """n muy distintos (2018-19 entra parcial): promediar le daria el mismo
    voto a una celda de 10 y a una de 990."""
    counts = {
        "2018-19": {"Questionable": {"n": 10, "jugo": 1}},      # 0.10
        "2019-20": {"Questionable": {"n": 990, "jugo": 891}},   # 0.90
    }
    pesos, ns = exp.weights_from_train(counts, ("2018-19", "2019-20"))
    assert pesos["Questionable"] == pytest.approx(892 / 1000)   # pooleado
    assert pesos["Questionable"] != pytest.approx(0.50)         # no promediado
    assert ns["Questionable"] == 1000


def test_los_pesos_no_se_redondean():
    """Redondear a 2 decimales (eleccion presentacional de D-EXP-1) inyectaria
    un escalon artificial que la comparacion entre folds leeria como señal."""
    counts = {"2018-19": {"Questionable": {"n": 1000, "jugo": 487}}}
    pesos, _ = exp.weights_from_train(counts, ("2018-19",))
    assert pesos["Questionable"] == pytest.approx(0.487)
    assert pesos["Questionable"] != pytest.approx(0.49)


def test_clave_sin_instancias_en_el_train_no_recibe_peso():
    counts = {"2018-19": {"Out": {"n": 5, "jugo": 0}}}
    pesos, _ = exp.weights_from_train(counts, ("2018-19",))
    assert "Probable" not in pesos
    assert exp.weight_of("Probable", pesos) == 1.0   # cae a no-listado


def test_train_vacio_no_da_pesos():
    pesos, _ = exp.weights_from_train({}, ("2018-19",))
    assert pesos == {}


# --------------------------------------------------------------------------
# weight_of / weight_binary
# --------------------------------------------------------------------------


def test_weight_of_usa_la_tabla_y_cae_a_uno():
    pesos = {"Out": 0.0, "Questionable": 0.48}
    assert exp.weight_of("Out", pesos) == 0.0
    assert exp.weight_of("Questionable", pesos) == 0.48
    assert exp.weight_of("Available::resto", pesos) == 1.0


def test_weight_binary_solo_resta_out():
    """Decision 2 del feed: en v1 solo Out resta disponibilidad."""
    assert exp.weight_binary("Out") == 0.0
    for k in ("Doubtful", "Questionable", "Probable",
              "Available::resto", "Available::gleague_o_twoway"):
        assert exp.weight_binary(k) == 1.0


def test_weight_binary_no_depende_del_fold():
    """Por eso BIN se computa UNA vez: es el brazo de misma construccion que V2
    y su unica diferencia es la tabla de pesos."""
    import inspect
    assert "pesos" not in inspect.signature(exp.weight_binary).parameters


# --------------------------------------------------------------------------
# mde_pareado + gate de resolucion
# --------------------------------------------------------------------------


def test_mde_es_el_multiplicador_por_sd_sobre_raiz_de_n():
    assert exp.mde_pareado(0.108, 6750) == pytest.approx(
        2.8016 * 0.108 / math.sqrt(6750), rel=1e-9)


def test_el_mde_baja_con_la_raiz_de_n():
    """Cuadruplicar n lo parte a la mitad: es lo que compra el corpus ampliado."""
    assert exp.mde_pareado(0.1, 4000) == pytest.approx(exp.mde_pareado(0.1, 1000) / 2)


def test_el_mde_de_d_exp_2_no_habria_pasado_el_gate():
    """Reconstruccion del caso que motivo esta tarea: sd ~0.108 con n=989 da un
    MDE de ~0.0096, el numero que dejo a D-EXP-2 sin poder interpretar su null."""
    mde = exp.mde_pareado(0.108, 989)
    assert mde == pytest.approx(0.0096, abs=0.0005)
    assert exp.resolution_verdict(mde) == "SIN_RESOLUCION"


def test_el_gate_pasa_cuando_el_mde_cabe_bajo_el_techo():
    assert exp.resolution_verdict(0.008) == "CUBRE"
    assert exp.resolution_verdict(0.0080001) == "SIN_RESOLUCION"


def test_el_techo_del_gate_es_el_del_pre_registro():
    assert exp.MDE_TECHO == 0.008


def test_mde_con_n_o_sd_degenerados_no_pasa_el_gate():
    for mde in (exp.mde_pareado(0.1, 1), exp.mde_pareado(0.0, 1000)):
        assert exp.resolution_verdict(mde) == "SIN_RESOLUCION"


# --------------------------------------------------------------------------
# weight_stability — el diagnostico que D-EXP-5 predice
# --------------------------------------------------------------------------


def test_weight_stability_marca_lo_que_sale_de_tolerancia():
    pesos = {"F0": {"Questionable": 0.48, "Probable": 0.80},   # Probable -11pp
             "F1": {"Questionable": 0.50}}                     # +2pp, dentro
    s = exp.weight_stability(pesos)
    fuera = {(f["fold"], f["clave"]) for f in s["fuera_de_tolerancia"]}
    assert fuera == {("F0", "Probable")}


def test_weight_stability_ignora_claves_que_el_fold_no_tiene():
    s = exp.weight_stability({"F0": {"Questionable": 0.48}})
    assert [f["clave"] for f in s["filas"]] == ["Questionable"]


def test_weight_stability_usa_la_tolerancia_pre_registrada():
    assert exp.weight_stability({"F0": {}})["tolerancia_pp"] == 5.0


# --------------------------------------------------------------------------
# freeze_audit_games — congelada antes de medir
# --------------------------------------------------------------------------


def test_la_muestra_de_auditoria_es_determinista():
    gids = [f"00219{i:05d}" for i in range(500)]
    a = exp.freeze_audit_games(gids)
    b = exp.freeze_audit_games(gids)
    assert a["game_ids"] == b["game_ids"]


def test_la_muestra_tiene_el_tamano_pre_registrado():
    gids = [f"00219{i:05d}" for i in range(500)]
    assert len(exp.freeze_audit_games(gids)["game_ids"]) == exp.AUDIT_N == 30


def test_la_muestra_no_inventa_partidos_si_el_universo_es_menor():
    a = exp.freeze_audit_games(["a", "b", "c"])
    assert a["game_ids"] == ["a", "b", "c"]


def test_la_muestra_registra_el_universo_del_que_salio():
    gids = [f"g{i}" for i in range(77)]
    assert exp.freeze_audit_games(gids)["n_universo"] == 77


# --------------------------------------------------------------------------
# Reutilizacion por import: una sola verdad de la aritmetica
# --------------------------------------------------------------------------


def test_la_aritmetica_de_disponibilidad_viene_importada_de_d_exp_2():
    """team_availability, fold_metrics y paired_diff no se reimplementan aqui:
    ya fueron auditadas en D-EXP-2 y una segunda copia podria divergir."""
    assert exp.dexp2.team_availability is not None
    assert exp.dexp2.fold_metrics is not None
    assert exp.dexp2.paired_diff is not None
    assert not hasattr(exp, "team_availability")


def test_el_corpus_arranca_en_el_borde_de_retencion():
    assert exp.CORPUS_START == "2018-12-18"
