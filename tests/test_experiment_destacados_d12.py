"""D-EXP-12 — funciones puras del experimento de umbrales binarios.

Lo que más importa: la puerta es de DOS lados (pasarse del nivel también
falla), la calibración usa SOLO las filas de la población que se le pasan, el
filtro de estatus funciona igual con instancias GemBox y legacy, y la unión
de estatus no deja dos verdades para el mismo (game_id, player_id).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "_d12", Path(__file__).resolve().parent.parent / "scripts" / "experiment_destacados_d12.py")
d12 = importlib.util.module_from_spec(_SPEC)
sys.modules["_d12"] = d12
_SPEC.loader.exec_module(d12)


# ── puerta de dos lados: los cuatro bordes ─────────────────────────────────

_FOLDS_OK = [0.80] * 6


@pytest.mark.parametrize("agg, cal", [
    (0.78, True), (0.82, True),            # bordes de la agregada, inclusivos
    (0.7799, False), (0.8201, False),      # justo fuera por abajo y por arriba
])
def test_puerta_u80_bordes_agregada(agg, cal):
    p = d12.puerta_par("U80", "pts", agg, _FOLDS_OK, 0.75, 0.0)
    assert p["calibra"] is cal


@pytest.mark.parametrize("fold, cal", [
    (0.75, True), (0.85, True),
    (0.7499, False), (0.8501, False),
])
def test_puerta_u80_bordes_por_fold(fold, cal):
    folds = [0.80] * 5 + [fold]
    assert d12.puerta_par("U80", "pts", 0.80, folds, 0.75, 0.0)["calibra"] is cal


@pytest.mark.parametrize("agg, fold, cal", [
    (0.48, 0.50, True), (0.52, 0.50, True), (0.4799, 0.50, False), (0.5201, 0.50, False),
    (0.50, 0.45, True), (0.50, 0.55, True), (0.50, 0.4499, False), (0.50, 0.5501, False),
])
def test_puerta_u50_bordes(agg, fold, cal):
    folds = [0.50] * 5 + [fold]
    assert d12.puerta_par("U50", "pts", agg, folds, 0.97, 0.0)["calibra"] is cal


def test_pasarse_del_nivel_tambien_falla():
    # Un "80%" que acierta el 90% no calibra (banda de dos lados a propósito).
    assert d12.puerta_par("U80", "pts", 0.90, [0.90] * 6, 0.75, 0.0)["calibra"] is False


# ── informatividad solo en U80; línea 50/50 solo en puntos; U70 descriptivo ─

def test_informatividad_solo_aplica_a_u80():
    p80 = d12.puerta_par("U80", "pts", 0.80, _FOLDS_OK, 0.68, 0.0)
    assert p80["calibra"] and p80["informa"] is False and not p80["cruza"]
    p50 = d12.puerta_par("U50", "pts", 0.50, [0.50] * 6, 0.10, 0.0)
    assert p50["informa"] is None and p50["cruza"]


def test_u50_solo_publicable_en_puntos():
    for stat, pub in (("pts", True), ("reb", False), ("ast", False), ("fg3m", False)):
        p = d12.puerta_par("U50", stat, 0.50, [0.50] * 6, 0.97, 0.0)
        assert p["cruza"] and p["publicable"] is pub


def test_u70_es_descriptivo_y_nunca_publicable():
    p = d12.puerta_par("U70", "pts", 0.70, [0.70] * 6, 0.80, 0.0)
    assert p["descriptivo"] and p["publicable"] is False and p["cruza"] is None


# ── regla del 30% de umbral 0, en los dos niveles ──────────────────────────

@pytest.mark.parametrize("nivel, agg, inf", [("U80", 0.80, 0.75), ("U50", 0.50, 0.97)])
@pytest.mark.parametrize("frac, sin_util", [(0.30, False), (0.3001, True)])
def test_regla_del_30_por_ciento(nivel, agg, inf, frac, sin_util):
    p = d12.puerta_par(nivel, "pts", agg, [agg] * 6, inf, frac)
    assert p["sin_umbral_util"] is sin_util
    assert p["cruza"] is (not sin_util)


# ── escalonada (a/b/c) ──────────────────────────────────────────────────────

def test_escalonada():
    a = d12.puerta_par("U80", "pts", 0.80, _FOLDS_OK, 0.72, 0.0)
    b = d12.puerta_par("U80", "pts", 0.80, _FOLDS_OK, 0.68, 0.0)
    c = d12.puerta_par("U80", "pts", 0.86, _FOLDS_OK, 0.60, 0.0)
    assert (d12.escalonada(a), d12.escalonada(b), d12.escalonada(c)) == ("a", "b", "c")


# ── calibración sobre la primaria contra cálculo directo ───────────────────

def test_calibracion_sobre_primaria_contra_calculo_directo():
    rng = np.random.default_rng(3)
    n = 400
    df = pd.DataFrame({"y": rng.integers(0, 40, n).astype(float),
                       "c": rng.uniform(5, 30, n), "d": rng.uniform(0, 6, n)})
    df.loc[:9, "d"] = 0.0                                  # algunas con dispersión 0
    prim = rng.random(n) < 0.3                             # "primaria" del train
    tr = df[prim]
    q_sc, q_ab = d12.calibrar_q(tr["y"], tr["c"], tr["d"], 0.80)
    ok = tr["d"] > 0
    esperado_sc = np.quantile(((tr["c"] - tr["y"]) / tr["d"])[ok], 0.80)
    esperado_ab = np.quantile(tr["c"] - tr["y"], 0.80)
    assert q_sc == pytest.approx(esperado_sc) and q_ab == pytest.approx(esperado_ab)
    # Calibrar sobre todo el train daría otro q: la población importa.
    assert d12.calibrar_q(df["y"], df["c"], df["d"], 0.80)[0] != pytest.approx(q_sc)


# ── redondeo hacia arriba y acierto sobre el entero publicado ──────────────

def test_umbral_redondeado_hacia_arriba_y_acotado_en_cero():
    u = d12.d7.floor_values([20.0, 3.0, 10.0], [4.0, 4.0, 0.0], 0.6, 2.5)
    assert u.tolist() == [18.0, 1.0, 8.0]                  # 17.6->18, 0.6->1, 7.5->8
    assert d12.d7.floor_values([1.0], [4.0], 0.6, 2.5).tolist() == [0.0]   # -1.4 -> 0
    assert d12.acierto([18, 17, 1, 0, 8], [18, 18, 1, 1, 8]) == pytest.approx(3 / 5)


# ── filtro de estatus con un fixture GemBox y uno legacy ───────────────────

def _gem() -> pd.DataFrame:
    # Forma de data/experiment_pjuega/instancias.json (GemBox).
    return pd.DataFrame([
        {"cut": "publish", "date": "2025-01-10", "game_id": "0022400600", "team_id": 1610612744,
         "player_id": 201939, "status": "Out", "suffix": "01PM", "season": "2024-25",
         "veredicto": "incluida", "team": "GoldenStateWarriors", "player_name": "Stephen Curry"},
        {"cut": "publish", "date": "2025-01-10", "game_id": "0022400600", "team_id": 1610612744,
         "player_id": 203110, "status": "Questionable", "suffix": "01PM", "season": "2024-25",
         "veredicto": "incluida", "team": "GoldenStateWarriors", "player_name": "Draymond Green"},
    ])


def _leg() -> pd.DataFrame:
    # Forma de data/experiment_pjuega_legacy/instancias.json (iTextSharp): columnas extra.
    return pd.DataFrame([
        {"cut": "publish", "date": "2021-02-10", "game_id": "0022000300", "team_id": 1610612747,
         "player_id": 2544, "status": "Doubtful", "suffix": "01PM", "season": "2020-21",
         "veredicto": "incluida", "team": "Los Angeles Lakers", "player_name": "LeBron James",
         "layout": "ITEXT_V2", "category": None, "previous_status": None, "coherent": True,
         "matchup": "LAL@DEN"},
    ])


def _sel() -> pd.DataFrame:
    return pd.DataFrame({
        "game_id": ["0022400600"] * 3 + ["0022000300"] * 3 + ["0021500001"] * 2,
        "team_id": [1610612744] * 3 + [1610612747] * 3 + [1610612737] * 2,
        "player_id": [201939, 203110, 1630228, 2544, 203076, 1628398, 10, 11],
        "sel_rank": [1, 2, 3, 1, 2, 3, 1, 2],
        "sel_games_in_window": [10] * 8,
    })


def test_filtro_de_estatus_gembox_y_legacy():
    ok = d12.unir_estatus(_gem(), _leg())
    estatus = {(g, int(p)): s for g, p, s in zip(ok["game_id"], ok["player_id"], ok["status"])}
    out = d12.d9.filtrar_selector(_sel(), estatus)
    por_partido = out.groupby("game_id")["player_id"].apply(list).to_dict()
    # GemBox: Curry Out sale, Green (Questionable) sube a 1, el tercero entra a 2.
    assert por_partido["0022400600"] == [203110, 1630228]
    # Legacy: LeBron Doubtful sale; los siguientes por minutos suben.
    assert por_partido["0022000300"] == [203076, 1628398]
    # Fecha sin PDF (sin estatus): sin filtro, top 2 intacto.
    assert por_partido["0021500001"] == [10, 11]


# ── unión de estatus sin duplicados por (game_id, player_id) ───────────────

def test_union_colapsa_duplicados_identicos():
    g = _gem()
    ok = d12.unir_estatus(g, g.iloc[[0]])
    assert len(ok) == 2
    assert not ok.duplicated(["game_id", "player_id"]).any()


def test_union_con_estatus_en_conflicto_falla_ruidosamente():
    g = _gem()
    otra = g.iloc[[0]].copy()
    otra["status"] = "Available"
    with pytest.raises(ValueError, match="conflicto"):
        d12.unir_estatus(g, otra)
