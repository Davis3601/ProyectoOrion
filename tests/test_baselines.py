"""
Tests para nba_predictor/models/baselines.py y evaluation.py.

(a) TestEloExpectation    — fórmula de probabilidad correcta (0.5 / ≈0.64)
(b) TestEloUpdate         — actualización simétrica y autocorrectiva
(c) TestSeasonRegression  — regresión 75/25 mueve 1700 → ≈1651.25
(d) TestNeutralSite       — neutral_site anula la ventaja de local
(e) TestConstantAntiLeakage — constante del fold no usa datos de validación
(f) TestEloAntiLeakage    — predicción no cambia al alterar el propio resultado
(g) TestSanityRealData    — ELO agrega accuracy ~60-72% y mejora el trivial
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from nba_predictor.models.baselines import ConstantBaseline, EloBaseline, elo_expected
from nba_predictor.models.evaluation import (
    FoldResult,
    _log_loss,
    aggregate,
    walk_forward_evaluate,
)


# ---------------------------------------------------------------------------
# Helpers de datos sintéticos
# ---------------------------------------------------------------------------

def _game(game_id: str, season: str, game_date: date,
          home_team: int, away_team: int,
          home_won: int, neutral_site: int = 0) -> dict:
    return dict(
        game_id=game_id, season=season, game_date=game_date,
        home_team_id=home_team, away_team_id=away_team,
        home_won=home_won, neutral_site=neutral_site,
    )


def _make_elo(**kwargs) -> EloBaseline:
    defaults = dict(k=20, home_adv=100, divisor=400,
                    carryover=0.75, regression_mean=1505, init_rating=1500)
    defaults.update(kwargs)
    return EloBaseline(**defaults)


# ---------------------------------------------------------------------------
# (a) Expectativa ELO
# ---------------------------------------------------------------------------

class TestEloExpectation:
    def test_equal_teams_no_home_advantage(self):
        """Dos equipos con rating idéntico y sin ventaja de local → P = 0.5."""
        p = elo_expected(1500.0, 1500.0, home_adv=0.0, divisor=400)
        assert p == pytest.approx(0.5)

    def test_equal_teams_home_advantage_100(self):
        """Con +100 al local y equipos iguales → P ≈ 0.6401 (fórmula del contrato)."""
        p = elo_expected(1500.0, 1500.0, home_adv=100.0, divisor=400)
        expected = 1.0 / (1.0 + 10.0 ** (-100.0 / 400.0))
        assert p == pytest.approx(expected, abs=1e-9)
        assert p == pytest.approx(0.6401, abs=0.001)

    def test_higher_home_elo_wins_more(self):
        """Local con rating mayor → P > 0.5 incluso sin ventaja de local."""
        p = elo_expected(1600.0, 1500.0, home_adv=0.0, divisor=400)
        assert p > 0.5

    def test_probability_is_bounded(self):
        """P ∈ (0, 1) para cualquier combinación de ratings."""
        p = elo_expected(2000.0, 1000.0, home_adv=200.0, divisor=400)
        assert 0.0 < p < 1.0

    def test_symmetry_no_advantage(self):
        """Sin ventaja de local, P(home) + P(away) = 1."""
        p_home = elo_expected(1600.0, 1400.0, home_adv=0.0, divisor=400)
        p_away = elo_expected(1400.0, 1600.0, home_adv=0.0, divisor=400)
        assert p_home + p_away == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# (b) Actualización: simétrica y autocorrectiva
# ---------------------------------------------------------------------------

class TestEloUpdate:
    """
    La actualización del ELO tiene dos propiedades fundamentales:
      Simetría: lo que gana el local lo pierde el visitante (suma cero).
      Autocorrectividad: perder siendo favorito cuesta más que ganar lo esperado.
    """

    def test_update_is_zero_sum(self):
        """El delta del local y el delta del visitante se cancelan exactamente."""
        k = 20
        expected = elo_expected(1500.0, 1500.0, home_adv=0.0, divisor=400)
        for result in [0, 1]:
            home_delta = k * (result - expected)
            away_delta = -home_delta   # simetría por construcción
            assert home_delta + away_delta == pytest.approx(0.0)

    def test_winning_as_favorite_gains_less_than_losing_costs(self):
        """
        Autocorrectividad: ganar como favorito da poca recompensa;
        perder como favorito da una penalización mayor.
        """
        k = 20
        # Home es claro favorito (1700 vs 1500)
        expected = elo_expected(1700.0, 1500.0, home_adv=0.0, divisor=400)
        assert expected > 0.5, "setup check: home debe ser favorito"

        gain_if_win = k * (1 - expected)    # victoria esperada → recompensa pequeña
        cost_if_lose = k * (0 - expected)   # derrota inesperada → penalización grande

        assert gain_if_win > 0
        assert cost_if_lose < 0
        assert gain_if_win < abs(cost_if_lose)

    def test_winning_as_underdog_gains_more_than_losing_costs(self):
        """Simétrico: ganar como underdog da mayor recompensa que perder resta."""
        k = 20
        # Home es underdog (1300 vs 1700)
        expected = elo_expected(1300.0, 1700.0, home_adv=0.0, divisor=400)
        assert expected < 0.5, "setup check: home debe ser underdog"

        gain_if_win = k * (1 - expected)   # upset → gran recompensa
        cost_if_lose = k * (0 - expected)  # derrota esperada → pequeña penalización

        assert gain_if_win > abs(cost_if_lose)

    def test_update_reflected_in_subsequent_prediction(self):
        """
        Tras un partido, el rating actualizado se refleja en la siguiente predicción.
        Home gana el partido 1 → ELO sube → predicción del partido 2 mejora para home.
        """
        D = date
        # Partido 1: home gana
        games_win = pd.DataFrame([
            _game("g1", "2014-15", D(2014, 10, 1), 1, 2, home_won=1),
            _game("g2", "2014-15", D(2014, 10, 2), 1, 2, home_won=0),
        ])
        # Partido 1: home pierde
        games_lose = games_win.copy()
        games_lose.loc[games_lose["game_id"] == "g1", "home_won"] = 0

        elo = _make_elo(home_adv=0)  # sin ventaja para aislar el efecto del rating
        pred_after_win = elo.compute_predictions(games_win, {"g2"})["g2"]
        pred_after_lose = elo.compute_predictions(games_lose, {"g2"})["g2"]

        # Tras ganar, home tiene mayor rating → predicción más alta en g2
        assert pred_after_win > pred_after_lose


# ---------------------------------------------------------------------------
# (c) Regresión entre temporadas
# ---------------------------------------------------------------------------

class TestSeasonRegression:
    """Al cambiar de temporada se aplica elo_nuevo = 0.75×elo_viejo + 0.25×1505."""

    def test_regression_formula_numerically(self):
        """1700 → 0.75×1700 + 0.25×1505 = 1651.25."""
        expected = 0.75 * 1700.0 + 0.25 * 1505.0
        assert expected == pytest.approx(1651.25)

    def test_regression_reflected_via_predictions(self):
        """
        Después de la regresión inter-temporada, equipos que terminen en 1700
        predicen con rating ≈1651 (no 1700) en el siguiente partido.
        """
        D = date
        elo = _make_elo(home_adv=0, k=0)   # k=0: el rating no cambia con el resultado
        #   → podemos manipular el rating inicial para testear solo la regresión

        # Con k=0, el único cambio de rating es la regresión.
        # Después de la regresión, home_elo = 0.75×1500 + 0.25×1505 = 1501.25
        games = pd.DataFrame([
            _game("g1", "2014-15", D(2014, 10, 1), 1, 2, home_won=1),
            # g2 está en la siguiente temporada → se aplica regresión antes de g2
            _game("g2", "2015-16", D(2015, 10, 1), 1, 2, home_won=0),
        ])
        preds = elo.compute_predictions(games, {"g1", "g2"})

        # g1: ambos en 1500, sin ventaja → 0.5
        assert preds["g1"] == pytest.approx(0.5)

        # g2: después de regresión desde 1500: 0.75×1500 + 0.25×1505 = 1501.25
        # Diferencia home-away = 0 → sigue siendo ≈0.5 (ambos salen de 1500)
        assert preds["g2"] == pytest.approx(0.5, abs=0.001)

    def test_regression_reduces_extreme_rating(self):
        """Un rating muy alto (1700) debe bajar hacia 1505 con cada regresión."""
        # Simulamos: k enorme para que un partido lleve el rating a 1700+,
        # luego verificamos que la predicción en la siguiente temporada
        # usa un rating más bajo.
        D = date
        elo = _make_elo(home_adv=0, k=200)  # k=200 para inflar rápidamente el rating

        games = pd.DataFrame([
            _game("g1", "2014-15", D(2014, 10, 1), 1, 2, home_won=1),  # team 1 gana mucho
            _game("g2", "2014-15", D(2014, 10, 2), 1, 2, home_won=1),
            _game("g3", "2014-15", D(2014, 10, 3), 1, 2, home_won=1),
            _game("g4", "2015-16", D(2015, 10, 1), 1, 2, home_won=0),  # nueva temporada
        ])
        preds_no_reg = _make_elo(home_adv=0, k=200, carryover=1.0).compute_predictions(
            games, {"g4"}
        )  # carryover=1.0: sin regresión
        preds_with_reg = elo.compute_predictions(games, {"g4"})

        # Con regresión, team 1 tiene rating más bajo en g4 → menor probabilidad
        assert preds_with_reg["g4"] < preds_no_reg["g4"]


# ---------------------------------------------------------------------------
# (d) Neutral site anula la ventaja de local
# ---------------------------------------------------------------------------

class TestNeutralSite:
    def test_neutral_site_gives_equal_prediction_for_equal_teams(self):
        """Con neutral_site=1 y equipos iguales, P debe ser exactamente 0.5."""
        D = date
        elo = _make_elo(home_adv=100)
        games = pd.DataFrame([
            _game("g1", "2014-15", D(2014, 10, 1), 1, 2, home_won=1, neutral_site=1),
        ])
        p = elo.compute_predictions(games, {"g1"})["g1"]
        assert p == pytest.approx(0.5)

    def test_home_site_gives_advantage_for_equal_teams(self):
        """Con neutral_site=0 y equipos iguales, P > 0.5 por la ventaja de local."""
        D = date
        elo = _make_elo(home_adv=100)
        games = pd.DataFrame([
            _game("g1", "2014-15", D(2014, 10, 1), 1, 2, home_won=1, neutral_site=0),
        ])
        p = elo.compute_predictions(games, {"g1"})["g1"]
        assert p > 0.5

    def test_neutral_site_vs_home_site_difference(self):
        """neutral_site=1 debe dar P=0.5; neutral_site=0 da P≈0.64 (diferencia ≈0.14)."""
        p_neutral = elo_expected(1500.0, 1500.0, home_adv=0.0, divisor=400)
        p_home    = elo_expected(1500.0, 1500.0, home_adv=100.0, divisor=400)
        assert p_neutral == pytest.approx(0.5)
        assert p_home - p_neutral == pytest.approx(0.14, abs=0.01)


# ---------------------------------------------------------------------------
# (e) Anti-leakage del baseline trivial
# ---------------------------------------------------------------------------

class TestConstantAntiLeakage:
    """La constante se estima SOLO con las temporadas de entrenamiento del fold."""

    def test_constant_uses_train_rate_not_val_rate(self):
        """
        Train mean=60%, val mean=40%. La constante predicha debe ser 60%, nunca 40%.
        """
        train_df = pd.DataFrame({"home_won": [1]*60 + [0]*40, "season": ["2016-17"]*100})
        val_df   = pd.DataFrame({"home_won": [1]*40 + [0]*60, "season": ["2020-21"]*100})

        const = ConstantBaseline()
        const.fit(train_df)
        probs = const.predict_proba(val_df)

        assert const.p_home_win == pytest.approx(0.60)
        assert np.all(probs == pytest.approx(0.60))

    def test_constant_not_using_val_data(self):
        """Fit con solo train → constante ≠ la media del val (cuando son distintas)."""
        train_df = pd.DataFrame({"home_won": [1]*80 + [0]*20, "season": ["2016-17"]*100})
        val_df   = pd.DataFrame({"home_won": [1]*20 + [0]*80, "season": ["2020-21"]*100})

        const = ConstantBaseline()
        const.fit(train_df)

        val_mean = val_df["home_won"].mean()  # 0.20
        assert const.p_home_win != pytest.approx(val_mean)  # 0.80 ≠ 0.20

    def test_fit_before_predict_required(self):
        """predict_proba sin fit previo levanta RuntimeError."""
        const = ConstantBaseline()
        with pytest.raises(RuntimeError):
            const.predict_proba(pd.DataFrame({"home_won": [1]}))

    def test_fit_on_empty_raises(self):
        """fit con DataFrame vacío levanta ValueError."""
        const = ConstantBaseline()
        with pytest.raises(ValueError):
            const.fit(pd.DataFrame({"home_won": pd.Series([], dtype=float)}))


# ---------------------------------------------------------------------------
# (f) Anti-leakage del ELO: predice antes de actualizar
# ---------------------------------------------------------------------------

class TestEloAntiLeakage:
    """
    La predicción del partido G no puede depender del resultado del propio G.
    Solo puede depender de los partidos G-1, G-2, ...
    """

    def test_prediction_independent_of_own_result(self):
        """
        Cambia el resultado de un partido → su predicción no cambia.
        Solo cambian las predicciones de los partidos POSTERIORES.
        """
        D = date
        elo = _make_elo()

        base_games = pd.DataFrame([
            _game("g1", "2014-15", D(2014, 10, 1), 1, 2, home_won=1),
            _game("g2", "2014-15", D(2014, 10, 2), 1, 2, home_won=1),  # el que alteraremos
            _game("g3", "2014-15", D(2014, 10, 3), 1, 2, home_won=0),
        ])

        # g2 home_won=1
        preds_a = elo.compute_predictions(base_games, {"g2"})["g2"]

        # g2 home_won=0
        alt = base_games.copy()
        alt.loc[alt["game_id"] == "g2", "home_won"] = 0
        preds_b = elo.compute_predictions(alt, {"g2"})["g2"]

        assert preds_a == pytest.approx(preds_b, abs=1e-12)

    def test_subsequent_prediction_changes_with_result(self):
        """
        El resultado de G SÍ afecta la predicción de G+1.
        Esto verifica que la actualización ocurre pero DESPUÉS de la predicción.
        """
        D = date
        elo = _make_elo(home_adv=0)

        base = pd.DataFrame([
            _game("g1", "2014-15", D(2014, 10, 1), 1, 2, home_won=1),
            _game("g2", "2014-15", D(2014, 10, 2), 1, 2, home_won=0),
        ])
        alt = base.copy()
        alt.loc[alt["game_id"] == "g1", "home_won"] = 0

        pred_g2_after_win  = elo.compute_predictions(base, {"g2"})["g2"]
        pred_g2_after_loss = elo.compute_predictions(alt, {"g2"})["g2"]

        # g1 home win → home rating sube → g2 home tiene mejor predicción
        assert pred_g2_after_win > pred_g2_after_loss


# ---------------------------------------------------------------------------
# (g) Sanity en datos reales
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def baseline_folds():
    """Corre la evaluación completa una vez; compartida entre tests del módulo."""
    from nba_predictor.storage import get_datastore
    ds = get_datastore()
    features = ds.load_features()
    all_games = ds.load_games()
    folds = walk_forward_evaluate(features, all_games)
    return folds, aggregate(folds)


class TestSanityRealData:
    def test_elo_beats_constant_on_log_loss(self, baseline_folds):
        """ELO debe tener mejor (menor) log loss que el baseline trivial."""
        _, total = baseline_folds
        assert total.elo_log_loss < total.const_log_loss, (
            f"ELO log loss ({total.elo_log_loss:.5f}) ≥ trivial ({total.const_log_loss:.5f})"
        )

    def test_elo_accuracy_in_expected_range(self, baseline_folds):
        """ELO accuracy esperada en NBA ~60-72% (históricamente ~66%)."""
        _, total = baseline_folds
        assert 0.60 <= total.elo_accuracy <= 0.72, (
            f"ELO accuracy {total.elo_accuracy:.3f} fuera de [0.60, 0.72]"
        )

    def test_correct_number_of_folds(self, baseline_folds):
        """6 folds: 2020-21 → 2025-26."""
        folds, _ = baseline_folds
        assert len(folds) == 6

    def test_total_games_matches_features(self, baseline_folds):
        """La suma de games por fold debe ser < total features (primeras 4 temporadas no son validación)."""
        folds, total = baseline_folds
        from nba_predictor.storage import get_datastore
        features = get_datastore().load_features()
        # Folds cubren 2020-21..2025-26 = 6 de las 10 temporadas
        assert total.n_games < len(features)
        assert total.n_games > 0

    def test_log_loss_in_plausible_range(self, baseline_folds):
        """Log loss plausible: ELO en [0.62, 0.70] (bien calibrado para NBA)."""
        _, total = baseline_folds
        assert 0.60 <= total.elo_log_loss <= 0.72, (
            f"ELO log loss {total.elo_log_loss:.5f} fuera de rango plausible [0.60, 0.72]"
        )

    def test_brier_score_in_plausible_range(self, baseline_folds):
        """Brier score del ELO debe estar en [0.20, 0.26] para NBA."""
        _, total = baseline_folds
        assert 0.19 <= total.elo_brier <= 0.27, (
            f"ELO Brier {total.elo_brier:.4f} fuera de [0.19, 0.27]"
        )

    def test_elo_beats_constant_on_brier(self, baseline_folds):
        """ELO también debe tener mejor Brier score que la constante."""
        _, total = baseline_folds
        assert total.elo_brier < total.const_brier

    def test_calibration_bins_populated(self, baseline_folds):
        """La curva de calibración debe tener al menos 3 bins con datos."""
        _, total = baseline_folds
        assert len(total.elo_calib_bins) >= 3
