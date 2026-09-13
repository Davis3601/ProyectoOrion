"""Cruces exactos y rechazo de asignaciones ambiguas; sin red ni datos reales."""
from copy import deepcopy

from nba_predictor.research.reconcile_odds import reconcile


def inputs():
    capture = {'events': [{'id': 'external', 'sport_key': 'basketball_nba',
                          'home_team': 'Boston Celtics', 'away_team': 'Los Angeles Lakers',
                          'commence_time': '2026-10-21T23:30:00Z'}]}
    game = {'gameId': '0022600001', 'homeTeam': {'teamTricode': 'BOS'},
            'awayTeam': {'teamTricode': 'LAL'}, 'gameDateTimeUTC': '2026-10-21T23:30:00Z'}
    schedule = {'leagueSchedule': {'seasonYear': '2026-27', 'gameDates': [{'games': [game]}]}}
    return capture, schedule, game


def test_exact_match_and_timezone_equivalence():
    capture, schedule, game = inputs()
    game['gameDateTimeUTC'] = '2026-10-21T19:30:00-04:00'
    result = reconcile(capture, schedule)
    assert result['counts'] == {'matched': 1}
    assert result['rows'][0]['game_id'] == '0022600001'


def test_wrong_time_never_assigns_id():
    capture, schedule, game = inputs()
    game['gameDateTimeUTC'] = '2026-10-22T23:30:00Z'
    result = reconcile(capture, schedule)
    assert result['counts'] == {'time_mismatch': 1}
    assert result['rows'][0]['game_id'] is None


def test_preseason_excluded():
    capture, schedule, game = inputs()
    game['gameId'] = '0012600001'
    assert reconcile(capture, schedule)['counts'] == {'no_matching_teams': 1}


def test_reversed_teams_not_matched():
    capture, schedule, game = inputs()
    game['homeTeam'], game['awayTeam'] = game['awayTeam'], game['homeTeam']
    assert reconcile(capture, schedule)['counts'] == {'no_matching_teams': 1}


def test_duplicate_provider_ids_rejected():
    capture, schedule, _ = inputs()
    capture['events'].append(deepcopy(capture['events'][0]))
    result = reconcile(capture, schedule)
    assert result['counts'] == {'ambiguous': 2}
    assert all(r['game_id'] is None for r in result['rows'])


def test_duplicate_schedule_candidates_rejected():
    capture, schedule, game = inputs()
    schedule['leagueSchedule']['gameDates'][0]['games'].append(deepcopy(game))
    assert reconcile(capture, schedule)['counts'] == {'ambiguous': 1}


def test_offset_requires_opt_in():
    capture, schedule, game = inputs()
    capture['events'][0]['commence_time'] = '2026-10-21T23:40:00Z'
    assert reconcile(capture, schedule)['counts'] == {'time_mismatch': 1}
    result = reconcile(capture, schedule, allow_ten_minute_offset=True)
    assert result['counts'] == {'matched_offset': 1}
    assert result['rows'][0]['nba_tip_off_utc'] == game['gameDateTimeUTC']


def test_other_offsets_rejected():
    for time in ('23:20:00', '23:39:00', '23:41:00'):
        capture, schedule, _ = inputs()
        capture['events'][0]['commence_time'] = '2026-10-21T' + time + 'Z'
        assert reconcile(capture, schedule, allow_ten_minute_offset=True)['counts'] == {
            'time_mismatch': 1}


def test_exact_and_offset_candidates_together_are_ambiguous():
    capture, schedule, game = inputs()
    other = deepcopy(game)
    other['gameId'] = '0022600002'
    other['gameDateTimeUTC'] = '2026-10-21T23:20:00Z'
    schedule['leagueSchedule']['gameDates'][0]['games'].append(other)
    assert reconcile(capture, schedule, allow_ten_minute_offset=True)['counts'] == {
        'ambiguous': 1}
