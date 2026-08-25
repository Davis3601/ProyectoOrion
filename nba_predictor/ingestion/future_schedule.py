"""
Calendário de partidos futuros — Fase 5a (Decisión 1 CERRADA).

Consulta el calendario de la temporada corriente vía ScheduleLeagueV2 y
devuelve partidos NO jugados aún (gameStatus != 3). NUNCA escribe en la
tabla games: esa tabla es la capa STRUCTURED de partidos JUGADOS y mezclar
programados contaminaría su semántica y rompería sanity checks.

Restricción de offseason:
    La NBA está en offseason hasta octubre 2026. Durante ese período este
    módulo devuelve lista vacía — no hay nada que predecir aún. El pipeline
    en vivo se valida contra partidos históricos (tests/test_live_equivalence).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

_log = logging.getLogger(__name__)

# gameStatus códigos del endpoint:
#   1 = programado (no empezado)
#   2 = en curso
#   3 = finalizado
_STATUS_NOT_STARTED = 1


@dataclass(frozen=True)
class ScheduledGame:
    """Partido programado; devuelto en memoria, nunca persistido en games."""

    game_id: str
    game_date: date
    home_team_id: int
    away_team_id: int
    home_tricode: str
    away_tricode: str
    season: str
    tip_off_et: datetime | None = None  # tip-off en ET; None si el schedule no lo provee


def fetch_future_schedule(
    season: str,
    from_date: date | None = None,
) -> list[ScheduledGame]:
    """
    Devuelve los partidos programados (no jugados) de una temporada.

    Consulta ScheduleLeagueV2 del endpoint stats.nba.com. No persiste nada —
    trabaja totalmente en memoria (Decisión 1).

    Parameters
    ----------
    season    : formato '2026-27' (la temporada que arranca en 2026).
    from_date : si se indica, filtra a game_date >= from_date. Por defecto
                hoy, lo que equivale a "partidos futuros desde ahora".

    Returns
    -------
    Lista de ScheduledGame con gameStatus == 1 (no empezados), ordenada por
    game_date. Lista vacía durante el offseason o si no hay partidos futuros.

    Raises
    ------
    RuntimeError si la llamada a la API falla después de los reintentos.
    """
    import pandas as pd
    from nba_api.stats.endpoints import scheduleleaguev2

    cutoff = from_date or date.today()

    _log.info(f"Consultando ScheduleLeagueV2 para temporada {season}...")
    try:
        endpoint = scheduleleaguev2.ScheduleLeagueV2(
            season=season,
            league_id="00",
            timeout=30,
        )
        raw = endpoint.get_data_frames()[0]
    except Exception as exc:
        raise RuntimeError(
            f"Error al consultar ScheduleLeagueV2 para {season}: {exc}"
        ) from exc

    if raw.empty:
        _log.info("  Calendario vacío — offseason o temporada no publicada aún.")
        return []

    # Filtrar a partidos no empezados con game_date >= cutoff
    raw["_date"] = pd.to_datetime(raw["gameDate"]).dt.date
    future = raw[
        (raw["gameStatus"] == _STATUS_NOT_STARTED) & (raw["_date"] >= cutoff)
    ]

    if future.empty:
        _log.info(f"  Sin partidos futuros desde {cutoff}.")
        return []

    games: list[ScheduledGame] = []
    for _, row in future.iterrows():
        tip_off_et: datetime | None = None
        try:
            raw_dt = row.get("gameDateTimeEst") or row.get("gameEt") or ""
            if raw_dt:
                tip_off_et = datetime.fromisoformat(str(raw_dt).rstrip("Z"))
        except (ValueError, TypeError):
            pass

        games.append(
            ScheduledGame(
                game_id=str(row["gameId"]),
                game_date=row["_date"],
                home_team_id=int(row["homeTeam_teamId"]),
                away_team_id=int(row["awayTeam_teamId"]),
                home_tricode=str(row["homeTeam_teamTricode"]),
                away_tricode=str(row["awayTeam_teamTricode"]),
                season=season,
                tip_off_et=tip_off_et,
            )
        )

    games.sort(key=lambda g: g.game_date)
    _log.info(f"  {len(games)} partidos futuros encontrados desde {cutoff}.")
    return games


def fetch_todays_schedule(season: str) -> list[ScheduledGame]:
    """Atajos: partidos programados para HOY en la temporada dada."""
    return fetch_future_schedule(season, from_date=date.today())
