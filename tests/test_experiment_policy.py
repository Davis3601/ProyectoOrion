"""Reglas congeladas y presupuesto, con datos sintéticos y sin red."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from nba_predictor.research.paper_trading import initialize, record
from nba_predictor.research.request_budget import reserve_request

NOW = datetime(2030, 10, 21, 18, tzinfo=timezone.utc)


@pytest.fixture
def setup(tmp_path):
    initialize(tmp_path, min_ev=.02, stake=100, max_age_seconds=300, commission=0,
               bookmaker='draftkings', monthly_requests=2, now=NOW-timedelta(minutes=1))
    snapshot = {
        'game_id': '0023000001', 'home_team': 'BOS', 'away_team': 'LAL',
        'bookmaker': 'draftkings', 'model_version': 'synthetic', 'source': 'synthetic',
        'market': 'moneyline_including_overtime', 'p_home_win': .6,
        'home_decimal_odds': 1.8, 'away_decimal_odds': 2.1,
        'predicted_at_utc': NOW.isoformat(), 'quoted_at_utc': NOW.isoformat(),
        'fetched_at_utc': NOW.isoformat(),
        'tip_off_utc': (NOW+timedelta(hours=1)).isoformat(),
        'nba_tip_off_utc': (NOW+timedelta(hours=1)).isoformat(),
        'provider_tip_off_utc': (NOW+timedelta(minutes=70)).isoformat(),
        'matching_rule': 'exact_or_plus_600s_v1',
        'request_budget': reserve_request(tmp_path, now=NOW)}
    return tmp_path, snapshot


def test_governed_record(setup):
    root, snapshot = setup
    assert record(root, snapshot, now=NOW)['side'] == 'home'


@pytest.mark.parametrize('changes', [
    {'bookmaker': 'fanduel'}, {'matching_rule': 'exact_v1'}, {'request_budget': None},
    {'provider_tip_off_utc': (NOW+timedelta(minutes=69)).isoformat()},
    {'fetched_at_utc': (NOW+timedelta(seconds=1)).isoformat()},
])
def test_reject_rule_changes(setup, changes):
    root, snapshot = setup
    with pytest.raises(ValueError):
        record(root, snapshot | changes, now=NOW)


@pytest.mark.parametrize('minutes', [54, 66])
def test_reject_outside_window_even_if_fresh(setup, minutes):
    root, snapshot = setup
    start = (NOW+timedelta(minutes=minutes)).isoformat()
    snapshot.update(tip_off_utc=start, nba_tip_off_utc=start, provider_tip_off_utc=start)
    with pytest.raises(ValueError, match='ventana'):
        record(root, snapshot, now=NOW)


def test_budget_limit_and_month_reset(setup):
    root, _ = setup
    assert reserve_request(root, now=NOW)['attempt'] == 2
    with pytest.raises(ValueError, match='agotado'):
        reserve_request(root, now=NOW)
    assert reserve_request(root, now=NOW.replace(month=11))['attempt'] == 1


def test_budget_policy_change_rejected(setup):
    root, _ = setup
    p = root/'policy.json'
    policy = json.loads(p.read_text())
    policy['monthly_request_limit'] = 500
    p.write_text(json.dumps(policy))
    with pytest.raises(ValueError, match='modificada'):
        reserve_request(root, now=NOW)


def test_cannot_reinitialize(setup):
    root, _ = setup
    with pytest.raises(FileExistsError):
        initialize(root, min_ev=0, stake=100, max_age_seconds=300, commission=0,
                   bookmaker='fanduel', now=NOW)
