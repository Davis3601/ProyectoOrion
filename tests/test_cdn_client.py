"""
Unit tests de cdn_client — funciones puras y lógica de fallback de URLs.

Sin red real: todas las llamadas HTTP están mockeadas.
Las funciones puras (parse_minutes_cdn, _derive_team_plus_minus, etc.) se testean
directamente. La lógica de fallback se testea mockeando _fetch_one_base para
aislarla del retry de tenacity y del throttle.
"""
from __future__ import annotations

import pytest
import requests
import pandas as pd
from unittest.mock import MagicMock, patch

from nba_predictor.ingestion.cdn_client import (
    CDNClient,
    DIAGNOSTIC_GAME_ID,
    _derive_team_plus_minus,
    _normalize_cdn_player_stats,
    _normalize_cdn_schedule,
    _normalize_cdn_team_stats,
    _season_from_year,
    parse_minutes_cdn,
)


# ---------------------------------------------------------------------------
# parse_minutes_cdn
# ---------------------------------------------------------------------------


class TestParseMinutesCDN:
    def test_full_format_with_seconds(self):
        """PT36M20.00S → 36 + 20/60 = 36.3333..."""
        result = parse_minutes_cdn("PT36M20.00S")
        assert result == pytest.approx(36 + 20 / 60, abs=1e-4)

    def test_fractional_seconds(self):
        """PT45M15.10S → 45.2517..."""
        result = parse_minutes_cdn("PT45M15.10S")
        assert result == pytest.approx(45 + 15.10 / 60, abs=1e-4)

    def test_dnp_returns_none(self):
        """PT00M00.00S → None (DNP — equivalente a None en SQLite)."""
        assert parse_minutes_cdn("PT00M00.00S") is None

    def test_empty_string_returns_none(self):
        assert parse_minutes_cdn("") is None

    def test_none_input_returns_none(self):
        assert parse_minutes_cdn(None) is None

    def test_minutes_only_no_seconds(self):
        """PT36M (minutesCalculated) → 36.0. Nunca usar este campo para precisión."""
        result = parse_minutes_cdn("PT36M")
        assert result == pytest.approx(36.0)

    def test_overtime_minutes(self):
        """Partido con OT: PT53M30.00S."""
        result = parse_minutes_cdn("PT53M30.00S")
        assert result == pytest.approx(53.5)

    def test_zero_minutes_explicit_seconds(self):
        """PT00M30.00S — 30 segundos — jugador muy poco tiempo."""
        result = parse_minutes_cdn("PT00M30.00S")
        assert result == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# _derive_team_plus_minus
# ---------------------------------------------------------------------------


class TestDeriveTeamPlusMinus:
    def test_positive(self):
        assert _derive_team_plus_minus(109, 99) == 10

    def test_negative(self):
        assert _derive_team_plus_minus(99, 109) == -10

    def test_zero(self):
        assert _derive_team_plus_minus(100, 100) == 0

    def test_large_blowout(self):
        assert _derive_team_plus_minus(142, 99) == 43


# ---------------------------------------------------------------------------
# _season_from_year
# ---------------------------------------------------------------------------


class TestSeasonFromYear:
    def test_passthrough(self):
        assert _season_from_year("2026-27") == "2026-27"
        assert _season_from_year("2025-26") == "2025-26"


# ---------------------------------------------------------------------------
# _normalize_cdn_team_stats
# ---------------------------------------------------------------------------


def _make_team_game(home_pts: int = 110, away_pts: int = 100) -> dict:
    """Fixture mínima de un game CDN con 2 equipos."""
    return {
        "homeTeam": {
            "teamId": "1610612747",
            "statistics": {
                "points": home_pts,
                "pointsAgainst": away_pts,
                "fieldGoalsMade": 40,
                "fieldGoalsAttempted": 85,
                "threePointersMade": 10,
                "threePointersAttempted": 30,
                "freeThrowsMade": 20,
                "freeThrowsAttempted": 24,
                "reboundsOffensive": 8,
                "reboundsDefensive": 32,
                "assists": 22,
                "steals": 6,
                "blocks": 5,
                "turnovers": 12,
                "foulsPersonal": 18,
            },
        },
        "awayTeam": {
            "teamId": "1610612744",
            "statistics": {
                "points": away_pts,
                "pointsAgainst": home_pts,
                "fieldGoalsMade": 38,
                "fieldGoalsAttempted": 90,
                "threePointersMade": 9,
                "threePointersAttempted": 28,
                "freeThrowsMade": 15,
                "freeThrowsAttempted": 20,
                "reboundsOffensive": 10,
                "reboundsDefensive": 30,
                "assists": 20,
                "steals": 5,
                "blocks": 3,
                "turnovers": 14,
                "foulsPersonal": 20,
            },
        },
    }


