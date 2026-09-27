"""Unit tests de las funciones puras de D-EXP-5 (P(juega|estatus), era legacy).

Guardas de REGRESION, no prueba de correccion: la correccion de los agregados
la establece la auditoria humana de los listados contra el PDF y el boxscore
(protocolo 13e-1). Lo que estos tests fijan es la aritmetica (Wilson, Wald),
la estratificacion por era, el pooleo y la descomposicion de Available — todo
lo que puede equivocarse en silencio y producir una tabla verosimil y falsa.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "experiment_pjuega_legacy",
    Path(__file__).resolve().parents[1] / "scripts" / "experiment_pjuega_legacy.py",
)
exp = importlib.util.module_from_spec(_SPEC)
sys.modules["experiment_pjuega_legacy"] = exp
_SPEC.loader.exec_module(exp)


# --------------------------------------------------------------------------
# era_expected — frontera de layouts de D-RES-3c
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fecha, esperado",
    [
        ("2018-12-18", "ITEXT_V1"),   # primera fecha con archivo regular
        ("2019-01-15", "ITEXT_V1"),   # fecha auditada en D-RES-3
        ("2019-11-14", "ITEXT_V1"),   # ultimo dia V1 (biseccion D-RES-3c)
        ("2019-11-15", "tramo_desconocido"),  # primer dia del tramo sin lista blanca
        ("2019-12-31", "tramo_desconocido"),
        ("2020-01-06", "tramo_desconocido"),  # vispera del primer V2 conocido
        ("2020-01-07", "ITEXT_V2"),   # primer dia V2 conocido
        ("2021-02-10", "ITEXT_V2"),   # fecha auditada en D-RES-3
        ("2023-04-09", "ITEXT_V2"),   # ultimo dia iTextSharp del corpus
    ],
)
def test_era_expected_respeta_la_frontera_medida(fecha, esperado):
    assert exp.era_expected(fecha) == esperado


def test_la_frontera_es_contigua_y_el_tramo_queda_en_medio():
    """V1 y V2 no se solapan y el tramo sin adjudicar vive entre ambos."""
    assert exp.V1_LAST < exp.V2_FIRST
    assert exp.era_expected(exp.V1_LAST) == "ITEXT_V1"
    assert exp.era_expected(exp.V2_FIRST) == "ITEXT_V2"


# --------------------------------------------------------------------------
# freeze_audit_sample — la muestra se congela, no se elige despues
# --------------------------------------------------------------------------


def test_freeze_audit_sample_es_determinista_con_el_mismo_seed():
    fechas = ["2019-01-05", "2019-02-20", "2020-03-01", "2021-11-11", "2019-12-01"]
    a = exp.freeze_audit_sample(fechas, seed=42)
    b = exp.freeze_audit_sample(fechas, seed=42)
    assert a["elegidas_antes_de_computar"] == b["elegidas_antes_de_computar"]


def test_freeze_audit_sample_elige_una_fecha_de_cada_era_auditada():
    """Una de V1 y una de V2: la muestra debe ejercer AMBOS caminos del parser."""
    fechas = ["2019-01-05", "2019-02-20", "2020-03-01", "2021-11-11"]
    m = exp.freeze_audit_sample(fechas, seed=42)
    elegidas = m["elegidas_antes_de_computar"]
    assert set(elegidas) == {"ITEXT_V1", "ITEXT_V2"}
    assert exp.era_expected(elegidas["ITEXT_V1"]) == "ITEXT_V1"
    assert exp.era_expected(elegidas["ITEXT_V2"]) == "ITEXT_V2"


def test_freeze_audit_sample_jamas_elige_del_tramo_sin_lista_blanca():
    """Esas fechas levantan UnknownLayoutError: auditarlas no tendria listado."""
    fechas = ["2019-11-20", "2019-12-05", "2019-12-30"]
    m = exp.freeze_audit_sample(fechas, seed=42)
    assert m["elegidas_antes_de_computar"] == {}
    assert m["candidatas_por_era"] == {"tramo_desconocido": 3}


def test_freeze_audit_sample_cuenta_las_candidatas_por_era():
    fechas = ["2019-01-05", "2019-01-06", "2020-03-01", "2019-12-01"]
    m = exp.freeze_audit_sample(fechas, seed=42)
    assert m["candidatas_por_era"] == {
        "ITEXT_V1": 2, "ITEXT_V2": 1, "tramo_desconocido": 1,
    }


# --------------------------------------------------------------------------
# wilson_ci — por que Wilson y no Wald en las celdas chicas
# --------------------------------------------------------------------------


def test_wilson_ci_no_tiene_anchura_cero_cuando_nadie_jugo():
    """El caso Out: k=0 con n grande. Wald daria [0,0], que es falso."""
    lo, hi = exp.wilson_ci(0, 100)
    assert lo == 0.0
    assert hi > 0.0


def test_wilson_ci_no_se_sale_del_intervalo_unitario():
    for k, n in [(0, 5), (5, 5), (1, 3), (99, 100)]:
        lo, hi = exp.wilson_ci(k, n)
        assert 0.0 <= lo <= hi <= 1.0


def test_wilson_ci_contiene_la_proporcion_observada():
    lo, hi = exp.wilson_ci(48, 100)
    assert lo < 0.48 < hi


def test_wilson_ci_se_estrecha_al_crecer_n():
    ancho_chico = (lambda c: c[1] - c[0])(exp.wilson_ci(5, 10))
    ancho_grande = (lambda c: c[1] - c[0])(exp.wilson_ci(500, 1000))
    assert ancho_grande < ancho_chico


def test_wilson_ci_con_n_cero_es_none():
    assert exp.wilson_ci(0, 0) is None


# --------------------------------------------------------------------------
# diff_ci — la comparacion legacy vs GemBox
# --------------------------------------------------------------------------


def test_diff_ci_signo_y_magnitud():
    d = exp.diff_ci(60, 100, 50, 100)
    assert d["p_legacy"] == 0.6
    assert d["p_gembox"] == 0.5
    assert d["diff"] == pytest.approx(0.10)
    assert d["diff_pp"] == pytest.approx(10.0)


def test_diff_ci_de_proporciones_iguales_contiene_el_cero():
    d = exp.diff_ci(500, 1000, 500, 1000)
    assert d["diff"] == 0.0
    assert d["ic95"][0] < 0 < d["ic95"][1]


def test_diff_ci_con_n_grandes_excluye_el_cero_ante_diferencia_real():
    """13pp con miles de instancias por lado: el IC no debe abrazar el cero."""
    d = exp.diff_ci(770, 1000, 900, 1000)
    assert d["ic95"][1] < 0


def test_diff_ci_con_n_cero_es_none():
    assert exp.diff_ci(0, 0, 5, 10) is None
    assert exp.diff_ci(5, 10, 0, 0) is None


# --------------------------------------------------------------------------
# pool_por_estatus — poolear, no promediar
# --------------------------------------------------------------------------


def _resumen(cells: dict) -> dict:
    """Arma un resumen con la forma que emite dexp1.summarize()."""
    return {
        season: {
            exp.CUT: {
                st: {"instancias": n, "jugo_primaria": k, "jugo_secundaria": k}
                for st, (n, k) in filas.items()
            }
        }
        for season, filas in cells.items()
    }


def test_pool_por_estatus_suma_las_temporadas():
    r = _resumen({
        "2018-19": {"Questionable": (100, 40)},
        "2019-20": {"Questionable": (200, 120)},
    })
    pool = exp.pool_por_estatus(r)
    assert pool["Questionable"] == {"n": 300, "jugo": 160}


def test_pool_por_estatus_no_promedia_proporciones():
    """La celda de n=900 debe pesar nueve veces mas que la de n=100.

    Promediar 0.10 y 0.90 daria 0.50; poolear da 0.82. Se fija por numero
    porque es el error que haria comparables dos tablas que no lo son.
    """
    r = _resumen({
        "2018-19": {"Questionable": (100, 10)},
        "2019-20": {"Questionable": (900, 810)},
    })
    pool = exp.pool_por_estatus(r)
    assert pool["Questionable"]["jugo"] / pool["Questionable"]["n"] == pytest.approx(0.82)


def test_pool_por_estatus_ignora_el_corte_que_no_se_mide():
    """El corte late quedo invalidado en D-EXP-1: no puede colarse al pooleo."""
    r = {"2018-19": {"publish": {"Out": {"instancias": 10, "jugo_primaria": 0,
                                        "jugo_secundaria": 0}},
                     "late": {"Out": {"instancias": 99, "jugo_primaria": 99,
                                      "jugo_secundaria": 99}}}}
    pool = exp.pool_por_estatus(r, cut="publish")
    assert pool["Out"] == {"n": 10, "jugo": 0}


def test_pool_por_estatus_omite_los_estatus_sin_instancias():
    pool = exp.pool_por_estatus(_resumen({"2018-19": {"Out": (5, 0)}}))
    assert set(pool) == {"Out"}


# --------------------------------------------------------------------------
# available_breakdown — categoria explicita vs inferencia por razon
# --------------------------------------------------------------------------


def _inst(status="Available", reason="", category=None, jugo=True, veredicto="incluida"):
    return {"status": status, "reason": reason, "category": category,
            "jugo_primaria": jugo, "veredicto": veredicto}


def test_available_breakdown_separa_gleague_de_resto_por_razon():
    inst = [
        _inst(reason="G League - On Assignment", jugo=False),
        _inst(reason="G League - Two-Way", jugo=False),
        _inst(reason="Injury/Illness - Left knee", jugo=True),
        _inst(reason="Injury/Illness - Rest", jugo=True),
    ]
    b = exp.available_breakdown(inst)
    assert b["por_razon_inferida"]["gleague_o_twoway"] == {"n": 2, "jugo": 0, "p_juega": 0.0}
    assert b["por_razon_inferida"]["resto"] == {"n": 2, "jugo": 2, "p_juega": 1.0}


def test_available_breakdown_usa_la_categoria_explicita_cuando_existe():
    """La columna Category solo la trae ITEXT_V1; ahi la etiqueta es del documento."""
    inst = [
        _inst(category="G League Team", reason="On Assignment", jugo=False),
        _inst(category="Injury/Illness", reason="Left ankle", jugo=True),
        _inst(category="Injury/Illness", reason="Right knee", jugo=True),
    ]
    b = exp.available_breakdown(inst)
    assert b["por_categoria_explicita"]["Injury/Illness"]["n"] == 2
    assert b["por_categoria_explicita"]["G League Team"]["p_juega"] == 0.0


def test_available_breakdown_mide_la_concordancia_de_los_dos_metodos():
    """La validacion que GemBox no podia dar: Category contra la inferencia.

    Fila 1: ambos dicen G-League -> coinciden.
    Fila 2: la categoria dice G League Team pero la razon no lo menciona ->
    discrepan, y ESA es la medida de cuanto se equivoca la inferencia de
    D-EXP-1 en la era donde hay etiqueta contra la que compararla.
    """
    inst = [
        _inst(category="G League Team", reason="G League - On Assignment"),
        _inst(category="G League Team", reason="On Assignment"),
        _inst(category="Injury/Illness", reason="Left knee soreness"),
    ]
    b = exp.available_breakdown(inst)
    assert b["concordancia_categoria_vs_razon"] == {"coinciden": 2, "discrepan": 1}


def test_available_breakdown_ignora_lo_no_incluido_y_lo_que_no_es_available():
    inst = [
        _inst(status="Out", reason="G League"),
        _inst(veredicto="sin_match_nombre", reason="Injury"),
        _inst(reason="Injury/Illness"),
    ]
    b = exp.available_breakdown(inst)
    assert sum(v["n"] for v in b["por_razon_inferida"].values()) == 1


def test_available_breakdown_sin_available_no_revienta():
    b = exp.available_breakdown([_inst(status="Out")])
    assert b["por_razon_inferida"] == {}
    assert b["por_categoria_explicita"] == {}
    assert b["concordancia_categoria_vs_razon"] == {}


# --------------------------------------------------------------------------
# divergencias — el gate que dispara el diagnostico de instrumento
# --------------------------------------------------------------------------


def test_divergencias_detecta_la_que_supera_el_umbral():
    legacy = {"Questionable": {"n": 1000, "jugo": 600},
              "Probable": {"n": 1000, "jugo": 905}}
    gembox = {"Questionable": {"n": 1000, "jugo": 480},
              "Probable": {"n": 1000, "jugo": 908}}
    assert exp.divergencias(legacy, gembox, umbral_pp=5.0) == ["Questionable"]


def test_divergencias_vacia_cuando_todo_esta_dentro_del_umbral():
    legacy = {"Out": {"n": 1000, "jugo": 3}}
    gembox = {"Out": {"n": 1000, "jugo": 4}}
    assert exp.divergencias(legacy, gembox) == []


def test_divergencias_ignora_los_estatus_que_falten_en_un_lado():
    legacy = {"Available": {"n": 10, "jugo": 10}}
    gembox = {"Out": {"n": 10, "jugo": 0}}
    assert exp.divergencias(legacy, gembox) == []


def test_divergencias_respeta_el_umbral_pre_registrado_por_defecto():
    """El 5pp del pre-registro es el default, no un parametro que se afloje."""
    legacy = {"Doubtful": {"n": 1000, "jugo": 80}}   # 8.0%
    gembox = {"Doubtful": {"n": 1000, "jugo": 20}}   # 2.0% -> 6pp de diferencia
    assert exp.divergencias(legacy, gembox) == ["Doubtful"]
    assert exp.divergencias(legacy, gembox, umbral_pp=10.0) == []


# --------------------------------------------------------------------------
# Definiciones compartidas — la comparabilidad no es por copia
# --------------------------------------------------------------------------


def test_las_definiciones_vienen_importadas_de_d_exp_1():
    """Si alguien reimplementa una definicion aqui, las tablas dejan de ser
    comparables y este test lo delata."""
    assert exp.STATUSES is exp.dexp1.STATUSES
    assert exp.dexp1.filter_rows_for_date is not None
    assert exp.dexp1.classify_instance is not None
    assert exp.dexp2._GLEAGUE_RE is not None


def test_el_corte_medido_es_solo_publish():
    """El late quedo invalidado como pronostico (D-EXP-1, adjudicacion 3)."""
    assert exp.CUT == "publish"


def test_las_temporadas_son_las_cinco_de_itextsharp():
    assert exp.SEASONS == ("2018-19", "2019-20", "2020-21", "2021-22", "2022-23")
    assert "2023-24" not in exp.SEASONS  # GemBox: ya medida en D-EXP-1
