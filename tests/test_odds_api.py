"""Pruebas aisladas: HTTP simulado, cruce de identidades y temporalidad real."""
import io
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

from nba_predictor.research.odds_api import build_snapshot, fetch_odds
from nba_predictor.research.paper_trading import initialize, record

STAMP = "2026-10-21T18:00:00Z"


@pytest.fixture
def capture():
    return {"provider": "the-odds-api.com", "fetched_at_utc": STAMP,
            "parameters": {"oddsFormat": "decimal", "markets": "h2h"},
            "events": [{"id": "provider-id", "sport_key": "basketball_nba",
                        "home_team": "Boston Celtics", "away_team": "Los Angeles Lakers",
                        "commence_time": "2026-10-21T23:30:00Z", "bookmakers": [
                            {"key": "testbook", "last_update": STAMP, "markets": [
                                {"key": "h2h", "outcomes": [
                                    {"name": "Los Angeles Lakers", "price": 2.1},
                                    {"name": "Boston Celtics", "price": 1.8}]}]}]}]}


@pytest.fixture
def prediction():
    return {"game_id": "0022600001", "home_team": "BOS", "away_team": "LAL",
            "p_home_win": 0.6, "predicted_at_utc": STAMP, "model_version": "test@sha",
            "tip_off_utc": "2026-10-21T23:30:00Z"}


def convert(capture, prediction):
    return build_snapshot(capture, prediction, event_id="provider-id", bookmaker="testbook")


def test_capture_http_parameters_and_no_key_in_archive(capture):
    response = io.StringIO(json.dumps(capture["events"]))
    response.headers = {"x-requests-remaining": "499"}
    opener = Mock(return_value=response)
    result = fetch_odds("secret-test-key", opener=opener)
    params = parse_qs(urlparse(opener.call_args.args[0]).query)
    assert params["markets"] == ["h2h"]
    assert params["oddsFormat"] == ["decimal"]
    assert params["regions"] == ["us"]
    assert opener.call_args.kwargs["timeout"] == 30
    assert "secret-test-key" not in json.dumps(result)
    assert result["quota"]["x-requests-remaining"] == "499"


@pytest.mark.parametrize("error", [
    HTTPError("https://test/?apiKey=secret", 401, "secret", {}, None),
    HTTPError("https://test/?apiKey=secret", 429, "secret", {}, None),
    URLError("secret"), TimeoutError("secret"),
])
def test_network_errors_do_not_leak_key_or_retry(error):
    opener = Mock(side_effect=error)
    with pytest.raises(RuntimeError) as raised:
        fetch_odds("secret", opener=opener)
    assert "secret" not in str(raised.value)
    opener.assert_called_once()


def test_missing_key_no_request():
    opener = Mock()
    with pytest.raises(ValueError):
        fetch_odds("", opener=opener)
    opener.assert_not_called()


def test_capture_to_ledger(tmp_path, capture, prediction):
    now = datetime(2026, 10, 21, 18, 0, 5, tzinfo=timezone.utc)
    initialize(tmp_path, min_ev=0.02, stake=100, max_age_seconds=300,
               commission=0, now=now - timedelta(minutes=1))
    snapshot = convert(capture, prediction)
    assert snapshot["home_decimal_odds"] == 1.8
    assert snapshot["away_decimal_odds"] == 2.1
    assert snapshot["game_id"] == "0022600001"
    row = record(tmp_path, snapshot, now=now)
    assert row["side"] == "home"
    assert row["estimated_net_ev"] == pytest.approx(0.08)
    with pytest.raises(ValueError, match="antigua"):
        record(tmp_path, snapshot, now=now + timedelta(minutes=6))


@pytest.mark.parametrize("case", ["teams", "time", "duplicate", "missing_book",
                                     "draw", "format", "future_quote"])
def test_rejects_mismatches(capture, prediction, case):
    event = capture["events"][0]
    book = event["bookmakers"][0]
    if case == "teams":
        prediction["home_team"] = "LAL"
    elif case == "time":
        prediction["tip_off_utc"] = "2026-10-22T23:30:00Z"
    elif case == "duplicate":
        capture["events"].append(deepcopy(event))
    elif case == "missing_book":
        book["key"] = "another"
    elif case == "draw":
        book["markets"][0]["outcomes"].append({"name": "Draw", "price": 12})
    elif case == "format":
        capture["parameters"]["oddsFormat"] = "american"
    else:
        book["last_update"] = "2026-10-21T18:01:00Z"
    with pytest.raises(ValueError):
        convert(capture, prediction)


def test_market_timestamp_takes_precedence(capture, prediction):
    capture["events"][0]["bookmakers"][0]["markets"][0]["last_update"] = (
        "2026-10-21T17:59:00Z")
    assert convert(capture, prediction)["quoted_at_utc"] == "2026-10-21T17:59:00Z"


def test_offset_preserves_nba_deadline(tmp_path, capture, prediction):
    event = capture['events'][0]
    event['commence_time'] = '2026-10-21T23:40:00Z'
    schedule = {'leagueSchedule': {'gameDates': [{'games': [{
        'gameId': prediction['game_id'], 'homeTeam': {'teamTricode': 'BOS'},
        'awayTeam': {'teamTricode': 'LAL'},
        'gameDateTimeUTC': prediction['tip_off_utc']}]}]}}
    with pytest.raises(ValueError, match='requiere calendario'):
        build_snapshot(capture, prediction, event_id='provider-id', bookmaker='testbook',
                       allow_ten_minute_offset=True)
    # Cuota y predicción frescas entre el inicio NBA y el inicio del proveedor.
    late = '2026-10-21T23:35:00Z'
    capture['fetched_at_utc'] = late
    event['bookmakers'][0]['last_update'] = late
    prediction['predicted_at_utc'] = late
    snapshot = build_snapshot(capture, prediction, event_id='provider-id', bookmaker='testbook',
                              schedule=schedule, allow_ten_minute_offset=True)
    assert snapshot['offset_seconds'] == 600
    assert snapshot['tip_off_utc'] == '2026-10-21T23:30:00Z'
    assert snapshot['provider_tip_off_utc'] == '2026-10-21T23:40:00Z'
    now = datetime(2026, 10, 21, 23, 35, tzinfo=timezone.utc)
    initialize(tmp_path, min_ev=0.02, stake=100, max_age_seconds=300, commission=0,
               now=now-timedelta(hours=1))
    with pytest.raises(ValueError, match='antes del partido'):
        record(tmp_path, snapshot, now=now)
    prediction['game_id'] = '0022600002'
    with pytest.raises(ValueError, match='ID/horario'):
        build_snapshot(capture, prediction, event_id='provider-id', bookmaker='testbook',
                       schedule=schedule, allow_ten_minute_offset=True)