class TestNormalizeCDNTeamStats:
    def test_returns_two_rows(self):
        df = _normalize_cdn_team_stats(_make_team_game(), "0022500002")
        assert len(df) == 2

    def test_is_home_flag(self):
        df = _normalize_cdn_team_stats(_make_team_game(), "0022500002")
        home = df[df["team_id"] == 1610612747].iloc[0]
        away = df[df["team_id"] == 1610612744].iloc[0]
        assert home["is_home"] == 1
        assert away["is_home"] == 0

    def test_plus_minus_home_positive(self):
        """home 110 − away 100 → home pm=+10, away pm=−10."""
        df = _normalize_cdn_team_stats(_make_team_game(110, 100), "0022500002")
        home_pm = df[df["team_id"] == 1610612747].iloc[0]["plus_minus"]
        away_pm = df[df["team_id"] == 1610612744].iloc[0]["plus_minus"]
        assert home_pm == 10
        assert away_pm == -10

    def test_plus_minus_away_wins(self):
        df = _normalize_cdn_team_stats(_make_team_game(99, 109), "0022500001")
        home_pm = df[df["team_id"] == 1610612747].iloc[0]["plus_minus"]
        assert home_pm == -10

    def test_counting_stats_home(self):
        df = _normalize_cdn_team_stats(_make_team_game(), "0022500002")
        home = df[df["team_id"] == 1610612747].iloc[0]
        assert home["fgm"] == 40
        assert home["tov"] == 12
        assert home["pf"] == 18

    def test_columns_complete(self):
        df = _normalize_cdn_team_stats(_make_team_game(), "0022500002")
        expected = [
            "game_id", "team_id", "is_home",
            "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
            "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "plus_minus",
        ]
        assert list(df.columns) == expected

    def test_game_id_set(self):
        df = _normalize_cdn_team_stats(_make_team_game(), "0022500002")
        assert (df["game_id"] == "0022500002").all()


# ---------------------------------------------------------------------------
# _normalize_cdn_player_stats
# ---------------------------------------------------------------------------


def _make_player_game() -> dict:
    """Fixture con 1 equipo que tiene 3 jugadores: played, DNP, INACTIVE."""
    return {
        "homeTeam": {
            "teamId": "1610612747",
            "players": [
                {
                    "personId": "2544",
                    "status": "ACTIVE",
                    "played": "1",
                    "starter": "1",
                    "statistics": {
                        "minutes": "PT36M20.00S",
                        "minutesCalculated": "PT36M",
                        "fieldGoalsMade": 8,
                        "fieldGoalsAttempted": 18,
                        "threePointersMade": 2,
                        "threePointersAttempted": 6,
                        "freeThrowsMade": 5,
                        "freeThrowsAttempted": 6,
                        "reboundsOffensive": 1,
                        "reboundsDefensive": 7,
                        "assists": 9,
                        "steals": 2,
                        "blocks": 0,
                        "turnovers": 3,
                        "foulsPersonal": 2,
                        "plusMinusPoints": 8.0,
                    },
                },
                {
                    "personId": "9999001",  # DNP — ACTIVE pero no jugó
                    "status": "ACTIVE",
                    "played": "0",
                    "starter": "0",
                    "statistics": {
                        "minutes": "PT00M00.00S",
                        "minutesCalculated": "PT00M",
                        "fieldGoalsMade": 0,
                        "fieldGoalsAttempted": 0,
                        "threePointersMade": 0,
                        "threePointersAttempted": 0,
                        "freeThrowsMade": 0,
                        "freeThrowsAttempted": 0,
                        "reboundsOffensive": 0,
                        "reboundsDefensive": 0,
                        "assists": 0,
                        "steals": 0,
                        "blocks": 0,
                        "turnovers": 0,
                        "foulsPersonal": 0,
                        "plusMinusPoints": 0.0,
                    },
                },
                {
                    "personId": "1628467",  # INACTIVE — debe filtrarse
                    "status": "INACTIVE",
                    "played": "0",
                    "starter": "0",
                    "statistics": {},
                },
            ],
        },
        "awayTeam": {
            "teamId": "1610612744",
            "players": [],
        },
    }


