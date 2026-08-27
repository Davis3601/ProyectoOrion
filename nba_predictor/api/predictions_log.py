"""
predictions_log — EVIDENCIA de cada servida del endpoint (Decisión 13e-2.4).

El log NO es telemetría: es el expediente contra el que se adjudicará el
criterio pre-registrado de comercialización en enero 2027. Cada predicción
queda congelada con un timestamp anterior al partido, en un almacén
append-only. Nada de esto se actualiza jamás — el grading se computa después
como JOIN contra los resultados en BigQuery (grading = query; log = intocable).

Reparto de responsabilidades:
    build_log_rows()      — función PURA: DailyResult → filas del schema.
    write_predictions_log() — efecto de borde BEST-EFFORT sobre el DataStore.

Best-effort (Decisión CERRADA 2026-08-26): un fallo de escritura produce
logging.warning y la respuesta se sirve completa (200). La misión del endpoint
es la predicción. La red que sostiene esa decisión es el gate operativo, que
audita a diario que haya filas — un fallo de log no puede pasar silencioso más
de un día.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nba_predictor.api.daily_predictions import DailyResult
    from nba_predictor.storage.base import DataStore

_log = logging.getLogger(__name__)

# Las 9 claves del schema cerrado, en orden. Definición única del contrato:
# los tests las comparan contra esta constante, no contra una lista repetida.
PREDICTIONS_LOG_FIELDS: tuple[str, ...] = (
    "game_id",
    "game_date",
    "home_team",
    "away_team",
    "p_home_win",
    "model_version",
    "predicted_at_utc",
    "served_by",
    "absences_applied",
)

# Identidad de la revisión cuando el proceso no corre en Cloud Run (laptop,
# tests). Nombrarlo explícitamente evita un None que luego habría que
# interpretar en el análisis.
_SERVED_BY_FALLBACK = "local"


def resolve_served_by() -> str:
    """Revisión de Cloud Run que sirvió la predicción.

    K_REVISION la inyecta Cloud Run en cada contenedor (p.ej.
    "predictions-api-00005-abc"). Fuera de Cloud Run → "local".
    """
    return os.getenv("K_REVISION") or _SERVED_BY_FALLBACK


def resolve_model_version(version_name: str | None, metadata: dict | None) -> str | None:
    """Compone el `model_version` del schema: id del registry + hash.

    El id solo no basta como evidencia: identifica el directorio, no los datos
    con los que se entrenó. El hash SHA-256 del parquet (que el registry ya
    guarda en metadata) cierra la cadena de integridad datos → modelo →
    predicción registrada.

    Formato: "{version_name}@{parquet_sha256}". Sin metadata utilizable
    devuelve el id a secas — degradar el detalle es preferible a no registrar
    la predicción.
    """
    if not version_name:
        return None
    sha = None
    if isinstance(metadata, dict):
        training = metadata.get("training_data")
        if isinstance(training, dict):
            sha = training.get("parquet_sha256")
    return f"{version_name}@{sha}" if sha else version_name


def _encode_absences(home_ids: list[int], away_ids: list[int]) -> str:
    """Serializa los player_ids Out aplicados como JSON compacto.

    JSON string (y no columna repetida) para que la fila sea IDÉNTICA en ambos
    adapters: SQLite no tiene tipo array y el espejo local debe poder compararse
    contra BigQuery sin traducción. Se conserva la atribución por equipo: una
    lista plana perdería de qué lado del partido faltaba el jugador.
    """
    return json.dumps(
        {"home": list(home_ids), "away": list(away_ids)},
        separators=(",", ":"),
    )


def build_log_rows(
    result: "DailyResult",
    *,
    served_by: str,
    model_version: str | None,
    predicted_at_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    """DailyResult → una fila por partido servido. Función pura salvo el reloj.

    predicted_at_utc se puede inyectar (tests); por defecto se sella AHORA, en
    UTC con precisión de microsegundos. El sello es el mismo para todas las
    filas de una servida: es el instante en que se sirvió, no el instante en
    que se serializó cada fila.

    Día sin partidos → lista vacía. No hay fila que escribir y tampoco una fila
    "vacía" que inventar: el heartbeat de descanso vive en el mensaje, no en la
    evidencia (13e-2.5, escenario 1).
    """
    stamp = predicted_at_utc or datetime.now(timezone.utc)

    return [
        {
            "game_id": gp.game_id,
            "game_date": gp.game_date,
            "home_team": gp.home_tricode,
            "away_team": gp.away_tricode,
            "p_home_win": float(gp.probability_home),
            "model_version": model_version,
            "predicted_at_utc": stamp,
            "served_by": served_by,
            "absences_applied": _encode_absences(
                gp.home_absence_ids, gp.away_absence_ids
            ),
        }
        for gp in result.games
    ]


def write_predictions_log(store: "DataStore", rows: list[dict[str, Any]]) -> bool:
    """Escribe las filas. BEST-EFFORT: nunca propaga la excepción.

    Devuelve True si escribió (o si no había nada que escribir), False si falló.
    El valor de retorno existe para los tests y para futura observabilidad; el
    endpoint lo ignora deliberadamente: pase lo que pase aquí, la respuesta se
    sirve completa e intacta.
    """
    if not rows:
        return True
    try:
        store.save_predictions_log(rows)
        return True
    except Exception as exc:
        # WARNING visible en Cloud Run: la red del gate operativo es la
        # auditoría diaria de que haya filas, y este log es su rastro.
        _log.warning(
            "No se pudo escribir predictions_log (%d filas, servida por %s): %s",
            len(rows),
            rows[0].get("served_by"),
            exc,
        )
        return False
