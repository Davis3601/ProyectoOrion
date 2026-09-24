"""
player_predictions_log — EVIDENCIA de los destacados publicados (D-PROD-1c).

Espejo exacto de predictions_log en semántica y en contrato: append-only, una
fila por jugador por servida, sin deduplicar, SIN resultado del partido. El
grading se computa después como JOIN contra player_game_stats (grading =
query; log = intocable).

POR QUÉ SE GUARDA EL FLOAT Y NO EL ENTERO PUBLICADO: el mensaje muestra
"24 pts" porque redondea; el log guarda 24.0 o 23.5 según lo observado. El
entero publicado es recuperable aplicando el redondeo documentado
(_round_half_up), mientras que al revés se pierde información para siempre.
La evidencia guarda el dato, no su presentación.

Best-effort (precedente CERRADO 2026-08-26 para predictions_log): un fallo de
escritura produce logging.warning y la respuesta se sirve completa (200). La
misión del endpoint es la predicción.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nba_predictor.api.daily_predictions import DailyResult
    from nba_predictor.storage.base import DataStore

_log = logging.getLogger(__name__)

# Los 16 campos del schema, en orden. Definición única del contrato: los tests
# comparan contra esta constante, no contra una lista repetida.
PLAYER_LOG_FIELDS: tuple[str, ...] = (
    "game_id",
    "game_date",
    "player_id",
    "player_name",
    "team_tricode",
    "status_flag",
    "pts_median",
    "pts_min",
    "pts_max",
    "reb_median",
    "ast_median",
    "fg3m_median",
    "games_in_window",
    "model_version",
    "predicted_at_utc",
    "served_by",
)


def build_player_log_rows(
    result: "DailyResult",
    *,
    served_by: str,
    model_version: str | None,
    predicted_at_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    """DailyResult → una fila por jugador destacado servido. Pura salvo el reloj.

    Solo entran los partidos con players_data_available: si el flag es False la
    sección no se publicó, y el log registra lo PUBLICADO. Un día sin
    destacados devuelve lista vacía — no hay fila que inventar.

    El sello de tiempo es el mismo para todas las filas de una servida: es el
    instante en que se sirvió, no en que se serializó cada fila. Se comparte
    con predictions_log cuando el llamador inyecta el mismo valor, que es lo
    que permite cruzar ambos logs de una misma servida.

    model_version viaja aunque el destacado no salga del modelo: identifica la
    SERVIDA, y sin él no se podría alinear esta evidencia con la de
    predictions_log.
    """
    stamp = predicted_at_utc or datetime.now(timezone.utc)

    filas: list[dict[str, Any]] = []
    for gp in result.games:
        if not gp.players_data_available:
            continue
        for p in gp.players:
            tricode = gp.home_tricode if p.team == "home" else gp.away_tricode
            filas.append({
                "game_id": gp.game_id,
                "game_date": gp.game_date,
                "player_id": p.player_id,
                "player_name": p.player_name,
                "team_tricode": tricode,
                "status_flag": p.status,
                "pts_median": float(p.pts_median),
                "pts_min": float(p.pts_min),
                "pts_max": float(p.pts_max),
                "reb_median": float(p.reb_median),
                "ast_median": float(p.ast_median),
                "fg3m_median": (
                    None if p.fg3m_median is None else float(p.fg3m_median)
                ),
                "games_in_window": int(p.games_in_window),
                "model_version": model_version,
                "predicted_at_utc": stamp,
                "served_by": served_by,
            })
    return filas


def write_player_predictions_log(store: "DataStore", rows: list[dict[str, Any]]) -> bool:
    """Escribe las filas. BEST-EFFORT: nunca propaga la excepción.

    Devuelve True si escribió (o si no había nada que escribir), False si
    falló. El endpoint ignora el valor deliberadamente: pase lo que pase aquí,
    la respuesta se sirve completa e intacta.
    """
    if not rows:
        return True
    try:
        store.save_player_predictions_log(rows)
        return True
    except Exception as exc:
        _log.warning(
            "No se pudo escribir player_predictions_log (%d filas, servida por %s): %s",
            len(rows),
            rows[0].get("served_by"),
            exc,
        )
        return False


# Reexportado por conveniencia: la revisión que sirvió es la misma para ambos
# logs, y duplicar la función habría permitido que divergieran.
def resolve_served_by() -> str:
    """Revisión de Cloud Run que sirvió (K_REVISION), o "local" fuera de ella."""
    return os.getenv("K_REVISION") or "local"
