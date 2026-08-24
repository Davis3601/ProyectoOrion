"""
Tests para nba_predictor/models/logistic.py.

(a) TestScalerAntiLeakage — el StandardScaler se ajusta SOLO con train data
(b) TestVariantComposition — A excluye raw ratings; B excluye adj; target fuera
(c) TestProbabilityRange   — probabilidades estrictamente en (0, 1), sin NaN
(d) TestSanityRealData     — ambas variantes baten al trivial; reporte honesto vs ELO
"""
from __future__ import annotations

import numpy as np
import pytest

from nba_predictor.models.logistic import (
    VARIANT_A_COLS,
    VARIANT_B_COLS,
    LogisticResult,
    _fit_fold,
    aggregate_logistic,
    walk_forward_logistic,
)

# Grupos de ratings — constantes locales del test (no importadas del módulo)
_RAW = {"off_rating_diff", "def_rating_diff", "net_rating_diff"}
_ADJ = {"off_rating_adj_diff", "def_rating_adj_diff", "net_rating_adj_diff"}


# ---------------------------------------------------------------------------
# Fixture de datos reales (compartida entre clases de sanity)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def logistic_result() -> LogisticResult:
    """Corre el walk-forward una vez; compartida entre tests del módulo."""
    from nba_predictor.storage import get_datastore
    ds = get_datastore()
    features = ds.load_features()
    all_games = ds.load_games()
    return walk_forward_logistic(features, all_games)


# ---------------------------------------------------------------------------
# (a) Anti-leakage del scaler
# ---------------------------------------------------------------------------

class TestScalerAntiLeakage:
    """
    El StandardScaler SOLO ve datos de train — nunca de validación.
    _fit_fold recibe únicamente X_train, por lo que el scaler no puede
    ver X_val aunque existiera. Verificamos dos propiedades:
    1. scaler.mean_ = media de X_train (no de X_train ∪ X_val).
    2. scaler.scale_ = std de X_train.
    """

    def test_scaler_mean_matches_train_mean(self):
        """
        Con train loc=5 y val loc=50, el scaler.mean_ debe ser ≈5, no ≈16.25
        (que sería la media ponderada si el scaler viera ambos conjuntos).
        """
        rng = np.random.default_rng(42)
        n_feat = len(VARIANT_A_COLS)
        X_train = rng.normal(5.0, 1.0, (300, n_feat))
        y_train = rng.integers(0, 2, 300).astype(float)

        scaler, _ = _fit_fold(X_train, y_train)

        # Media del scaler ≈ media de train (≈ 5)
        np.testing.assert_allclose(scaler.mean_, X_train.mean(axis=0), rtol=1e-10)

        # Confirmar que la diferencia vs la media de val sería detectable
        X_val = rng.normal(50.0, 1.0, (100, n_feat))
        combined_mean = (300 * X_train.mean(axis=0) + 100 * X_val.mean(axis=0)) / 400
        assert np.all(np.abs(scaler.mean_ - combined_mean) > 1.0), (
            "Si el scaler viera val, su media estaría entre 5 y 50, no en 5"
        )

    def test_scaler_std_matches_train_std(self):
        """scaler.scale_ = desviación estándar de X_train (no combinada)."""
        rng = np.random.default_rng(0)
        n_feat = len(VARIANT_A_COLS)
        X_train = rng.normal(0.0, 2.0, (200, n_feat))
        y_train = rng.integers(0, 2, 200).astype(float)

        scaler, _ = _fit_fold(X_train, y_train)

        np.testing.assert_allclose(scaler.scale_, X_train.std(axis=0), rtol=1e-10)

    def test_earlier_fold_predictions_unaffected_by_later_val_perturbation(
        self, logistic_result
    ):
        """
        Perturbar el val del fold 5 (2025-26) no altera las predicciones
        del fold 4 (val=2024-25) — cuyo train y val son independientes.

        Si el scaler de un fold viera datos de otro fold, esta perturbación
        cambiaría las predicciones del fold 4.
        """
        from nba_predictor.storage import get_datastore
        ds = get_datastore()
        features = ds.load_features()
        all_games = ds.load_games()

        # Perturbar val de 2025-26 (no afecta al train ni val de 2024-25).
        # Solo columnas float para evitar error de tipo en cols booleanas (b2b, neutral).
        float_cols = [c for c in VARIANT_A_COLS if c not in ("home_b2b", "away_b2b", "neutral_site")]
        features_mod = features.copy()
        mask = features_mod["season"] == "2025-26"
        features_mod.loc[mask, float_cols] = 999.0

        result_mod = walk_forward_logistic(features_mod, all_games)

        # Fold 4 (idx=4, val=2024-25): predicciones deben ser idénticas
        np.testing.assert_array_equal(
            logistic_result.folds[4].log_a_probs,
            result_mod.folds[4].log_a_probs,
            err_msg=(
                "Perturbar val de 2025-26 cambió predicciones de 2024-25: "
                "el scaler del fold 4 está viendo datos del fold 5 (LEAKAGE)"
            ),
        )


