"""
Calendário de partidos futuros — Fase 5a (Decisión 1 CERRADA).

Consulta el calendario de la temporada corriente vía CDNClient (dual-URL CDN/S3)
y devuelve partidos NO jugados aún (gameStatus != 3). NUNCA escribe en la tabla
games: esa tabla es la capa STRUCTURED de partidos JUGADOS y mezclar programados
contaminaría su semántica y rompería sanity checks.

Migración de nba_api → CDN (Decisión 9 de Fase 5b):
    stats.nba.com está bloqueado desde IPs de datacenter (Akamai WAF). El pipeline
    de predicción en vivo usaba ScheduleLeagueV2 → bloqueado en Cloud Run.
    CDNClient provee el mismo schedule vía cdn.nba.com / S3 (dual-URL con fallback).

Restricción de offseason:
    La NBA está en offseason hasta octubre 2026. Durante ese período este módulo
    devuelve lista vacía — no hay nada que predecir aún. El pipeline en vivo se
    valida contra partidos históricos (tests/test_live_equivalence).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

_log = logging.getLogger(__name__)

# gameStatus códigos del CDN:
#   1 = programado (no empezado)
#   2 = en curso
#   3 = finalizado
_STATUS_FINISHED = 3


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
    *,
    cdn_client=None,
) -> list[ScheduledGame]:
    """
    Devuelve los partidos programados (no finalizados) de una temporada.

    Consulta el schedule CDN (dual-URL con fallback S3). No persiste nada —
    trabaja totalmente en memoria (Decisión 1).

    Parameters
    ----------
    season      : formato '2026-27' (la temporada que arranca en 2026).
    from_date   : si se indica, filtra a game_date >= from_date. Por defecto
                  hoy, lo que equivale a "partidos futuros desde ahora".
    cdn_client  : instancia de CDNClient para tests (inyección de dependencia).
                  None → construye un CDNClient con las URLs base por defecto.

    Returns
    -------
    Lista de ScheduledGame con gameStatus != 3 (no finalizados), ordenada por
    game_date. Lista vacía durante el offseason o si no hay partidos futuros.

    Raises
    ------
    RuntimeError si la llamada al CDN falla después de los reintentos en ambas
    bases URL (fallo ruidoso — nunca silencioso).
    """
    from nba_predictor.ingestion.cdn_client import CDNClient

    cutoff = from_date or date.today()
    client = cdn_client or CDNClient()

    _log.info("Consultando schedule CDN para temporada %s...", season)
    _, raw = client.fetch_season_schedule(season)

    league = raw.get("leagueSchedule", {})
    games: list[ScheduledGame] = []

    for game_date_block in league.get("gameDates", []):
        for g in game_date_block.get("games", []):
            # Solo temporada regular; el CDN puede devolver el tipo como int o str
            if g.get("gameType") not in (2, "2"):
                continue
            # Excluir partidos finalizados
            if g.get("gameStatus") == _STATUS_FINISHED:
                continue

            raw_date = g.get("gameDateEst", "")
            if not raw_date:
                continue
            try:
                game_date = datetime.fromisoformat(str(raw_date).rstrip("Z")).date()
            except (ValueError, TypeError):
                continue

            if game_date < cutoff:
                continue

            tip_off_et: datetime | None = None
            raw_dt = g.get("gameDateTimeEst", "")
            if raw_dt:
                try:
                    tip_off_et = datetime.fromisoformat(str(raw_dt).rstrip("Z"))
                except (ValueError, TypeError):
                    pass

            ht = g.get("homeTeam", {})
            at = g.get("awayTeam", {})

            try:
                games.append(
                    ScheduledGame(
                        game_id=str(g["gameId"]),
                        game_date=game_date,
                        home_team_id=int(ht["teamId"]),
                        away_team_id=int(at["teamId"]),
                        home_tricode=str(ht.get("teamTricode", "")),
                        away_tricode=str(at.get("teamTricode", "")),
                        season=season,
                        tip_off_et=tip_off_et,
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                _log.warning(
                    "Partido ignorado por datos incompletos: %s — %r",
                    g.get("gameId"), exc,
                )
                continue

    games.sort(key=lambda g: g.game_date)
    _log.info("  %d partidos futuros encontrados desde %s.", len(games), cutoff)
    return games


def fetch_todays_schedule(season: str, *, cdn_client=None) -> list[ScheduledGame]:
    """Atajo: partidos programados para HOY en la temporada dada."""
    return fetch_future_schedule(season, from_date=date.today(), cdn_client=cdn_client)
