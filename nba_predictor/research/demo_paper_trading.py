"""Demostración SINTÉTICA sin red, .env ni dinero real; reloj inyectado."""
from __future__ import annotations

import argparse
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nba_predictor.research.odds_api import build_snapshot
from nba_predictor.research.paper_trading import initialize, record, report, write_new
from nba_predictor.research.reconcile_odds import reconcile


def run_demo(root: Path) -> dict:
    """Exige directorio nuevo para separar evidencia sintética de capturas reales."""
    root.mkdir(parents=True, exist_ok=False)
    now = datetime(2030, 10, 21, 18, tzinfo=timezone.utc)
    stamp = now.isoformat()
    start = (now + timedelta(hours=1)).isoformat()
    game_id = '0023000001'
    prediction = {
        'synthetic': True, 'game_id': game_id, 'home_team': 'BOS', 'away_team': 'LAL',
        'p_home_win': 0.6, 'model_version': 'SYNTHETIC-NOT-A-TRAINED-MODEL',
        'predicted_at_utc': stamp, 'tip_off_utc': start}
    capture = {
        'synthetic': True, 'provider': 'the-odds-api.com', 'fetched_at_utc': stamp,
        'parameters': {'oddsFormat': 'decimal', 'markets': 'h2h'},
        'events': [{'id': 'synthetic-event', 'sport_key': 'basketball_nba',
                    'home_team': 'Boston Celtics', 'away_team': 'Los Angeles Lakers',
                    'commence_time': start, 'bookmakers': [{
                        'key': 'synthetic-book', 'last_update': stamp, 'markets': [{
                            'key': 'h2h', 'outcomes': [
                                {'name': 'Boston Celtics', 'price': 1.8},
                                {'name': 'Los Angeles Lakers', 'price': 2.1}]}]}]}]}
    schedule = {'synthetic': True, 'leagueSchedule': {'seasonYear': '2030-31',
                'gameDates': [{'games': [{'gameId': game_id, 'gameDateTimeUTC': start,
                                         'homeTeam': {'teamTricode': 'BOS'},
                                         'awayTeam': {'teamTricode': 'LAL'}}]}]}}
    for name, data in [('prediction', prediction), ('capture', capture), ('schedule', schedule)]:
        write_new(root / f'{name}.json', data)
    write_new(root / 'reconciliation.json', reconcile(capture, schedule))
    ledger = root / 'ledger'
    initialize(ledger, min_ev=0.02, stake=100, max_age_seconds=300, commission=0,
               now=now-timedelta(minutes=1))
    snapshot = build_snapshot(capture, prediction, event_id='synthetic-event',
                              bookmaker='synthetic-book', schedule=schedule)
    write_new(root / 'snapshot.json', snapshot)
    decision = record(ledger, snapshot, now=now)
    if decision['side'] != 'home' or not math.isclose(decision['estimated_net_ev'], 0.08):
        raise AssertionError('Decisión inesperada en demo')
    checks = {}
    for name, test_snapshot, clock, expected in [
        ('duplicate_rejected', snapshot, now, FileExistsError),
        ('stale_rejected', snapshot, now+timedelta(minutes=6), ValueError),
        ('late_rejected', snapshot, now+timedelta(hours=2), ValueError),
    ]:
        try:
            record(ledger, test_snapshot, now=clock)
        except expected:
            checks[name] = True
        else:
            raise AssertionError(f'Protección falló: {name}')
    balances = {}
    for outcome, expected_profit in [('home', 80), ('away', -100), ('void', 0)]:
        results = {game_id: outcome}
        write_new(root / f'results-{outcome}.json', results)
        balance = report(ledger, results, now=now+timedelta(hours=4))
        if not math.isclose(balance['net_profit'], expected_profit):
            raise AssertionError(f'Balance incorrecto: {outcome}')
        write_new(root / f'balance-{outcome}.json', balance)
        balances[outcome] = {'profit': balance['net_profit'], 'roi': balance['roi']}
    summary = {'synthetic': True, 'note': 'Tres escenarios alternativos; no sumar sus balances.',
               'side': decision['side'], 'stake': decision['stake'],
               'estimated_ev': decision['estimated_net_ev'], 'balances': balances,
               'checks': checks, 'api_requests': 0}
    write_new(root / 'summary.json', summary)
    (root / 'LEEME.txt').write_text(
        'DEMOSTRACIÓN SINTÉTICA: fechas y partido ficticios, sin API ni dinero real.\n'
        '1. prediction.json + capture.json + schedule.json: entradas.\n'
        '2. reconciliation.json: cruce de identidad.\n'
        '3. snapshot.json: predicción combinada con cuotas.\n'
        '4. ledger/: reglas y decisión congelada.\n'
        '5. results-*.json y balance-*.json: tres resultados ALTERNATIVOS.\n'
        '6. summary.json: comprobaciones y resumen. No demuestra rentabilidad.\n',
        encoding='utf-8')
    return summary


def main() -> None:
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_demo(args.output), indent=2))


if __name__ == '__main__':
    main()
