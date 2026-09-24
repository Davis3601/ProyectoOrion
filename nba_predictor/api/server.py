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
from datetime import date, datetime, timezone

from fastapi import FastAPI, HTTPException, Query

from nba_predictor.api.daily_predictions import (
    DailyResult,
    build_daily_predictions,
    format_daily_message,
    format_players_message,
)
from nba_predictor.api.predictions_log import (
    build_log_rows,
    resolve_model_version,
    resolve_served_by,
    write_predictions_log,
)
from nba_predictor.api.player_predictions_log import (
    build_player_log_rows,
    write_player_predictions_log,
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


def _current_season(reference_date: date | None = None) -> str:
    """Temporada activa. Env NBA_PREDICTOR_SEASON si presente; si no, derivada de reference_date.

    reference_date debe ser target_date del request (no date.today()): en pruebas
    de pre-temporada con ?date=2026-10-21 desde agosto, usar today daría '2025-26'.
    """
    env_season = os.getenv("NBA_PREDICTOR_SEASON")
    if env_season:
        return env_season
    d = reference_date or date.today()
    # La temporada NBA arranca en octubre: oct-dic es el año en curso
    if d.month >= 10:
        return f"{d.year}-{(d.year + 1) % 100:02d}"
    return f"{d.year - 1}-{d.year % 100:02d}"


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
    # Env var NBA_PREDICTOR_MODEL_VERSION fija la versión en despliegues pinados;
    # si no está presente, el store descubre la más reciente (local o GCS).
    version_name = os.getenv("NBA_PREDICTOR_MODEL_VERSION") or store.get_latest_model_version()
    pipeline, metadata = store.load_model(version_name)
    app.state.store = _ModelCachedStore(store, pipeline, metadata)
    app.state.version_name = version_name
    # model_version del schema de predictions_log: id del registry + hash del
    # parquet. Se compone UNA vez al arranque (el metadata no cambia en vida
    # de la instancia) y viaja idéntico a todas las filas que sirva.
    app.state.log_model_version = resolve_model_version(version_name, metadata)
    _log.info("Modelo cargado: %s", version_name)
    yield
    _log.info("Shutdown.")


# ---------------------------------------------------------------------------
# Aplicación
# ---------------------------------------------------------------------------


app = FastAPI(title="NBA Predictions API", version="0.1.0", lifespan=lifespan)


# Catálogo de nombres cacheado por FECHA. Una lectura por proceso y día: la
# tabla players cambia como mucho una vez al día (la escribe el ingest job a
# las 12:00 UTC), y releerla en cada request costaría una consulta a BigQuery
# por publicación sin aportar nada. La clave es la fecha y no un TTL para que
# la recarga caiga siempre del lado correcto de la corrida del job.
_PLAYER_MAP_CACHE: dict[str, dict[int, str]] = {}


def _player_map(store) -> dict[int, str]:
    """Catálogo player_id → nombre, cacheado por día.

    Un fallo de lectura devuelve {} y NO levanta: quien decide qué hacer con un
    catálogo vacío es build_daily_predictions, que lo declara como feed caído
    (D-PROD-1d). Aquí solo se registra el error con todo el ruido posible.
    """
    hoy = date.today().isoformat()
    if hoy in _PLAYER_MAP_CACHE:
        return _PLAYER_MAP_CACHE[hoy]
    try:
        nombres = store.load_player_names()
    except Exception as exc:
        _log.error("No se pudo cargar el catálogo de nombres (players): %s", exc)
        nombres = {}
    if nombres:
        # Solo se cachea lo bueno: un {} cacheado condenaría al proceso a
        # publicar degradado el resto del día aunque la tabla se recupere.
        _PLAYER_MAP_CACHE.clear()
        _PLAYER_MAP_CACHE[hoy] = nombres
    _log.info("Catálogo de nombres: %d jugadores", len(nombres))
    return nombres


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
    season = _current_season(target_date)  # usa target_date, no date.today()

    result: DailyResult = build_daily_predictions(
        target_date=target_date,
        store=app.state.store,
        season=season,
        version_name=app.state.version_name,
        player_map=_player_map(app.state.store),
    )
    message = format_daily_message(result)
    # players_message es el SEGUNDO mensaje de Telegram (D-PROD-1b): cadena
    # vacia cuando ningun partido trae datos de jugador, y entonces n8n no
    # dispara su Send. message queda INTACTO — lo validado no se toca.
    response = {
        "message": message,
        "players_message": format_players_message(result),
        "data": asdict(result),
    }

    # predictions_log (13e-2.4): la evidencia se registra DESPUÉS de tener la
    # respuesta construida y ANTES de devolverla — una fila por partido servido,
    # sin deduplicar (cada servida es un hecho distinto).
    # Best-effort (Decisión CERRADA 2026-08-26): write_predictions_log jamás
    # levanta; un fallo de escritura deja WARNING y la respuesta se sirve
    # completa e intacta. Día sin partidos → cero filas, cero escritura.
    served_by = resolve_served_by()
    model_version = (
        getattr(app.state, "log_model_version", None) or result.model_version
    )
    # Un unico sello de tiempo para los dos logs: es lo que permite cruzarlos
    # como la MISMA servida. Dos relojes darian dos instantes distintos para un
    # solo hecho.
    stamp = datetime.now(timezone.utc)

    write_predictions_log(
        app.state.store,
        build_log_rows(
            result,
            served_by=served_by,
            model_version=model_version,
            predicted_at_utc=stamp,
        ),
    )

    # player_predictions_log (D-PROD-1c): misma disciplina y mismo best-effort.
    # Solo registra los partidos cuya seccion se PUBLICO (players_data_available):
    # el log es evidencia de lo publicado, no de lo que se pudo haber publicado.
    write_player_predictions_log(
        app.state.store,
        build_player_log_rows(
            result,
            served_by=served_by,
            model_version=model_version,
            predicted_at_utc=stamp,
        ),
    )

    return response