class TestNormalizeCDNPlayerStats:
    def test_inactive_filtered(self):
        """Jugadores INACTIVE no tienen fila — semántica legacy preservada."""
        df = _normalize_cdn_player_stats(_make_player_game(), "0022500002")
        assert 1628467 not in df["player_id"].values

    def test_active_played_included(self):
        df = _normalize_cdn_player_stats(_make_player_game(), "0022500002")
        assert 2544 in df["player_id"].values

    def test_dnp_active_included(self):
        """DNP-activo tiene fila (status=ACTIVE), igual que en el legacy."""
        df = _normalize_cdn_player_stats(_make_player_game(), "0022500002")
        assert 9999001 in df["player_id"].values

    def test_dnp_minutes_is_none(self):
        """PT00M00.00S → None — equivalente a None/NaN del DNP en SQLite."""
        df = _normalize_cdn_player_stats(_make_player_game(), "0022500002")
        dnp = df[df["player_id"] == 9999001].iloc[0]
        assert dnp["minutes"] is None or pd.isna(dnp["minutes"])

    def test_played_minutes_decimal(self):
        df = _normalize_cdn_player_stats(_make_player_game(), "0022500002")
        played = df[df["player_id"] == 2544].iloc[0]
        assert played["minutes"] == pytest.approx(36 + 20 / 60, abs=1e-4)

    def test_started_flag_set(self):
        df = _normalize_cdn_player_stats(_make_player_game(), "0022500002")
        starter = df[df["player_id"] == 2544].iloc[0]
        assert starter["started"] == 1

    def test_started_flag_unset_for_dnp(self):
        df = _normalize_cdn_player_stats(_make_player_game(), "0022500002")
        dnp = df[df["player_id"] == 9999001].iloc[0]
        assert dnp["started"] == 0

    def test_is_home_flag(self):
        df = _normalize_cdn_player_stats(_make_player_game(), "0022500002")
        for _, row in df.iterrows():
            assert row["is_home"] == (1 if row["team_id"] == 1610612747 else 0)

    def test_counting_stats(self):
        df = _normalize_cdn_player_stats(_make_player_game(), "0022500002")
        played = df[df["player_id"] == 2544].iloc[0]
        assert played["fgm"] == 8
        assert played["ast"] == 9
        assert played["tov"] == 3


# ---------------------------------------------------------------------------
# _normalize_cdn_schedule
# ---------------------------------------------------------------------------


def _make_schedule_raw(
    game_type: int | str = 2,
    game_status: int = 3,
    home_score: Any = 110,
    away_score: Any = 100,
) -> dict:
    return {
        "leagueSchedule": {
            "seasonYear": "2026-27",
            "gameDates": [
                {
                    "games": [
                        {
                            "gameId": "0022600001",
                            "gameDateEst": "2026-10-22T00:00:00",
                            "gameType": game_type,
                            "gameStatus": game_status,
                            "homeTeam": {"teamId": "1610612747", "score": home_score},
                            "awayTeam": {"teamId": "1610612744", "score": away_score},
                        }
                    ]
                }
            ],
        }
    }


class TestNormalizeCDNSchedule:
    def test_regular_season_included(self):
        df = _normalize_cdn_schedule(_make_schedule_raw(game_type=2))
        assert "0022600001" in df["game_id"].values

    def test_preseason_filtered(self):
        df = _normalize_cdn_schedule(_make_schedule_raw(game_type=1))
        assert len(df) == 0

    def test_playoffs_filtered(self):
        df = _normalize_cdn_schedule(_make_schedule_raw(game_type=4))
        assert len(df) == 0

    def test_home_won_derived_home_wins(self):
        df = _normalize_cdn_schedule(_make_schedule_raw(home_score=110, away_score=100))
        row = df.iloc[0]
        assert row["home_won"] == 1

    def test_home_won_derived_away_wins(self):
        df = _normalize_cdn_schedule(_make_schedule_raw(home_score=98, away_score=105))
        row = df.iloc[0]
        assert row["home_won"] == 0

    def test_unplayed_game_scores_are_na(self):
        """gameStatus != 3 → scores pd.NA (el job luego filtra 'completed')."""
        df = _normalize_cdn_schedule(_make_schedule_raw(game_status=2))
        row = df.iloc[0]
        assert pd.isna(row["home_pts"])
        assert pd.isna(row["home_won"])

    def test_neutral_site_always_zero(self):
        df = _normalize_cdn_schedule(_make_schedule_raw())
        assert (df["neutral_site"] == 0).all()

    def test_season_from_season_year(self):
        df = _normalize_cdn_schedule(_make_schedule_raw())
        assert (df["season"] == "2026-27").all()

    def test_season_type_regular_season(self):
        df = _normalize_cdn_schedule(_make_schedule_raw())
        assert (df["season_type"] == "Regular Season").all()

    def test_home_team_id(self):
        df = _normalize_cdn_schedule(_make_schedule_raw())
        assert df.iloc[0]["home_team_id"] == 1610612747

    def test_game_type_as_string_also_works(self):
        """CDN puede enviar gameType como string o int."""
        df = _normalize_cdn_schedule(_make_schedule_raw(game_type="2"))
        assert "0022600001" in df["game_id"].values