# ---------------------------------------------------------------------------
# (b) Composición de variantes
# ---------------------------------------------------------------------------

class TestVariantComposition:
    """
    Variante A = todas las features menos ratings crudos (usa adj).
    Variante B = todas las features menos ratings ajustados (usa raw).
    El target y los identificadores no deben aparecer en ninguna variante.
    """

    def test_variant_a_excludes_raw_ratings(self):
        """Ninguna columna de ratings crudos en variante A."""
        for col in _RAW:
            assert col not in VARIANT_A_COLS, f"{col} no debería estar en variante A"

    def test_variant_b_excludes_adj_ratings(self):
        """Ninguna columna de ratings ajustados en variante B."""
        for col in _ADJ:
            assert col not in VARIANT_B_COLS, f"{col} no debería estar en variante B"

    def test_variant_a_includes_adj_ratings(self):
        """Variante A SÍ debe incluir los ratings ajustados."""
        for col in _ADJ:
            assert col in VARIANT_A_COLS, f"{col} debería estar en variante A"

    def test_variant_b_includes_raw_ratings(self):
        """Variante B SÍ debe incluir los ratings crudos."""
        for col in _RAW:
            assert col in VARIANT_B_COLS, f"{col} debería estar en variante B"

    def test_target_not_in_variants(self):
        """home_won nunca es feature de entrada."""
        assert "home_won" not in VARIANT_A_COLS
        assert "home_won" not in VARIANT_B_COLS

    def test_id_cols_not_in_variants(self):
        """Identificadores (game_id, season, game_date) no son features."""
        for col in ("game_id", "season", "game_date"):
            assert col not in VARIANT_A_COLS, f"{col} no debería estar en variante A"
            assert col not in VARIANT_B_COLS, f"{col} no debería estar en variante B"

    def test_variants_same_size(self):
        """A y B tienen el mismo número de features (intercambian 3 por 3)."""
        assert len(VARIANT_A_COLS) == len(VARIANT_B_COLS)

    def test_variants_differ_only_in_rating_group(self):
        """A y B difieren exactamente en raw vs adj ratings — nada más."""
        a_only = set(VARIANT_A_COLS) - set(VARIANT_B_COLS)
        b_only = set(VARIANT_B_COLS) - set(VARIANT_A_COLS)
        assert a_only == _ADJ, f"A tiene features extra inesperadas: {a_only - _ADJ}"
        assert b_only == _RAW, f"B tiene features extra inesperadas: {b_only - _RAW}"


# ---------------------------------------------------------------------------
# (c) Rango de probabilidades
# ---------------------------------------------------------------------------

