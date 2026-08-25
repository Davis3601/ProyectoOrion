"""
Servidor FastAPI — endpoint de predicciones del día (13e-2, Decisión 13e-2.2).

Capa DELGADA sobre daily_predictions.py: ciclo de vida + rutas.
Sin código de auth: IAM de Cloud Run valida antes de que el request llegue aquí
(el servicio se despliega SIN --allow-unauthenticated).

Ciclo de vida:
    Startup: DataStore + modelo se inicializan UNA vez. Cloud Run reutiliza
             instancias; recargar el joblib (o descargarlo de GCS) por request
             sería desperdicio.
    Por request: feed de injury report se consulta FRESCO — snapshot más
                 reciente al momento de invocar (Decisión 3 del feed).
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date

from fastapi import FastAPI, HTTPException, Query

from nba_predictor.api.daily_predictions import (
    DailyResult,
    build_daily_predictions,
    format_daily_message,
)
from nba_predictor.storage import get_datastore

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Proxy: sirve el modelo ya cargado al arranque
# ---------------------------------------------------------------------------


class _ModelCachedStore:
    """Forwarding proxy sobre DataStore: load_model retorna el pipeline en caché.

    Todos los demás métodos se delegan al store real — el feed de injury report
    (save_raw_injury_report) y los lookups de features (load_*) siguen vivos.
    """

    def __init__(self, store, pipeline, metadata):
        self._store = store
        self._pipeline = pipeline
        self._metadata = metadata

    def __getattr__(self, name: str):
        return getattr(self._store, name)

    def load_model(self, version_name: str):  # noqa: ARG002
        return self._pipeline, self._metadata


# ---------------------------------------------------------------------------
# Helpers de startup / request
# ---------------------------------------------------------------------------


def _discover_latest_version() -> str:
    """Versión más reciente del registry. Env NBA_PREDICTOR_MODEL_VERSION si presente."""
    env_ver = os.getenv("NBA_PREDICTOR_MODEL_VERSION")
    if env_ver:
        return env_ver

    from nba_predictor.config import settings
    from nba_predictor.models.registry import VERSION_PREFIX

    models_dir = settings.processed_dir.parent / "models"
    if not models_dir.exists():
        raise FileNotFoundError(f"Directorio de modelos no encontrado: {models_dir}")
    versions = sorted(
        p.name for p in models_dir.glob(f"{VERSION_PREFIX}_*") if p.is_dir()
    )
    if not versions:
        raise FileNotFoundError(f"Sin versiones de modelo en {models_dir}")
    return versions[-1]


def _current_season() -> str:
    """Temporada activa. Env NBA_PREDICTOR_SEASON si presente; si no, derivada de la fecha."""
    env_season = os.getenv("NBA_PREDICTOR_SEASON")
    if env_season:
        return env_season
    today = date.today()
    # La temporada NBA arranca en octubre: oct-dic es el año en curso
    if today.month >= 10:
        return f"{today.year}-{(today.year + 1) % 100:02d}"
    return f"{today.year - 1}-{today.year % 100:02d}"


def _parse_date(date_str: str | None) -> date:
    if date_str is None:
        return date.today()
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Fecha inválida: {date_str!r}")


# ---------------------------------------------------------------------------
# Ciclo de vida: carga única al arranque
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    _log.info("Startup: inicializando DataStore y cargando modelo...")
    store = get_datastore()
    version_name = _discover_latest_version()
    pipeline, metadata = store.load_model(version_name)
    app.state.store = _ModelCachedStore(store, pipeline, metadata)
    app.state.version_name = version_name
    _log.info("Modelo cargado: %s", version_name)
    yield
    _log.info("Shutdown.")


# ---------------------------------------------------------------------------
# Aplicación
# ---------------------------------------------------------------------------


app = FastAPI(title="NBA Predictions API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    """
    Liveness probe. Verifica que el modelo cargó al arranque.

    200 si el startup tuvo éxito (model_version confirma qué versión está activa).
    503 si el estado del servidor es inconsistente.
    """
    version_name = getattr(app.state, "version_name", None)
    if not version_name:
        raise HTTPException(status_code=503, detail="Modelo no inicializado")
    return {"status": "ok", "model_version": version_name}


@app.get("/predictions/today")
def predictions_today(
    date_str: str | None = Query(None, alias="date"),
):
    """
    Predicciones del día.

    ?date=YYYY-MM-DD para re-invocación tardía o tests (omitir = hoy).

    200: escenarios 1-3 de Decisión 13e-2.5 (sin partidos, feed caído, NYS).
         La degradación viene declarada DENTRO del payload — n8n no necesita
         lógica condicional para distinguirlos.
    500: escenario 4 (schedule inaccesible, modelo no carga, excepción no
         manejada del núcleo). La excepción sube con logging claro; nunca se
         traga para devolver un 200 vacío (mentira silenciosa prohibida).
    """
    target_date = _parse_date(date_str)
    season = _current_season()

    result: DailyResult = build_daily_predictions(
        target_date=target_date,
        store=app.state.store,
        season=season,
        version_name=app.state.version_name,
    )
    message = format_daily_message(result)
    return {"message": message, "data": asdict(result)}
