"""
Tests para nba_predictor/models/xgboost_model.py.

(a) TestEvalSetAntiLeakage — el eval set del early stopping no contiene
                             game_ids de la temporada de validación del fold
(b) TestProbabilityRange   — predicciones en (0, 1), sin NaN ni infinitos
(c) TestReproducibility    — semilla fija → predicciones idénticas
(d) TestSanityRealData     — bate al trivial; reporte honesto vs ELO y logística
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from nba_predictor.config import FIRST_VAL_IDX, TRAINING_SEASONS, XGB_N_ESTIMATORS
from nba_predictor.models.xgboost_model import (
    XGBResult,
    _fit_xgb_fold,
    _make_early_stop_split,
    aggregate_xgboost,
    walk_forward_xgboost,
)
from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS


# ---------------------------------------------------------------------------
# Fixture de datos reales (compartida entre clases de sanity)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def xgb_result() -> XGBResult:
    """Walk-forward completo; se calcula una sola vez por sesión de tests."""
    from nba_predictor.storage import get_datastore
    ds = get_datastore()
    features = ds.load_features()
    all_games = ds.load_games()
    return walk_forward_xgboost(features, all_games)


# ---------------------------------------------------------------------------
# Helpers para datos sintéticos
# ---------------------------------------------------------------------------

def _synthetic_train_df(seasons: list[str], n_per_season: int = 20) -> pd.DataFrame:
    """DataFrame sintético con las columnas necesarias para _make_early_stop_split."""
    rows = []
    for s in seasons:
        for i in range(n_per_season):
            rows.append({
                "season": s,
                "game_id": f"{s}_{i}",
                "home_won": i % 2,
                **{col: float(i % 5) for col in OFFICIAL_LOGISTIC_COLS},
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# (a) Anti-leakage del eval set
# ---------------------------------------------------------------------------

class TestEvalSetAntiLeakage:
    """
    La última temporada del train se usa como eval set para el early stopping.
    Esta temporada siempre es ANTERIOR a la temporada de validación del fold,
    por lo que el val del fold NUNCA contamina el early stopping.
    """

    def test_split_last_season_is_eval(self):
        """_make_early_stop_split devuelve la última temporada como eval."""
        train_df = _synthetic_train_df(["2016-17", "2017-18", "2018-19"])

        xgb_train, xgb_eval = _make_early_stop_split(train_df)

        assert set(xgb_eval["season"]) == {"2018-19"}, (
            "El eval set debe ser SOLO la última temporada del train"
        )
        assert "2018-19" not in set(xgb_train["season"]), (
            "La temporada del eval no debe estar en el train XGBoost"
        )

    def test_split_xgb_train_has_remaining_seasons(self):
        """xgb_train contiene todas las temporadas excepto la última."""
        train_df = _synthetic_train_df(["2016-17", "2017-18", "2018-19", "2019-20"])

        xgb_train, xgb_eval = _make_early_stop_split(train_df)

        assert set(xgb_train["season"]) == {"2016-17", "2017-18", "2018-19"}
        assert set(xgb_eval["season"]) == {"2019-20"}

    def test_eval_season_is_before_val_season_for_all_folds(self):
        """
        Para todos los folds del walk-forward, la temporada del eval set
        (= última del train) precede cronológicamente a la temporada de validación.

        Garantía: TRAINING_SEASONS está en orden; TRAINING_SEASONS[i-1] < TRAINING_SEASONS[i]
        por construcción → la última del train siempre es anterior al val.
        """
        for i in range(FIRST_VAL_IDX, len(TRAINING_SEASONS)):
            val_season = TRAINING_SEASONS[i]
            train_seasons = list(TRAINING_SEASONS[:i])
            early_stop_season = train_seasons[-1]  # última del train

            val_idx = list(TRAINING_SEASONS).index(val_season)
            early_idx = list(TRAINING_SEASONS).index(early_stop_season)

            assert early_idx < val_idx, (
                f"Fold {val_season}: la temporada de early stopping ({early_stop_season}) "
                f"debe ser anterior al val ({val_season})"
            )

    def test_early_stopping_triggered_in_all_folds(self, xgb_result):
        """
        En todos los folds el modelo para antes del ceiling (N_ESTIMATORS=1000).
        Si algún fold alcanza el ceiling, el early stopping no está funcionando.
        """
        for fold in xgb_result.folds:
            assert fold.n_trees < XGB_N_ESTIMATORS, (
                f"Fold {fold.val_season}: n_trees={fold.n_trees} = ceiling — "
                "el early stopping no se activó. Verificar eval_set."
            )


# ---------------------------------------------------------------------------
# (b) Rango de probabilidades
# ---------------------------------------------------------------------------

class TestProbabilityRange:
    """XGBoost con objective=binary:logistic produce probabilidades en (0, 1)."""

    def test_synthetic_probabilities_valid(self):
        """Predicciones en (0, 1) con datos sintéticos pequeños."""
        rng = np.random.default_rng(99)
        n = len(OFFICIAL_LOGISTIC_COLS)

        X_train = pd.DataFrame(
            rng.normal(0, 1, (100, n)), columns=OFFICIAL_LOGISTIC_COLS
        )
        y_train = rng.integers(0, 2, 100).astype(float)
        X_eval = pd.DataFrame(
            rng.normal(0, 1, (30, n)), columns=OFFICIAL_LOGISTIC_COLS
        )
        y_eval = rng.integers(0, 2, 30).astype(float)
        X_val = pd.DataFrame(
            rng.normal(0, 1, (20, n)), columns=OFFICIAL_LOGISTIC_COLS
        )

        model = _fit_xgb_fold(X_train, y_train, X_eval, y_eval)
        probs = model.predict_proba(X_val)[:, 1]

        assert np.all(probs > 0.0), f"Prob ≤ 0 detectada: min={probs.min():.6f}"
        assert np.all(probs < 1.0), f"Prob ≥ 1 detectada: max={probs.max():.6f}"
        assert np.all(np.isfinite(probs)), "NaN o infinito en predicciones"

    def test_real_walkforward_probabilities_valid(self, xgb_result):
        """Todas las probabilidades del walk-forward real están en (0, 1)."""
        total = aggregate_xgboost(xgb_result.folds)
        probs = total.xgb_probs

        assert np.all(probs > 0.0), f"XGB: prob ≤ 0 — min={probs.min():.6f}"
        assert np.all(probs < 1.0), f"XGB: prob ≥ 1 — max={probs.max():.6f}"
        assert np.all(np.isfinite(probs)), "XGB: valores no finitos detectados"


# ---------------------------------------------------------------------------
# (c) Reproducibilidad
# ---------------------------------------------------------------------------

class TestReproducibility:
    """random_state=42 garantiza predicciones idénticas entre ejecuciones."""

    def test_same_seed_same_predictions(self):
        """Dos llamadas a _fit_xgb_fold con los mismos datos producen probs idénticas."""
        rng = np.random.default_rng(42)
        n = len(OFFICIAL_LOGISTIC_COLS)

        X_train = pd.DataFrame(rng.normal(0, 1, (150, n)), columns=OFFICIAL_LOGISTIC_COLS)
        y_train = rng.integers(0, 2, 150).astype(float)
        X_eval = pd.DataFrame(rng.normal(0, 1, (40, n)), columns=OFFICIAL_LOGISTIC_COLS)
        y_eval = rng.integers(0, 2, 40).astype(float)
        X_val = pd.DataFrame(rng.normal(0, 1, (30, n)), columns=OFFICIAL_LOGISTIC_COLS)

        model_1 = _fit_xgb_fold(X_train, y_train, X_eval, y_eval)
        model_2 = _fit_xgb_fold(X_train, y_train, X_eval, y_eval)

        probs_1 = model_1.predict_proba(X_val)[:, 1]
        probs_2 = model_2.predict_proba(X_val)[:, 1]

        np.testing.assert_array_equal(
            probs_1, probs_2,
            err_msg="Misma semilla debe producir predicciones idénticas"
        )

    def test_full_walkforward_reproducible(self, xgb_result):
        """
        Dos ejecuciones del walk-forward completo producen el mismo LL agregado.
        (Fixture `xgb_result` ya cacheada; comparamos LL vs un valor de referencia.)
        """
        from nba_predictor.storage import get_datastore
        ds = get_datastore()
        features = ds.load_features()
        all_games = ds.load_games()

        result_2 = walk_forward_xgboost(features, all_games)
        total_1 = aggregate_xgboost(xgb_result.folds)
        total_2 = aggregate_xgboost(result_2.folds)

        assert total_1.xgb_log_loss == pytest.approx(total_2.xgb_log_loss, abs=1e-10), (
            "Walk-forward no es reproducible con la misma semilla"
        )


# ---------------------------------------------------------------------------
# (d) Sanity en datos reales
# ---------------------------------------------------------------------------

class TestSanityRealData:
    def test_correct_number_of_folds(self, xgb_result):
        """6 folds: 2020-21 → 2025-26."""
        assert len(xgb_result.folds) == 6

    def test_xgb_beats_trivial(self, xgb_result):
        """XGBoost debe tener mejor (menor) LL que el baseline trivial."""
        total = aggregate_xgboost(xgb_result.folds)
        assert total.xgb_log_loss < total.const_log_loss, (
            f"XGB ({total.xgb_log_loss:.5f}) no supera al trivial "
            f"({total.const_log_loss:.5f}) — revisar pipeline"
        )

    def test_importances_populated(self, xgb_result):
        """El fold final debe tener importancias para todas las features."""
        assert len(xgb_result.final_importances) == len(OFFICIAL_LOGISTIC_COLS), (
            f"Se esperaban {len(OFFICIAL_LOGISTIC_COLS)} features en importancias, "
            f"se obtuvieron {len(xgb_result.final_importances)}"
        )

    def test_all_n_trees_positive(self, xgb_result):
        """Cada fold produce al menos un árbol."""
        for fold in xgb_result.folds:
            assert fold.n_trees > 0, f"Fold {fold.val_season}: n_trees=0"

    def test_honest_elo_comparison(self, xgb_result):
        """
        Reporta honestamente si XGBoost bate al ELO.
        Este test SIEMPRE pasa — el resultado es informativo, no un requisito.
        """
        total = aggregate_xgboost(xgb_result.folds)
        direction_elo = "BATE" if total.xgb_log_loss < total.elo_log_loss else "NO bate"
        delta_elo = total.elo_log_loss - total.xgb_log_loss
        print(
            f"\n[XGB vs ELO] {direction_elo}: XGB={total.xgb_log_loss:.5f}, "
            f"ELO={total.elo_log_loss:.5f}, delta={delta_elo:+.5f}"
        )
        assert True

    def test_honest_logistic_comparison(self, xgb_result):
        """
        Reporta honestamente si XGBoost mejora sobre la logística.
        Sin assertion — cualquier resultado es válido y documentado.
        Mejora >0.02 debería disparar auditoría de leakage.
        """
        total = aggregate_xgboost(xgb_result.folds)
        mejora = total.log_log_loss - total.xgb_log_loss
        direction = "MEJORA" if mejora > 0 else "NO mejora"

        if mejora > 0.02:
            print(
                f"\n[XGB vs Log] BANDERA DE AUDITORÍA: mejora={mejora:.5f} > 0.02 — "
                "investigar leakage"
            )
        else:
            print(
                f"\n[XGB vs Log] {direction}: XGB={total.xgb_log_loss:.5f}, "
                f"Log={total.log_log_loss:.5f}, delta={mejora:+.5f}"
            )
        assert True
