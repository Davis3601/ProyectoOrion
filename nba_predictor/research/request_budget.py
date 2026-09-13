"""Presupuesto local de intentos; reserva atómica antes de cada petición.

Los fallos también consumen intento local. No es el saldo global de la cuenta.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from nba_predictor.research.paper_trading import digest


def reserve_request(root: Path, *, now: datetime | None = None) -> dict:
    policy = json.loads((root / 'policy.json').read_text(encoding='utf-8'))
    if policy.get('schema_version') != 2:
        raise ValueError('Captura gobernada requiere política v2')
    month = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime('%Y-%m')
    with sqlite3.connect(root / 'request_budget.sqlite', timeout=30) as db:
        db.execute('CREATE TABLE IF NOT EXISTS rules (hash TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS usage (month TEXT PRIMARY KEY, attempts INTEGER)')
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        stored = db.execute('SELECT hash FROM rules').fetchone()
        if stored and stored[0] != digest(policy):
            raise ValueError('Política modificada tras iniciar el presupuesto')
        if not stored:
            db.execute('INSERT INTO rules VALUES (?)', (digest(policy),))
        used = db.execute('SELECT attempts FROM usage WHERE month=?', (month,)).fetchone()
        count = used[0] if used else 0
        if count >= policy['monthly_request_limit']:
            raise ValueError('Presupuesto mensual agotado; no se hizo petición')
        db.execute('INSERT INTO usage VALUES (?, ?) ON CONFLICT(month) DO UPDATE '
                   'SET attempts=excluded.attempts', (month, count+1))
    return {'month': month, 'attempt': count+1, 'limit': policy['monthly_request_limit'],
            'policy_sha256': digest(policy)}