class TestProbabilityRange:
    """
    La regresión logística con L2 siempre produce probabilidades en (0, 1),
    nunca valores degenerados (0.0 exacto o 1.0 exacto) ni NaN.
    """

    def test_probabilities_strictly_between_zero_and_one(self):
        """P ∈ (0, 1) estricto — L2 regulariza contra coeficientes extremos."""
        rng = np.random.default_rng(7)
        n_feat = len(VARIANT_A_COLS)
        X_train = rng.normal(0, 1, (120, n_feat))
        X_val = rng.normal(0, 1, (40, n_feat))
        y_train = rng.integers(0, 2, 120).astype(float)

        scaler, lr = _fit_fold(X_train, y_train)
        probs = lr.predict_proba(scaler.transform(X_val))[:, 1]

        assert np.all(probs > 0.0), f"Hay prob ≤ 0: min={probs.min():.6f}"
        assert np.all(probs < 1.0), f"Hay prob ≥ 1: max={probs.max():.6f}"

    def test_probabilities_are_finite(self):
        """Sin NaN ni infinitos."""
        rng = np.random.default_rng(13)
        n_feat = len(VARIANT_B_COLS)
        X_train = rng.normal(0, 1, (150, n_feat))
        X_val = rng.normal(0, 1, (50, n_feat))
        y_train = rng.integers(0, 2, 150).astype(float)

        scaler, lr = _fit_fold(X_train, y_train, C=0.1)  # alta regularización
        probs = lr.predict_proba(scaler.transform(X_val))[:, 1]

        assert np.all(np.isfinite(probs)), "Hay valores no finitos en las predicciones"

    def test_walk_forward_probabilities_valid(self, logistic_result):
        """Todas las probabilidades walk-forward agregadas están en (0, 1)."""
        total = aggregate_logistic(logistic_result.folds)
        for probs, label in [
            (total.log_a_probs, "logística A"),
            (total.log_b_probs, "logística B"),
        ]:
            assert np.all(probs > 0.0), f"{label}: prob ≤ 0 detectada (min={probs.min():.6f})"
            assert np.all(probs < 1.0), f"{label}: prob ≥ 1 detectada (max={probs.max():.6f})"
            assert np.all(np.isfinite(probs)), f"{label}: valores no finitos"


# ---------------------------------------------------------------------------
# (d) Sanity en datos reales
# ---------------------------------------------------------------------------

class TestSanityRealData:
    def test_correct_number_of_folds(self, logistic_result):
        """6 folds: 2020-21 → 2025-26."""
        assert len(logistic_result.folds) == 6

    def test_logistic_a_beats_trivial(self, logistic_result):
        """
        Logística A debe tener log loss < baseline trivial.
        Si no lo hace, la logística no está aprendiendo nada — revisar features.
        """
        total = aggregate_logistic(logistic_result.folds)
        assert total.log_a_log_loss < total.const_log_loss, (
            f"Logística A ({total.log_a_log_loss:.5f}) no supera al trivial "
            f"({total.const_log_loss:.5f}) — revisar pipeline"
        )

    def test_logistic_b_beats_trivial(self, logistic_result):
        """Logística B también debe superar al trivial."""
        total = aggregate_logistic(logistic_result.folds)
        assert total.log_b_log_loss < total.const_log_loss, (
            f"Logística B ({total.log_b_log_loss:.5f}) no supera al trivial "
            f"({total.const_log_loss:.5f}) — revisar pipeline"
        )

    def test_coefs_populated(self, logistic_result):
        """El fold final debe tener coeficientes para ambas variantes."""
        assert len(logistic_result.final_coefs_a) > 0, "Sin coeficientes para variante A"
        assert len(logistic_result.final_coefs_b) > 0, "Sin coeficientes para variante B"

    def test_honest_elo_comparison(self, logistic_result):
        """
        Reporta honestamente si la logística bate al ELO.

        Este test SIEMPRE PASA — el resultado es informativo, no un requisito.
        Ver CLAUDE.md para el veredicto oficial y el diagnóstico posterior.
        Si la logística no bate al ELO, no es fracaso: es un resultado válido que
        dice que el ELO captura la mayor parte de la señal predictiva.
        """
        total = aggregate_logistic(logistic_result.folds)
        winner_ll = min(total.log_a_log_loss, total.log_b_log_loss)
        winner_label = "A" if total.log_a_log_loss <= total.log_b_log_loss else "B"
        elo_ll = total.elo_log_loss
        delta = elo_ll - winner_ll
        direction = "BATE" if winner_ll < elo_ll else "NO bate"

        print(
            f"\n[Logística vs ELO] Variante ganadora: {winner_label} "
            f"({winner_ll:.5f}) {direction} al ELO ({elo_ll:.5f}), "
            f"delta={delta:+.5f}"
        )
        # No assertion — el resultado es válido en ambos sentidos.
        # El test pasa siempre; el veredicto está en la salida de train_logistic.py
        assert True