# ---------------------------------------------------------------------------
# CDNClient — lógica de fallback de URLs (mocking _fetch_one_base)
# ---------------------------------------------------------------------------


class TestCDNClientFallback:
    """Tests del fallback entre bases. _fetch_one_base está mockeado para aislar
    la lógica de fallback del retry de tenacity y del throttle."""

    def _client(self):
        return CDNClient(
            base_urls=["https://base1.test", "https://base2.test"],
            request_delay=0,  # sin throttle en tests
        )

    def _http_error(self, status: int = 403) -> requests.HTTPError:
        resp = MagicMock()
        resp.status_code = status
        return requests.HTTPError(response=resp)

    def test_first_base_success_returns_data(self):
        """Si la primera base funciona, se devuelve su resultado."""
        client = self._client()
        expected = {"game": {"gameId": "test"}}

        with patch.object(client, "_fetch_one_base", return_value=expected):
            data, base_used = client._fetch_with_fallback("liveData/test.json", "test")

        assert data == expected
        assert base_used == "https://base1.test"

    def test_first_base_success_no_second_call(self):
        """Con la primera base exitosa, no se llama a la segunda."""
        client = self._client()
        call_log: list[str] = []

        def mock_fetch(base_url: str, path: str) -> dict:
            call_log.append(base_url)
            return {"ok": True}

        with patch.object(client, "_fetch_one_base", side_effect=mock_fetch):
            client._fetch_with_fallback("liveData/test.json", "test")

        assert call_log == ["https://base1.test"]

    def test_403_on_first_falls_to_second(self):
        """403 en la primera base → caída inmediata a la segunda."""
        client = self._client()

        def mock_fetch(base_url: str, path: str) -> dict:
            if "base1" in base_url:
                raise self._http_error(403)
            return {"ok": True}

        with patch.object(client, "_fetch_one_base", side_effect=mock_fetch):
            data, base_used = client._fetch_with_fallback("liveData/test.json", "test")

        assert base_used == "https://base2.test"

    def test_timeout_on_first_falls_to_second(self):
        """Timeout (después de reintentos internos) → cae a la siguiente base."""
        client = self._client()

        def mock_fetch(base_url: str, path: str) -> dict:
            if "base1" in base_url:
                raise requests.exceptions.Timeout()
            return {"ok": True}

        with patch.object(client, "_fetch_one_base", side_effect=mock_fetch):
            data, base_used = client._fetch_with_fallback("liveData/test.json", "test")

        assert base_used == "https://base2.test"

    def test_all_bases_fail_raises_runtime_error(self):
        """Si todas las bases fallan → RuntimeError (fallo ruidoso)."""
        client = self._client()

        with patch.object(client, "_fetch_one_base", side_effect=self._http_error(403)):
            with pytest.raises(RuntimeError, match="Todas las URLs base fallaron"):
                client._fetch_with_fallback("liveData/test.json", "test")

    def test_error_message_includes_bases(self):
        """El mensaje de error lista las bases intentadas para diagnóstico."""
        client = self._client()

        with patch.object(client, "_fetch_one_base", side_effect=self._http_error(403)):
            with pytest.raises(RuntimeError) as exc_info:
                client._fetch_with_fallback("liveData/test.json", "test")

        assert "base1.test" in str(exc_info.value)
        assert "base2.test" in str(exc_info.value)

    def test_second_base_fails_after_first_404(self):
        """Si la segunda base también falla → RuntimeError."""
        client = self._client()
        call_count = {"n": 0}

        def mock_fetch(base_url: str, path: str) -> dict:
            call_count["n"] += 1
            raise self._http_error(404)

        with patch.object(client, "_fetch_one_base", side_effect=mock_fetch):
            with pytest.raises(RuntimeError):
                client._fetch_with_fallback("liveData/test.json", "test")

        assert call_count["n"] == 2  # intentó ambas bases
