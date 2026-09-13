"""Planificación y ejecución end-to-end con proveedor simulado."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from nba_predictor.research.automation import schedule_plan, tick
from nba_predictor.research.demo_paper_trading import run_demo
from nba_predictor.research.paper_trading import initialize

NOW = datetime(2030, 10, 21, 18, tzinfo=timezone.utc)


@pytest.fixture
def setup(tmp_path):
    demo = tmp_path/'demo'
    run_demo(demo)
    capture = json.loads((demo/'capture.json').read_text())
    schedule = json.loads((demo/'schedule.json').read_text())
    schedule['leagueSchedule']['gameDates'][0]['games'][0]['gameStatus'] = 1
    prediction = json.loads((demo/'prediction.json').read_text())
    root = tmp_path/'worker'
    initialize(root, min_ev=.02, stake=100, max_age_seconds=300, commission=0,
               bookmaker='synthetic_book', now=NOW-timedelta(minutes=1))
    capture['events'][0]['bookmakers'][0]['key'] = 'synthetic_book'
    (root/'inbox').mkdir()
    (root/'inbox'/'0023000001.json').write_text(json.dumps(prediction))
    envelope = {'fetched_at_utc': NOW.isoformat(), 'schedule': schedule}
    fetcher = Mock(return_value=capture)
    return root, envelope, fetcher


def test_full_tick_records_then_deduplicates(setup):
    root, envelope, fetcher = setup
    result = tick(root, envelope, now=NOW, api_key='fake', fetcher=fetcher, clock=lambda: NOW)
    assert result['outcomes'] == [{'game_id': '0023000001', 'status': 'recorded', 'side': 'home'}]
    again = tick(root, envelope, now=NOW, api_key='fake', fetcher=fetcher, clock=lambda: NOW)
    assert again['api_requests'] == 0
    fetcher.assert_called_once()


def test_no_due_means_no_key_or_request(setup):
    root, envelope, fetcher = setup
    envelope['fetched_at_utc'] = (NOW-timedelta(minutes=20)).isoformat()
    result = tick(root, envelope, now=NOW-timedelta(minutes=10), api_key='', fetcher=fetcher)
    assert result['api_requests'] == 0
    fetcher.assert_not_called()


def test_missing_prediction_still_archives_capture(setup):
    root, envelope, fetcher = setup
    # Mover fixture al nombre de otro partido, sin eliminar evidencia.
    p = root/'inbox'/'0023000001.json'
    p.rename(root/'inbox'/'unused.json')
    result = tick(root, envelope, now=NOW, api_key='fake', fetcher=fetcher)
    assert result['outcomes'][0]['status'] == 'missing_prediction'
    assert not (root/'0023000001.json').exists()
    assert len(list((root/'captures').glob('*.json'))) == 2


def test_stale_schedule_does_not_spend(setup):
    root, envelope, fetcher = setup
    with pytest.raises(ValueError, match='antiguo'):
        tick(root, envelope, now=NOW+timedelta(hours=13), api_key='fake', fetcher=fetcher)
    fetcher.assert_not_called()


def test_failed_http_does_not_retry_automatically(setup):
    root, envelope, fetcher = setup
    fetcher.side_effect = RuntimeError('HTTP failed')
    with pytest.raises(RuntimeError):
        tick(root, envelope, now=NOW, api_key='fake', fetcher=fetcher)
    assert tick(root, envelope, now=NOW, api_key='fake', fetcher=fetcher)['api_requests'] == 0
    fetcher.assert_called_once()


def test_plan_excludes_preseason_and_finished(setup):
    root, envelope, _ = setup
    policy = json.loads((root/'policy.json').read_text())
    assert len(schedule_plan(envelope['schedule'], policy)) == 1
    game = envelope['schedule']['leagueSchedule']['gameDates'][0]['games'][0]
    game['gameStatus'] = 3
    assert schedule_plan(envelope['schedule'], policy) == []
    game['gameStatus'] = 1
    game['gameId'] = '0013000001'
    assert schedule_plan(envelope['schedule'], policy) == []
