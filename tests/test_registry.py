"""
Tests para nba_predictor/models/registry.py y storage save_model/load_model.

(a) TestRoundTrip       — save → load produce predicciones idénticas al original
(b) TestMetadata        — metadata tiene campos obligatorios y hash coincide con parquet real
(c) TestLoadMissing     — load de versión inexistente falla ruidosamente con mensaje claro
(d) TestRealDataProbs   — modelo cargado predice probabilidades en (0,1) sobre muestra real
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS
from nba_predictor.models.registry import (
    REQUIRED_METADATA_FIELDS,
    REQUIRED_TRAINING_DATA_FIELDS,
    REQUIRED_WALK_FORWARD_FIELDS,
    build_metadata,
    compute_parquet_sha256,
    load_version,
    make_version_name,
    save_version,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_synthetic_pipeline(seed: int = 0) -> Pipeline:
    """Pipeline sklearn con datos sintéticos, listo para predict_proba."""
    rng = np.random.default_rng(seed)
    n, nf = 200, len(OFFICIAL_LOGISTIC_COLS)
    X = rng.normal(0, 1, (n, nf))
    y = rng.integers(0, 2, n).astype(float)
    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("logistic", LogisticRegression(C=1.0, solver="lbfgs", max_iter=500, random_state=42)),
    ])
    pipeline.fit(X, y)
    return pipeline


def _make_minimal_metadata(
    parquet_sha256: str = "abc123",
    version_name: str = "v1_logistic_bclean_2099-01-01",
) -> dict:
    """Metadata mínimo con todos los campos obligatorios."""
    return build_metadata(
        version_name=version_name,
        parquet_sha256=parquet_sha256,
        n_rows=9643,
        feature_cols=OFFICIAL_LOGISTIC_COLS,
        hyperparameters={"C": 1.0, "solver": "lbfgs"},
        walk_forward_metrics={
            "log_loss": 0.63138,
            "accuracy": 0.645,
            "brier": 0.22000,
            "n_val_games": 5823,
        },
        retrain_cadence_days=7,
        git_commit="test_commit",
    )


# ---------------------------------------------------------------------------
# (a) Round-trip: save → load → predicciones idénticas
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_round_trip_predictions_identical(self, tmp_path: Path):
        """
        Guardar y cargar un pipeline debe producir probabilidades bit a bit idénticas.

        Este test captura la regresión más peligrosa del registry: que joblib
        cambie la serialización entre versiones y las predicciones cambien.
        """
        pipeline = _make_synthetic_pipeline(seed=42)
        version_name = make_version_name()
        metadata = _make_minimal_metadata(version_name=version_name)

        models_dir = tmp_path / "models"
        save_version(pipeline, metadata, models_dir, version_name)
        loaded_pipeline, loaded_meta = load_version(models_dir, version_name)

        rng = np.random.default_rng(7)
        X_test = pd.DataFrame(
            rng.normal(0, 1, (50, len(OFFICIAL_LOGISTIC_COLS))),
            columns=OFFICIAL_LOGISTIC_COLS,
        )
        probs_original = pipeline.predict_proba(X_test)[:, 1]
        probs_loaded = loaded_pipeline.predict_proba(X_test)[:, 1]

        np.testing.assert_array_equal(
            probs_original, probs_loaded,
            err_msg="Predicciones difieren tras serialización/deserialización",
        )

    def test_round_trip_metadata_preserved(self, tmp_path: Path):
        """El metadata cargado es idéntico al que se guardó."""
        pipeline = _make_synthetic_pipeline(seed=1)
        version_name = make_version_name()
        metadata = _make_minimal_metadata(version_name=version_name)

        models_dir = tmp_path / "models"
        save_version(pipeline, metadata, models_dir, version_name)
        _, loaded_meta = load_version(models_dir, version_name)

        assert loaded_meta == metadata, "Metadata difiere tras guardar/cargar"

    def test_round_trip_via_datastore(self, tmp_path: Path):
        """El round-trip funciona a través de la interfaz DataStore (LocalDataStore)."""
        from nba_predictor.storage.local import LocalDataStore

        db_path = tmp_path / "nba.sqlite"
        raw_dir = tmp_path / "raw"
        processed_dir = tmp_path / "processed"
        processed_dir.mkdir(parents=True)

        ds = LocalDataStore(db_path=db_path, raw_dir=raw_dir, processed_dir=processed_dir)
        pipeline = _make_synthetic_pipeline(seed=3)
        version_name = make_version_name()
        metadata = _make_minimal_metadata(version_name=version_name)

        saved_path = ds.save_model(pipeline, metadata, version_name)
        loaded_pipeline, loaded_meta = ds.load_model(version_name)

        assert saved_path.exists(), "La ruta guardada no existe"
        assert loaded_meta["version"] == version_name

        rng = np.random.default_rng(11)
        X_test = pd.DataFrame(
            rng.normal(0, 1, (30, len(OFFICIAL_LOGISTIC_COLS))),
            columns=OFFICIAL_LOGISTIC_COLS,
        )
        probs_orig = pipeline.predict_proba(X_test)[:, 1]
        probs_load = loaded_pipeline.predict_proba(X_test)[:, 1]
        np.testing.assert_array_equal(probs_orig, probs_load)


# ---------------------------------------------------------------------------
# (b) Metadata: campos obligatorios y hash coincide con el parquet real
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_required_top_level_fields_present(self, tmp_path: Path):
        """Todos los campos obligatorios de primer nivel están en el metadata."""
        metadata = _make_minimal_metadata()
        for field in REQUIRED_METADATA_FIELDS:
            assert field in metadata, f"Campo obligatorio ausente: '{field}'"

    def test_required_training_data_fields_present(self, tmp_path: Path):
        """training_data contiene todos sus subcampos obligatorios."""
        metadata = _make_minimal_metadata()
        for field in REQUIRED_TRAINING_DATA_FIELDS:
            assert field in metadata["training_data"], (
                f"training_data falta campo: '{field}'"
            )

    def test_required_walk_forward_fields_present(self, tmp_path: Path):
        """walk_forward_metrics contiene los campos obligatorios."""
        metadata = _make_minimal_metadata()
        for field in REQUIRED_WALK_FORWARD_FIELDS:
            assert field in metadata["walk_forward_metrics"], (
                f"walk_forward_metrics falta campo: '{field}'"
            )

    def test_feature_cols_matches_official(self, tmp_path: Path):
        """La lista de features en metadata coincide con OFFICIAL_LOGISTIC_COLS."""
        metadata = _make_minimal_metadata()
        assert metadata["training_data"]["feature_cols"] == OFFICIAL_LOGISTIC_COLS

    def test_n_features_consistent(self, tmp_path: Path):
        """n_features == len(feature_cols)."""
        metadata = _make_minimal_metadata()
        td = metadata["training_data"]
        assert td["n_features"] == len(td["feature_cols"])

    def test_build_metadata_rejects_missing_walk_forward_fields(self):
        """build_metadata falla si walk_forward_metrics no tiene campos obligatorios."""
        with pytest.raises(ValueError, match="walk_forward_metrics"):
            build_metadata(
                version_name="v1_logistic_bclean_2099-01-01",
                parquet_sha256="abc",
                n_rows=100,
                feature_cols=OFFICIAL_LOGISTIC_COLS,
                hyperparameters={},
                walk_forward_metrics={"log_loss": 0.63},  # faltan accuracy, brier, n_val_games
                retrain_cadence_days=7,
            )

    def test_sha256_matches_real_parquet(self):
        """
        El hash SHA-256 en el metadata coincide con el del parquet real en disco.

        Este es el test de trazabilidad: garantiza que el modelo guardado
        se puede asociar al exacto conjunto de entrenamiento que lo produjo.
        """
        from nba_predictor.config import settings
        parquet_path = settings.processed_dir / "features_v1.parquet"

        if not parquet_path.exists():
            pytest.skip("features_v1.parquet no disponible")

        expected_hash = hashlib.sha256(parquet_path.read_bytes()).hexdigest()
        computed_hash = compute_parquet_sha256(parquet_path)

        assert computed_hash == expected_hash, (
            "compute_parquet_sha256 devuelve un hash incorrecto"
        )

        metadata = _make_minimal_metadata(parquet_sha256=computed_hash)
        assert metadata["training_data"]["parquet_sha256"] == expected_hash, (
            "El hash en metadata no coincide con el parquet real"
        )

    def test_version_name_format(self):
        """make_version_name genera el formato canónico."""
        from datetime import date
        name = make_version_name(training_date=date(2026, 8, 12))
        assert name == "v1_logistic_bclean_2026-08-12"


# ---------------------------------------------------------------------------
# (c) Load de versión inexistente: falla ruidosamente con mensaje claro
# ---------------------------------------------------------------------------

class TestLoadMissing:
    def test_load_nonexistent_version_raises(self, tmp_path: Path):
        """load_version lanza FileNotFoundError si la versión no existe."""
        models_dir = tmp_path / "models"
        models_dir.mkdir()

        with pytest.raises(FileNotFoundError):
            load_version(models_dir, "v1_logistic_bclean_9999-01-01")

    def test_error_message_contains_version_name(self, tmp_path: Path):
        """El mensaje de error incluye el nombre de la versión buscada."""
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        missing = "v1_logistic_bclean_9999-06-15"

        with pytest.raises(FileNotFoundError, match=missing):
            load_version(models_dir, missing)

    def test_error_message_lists_available_versions(self, tmp_path: Path):
        """El mensaje de error lista las versiones disponibles para facilitar diagnóstico."""
        models_dir = tmp_path / "models"
        # Crear una versión existente
        existing = "v1_logistic_bclean_2026-01-01"
        (models_dir / existing).mkdir(parents=True)

        with pytest.raises(FileNotFoundError, match=existing):
            load_version(models_dir, "v1_logistic_bclean_9999-01-01")

    def test_load_via_datastore_raises(self, tmp_path: Path):
        """El error se propaga correctamente a través de la interfaz DataStore."""
        from nba_predictor.storage.local import LocalDataStore

        db_path = tmp_path / "nba.sqlite"
        raw_dir = tmp_path / "raw"
        processed_dir = tmp_path / "processed"
        processed_dir.mkdir(parents=True)

        ds = LocalDataStore(db_path=db_path, raw_dir=raw_dir, processed_dir=processed_dir)

        with pytest.raises(FileNotFoundError):
            ds.load_model("v1_logistic_bclean_9999-01-01")

    def test_missing_model_joblib_raises(self, tmp_path: Path):
        """Si el directorio existe pero falta model.joblib, falla con mensaje claro."""
        models_dir = tmp_path / "models"
        version_name = "v1_logistic_bclean_2026-08-12"
        version_dir = models_dir / version_name
        version_dir.mkdir(parents=True)
        # Creamos metadata.json pero no model.joblib
        (version_dir / "metadata.json").write_text("{}", encoding="utf-8")

        with pytest.raises(FileNotFoundError, match="model.joblib"):
            load_version(models_dir, version_name)


# ---------------------------------------------------------------------------
# (d) Modelo cargado predice probabilidades en (0,1) sobre muestra real
# ---------------------------------------------------------------------------

class TestRealDataProbs:
    """
    Carga el modelo guardado (o lo entrena y guarda si no existe) y verifica
    que las predicciones sobre datos reales están en el rango (0,1).
    """

    @pytest.fixture(scope="class")
    def loaded_pipeline_and_features(self, tmp_path_factory: pytest.TempPathFactory):
        """Entrena, guarda y carga un pipeline sobre datos reales."""
        from nba_predictor.storage import get_datastore

        ds = get_datastore()
        try:
            features = ds.load_features()
        except FileNotFoundError:
            pytest.skip("features_v1.parquet no disponible")

        pipeline = _make_synthetic_pipeline(seed=99)
        # Re-entrenar sobre datos reales para que las features sean compatibles
        X = features[OFFICIAL_LOGISTIC_COLS].to_numpy(dtype=float)
        y = features["home_won"].to_numpy(dtype=float)
        pipeline.fit(X, y)

        tmp_path = tmp_path_factory.mktemp("registry_real")
        models_dir = tmp_path / "models"
        version_name = make_version_name()
        metadata = _make_minimal_metadata(version_name=version_name)
        save_version(pipeline, metadata, models_dir, version_name)
        loaded_pipeline, _ = load_version(models_dir, version_name)
        return loaded_pipeline, features

    def test_probs_in_open_interval(self, loaded_pipeline_and_features):
        """Las probabilidades del modelo cargado sobre datos reales están en (0,1)."""
        loaded_pipeline, features = loaded_pipeline_and_features
        sample = features.head(200)
        X = sample[OFFICIAL_LOGISTIC_COLS].to_numpy(dtype=float)
        probs = loaded_pipeline.predict_proba(X)[:, 1]

        assert np.all(probs > 0.0), f"Prob ≤ 0 — min={probs.min():.6f}"
        assert np.all(probs < 1.0), f"Prob ≥ 1 — max={probs.max():.6f}"
        assert np.all(np.isfinite(probs)), "NaN o infinito detectado"

    def test_probs_have_reasonable_spread(self, loaded_pipeline_and_features):
        """El modelo no colapsa a predecir siempre el mismo valor."""
        loaded_pipeline, features = loaded_pipeline_and_features
        X = features[OFFICIAL_LOGISTIC_COLS].to_numpy(dtype=float)
        probs = loaded_pipeline.predict_proba(X)[:, 1]
        assert probs.std() > 0.05, (
            f"Poca varianza en predicciones (std={probs.std():.4f}) — "
            "el modelo podría no estar usando las features"
        )
