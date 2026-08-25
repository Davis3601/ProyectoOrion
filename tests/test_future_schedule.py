"""
Tests de future_schedule.py — migrado a CDNClient (Decisión 9).

Sin red: CDNClient se inyecta como mock.

FIXTURE REAL: _REAL_SCHEDULE_FIXTURE es un recorte del scheduleLeagueV2_2026-08-24.json
archivado en GCS por el ingest_job. Incluye los 11 partidos regulares del 21-oct-2026
(semana inaugural 2026-27), los 2 del 22-oct, 2 partidos de preseason (gameId 001) y
1 partido con prefijo 006, para cubrir todos los casos de filtrado contra datos reales.

FIXTURE SINTÉTICO: los helpers _make_cdn_game/_make_cdn_raw se usan en tests que
necesitan escenarios controlados (finished, empty, tip-off ausente, etc.); el formato
de los campos refleja el payload REAL (Z suffix en fechas, sin campo gameType).

Campos verificados en el payload real (scheduleLeagueV2_2026-08-24.json):
  - gameId: str (prefijo 001/002/006 indica preseason/regular/other)
  - gameStatus: int (1=programado, 2=en curso, 3=finalizado)
  - gameDateEst: "YYYY-MM-DDTHH:MM:SSZ" — hora siempre 00:00, sirve solo la fecha
  - gameDateTimeEst: "YYYY-MM-DDTHH:MM:SSZ" — hora en ET con Z nominal (no UTC real)
  - homeTeam/awayTeam: {teamId, teamTricode, ...}
  - isNeutral: bool
  - gameType: CAMPO AUSENTE en este endpoint
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
# Fixture REAL — recorte de scheduleLeagueV2_2026-08-24.json
# ---------------------------------------------------------------------------
# Fuente: gs://predictorsnonprod-nba-predictors/raw/schedules/scheduleLeagueV2_2026-08-24.json
# Campos slimmed: gameId, gameStatus, gameDateEst, gameDateTimeEst, homeTeam, awayTeam, isNeutral

_REAL_SCHEDULE_FIXTURE: dict = {
    "leagueSchedule": {
        "seasonYear": "2026-27",
        "gameDates": [
            {
                # 21-oct-2026: 11 partidos regulares (semana inaugural, weekNumber=1)
                "games": [
                    {"gameId": "0022600004", "gameStatus": 1,
                     "gameDateEst": "2026-10-21T00:00:00Z",
                     "gameDateTimeEst": "2026-10-21T19:30:00Z",
                     "homeTeam": {"teamId": 1610612748, "teamTricode": "MIA"},
                     "awayTeam": {"teamId": 1610612750, "teamTricode": "MIN"},
                     "isNeutral": False},
                    {"gameId": "0022600005", "gameStatus": 1,
                     "gameDateEst": "2026-10-21T00:00:00Z",
                     "gameDateTimeEst": "2026-10-21T22:00:00Z",
                     "homeTeam": {"teamId": 1610612747, "teamTricode": "LAL"},
                     "awayTeam": {"teamId": 1610612744, "teamTricode": "GSW"},
                     "isNeutral": False},
                    {"gameId": "0022600085", "gameStatus": 1,
                     "gameDateEst": "2026-10-21T00:00:00Z",
                     "gameDateTimeEst": "2026-10-21T19:00:00Z",
                     "homeTeam": {"teamId": 1610612753, "teamTricode": "ORL"},
                     "awayTeam": {"teamId": 1610612737, "teamTricode": "ATL"},
                     "isNeutral": False},
                    {"gameId": "0022600086", "gameStatus": 1,
                     "gameDateEst": "2026-10-21T00:00:00Z",
                     "gameDateTimeEst": "2026-10-21T19:00:00Z",
                     "homeTeam": {"teamId": 1610612764, "teamTricode": "WAS"},
                     "awayTeam": {"teamId": 1610612749, "teamTricode": "MIL"},
                     "isNeutral": False},
                    {"gameId": "0022600087", "gameStatus": 1,
                     "gameDateEst": "2026-10-21T00:00:00Z",
                     "gameDateTimeEst": "2026-10-21T19:30:00Z",
                     "homeTeam": {"teamId": 1610612751, "teamTricode": "BKN"},
                     "awayTeam": {"teamId": 1610612766, "teamTricode": "CHA"},
                     "isNeutral": False},
                    {"gameId": "0022600088", "gameStatus": 1,
                     "gameDateEst": "2026-10-21T00:00:00Z",
                     "gameDateTimeEst": "2026-10-21T19:30:00Z",
                     "homeTeam": {"teamId": 1610612761, "teamTricode": "TOR"},
                     "awayTeam": {"teamId": 1610612741, "teamTricode": "CHI"},
                     "isNeutral": False},
                    {"gameId": "0022600089", "gameStatus": 1,
                     "gameDateEst": "2026-10-21T00:00:00Z",
                     "gameDateTimeEst": "2026-10-21T20:00:00Z",
                     "homeTeam": {"teamId": 1610612763, "teamTricode": "MEM"},
                     "awayTeam": {"teamId": 1610612762, "teamTricode": "UTA"},
                     "isNeutral": False},
                    {"gameId": "0022600090", "gameStatus": 1,
                     "gameDateEst": "2026-10-21T00:00:00Z",
                     "gameDateTimeEst": "2026-10-21T20:00:00Z",
                     "homeTeam": {"teamId": 1610612740, "teamTricode": "NOP"},
                     "awayTeam": {"teamId": 1610612754, "teamTricode": "IND"},
                     "isNeutral": False},
                    {"gameId": "0022600091", "gameStatus": 1,
                     "gameDateEst": "2026-10-21T00:00:00Z",
                     "gameDateTimeEst": "2026-10-21T20:30:00Z",
                     "homeTeam": {"teamId": 1610612745, "teamTricode": "HOU"},
                     "awayTeam": {"teamId": 1610612742, "teamTricode": "DAL"},
                     "isNeutral": False},
                    {"gameId": "0022600092", "gameStatus": 1,
                     "gameDateEst": "2026-10-21T00:00:00Z",
                     "gameDateTimeEst": "2026-10-21T22:00:00Z",
                     "homeTeam": {"teamId": 1610612757, "teamTricode": "POR"},
                     "awayTeam": {"teamId": 1610612756, "teamTricode": "PHX"},
                     "isNeutral": False},
                    {"gameId": "0022600093", "gameStatus": 1,
                     "gameDateEst": "2026-10-21T00:00:00Z",
                     "gameDateTimeEst": "2026-10-21T22:30:00Z",
                     "homeTeam": {"teamId": 1610612746, "teamTricode": "LAC"},
                     "awayTeam": {"teamId": 1610612758, "teamTricode": "SAC"},
                     "isNeutral": False},
                ],
            },
            {
                # 22-oct-2026: 2 partidos regulares
                "games": [
                    {"gameId": "0022600006", "gameStatus": 1,
                     "gameDateEst": "2026-10-22T00:00:00Z",
                     "gameDateTimeEst": "2026-10-22T19:00:00Z",
                     "homeTeam": {"teamId": 1610612755, "teamTricode": "PHI"},
                     "awayTeam": {"teamId": 1610612739, "teamTricode": "CLE"},
                     "isNeutral": False},
                    {"gameId": "0022600007", "gameStatus": 1,
                     "gameDateEst": "2026-10-22T00:00:00Z",
                     "gameDateTimeEst": "2026-10-22T21:30:00Z",
                     "homeTeam": {"teamId": 1610612760, "teamTricode": "OKC"},
                     "awayTeam": {"teamId": 1610612743, "teamTricode": "DEN"},
                     "isNeutral": False},
                ],
            },
            {
                # Preseason (gameId 001 prefix) — deben EXCLUIRSE
                "games": [
                    {"gameId": "0012600009", "gameStatus": 1,
                     "gameDateEst": "2026-10-03T00:00:00Z",
                     "gameDateTimeEst": "2026-10-03T19:00:00Z",
                     "homeTeam": {"teamId": 1610612761, "teamTricode": "TOR"},
                     "awayTeam": {"teamId": 1610612748, "teamTricode": "MIA"},
                     "isNeutral": False},
                    {"gameId": "0012600004", "gameStatus": 1,
                     "gameDateEst": "2026-10-05T00:00:00Z",
                     "gameDateTimeEst": "2026-10-05T19:00:00Z",
                     "homeTeam": {"teamId": 1610612737, "teamTricode": "ATL"},
                     "awayTeam": {"teamId": 1610612763, "teamTricode": "MEM"},
                     "isNeutral": False},
                ],
            },
            {
                # Partido con prefijo 006 (Emirates NBA Cup final o similar) — debe EXCLUIRSE
                "games": [
                    {"gameId": "0062600001", "gameStatus": 1,
                     "gameDateEst": "2026-12-11T00:00:00Z",
                     "gameDateTimeEst": "2026-12-11T00:00:00Z",
                     "homeTeam": {"teamId": 0, "teamTricode": ""},
                     "awayTeam": {"teamId": 0, "teamTricode": ""},
                     "isNeutral": True},
                ],
            },
        ],
    }
}

# ---------------------------------------------------------------------------
# Helpers sintéticos (escenarios controlados)
# ---------------------------------------------------------------------------

SEASON = "2026-27"
HOME_ID = 1610612738  # BOS
AWAY_ID = 1610612747  # LAL


def _make_cdn_game(
    game_id: str = "0022600001",
    game_date_est: str = "2026-10-28T00:00:00Z",   # Z suffix — formato real del CDN
    game_date_time_est: str = "2026-10-28T19:30:00Z",
    game_status: int = 1,
    home_team_id: int = HOME_ID,
    home_tricode: str = "BOS",
    away_team_id: int = AWAY_ID,
    away_tricode: str = "LAL",
) -> dict:
    """
    Crea un juego minimal que replica la estructura REAL del CDN.

    NOTA: no hay campo gameType en el payload real — el filtro usa gameId prefix.
    El gameId por defecto ("0022600001") empieza en "002" (regular season).
    """
    return {
        "gameId": game_id,
        "gameDateEst": game_date_est,
        "gameDateTimeEst": game_date_time_est,
        "gameStatus": game_status,
        "homeTeam": {"teamId": home_team_id, "teamTricode": home_tricode},
        "awayTeam": {"teamId": away_team_id, "teamTricode": away_tricode},
        "isNeutral": False,
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
# Tests contra fixture real
# ---------------------------------------------------------------------------


class TestRealScheduleFixture:
    """Tests que usan datos reales del CDN — la fuente de verdad."""

    def _client(self) -> MagicMock:
        return _mock_client(_REAL_SCHEDULE_FIXTURE)

    def test_oct21_returns_exactly_11_games(self):
        """21-oct-2026 (semana inaugural 2026-27): exactamente 11 partidos regulares."""
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 21), cdn_client=self._client())
        oct21 = [g for g in games if g.game_date == date(2026, 10, 21)]
        assert len(oct21) == 11

    def test_oct21_game_ids_all_start_with_002(self):
        """Todos los partidos del fixture real tienen gameId con prefijo 002."""
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 21), cdn_client=self._client())
        oct21 = [g for g in games if g.game_date == date(2026, 10, 21)]
        assert all(g.game_id.startswith("002") for g in oct21)

    def test_preseason_and_006_games_excluded(self):
        """Los 2 partidos de preseason (001) y el 006 no aparecen — solo quedan 13 (11+2)."""
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=self._client())
        assert len(games) == 13  # 11 del 21-oct + 2 del 22-oct
        assert all(g.game_id.startswith("002") for g in games)

    def test_oct22_returns_2_games(self):
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 22), cdn_client=self._client())
        oct22 = [g for g in games if g.game_date == date(2026, 10, 22)]
        assert len(oct22) == 2

    def test_real_tip_off_et_parsed_from_Z_field(self):
        """gameDateTimeEst '2026-10-21T19:30:00Z' → tip_off_et = datetime(2026,10,21,19,30)."""
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 21), cdn_client=self._client())
        mia_min = next(g for g in games if g.game_id == "0022600004")
        assert mia_min.tip_off_et == datetime(2026, 10, 21, 19, 30, 0)

    def test_real_game_fields_correct(self):
        """Campos de un partido real del fixture: ids, tricodes, date."""
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 21), cdn_client=self._client())
        lal_gsw = next(g for g in games if g.game_id == "0022600005")
        assert lal_gsw.home_tricode == "LAL"
        assert lal_gsw.away_tricode == "GSW"
        assert lal_gsw.home_team_id == 1610612747
        assert lal_gsw.game_date == date(2026, 10, 21)


# ---------------------------------------------------------------------------
# Tests unitarios (helpers sintéticos)
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

    def test_excludes_preseason_gameid_001_prefix(self):
        """gameId con prefijo 001 (preseason) → excluido."""
        raw = _make_cdn_raw([_make_cdn_game(game_id="0012600001")])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert games == []

    def test_excludes_non_regular_gameid_006_prefix(self):
        """gameId con prefijo 006 (other) → excluido."""
        raw = _make_cdn_raw([_make_cdn_game(game_id="0062600001")])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert games == []

    def test_includes_regular_gameid_002_prefix(self):
        """gameId con prefijo 002 → incluido (temporada regular)."""
        raw = _make_cdn_raw([_make_cdn_game(game_id="0022600042")])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert len(games) == 1

    def test_excludes_games_strictly_before_cutoff(self):
        raw = _make_cdn_raw([_make_cdn_game(game_date_est="2026-10-28T00:00:00Z")])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 29), cdn_client=_mock_client(raw))
        assert games == []

    def test_includes_game_on_cutoff_date_boundary(self):
        raw = _make_cdn_raw([_make_cdn_game(game_date_est="2026-10-28T00:00:00Z")])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 28), cdn_client=_mock_client(raw))
        assert len(games) == 1

    def test_gameDateEst_Z_suffix_parsed_correctly(self):
        """gameDateEst con Z suffix ('2026-10-28T00:00:00Z') se parsea a date correcto."""
        raw = _make_cdn_raw([_make_cdn_game(game_date_est="2026-10-28T00:00:00Z")])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert games[0].game_date == date(2026, 10, 28)

    def test_tip_off_et_parsed_with_Z_suffix(self):
        """gameDateTimeEst con Z ('2026-10-28T19:30:00Z') → tip_off_et naive en ET."""
        raw = _make_cdn_raw([_make_cdn_game(game_date_time_est="2026-10-28T19:30:00Z")])
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
            _make_cdn_game(game_id="0022600002", game_date_est="2026-10-30T00:00:00Z",
                           game_date_time_est="2026-10-30T20:00:00Z"),
            _make_cdn_game(game_id="0022600001", game_date_est="2026-10-28T00:00:00Z",
                           game_date_time_est="2026-10-28T19:30:00Z"),
        ])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert [g.game_id for g in games] == ["0022600001", "0022600002"]

    def test_empty_when_no_games_in_raw(self):
        raw = _make_cdn_raw([])
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert games == []

    def test_empty_when_all_games_finished(self):
        raw = _make_cdn_raw([
            _make_cdn_game(game_id="0022600001", game_status=3),
            _make_cdn_game(game_id="0022600002", game_status=3),
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
                game_date_est="2026-10-21T00:00:00Z",
                game_date_time_est="2026-10-21T17:30:00Z",
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
                    {"games": [_make_cdn_game(game_id="0022600001",
                                              game_date_est="2026-10-28T00:00:00Z",
                                              game_date_time_est="2026-10-28T19:30:00Z")]},
                    {"games": [_make_cdn_game(game_id="0022600002",
                                              game_date_est="2026-10-29T00:00:00Z",
                                              game_date_time_est="2026-10-29T19:30:00Z")]},
                ],
            }
        }
        games = fetch_future_schedule(SEASON, from_date=date(2026, 10, 1), cdn_client=_mock_client(raw))
        assert len(games) == 2
        assert {g.game_id for g in games} == {"0022600001", "0022600002"}


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
