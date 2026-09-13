"""Checks de temporalidad, decisiones congeladas y contabilidad simulada."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from nba_predictor.research.paper_trading import initialize, record, report

NOW = datetime(2026, 9, 13, 18, tzinfo=timezone.utc)


@pytest.fixture
def ledger(tmp_path):
    initialize(tmp_path, min_ev=0.02, stake=100, max_age_seconds=300,
               commission=0.05, now=NOW - timedelta(hours=1))
    return tmp_path


def snapshot(game_id="0022600001", **changes):
    value = {
        "game_id": game_id, "home_team": "BOS", "away_team": "LAL",
        "model_version": "test@sha", "p_home_win": 0.6,
        "predicted_at_utc": NOW.isoformat(), "quoted_at_utc": NOW.isoformat(),
        "tip_off_utc": (NOW + timedelta(hours=1)).isoformat(),
        "bookmaker": "synthetic", "source": "test fixture",
        "market": "moneyline_including_overtime",
        "home_decimal_odds": 1.8, "away_decimal_odds": 2.1,
    }
    return value | changes


def test_profit_commission_loss_void_pending_and_abstention(ledger):
    record(ledger, snapshot(), now=NOW)
    record(ledger, snapshot("0022600002"), now=NOW)
    record(ledger, snapshot("0022600003"), now=NOW)
    record(ledger, snapshot("0022600004"), now=NOW)
    record(ledger, snapshot("0022600005", p_home_win=0.5, away_decimal_odds=1.9), now=NOW)
    result = report(ledger, {"0022600001": "home", "0022600002": "away",
                            "0022600003": "void"}, now=NOW + timedelta(hours=4))
    assert result["net_profit"] == pytest.approx(-24)
    assert result["roi"] == pytest.approx(-0.12)
    assert result["max_drawdown_decision_order"] == pytest.approx(100)
    assert result["pending_bets"] == result["void_bets"] == result["abstentions"] == 1
    assert result["turnover"] == 200


def test_first_snapshot_and_policy_cannot_be_replaced(ledger):
    record(ledger, snapshot(p_home_win=0.5, away_decimal_odds=1.9), now=NOW)
    with pytest.raises(FileExistsError):
        record(ledger, snapshot(), now=NOW)
    with pytest.raises(FileExistsError):
        initialize(ledger, min_ev=0, stake=100, max_age_seconds=10, commission=0, now=NOW)


@pytest.mark.parametrize("changes", [
    {"quoted_at_utc": (NOW - timedelta(minutes=6)).isoformat()},
    {"predicted_at_utc": (NOW + timedelta(seconds=1)).isoformat()},
    {"predicted_at_utc": "2026-09-13T18:00:00"},
    {"tip_off_utc": NOW.isoformat()},
    {"p_home_win": float("nan")}, {"p_home_win": 1.1},
    {"home_decimal_odds": 1}, {"away_decimal_odds": float("inf")},
    {"market": "regulation_only"}, {"game_id": "../escape"},
])
def test_rejects_invalid_or_nonprospective_capture(ledger, changes):
    with pytest.raises(ValueError):
        record(ledger, snapshot(**changes), now=NOW)
    assert not list(ledger.glob("002*.json"))


def test_away_selection(ledger):
    row = record(ledger, snapshot(p_home_win=0.3), now=NOW)
    assert row["side"] == "away"
    result = report(ledger, {"0022600001": "away"}, now=NOW + timedelta(hours=4))
    assert result["net_profit"] == pytest.approx(104.5)


def test_policy_tampering_is_detected(ledger):
    record(ledger, snapshot(), now=NOW)
    path = ledger / "policy.json"
    policy = json.loads(path.read_text())
    policy["stake"] = 200
    path.write_text(json.dumps(policy))
    with pytest.raises(ValueError, match="reglas cambiaron"):
        report(ledger, {}, now=NOW)


def test_results_unknown_and_early_settlement(ledger):
    record(ledger, snapshot(), now=NOW)
    for results in ({"0022600002": "home"}, {"0022600001": "draw"},
                    {"0022600001": "home"}):
        with pytest.raises(ValueError):
            report(ledger, results, now=NOW)
    assert report(ledger, {}, now=NOW)["roi"] is None
