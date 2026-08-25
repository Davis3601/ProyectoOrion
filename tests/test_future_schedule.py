"""
Tests de future_schedule.py — migrado a CDNClient (Decisión 9).

Sin red: CDNClient se inyecta como mock. Los tests cubren:
- Casos normales (partidos programados devueltos correctamente)
- Filtros: gameStatus==3 excluido, gameType!=2 excluido, cutoff de fecha
- tip_off_et: parseo correcto y caso None
- Orden: resultados ordenados por game_date
- Partidos en curso (gameStatus==2): se incluyen (no son finalizados)
"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from nba_predictor.ingestion.future_schedule import (
    ScheduledGame,
    fetch_future_schedule,
    fetch_todays_schedule,
)

# ---------------------------------------------------------------------------
# Helpers de fixtures
# ---------------------------------------------------------------------------

SEASON = "2026-27"
HOME_ID = 1610612738  # BOS
AWAY_ID = 1610612747  # LAL


def _make_cdn_game(
    game_id: str = "0022600001",
    game_date_est: str = "2026-10-28T00:00:00",
    game_date_time_est: str = "2026-10-28T19:30:00",
    game_status: int = 1,
    game_type: int = 2,
    home_team_id: int = HOME_ID,
    home_tricode: str = "BOS",
    away_team_id: int = AWAY_ID,
    away_tricode: str = "LAL",
) -> dict:
    return {
        "gameId": game_id,
        "gameDateEst": game_date_est,
        "gameDateTimeEst": game_date_time_est,
        "gameStatus": game_status,
        "gameType": game_type,
        "homeTeam": {"teamId": home_team_id, "teamTricode": home_tricode},
        "awayTeam": {"teamId": away_team_id, "teamTricode": away_tricode},
    }


def _make_cdn_raw(games: list[dict], season_year: str = SEASON) -> dict:
    return {
        "leagueSchedule": {
            "seasonYear": season_year,
            "gameDates": [{"games": games}],
        }
    }


def _mock_client(raw: dict) -> MagicMock:
    """CDNClient mock que devuelve raw de fetch_season_schedule."""
    client = MagicMock()
    client.fetch_season_schedule.return_value = (MagicMock(), raw)
    return client


# ---------------------------------------------------------------------------
# fetch_future_schedule
# ---------------------------------------------------------------------------


class TestFetchFutureSchedule:
    def test_returns_scheduled_game_with_correct_fields(self):
        raw = _make_cdn_raw([_make_cdn_game()])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert len(games) == 1
        g = games[0]
        assert g.game_id == "0022600001"
        assert g.game_date == date(2026, 10, 28)
        assert g.home_team_id == HOME_ID
        assert g.home_tricode == "BOS"
        assert g.away_team_id == AWAY_ID
        assert g.away_tricode == "LAL"
        assert g.season == SEASON

    def test_scheduled_game_is_frozen_dataclass(self):
        raw = _make_cdn_raw([_make_cdn_game()])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        with pytest.raises(AttributeError):
            games[0].game_id = "modified"  # type: ignore[misc]

    def test_excludes_finished_games_status_3(self):
        raw = _make_cdn_raw([_make_cdn_game(game_status=3)])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert games == []

    def test_includes_in_progress_games_status_2(self):
        """Partidos en curso (gameStatus==2) se incluyen — no son finalizados."""
        raw = _make_cdn_raw([_make_cdn_game(game_status=2)])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert len(games) == 1

    def test_excludes_preseason_game_type_1(self):
        raw = _make_cdn_raw([_make_cdn_game(game_type=1)])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert games == []

    def test_excludes_playoff_game_type_4(self):
        raw = _make_cdn_raw([_make_cdn_game(game_type=4)])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert games == []

    def test_accepts_game_type_as_string_2(self):
        """El CDN puede devolver gameType como str '2' además de int 2."""
        g = _make_cdn_game()
        g["gameType"] = "2"
        raw = _make_cdn_raw([g])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert len(games) == 1

    def test_excludes_games_strictly_before_cutoff(self):
        raw = _make_cdn_raw([_make_cdn_game(game_date_est="2026-10-28T00:00:00")])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 29), cdn_client=_mock_client(raw))
        assert games == []

    def test_includes_game_on_cutoff_date_boundary(self):
        raw = _make_cdn_raw([_make_cdn_game(game_date_est="2026-10-28T00:00:00")])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 28), cdn_client=_mock_client(raw))
        assert len(games) == 1

    def test_tip_off_et_parsed_correctly(self):
        raw = _make_cdn_raw([_make_cdn_game(game_date_time_est="2026-10-28T19:30:00")])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert games[0].tip_off_et == datetime(2026, 10, 28, 19, 30, 0)

    def test_tip_off_et_none_when_field_empty(self):
        g = _make_cdn_game()
        g["gameDateTimeEst"] = ""
        raw = _make_cdn_raw([g])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert games[0].tip_off_et is None

    def test_tip_off_et_none_when_field_absent(self):
        g = _make_cdn_game()
        del g["gameDateTimeEst"]
        raw = _make_cdn_raw([g])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert games[0].tip_off_et is None

    def test_results_sorted_by_game_date(self):
        raw = _make_cdn_raw([
            _make_cdn_game(game_id="G2", game_date_est="2026-10-30T00:00:00",
                           game_date_time_est="2026-10-30T20:00:00"),
            _make_cdn_game(game_id="G1", game_date_est="2026-10-28T00:00:00",
                           game_date_time_est="2026-10-28T19:30:00"),
        ])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert [g.game_id for g in games] == ["G1", "G2"]

    def test_empty_when_no_games_in_raw(self):
        raw = _make_cdn_raw([])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert games == []

    def test_empty_when_all_games_finished(self):
        raw = _make_cdn_raw([
            _make_cdn_game(game_id="G1", game_status=3),
            _make_cdn_game(game_id="G2", game_status=3),
        ])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert games == []

    def test_calls_cdn_client_with_correct_season(self):
        raw = _make_cdn_raw([])
        client = _mock_client(raw)
        fetch_future_schedule("2025-26", from_date=date(2025, 10, 1), cdn_client=client)
        client.fetch_season_schedule.assert_called_once_with("2025-26")

    def test_season_field_uses_cdn_season(self):
        """ScheduledGame.season refleja leagueSchedule.seasonYear del CDN (patrón e-0)."""
        raw = _make_cdn_raw([_make_cdn_game()], season_year="2026-27")
        games = fetch_future_schedule("2026-27", from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert games[0].season == "2026-27"

    def test_cdn_season_wins_when_param_differs(self):
        """e-0: payload CDN '2026-27', parámetro '2025-26' → games con season='2026-27' Y lista no vacía."""
        raw = _make_cdn_raw([_make_cdn_game()], season_year="2026-27")
        games = fetch_future_schedule("2025-26", from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert len(games) == 1
        assert games[0].season == "2026-27"  # CDN gana sobre el parámetro

    def test_inaugural_week_non_empty(self):
        """Semana inaugural 2026-27 (2026-10-21): fixture CDN con ese partido → lista NO vacía."""
        raw = _make_cdn_raw([
            _make_cdn_game(
                game_id="0022600050",
                game_date_est="2026-10-21T00:00:00",
                game_date_time_est="2026-10-21T17:30:00",
                game_status=1,
                home_team_id=1610612738,
                home_tricode="BOS",
                away_team_id=1610612747,
                away_tricode="LAL",
            )
        ])
        games = fetch_future_schedule("2026-27", from_date=date(2026, 10, 21), cdn_client=_mock_client(raw))
        assert len(games) >= 1
        assert games[0].game_date == date(2026, 10, 21)

    def test_multiple_game_dates_flattened(self):
        """gameDates con múltiples bloques se procesan todos."""
        raw = {
            "leagueSchedule": {
                "seasonYear": SEASON,
                "gameDates": [
                    {"games": [_make_cdn_game(game_id="G1", game_date_est="2026-10-28T00:00:00",
                                              game_date_time_est="2026-10-28T19:30:00")]},
                    {"games": [_make_cdn_game(game_id="G2", game_date_est="2026-10-29T00:00:00",
                                              game_date_time_est="2026-10-29T19:30:00")]},
                ],
            }
        }
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert len(games) == 2
        assert {g.game_id for g in games} == {"G1", "G2"}


# ---------------------------------------------------------------------------
# fetch_todays_schedule
# ---------------------------------------------------------------------------


class TestFetchTodaysSchedule:
    def test_returns_list(self):
        """fetch_todays_schedule devuelve lista (vacía en offseason)."""
        raw = _make_cdn_raw([])
        client = _mock_client(raw)
        result = fetch_todays_schedule(SEASON, cdn_client=client)
        assert isinstance(result, list)

    def test_delegates_to_fetch_season_schedule(self):
        raw = _make_cdn_raw([])
        client = _mock_client(raw)
        fetch_todays_schedule(SEASON, cdn_client=client)
        client.fetch_season_schedule.assert_called_once_with(SEASON)
