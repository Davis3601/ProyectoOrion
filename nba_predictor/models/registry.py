"""
Model registry — Fase 4.

Gestión del ciclo de vida del modelo: serialización, versionado y metadatos.

Por qué un directorio por versión (no un único archivo):
  Agrupa el pipeline serializado con su metadata en la misma unidad atómica.
  Si el pipeline cambia, el metadata que lo describe cambia con él — nunca
  quedan desincronizados. La ruta GCS espeja la misma estructura (Fase 5b).

Formato:
    data/models/v1_logistic_bclean_YYYY-MM-DD/
        model.joblib    — sklearn Pipeline (StandardScaler → LogisticRegression)
        metadata.json   — hash SHA-256 del parquet, features, hiperparámetros,
                          métricas walk-forward, fecha, commit git.

Regla de oro:
  El modelo de producción se entrena sobre TODOS los datos disponibles.
  Sus métricas oficiales son las del walk-forward — nunca in-sample.
  El campo "metrics_note" en el metadata lo deja explícito.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import joblib

VERSION_PREFIX = "v1_logistic_bclean"
MODEL_FILENAME = "model.joblib"
METADATA_FILENAME = "metadata.json"

# Campos que el metadata DEBE contener (para validación en tests).
REQUIRED_METADATA_FIELDS: tuple[str, ...] = (
    "version",
    "model_type",
    "training_date",
    "git_commit",
    "training_data",
    "hyperparameters",
    "walk_forward_metrics",
    "retrain_cadence_days",
    "metrics_note",
)

REQUIRED_TRAINING_DATA_FIELDS: tuple[str, ...] = (
    "parquet_sha256",
    "n_rows",
    "feature_cols",
    "n_features",
)

REQUIRED_WALK_FORWARD_FIELDS: tuple[str, ...] = (
    "log_loss",
    "accuracy",
    "brier",
    "n_val_games",
)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def make_version_name(training_date: date | None = None) -> str:
    """Genera el nombre de versión canónico: v1_logistic_bclean_YYYY-MM-DD."""
    d = training_date or date.today()
    return f"{VERSION_PREFIX}_{d.isoformat()}"


def compute_parquet_sha256(parquet_path: Path) -> str:
    """
    SHA-256 del archivo parquet de features. Garantiza trazabilidad del entrenamiento.

    El hash es del binario del parquet — si cualquier fila o columna cambia,
    el hash cambia. El metadata lo registra para reproducibilidad.
    """
    return hashlib.sha256(parquet_path.read_bytes()).hexdigest()


def _get_git_commit() -> str:
    """Obtiene el commit HEAD actual. Devuelve 'unknown' si git no está disponible."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Serialización / deserialización
# ---------------------------------------------------------------------------

def save_version(
    pipeline: Any,
    metadata: dict,
    models_dir: Path,
    version_name: str,
) -> Path:
    """
    Serializa el pipeline y el metadata en models_dir/version_name/.

    Idempotente: sobreescribe si ya existe. Crea el directorio si no existe.

    Parameters
    ----------
    pipeline     : sklearn Pipeline (StandardScaler + LogisticRegression).
    metadata     : Diccionario construido con build_metadata().
    models_dir   : Raíz del registry (data/models/).
    version_name : Nombre de versión (make_version_name()).

    Returns
    -------
    Path del directorio de la versión.
    """
    version_dir = models_dir / version_name
    version_dir.mkdir(parents=True, exist_ok=True)

    joblib.dump(pipeline, version_dir / MODEL_FILENAME)
    (version_dir / METADATA_FILENAME).write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return version_dir


def load_version(
    models_dir: Path,
    version_name: str,
) -> tuple[Any, dict]:
    """
    Carga (pipeline, metadata) desde models_dir/version_name/.

    Falla ruidosamente si la versión no existe — la filosofía del proyecto
    es "fallar ruidosamente, nunca datos a medias en silencio".

    Raises
    ------
    FileNotFoundError con mensaje que incluye el nombre de versión buscado
    y las versiones disponibles, para facilitar el diagnóstico.
    """
    version_dir = models_dir / version_name
    if not version_dir.exists():
        available = sorted(
            p.name
            for p in models_dir.glob(f"{VERSION_PREFIX}_*")
            if p.is_dir()
        ) if models_dir.exists() else []
        available_str = ", ".join(available) if available else "(ninguna)"
        raise FileNotFoundError(
            f"Versión del modelo no encontrada: '{version_name}'\n"
            f"  Buscada en: {version_dir}\n"
            f"  Versiones disponibles: {available_str}"
        )

    model_path = version_dir / MODEL_FILENAME
    metadata_path = version_dir / METADATA_FILENAME

    if not model_path.exists():
        raise FileNotFoundError(
            f"model.joblib no encontrado en: {version_dir}\n"
            f"  El directorio existe pero está incompleto."
        )
    if not metadata_path.exists():
        raise FileNotFoundError(
            f"metadata.json no encontrado en: {version_dir}\n"
            f"  El directorio existe pero está incompleto."
        )

    pipeline = joblib.load(model_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return pipeline, metadata


# ---------------------------------------------------------------------------
# Constructor de metadata
# ---------------------------------------------------------------------------

def build_metadata(
    *,
    version_name: str,
    parquet_sha256: str,
    n_rows: int,
    feature_cols: list[str],
    hyperparameters: dict,
    walk_forward_metrics: dict,
    retrain_cadence_days: int,
    training_date: date | None = None,
    git_commit: str | None = None,
) -> dict:
    """
    Construye el diccionario canónico de metadata para metadata.json.

    Todos los parámetros son obligatorios como keyword arguments para evitar
    omisiones silenciosas. La función valida que walk_forward_metrics tenga
    los campos requeridos.

    Las métricas son siempre del walk-forward — nunca in-sample. El campo
    metrics_note lo deja explícito en el JSON para cualquier consumidor futuro.
    """
    missing = [f for f in REQUIRED_WALK_FORWARD_FIELDS if f not in walk_forward_metrics]
    if missing:
        raise ValueError(
            f"walk_forward_metrics faltan campos obligatorios: {missing}"
        )

    return {
        "version": version_name,
        "model_type": "logistic_b_clean",
        "training_date": (training_date or date.today()).isoformat(),
        "git_commit": git_commit or _get_git_commit(),
        "training_data": {
            "parquet_sha256": parquet_sha256,
            "n_rows": n_rows,
            "feature_cols": feature_cols,
            "n_features": len(feature_cols),
        },
        "hyperparameters": hyperparameters,
        "walk_forward_metrics": walk_forward_metrics,
        "retrain_cadence_days": retrain_cadence_days,
        "metrics_note": (
            "All metrics are from temporal walk-forward validation "
            "(6 folds, 5823 games, seasons 2020-21 to 2025-26). "
            "The production model is trained on all available data and has "
            "no separate validation set. In-sample metrics are not reported "
            "to avoid misleading performance comparisons."
        ),
    }
