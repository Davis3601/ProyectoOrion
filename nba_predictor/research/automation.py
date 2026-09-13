"""Planificador local del ejercicio. Sin GCP; no genera probabilidades inventadas."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from nba_predictor.research.odds_api import build_snapshot, fetch_odds
from nba_predictor.research.paper_trading import digest, record, timestamp, write_new
from nba_predictor.research.reconcile_odds import reconcile
from nba_predictor.research.request_budget import reserve_request


def schedule_plan(schedule: dict, policy: dict) -> list[dict]:
    rows = []
    for block in schedule['leagueSchedule']['gameDates']:
        for game in block['games']:
            if not str(game['gameId']).startswith('002') or game.get('gameStatus') != 1:
                continue
            if not game.get('gameDateTimeUTC'):
                continue
            start = timestamp(game['gameDateTimeUTC'])
            rows.append({'game_id': game['gameId'], 'tip_off_utc': start.isoformat(),
                         'home_team': game['homeTeam']['teamTricode'],
                         'away_team': game['awayTeam']['teamTricode'],
                         'opens_at': (start-timedelta(
                             minutes=policy['capture_max_minutes_before'])).isoformat(),
                         'target_at': (start-timedelta(minutes=60)).isoformat(),
                         'closes_at': (start-timedelta(
                             minutes=policy['capture_min_minutes_before'])).isoformat()})
    return sorted(rows, key=lambda row: (row['target_at'], row['game_id']))


def refresh_schedule(root: Path, *, now: datetime) -> dict:
    from nba_predictor.ingestion.cdn_client import CDNClient
    year = now.year if now.month >= 7 else now.year-1
    _, raw = CDNClient().fetch_season_schedule(f'{year}-{(year+1)%100:02d}')
    if not raw.get('leagueSchedule', {}).get('gameDates'):
        raise ValueError('Calendario incompleto')
    folder = root/'schedules'
    folder.mkdir(exist_ok=True)
    envelope = {'fetched_at_utc': now.isoformat(), 'schedule': raw}
    write_new(folder/f'{now.strftime("%Y%m%dT%H%M%S")}-{uuid4().hex}.json', envelope)
    return envelope


def load_schedule(root: Path, *, now: datetime, refresh: bool = False) -> dict:
    paths = sorted((root/'schedules').glob('*.json'))
    envelope = json.loads(paths[-1].read_text(encoding='utf-8')) if paths else None
    age = (now-timestamp(envelope['fetched_at_utc'])).total_seconds() if envelope else None
    if age is not None and 0 <= age < 43200:
        return envelope
    if refresh:
        return refresh_schedule(root, now=now)
    raise ValueError('Se necesita un calendario descargado hace menos de 12 horas')


def claim(root: Path, game_id: str) -> bool:
    """Un intento automático por partido, incluso si falla; no reintenta tras caída."""
    with sqlite3.connect(root/'automation.sqlite', timeout=30) as db:
        db.execute('CREATE TABLE IF NOT EXISTS claimed (game_id TEXT PRIMARY KEY)')
        cursor = db.execute('INSERT OR IGNORE INTO claimed VALUES (?)', (game_id,))
        return cursor.rowcount == 1


def tick(root: Path, envelope: dict, *, now: datetime, api_key: str,
         fetcher=fetch_odds, clock=None) -> dict:
    clock = clock or (lambda: datetime.now(timezone.utc))
    policy = json.loads((root/'policy.json').read_text(encoding='utf-8'))
    if policy.get('schema_version') != 2:
        raise ValueError('Requiere política v2')
    age = (now-timestamp(envelope['fetched_at_utc'])).total_seconds()
    if not 0 <= age < 43200:
        raise ValueError('Calendario antiguo o futuro')
    plan = schedule_plan(envelope['schedule'], policy)
    # Solo intenta desde T-60 hasta T-55, nunca al abrir T-65 por adelantado.
    due = [r for r in plan if timestamp(r['target_at']) <= now <= timestamp(r['closes_at'])]
    output = {'checked_at_utc': now.isoformat(), 'policy_sha256': digest(policy),
              'due_games': len(due), 'outcomes': [], 'api_requests': 0,
              'next_target': next((r['target_at'] for r in plan
                                   if timestamp(r['target_at']) > now), None)}
    if not due:
        return output
    if not api_key.strip():
        raise ValueError('ODDS_API_KEY no disponible')
    due = [r for r in due if claim(root, r['game_id'])]
    if not due:
        return output
    budget = reserve_request(root, now=now)
    output['api_requests'] = 1
    capture = fetcher(api_key, region=policy['region'])
    capture['request_budget'] = budget
    capture_dir = root/'captures'
    capture_dir.mkdir(exist_ok=True)
    path = capture_dir/f'{now.strftime("%Y%m%dT%H%M%S")}-{uuid4().hex}.json'
    write_new(path, capture)
    output['capture'] = str(path)
    schedule = envelope['schedule']
    audit = reconcile(capture, schedule,
                      allow_ten_minute_offset=policy['matching_rule'] == 'exact_or_plus_600s_v1')
    write_new(path.with_name(path.stem+'-audit.json'), audit)
    for game in due:
        gid = game['game_id']
        matches = [r for r in audit['rows'] if r['game_id'] == gid]
        if len(matches) != 1:
            output['outcomes'].append({'game_id': gid, 'status': 'unmatched'})
            continue
        prediction_path = root/'inbox'/f'{gid}.json'
        if not prediction_path.exists():
            output['outcomes'].append({'game_id': gid, 'status': 'missing_prediction'})
            continue
        try:
            prediction = json.loads(prediction_path.read_text(encoding='utf-8-sig'))
            snapshot = build_snapshot(capture, prediction,
                                      event_id=matches[0]['provider_event_id'],
                                      bookmaker=policy['bookmaker'], schedule=schedule,
                                      allow_ten_minute_offset=(
                                          policy['matching_rule'] == 'exact_or_plus_600s_v1'))
            if prediction['game_id'] != gid:
                raise ValueError('ID del inbox incorrecto')
            decision = record(root, snapshot, now=clock())
            output['outcomes'].append({'game_id': gid, 'status': 'recorded',
                                       'side': decision['side']})
        except (ValueError, KeyError, TypeError, OSError) as exc:
            output['outcomes'].append({'game_id': gid, 'status': 'rejected',
                                       'reason': type(exc).__name__})
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'tick'])
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--refresh', action='store_true', help='Actualizar NBA si cache >12h')
    parser.add_argument('--env-file', type=Path, help='Lee SOLO ODDS_API_KEY')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    root = args.directory
    (root/'runs').mkdir(parents=True, exist_ok=True)
    run_path = root/'runs'/f'{now.strftime("%Y%m%dT%H%M%S")}-{uuid4().hex}.json'
    try:
        envelope = load_schedule(root, now=now, refresh=args.refresh)
        policy = json.loads((root/'policy.json').read_text(encoding='utf-8'))
        if args.command == 'plan':
            future = [r for r in schedule_plan(envelope['schedule'], policy)
                      if timestamp(r['closes_at']) >= now]
            result = {'status': 'planned', 'games': len(future), 'next_games': future[:10],
                      'plan': future, 'policy_sha256': digest(policy)}
        else:
            key = os.environ.get('ODDS_API_KEY', '')
            if not key and args.env_file:
                from dotenv import dotenv_values
                key = dotenv_values(args.env_file).get('ODDS_API_KEY') or ''
            result = tick(root, envelope, now=now, api_key=key)
        write_new(run_path, result)
        print(json.dumps({k: v for k, v in result.items() if k != 'plan'}, indent=2))
    except Exception as exc:
        # Sin mensajes externos: una excepción puede incluir una URL autenticada.
        write_new(run_path, {'status': 'failed', 'error_type': type(exc).__name__,
                             'checked_at_utc': now.isoformat()})
        parser.exit(1, f'Error {type(exc).__name__}; ver {run_path}\n')


if __name__ == '__main__':
    main()
