"""Auditoría local reproducible Odds API vs calendario NBA; nunca crea apuestas."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from nba_predictor.research.odds_api import TEAM_NAMES
from nba_predictor.research.paper_trading import timestamp, write_new


def reconcile(capture: dict, schedule: dict) -> dict:
    """Solo asigna game_id con equipos, UTC exacto y candidato único.

    Candidatos con los mismos equipos y hora distinta son diagnósticos; nunca
    implican que se trate del mismo partido ni autorizan corregir horarios.
    """
    league = schedule['leagueSchedule']
    games = [g for block in league['gameDates'] for g in block['games']
             if str(g['gameId']).startswith('002')]
    rows = []
    for event in capture['events']:
        row = {'provider_event_id': event['id'], 'home_team': event['home_team'],
               'away_team': event['away_team'], 'odds_tip_off_utc': event['commence_time'],
               'game_id': None}
        candidates = [g for g in games if
                      TEAM_NAMES.get(g['homeTeam']['teamTricode']) == event['home_team']
                      and TEAM_NAMES.get(g['awayTeam']['teamTricode']) == event['away_team']]
        start = timestamp(event['commence_time'])
        exact = [g for g in candidates if g.get('gameDateTimeUTC')
                 and timestamp(g['gameDateTimeUTC']) == start]
        if event.get('sport_key') != 'basketball_nba':
            row['status'] = 'wrong_sport'
        elif len(exact) == 1:
            row.update(status='matched', game_id=exact[0]['gameId'],
                       home_tricode=exact[0]['homeTeam']['teamTricode'],
                       away_tricode=exact[0]['awayTeam']['teamTricode'])
        elif len(exact) > 1:
            row['status'] = 'ambiguous'
        elif candidates:
            row['status'] = 'time_mismatch'
        else:
            row['status'] = 'no_matching_teams'
        row['schedule_candidates'] = [
            {'game_id': g['gameId'], 'tip_off_utc': g.get('gameDateTimeUTC')}
            for g in candidates]
        rows.append(row)
    # Repetición de IDs del proveedor o de asignaciones NBA: ninguna es utilizable.
    external_counts = Counter(r['provider_event_id'] for r in rows)
    game_counts = Counter(r['game_id'] for r in rows if r['game_id'])
    for row in rows:
        if external_counts[row['provider_event_id']] > 1 or (
                row['game_id'] and game_counts[row['game_id']] > 1):
            row.update(status='ambiguous', game_id=None)
    return {'season': league.get('seasonYear'), 'regular_season_games': len(games),
            'odds_events': len(rows), 'counts': dict(Counter(r['status'] for r in rows)),
            'rows': rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--schedule', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    capture_bytes = args.capture.read_bytes()
    schedule_bytes = args.schedule.read_bytes()
    result = reconcile(json.loads(capture_bytes), json.loads(schedule_bytes))
    result['sources'] = {
        'capture': str(args.capture), 'schedule': str(args.schedule),
        'capture_sha256': hashlib.sha256(capture_bytes).hexdigest(),
        'schedule_sha256': hashlib.sha256(schedule_bytes).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_new(args.output, result)
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, indent=2))


if __name__ == '__main__':
    main()
