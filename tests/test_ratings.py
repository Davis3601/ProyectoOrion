"""
Tests del Grupo 2 — Ratings ofensivo/defensivo (nba_predictor/features/ratings.py).

Cinco clases de tests:
  (a) TestReinaNoLeakage    : fgm=999 en partido K no infla off_rating_diff de K;
                              pero SÍ el de K+1 (confirmación de captura).
  (b) TestNumericalFormulas : verificación numérica exacta de poss y ratings
                              (con oreb=0 y fta=0 para simplificar la fórmula).
  (c) TestRatioOfAverages   : ratio-de-medias ≠ media-de-ratios en partidos de
                              ritmo variado; el código debe usar el primero.
  (d) TestNetCoherence      : net_rating_diff = off_rating_diff − def_rating_diff
                              se verifica como identidad algebraica en todos los rows.
  (e) TestSanityRealData    : off_rating rolling ∈ [95, 125] en ≥98% de las filas
                              de los datos históricos reales.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from nba_predictor.features.ratings import compute_ratings

# ---------------------------------------------------------------------------
# Stats de partido "normal" (oreb=0, fta=0 → simplifica la fórmula de poss)
#
# poss = 0.5 * (team_half + opp_half)
#      = 0.5 * ((fga + tov) + (opp_fga + opp_tov))
#      = 0.5 * ((80 + 10) + (75 + 12))
#      = 0.5 * (90 + 87) = 88.5
#
# pts_home = 40*2 + 20 = 100
# pts_away = 35*2 + 15 = 85
#
# off_rating_diff = 100*(100 - 85) / 88.5 =  1500/88.5 ≈ +16.949
# def_rating_diff = 100*(85  - 100) / 88.5 = -1500/88.5 ≈ -16.949
# net_rating_diff = 100*30 / 88.5          =  3000/88.5 ≈ +33.898
# ---------------------------------------------------------------------------
_H = dict(
    h_fgm=40, h_fg3m=0, h_fga=80, h_fta=0,
    h_oreb=0, h_dreb=30, h_tov=10, h_ftm=20,
)
_A = dict(
    a_fgm=35, a_fg3m=0, a_fga=75, a_fta=0,
    a_oreb=0, a_dreb=30, a_tov=12, a_ftm=15,
)
_POSS = 88.5
_OFF_DIFF = 1500 / _POSS   # ≈ +16.949
_DEF_DIFF = -1500 / _POSS  # ≈ -16.949
_NET_DIFF = 3000 / _POSS   # ≈ +33.898


def _make_pair(
    game_id: str,
    game_date: date,
    season: str,
    h_fgm: int, h_fg3m: int, h_fga: int, h_fta: int,
    h_oreb: int, h_dreb: int, h_tov: int, h_ftm: int,
    a_fgm: int, a_fg3m: int, a_fga: int, a_fta: int,
    a_oreb: int, a_dreb: int, a_tov: int, a_ftm: int,
) -> list[dict]:
    """
    Crea las dos filas (local + visitante) de un partido con opp_* ya poblados.

    Permite construir DataFrames sintéticos para tests sin pasar por el self-join
    ni por la BD — los opp_* se derivan simétricamente en el propio constructor.
    """
    home = {
        "team_id": 1, "game_id": game_id, "game_date": game_date,
        "season": season, "is_home": 1,
        "fgm": h_fgm, "fg3m": h_fg3m, "fga": h_fga, "fta": h_fta,
        "oreb": h_oreb, "dreb": h_dreb, "tov": h_tov, "ftm": h_ftm,
        "opp_fgm": a_fgm, "opp_fg3m": a_fg3m, "opp_fga": a_fga, "opp_fta": a_fta,
        "opp_oreb": a_oreb, "opp_dreb": a_dreb, "opp_tov": a_tov, "opp_ftm": a_ftm,
    }
    away = {
        "team_id": 2, "game_id": game_id, "game_date": game_date,
        "season": season, "is_home": 0,
        "fgm": a_fgm, "fg3m": a_fg3m, "fga": a_fga, "fta": a_fta,
        "oreb": a_oreb, "dreb": a_dreb, "tov": a_tov, "ftm": a_ftm,
        "opp_fgm": h_fgm, "opp_fg3m": h_fg3m, "opp_fga": h_fga, "opp_fta": h_fta,
        "opp_oreb": h_oreb, "opp_dreb": h_dreb, "opp_tov": h_tov, "opp_ftm": h_ftm,
    }
    return [home, away]


def _normal_games(n: int, start: date = date(2016, 10, 1)) -> pd.DataFrame:
    """Genera n partidos con los stats normales (_H vs _A)."""
    rows = []
    for i in range(n):
        rows += _make_pair(
            game_id=f"g{i}", game_date=start + timedelta(days=i),
            season="2016-17", **_H, **_A,
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# (a) Test reina de no-leakage
# ---------------------------------------------------------------------------

class TestReinaNoLeakage:
    """
    Plantar fgm=999 en el partido K.
    - off_rating_diff EN K debe reflejar solo K−1, K−2, … → valor normal.
    - off_rating_diff EN K+1 debe reflejar el 999 → valor muy alto.
    """

    WINDOW = 3
    EXTREME_GAME = 3  # índice del partido con fgm=999

    def _build(self) -> pd.DataFrame:
        rows = []
        start = date(2016, 10, 1)
        for i in range(6):
            h_fgm = 999 if i == self.EXTREME_GAME else _H["h_fgm"]
            rows += _make_pair(
                game_id=f"g{i}", game_date=start + timedelta(days=i),
                season="2016-17",
                h_fgm=h_fgm, h_fg3m=_H["h_fg3m"], h_fga=_H["h_fga"], h_fta=_H["h_fta"],
                h_oreb=_H["h_oreb"], h_dreb=_H["h_dreb"], h_tov=_H["h_tov"], h_ftm=_H["h_ftm"],
                **_A,
            )
        return pd.DataFrame(rows)

    def test_extreme_fgm_absent_from_own_rolling(self):
        """fgm=999 en g3 NO debe inflar off_rating_diff de ese mismo partido."""
        result = compute_ratings(self._build(), window=self.WINDOW)
        row = result[result["game_id"] == "g3"].iloc[0]
        assert row["off_rating_diff"] == pytest.approx(_OFF_DIFF, rel=1e-4), (
            f"LEAKAGE DETECTADO: off_rating_diff en g3 = {row['off_rating_diff']:.2f} "
            f"(debería ser ≈{_OFF_DIFF:.3f}, sin reflejar el fgm=999 plantado ahí)"
        )

    def test_extreme_fgm_visible_to_next_game(self):
        """off_rating_diff en g4 debe ser muy alto (fgm=999 ya está en la ventana)."""
        result = compute_ratings(self._build(), window=self.WINDOW)
        row = result[result["game_id"] == "g4"].iloc[0]
        # pts_home rolling = (100+100+2018)/3 ≈ 739 → off_rating ≈ 835
        assert row["off_rating_diff"] > 500, (
            f"El partido posterior al fgm=999 debe verlo: "
            f"obtenido off_rating_diff={row['off_rating_diff']:.2f}, esperado > 500"
        )


# ---------------------------------------------------------------------------
# (b) Verificación numérica de la fórmula de poss y los tres ratings
# ---------------------------------------------------------------------------

class TestNumericalFormulas:
    """
    Con oreb=0 y fta=0 la fórmula de poss se simplifica a:
        poss = 0.5 * ((fga + tov) + (opp_fga + opp_tov))
             = 0.5 * (90 + 87) = 88.5

    Con WINDOW partidos idénticos, los rolling means son iguales a los valores
    del partido único, por lo que la verificación numérica es exacta.
    """

    WINDOW = 3

    def _build(self) -> pd.DataFrame:
        return _normal_games(self.WINDOW + 1)

    def test_possessions_formula_via_off_diff(self):
        """
        off_rating_diff = 100*(pts_home - pts_away) / poss
                        = 100*15 / 88.5
        Si poss estuviera mal calculada, este valor diferiría de 1500/88.5.
        """
        result = compute_ratings(self._build(), window=self.WINDOW)
        last = result.iloc[-1]
        assert last["off_rating_diff"] == pytest.approx(_OFF_DIFF, rel=1e-4), (
            f"off_rating_diff = {last['off_rating_diff']:.6f}, esperado 1500/88.5 = {_OFF_DIFF:.6f}"
        )

    def test_def_and_net_rating_diffs(self):
        """def_rating_diff y net_rating_diff verificados contra valores exactos."""
        result = compute_ratings(self._build(), window=self.WINDOW)
        last = result.iloc[-1]
        assert last["def_rating_diff"] == pytest.approx(_DEF_DIFF, rel=1e-4), (
            f"def_rating_diff = {last['def_rating_diff']:.6f}, "
            f"esperado -1500/88.5 = {_DEF_DIFF:.6f}"
        )
        assert last["net_rating_diff"] == pytest.approx(_NET_DIFF, rel=1e-4), (
            f"net_rating_diff = {last['net_rating_diff']:.6f}, "
            f"esperado 3000/88.5 = {_NET_DIFF:.6f}"
        )


# ---------------------------------------------------------------------------
# (c) Ratio de medias ≠ media de ratios (impacto del ritmo de juego)
# ---------------------------------------------------------------------------

class TestRatioOfAverages:
    """
    Dos partidos con ritmo muy diferente (poss=44 vs poss=66):

        Partido 0 rápido: home pts=60, poss=44 → off_rating = 136.4
        Partido 1 lento : home pts=110, poss=66 → off_rating = 166.7

    Media de ratios     (incorrecto) : (136.4 + 166.7) / 2  ≈ 151.5
    Ratio de medias rolling (correcto): 100*(60+110)/(44+66) = 154.5

    La diff considerando el away team (uniform, pts=54):
        Correcto : 100*(85-54)/(44+66)   = 3100/55 ≈ 56.36
        Incorrecto: sería ≈ 49.24 si se promedian ratios
    """

    WINDOW = 2

    def _build(self) -> pd.DataFrame:
        """
        Away team: stats idénticas en todos los partidos (pts=54, poss varía con el home).
        Home team: varía el ritmo (poss=44 en g0, poss=66 en g1).
        """
        away_base = dict(
            a_fgm=22, a_fg3m=0, a_fga=40, a_fta=0,
            a_oreb=0, a_dreb=20, a_tov=4, a_ftm=10,
        )
        home_games = [
            # (fgm, fg3m, fga, fta, oreb, dreb, tov, ftm)
            # g0: team_half=40+4=44, opp_half=40+4=44 → poss=44; pts=60
            (25, 0, 40, 0, 0, 20, 4, 10),
            # g1: team_half=80+8=88, opp_half=40+4=44 → poss=66; pts=110
            (45, 0, 80, 0, 0, 20, 8, 20),
            # g2: mismo ritmo que g0 (target del rolling)
            (25, 0, 40, 0, 0, 20, 4, 10),
        ]
        rows = []
        start = date(2016, 10, 1)
        for i, (h_fgm, h_fg3m, h_fga, h_fta, h_oreb, h_dreb, h_tov, h_ftm) in enumerate(home_games):
            rows += _make_pair(
                game_id=f"g{i}", game_date=start + timedelta(days=i),
                season="2016-17",
                h_fgm=h_fgm, h_fg3m=h_fg3m, h_fga=h_fga, h_fta=h_fta,
                h_oreb=h_oreb, h_dreb=h_dreb, h_tov=h_tov, h_ftm=h_ftm,
                **away_base,
            )
        return pd.DataFrame(rows)

    def test_ratio_of_means_not_mean_of_ratios(self):
        """
        Con rolling de pts y poss separados:
            pts_home_rolling = (60+110)/2 = 85
            poss_rolling     = (44+66)/2  = 55
            off_rating_home  = 100*85/55
            off_rating_away  = 100*54/55  (away pts uniform=54)
            off_rating_diff  = 100*31/55 = 3100/55 ≈ 56.36

        Si se promediasen los ratios per-game:
            home_avg_rating = (136.4 + 166.7)/2 ≈ 151.5
            away_avg_rating = (122.7 + 81.8)/2  ≈ 102.3
            wrong_diff      ≈ 49.24
        """
        result = compute_ratings(self._build(), window=self.WINDOW)
        g2 = result[result["game_id"] == "g2"].iloc[0]

        correct = 3100 / 55  # ≈ 56.36
        wrong = 49.24

        assert g2["off_rating_diff"] == pytest.approx(correct, rel=1e-4), (
            f"Se esperaba off_rating_diff ≈ {correct:.4f} (ratio de medias), "
            f"obtenido {g2['off_rating_diff']:.4f}. "
            f"Si el resultado fuera ≈ {wrong:.2f}, indicaría media de ratios (bug)."
        )


# ---------------------------------------------------------------------------
# (d) Coherencia interna: net_rating_diff = off_rating_diff − def_rating_diff
# ---------------------------------------------------------------------------

class TestNetCoherence:
    """
    net_rating = off_rating − def_rating (a nivel de equipo).
    Por álgebra: net_diff = net_home − net_away
                          = (off_home − def_home) − (off_away − def_away)
                          = (off_home − off_away) − (def_home − def_away)
                          = off_diff − def_diff
    Esta identidad debe cumplirse exactamente para todos los partidos del resultado.
    """

    WINDOW = 3

    def _build(self) -> pd.DataFrame:
        return _normal_games(self.WINDOW + 3)

    def test_net_equals_off_minus_def_for_all_rows(self):
        result = compute_ratings(self._build(), window=self.WINDOW).dropna()
        assert len(result) > 0, "No hay filas válidas para verificar la coherencia"

        expected_net = result["off_rating_diff"] - result["def_rating_diff"]
        pd.testing.assert_series_equal(
            result["net_rating_diff"].reset_index(drop=True),
            expected_net.reset_index(drop=True).rename("net_rating_diff"),
            check_names=True,
        )


# ---------------------------------------------------------------------------
# (e) Sanidad sobre datos reales: off_rating ∈ [95, 125] en ≥ 98 % de los rows
# ---------------------------------------------------------------------------

class TestSanityRealData:
    """
    Los ratings individuales de equipos en la NBA contemporánea caen
    consistentemente en el rango [95, 125] por 100 posesiones.
    Este test corre contra la BD real para detectar bugs de escala o signo.
    """

    def test_off_rating_in_realistic_range(self):
        from nba_predictor.features.ratings import build_team_ratings

        team_ratings = build_team_ratings()
        valid_off = team_ratings["off_rating"].dropna()

        assert len(valid_off) > 10_000, (
            f"Se esperan >10 000 filas con off_rating válido, obtenido {len(valid_off)}"
        )

        pct = ((valid_off >= 95) & (valid_off <= 125)).mean()
        assert pct >= 0.98, (
            f">98 % de off_ratings deberían estar en [95, 125]. "
            f"Obtenido: {pct:.1%}. "
            f"Si el porcentaje es bajo, puede haber un error en la fórmula de poss "
            f"(escala incorrecta) o en la asignación de pts/opp_pts."
        )
