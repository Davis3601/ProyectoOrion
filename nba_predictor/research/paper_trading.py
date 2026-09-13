"""Registro prospectivo local y simulación de moneyline NBA (incluye prórroga).

No importa Settings ni DataStore: nunca hereda credenciales o modo cloud.
Archivos exclusivos evitan reemplazar decisiones; no constituyen un sello externo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("Las fechas requieren zona horaria")
    return result.astimezone(timezone.utc)


def number(value: object, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise ValueError("Booleano donde se esperaba número")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"Número fuera de [{low}, {high}]")
    return result


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def initialize(root: Path, *, min_ev: float, stake: float, max_age_seconds: float,
               commission: float, now: datetime | None = None,
               bookmaker: str | None = None, matching_rule: str = "exact_or_plus_600s_v1",
               monthly_requests: int = 300) -> None:
    """Congela reglas antes de registrar observaciones. Comisión sobre ganancias."""
    policy = {
        "min_ev": number(min_ev, 0, 1),
        "stake": number(stake, 0.01, 1_000_000),
        "max_age_seconds": number(max_age_seconds, 1, 3600),
        "commission": number(commission, 0, 0.99),
        "created_at_utc": (now or datetime.now(timezone.utc)).isoformat(),
        "market": "moneyline_including_overtime",
        "selection": "highest_net_ev_first_snapshot_one_bet_per_game",
    }
    if bookmaker is not None:
        if not re.fullmatch(r"[a-z0-9_]+", bookmaker):
            raise ValueError("Casa inv?lida")
        if matching_rule not in {"exact_v1", "exact_or_plus_600s_v1"}:
            raise ValueError("Regla de vinculaci?n inv?lida")
        if type(monthly_requests) is not int or not 1 <= monthly_requests <= 500:
            raise ValueError("Presupuesto debe ser entero entre 1 y 500")
        policy.update(schema_version=2, bookmaker=bookmaker, matching_rule=matching_rule,
                      capture_min_minutes_before=55, capture_max_minutes_before=65,
                      monthly_request_limit=monthly_requests, region="us",
                      provider="the-odds-api.com", budget_period="UTC calendar month",
                      budget_unit="attempts", capture_market="h2h")
    root.mkdir(parents=True, exist_ok=True)
    write_new(root / "policy.json", policy)


def record(root: Path, snapshot: dict, *, now: datetime | None = None) -> dict:
    """Congela primera observación válida, incluso cuando la decisión es abstenerse.

    Entrada: fila exportada de predictions_log + cuota para cada lado de UNA casa.
    Rechaza captura retrospectiva o desfasada; no certifica aceptación de una casa.
    """
    now = now or datetime.now(timezone.utc)
    policy = json.loads((root / "policy.json").read_text(encoding="utf-8"))
    game_id = snapshot["game_id"]
    if not isinstance(game_id, str) or not re.fullmatch(r"002\d{7}", game_id):
        raise ValueError("game_id debe ser regular season NBA: 002 + 7 dígitos")
    for field in ("bookmaker", "source", "model_version", "home_team", "away_team"):
        if not isinstance(snapshot[field], str) or not snapshot[field].strip():
            raise ValueError(f"Falta {field}")
    if snapshot["home_team"] == snapshot["away_team"]:
        raise ValueError("Los equipos deben ser distintos")
    if snapshot["market"] != policy["market"]:
        raise ValueError("Mercado incompatible: requiere moneyline incluyendo prórroga")
    start = timestamp(snapshot["tip_off_utc"])
    predicted = timestamp(snapshot["predicted_at_utc"])
    quoted = timestamp(snapshot["quoted_at_utc"])
    if not timestamp(policy["created_at_utc"]) <= now < start:
        raise ValueError("El registro debe ocurrir después de crear reglas y antes del partido")
    for stamp in (predicted, quoted):
        age = (now - stamp).total_seconds()
        if not 0 <= age <= policy["max_age_seconds"]:
            raise ValueError("Predicción/cuota futura o demasiado antigua")
    if policy.get("schema_version") == 2:
        budget = snapshot.get("request_budget") or {}
        if budget.get("policy_sha256") != digest(policy):
            raise ValueError("Captura sin presupuesto de esta pol?tica")
        if snapshot["bookmaker"] != policy["bookmaker"]:
            raise ValueError("Casa distinta de la pol?tica congelada")
        if snapshot.get("matching_rule") != policy["matching_rule"]:
            raise ValueError("Regla horaria distinta de la pol?tica congelada")
        if timestamp(snapshot["nba_tip_off_utc"]) != start:
            raise ValueError("El l?mite debe ser el horario NBA")
        offset = (timestamp(snapshot["provider_tip_off_utc"]) - start).total_seconds()
        allowed = (0,) if policy["matching_rule"] == "exact_v1" else (0, 600)
        if offset not in allowed:
            raise ValueError("Desfase no permitido")
        for instant in (now, timestamp(snapshot["fetched_at_utc"])):
            minutes = (start - instant).total_seconds() / 60
            if not policy["capture_min_minutes_before"] <= minutes <= (
                    policy["capture_max_minutes_before"]):
                raise ValueError("Fuera de ventana de captura 55-65 minutos antes de NBA")
        fetched = timestamp(snapshot["fetched_at_utc"])
        if not 0 <= (now-fetched).total_seconds() <= policy["max_age_seconds"]:
            raise ValueError("Captura antigua o futura")
    p_home = number(snapshot["p_home_win"], 0, 1)
    candidates = []
    for side, probability in (("home", p_home), ("away", 1 - p_home)):
        odds = number(snapshot[f"{side}_decimal_odds"], 1.000001, 10000)
        net_odds = 1 + (odds - 1) * (1 - policy["commission"])
        candidates.append((probability * net_odds - 1, side, odds))
    ev, side, odds = max(candidates, key=lambda item: item[0])
    selected = ev > policy["min_ev"]
    row = {
        "snapshot": snapshot, "policy_sha256": digest(policy),
        "recorded_at_utc": now.isoformat(), "side": side if selected else None,
        "decimal_odds": odds if selected else None, "estimated_net_ev": ev,
        "stake": policy["stake"] if selected else 0,
    }
    write_new(root / f"{game_id}.json", row)
    return row


def report(root: Path, results: dict, *, now: datetime | None = None) -> dict:
    """Resultados explícitos: game_id -> home / away / void. Ausente = pendiente.

    Orden de curva = registro de decisiones, no flujo de caja real. No supone
    que cuotas capturadas hayan podido ejecutarse ni calcula impuestos.
    """
    now = now or datetime.now(timezone.utc)
    policy = json.loads((root / "policy.json").read_text(encoding="utf-8"))
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in root.glob("002*.json")]
    known = {row["snapshot"]["game_id"] for row in rows}
    if set(results) - known or any(v not in ("home", "away", "void") for v in results.values()):
        raise ValueError("Resultado desconocido o game_id no registrado")
    bets = []
    cumulative = peak = drawdown = turnover = 0.0
    pending = voided = abstained = 0
    for row in sorted(rows, key=lambda row: (row["recorded_at_utc"], row["snapshot"]["game_id"])):
        if row["policy_sha256"] != digest(policy):
            raise ValueError("Las reglas cambiaron después del registro")
        game_id = row["snapshot"]["game_id"]
        if row["side"] is None:
            abstained += 1
            continue
        outcome = results.get(game_id)
        if outcome is None:
            pending += 1
            continue
        if now <= timestamp(row["snapshot"]["tip_off_utc"]):
            raise ValueError("No se puede liquidar antes del inicio")
        if outcome == "void":
            voided += 1
            continue
        stake = row["stake"]
        profit = (stake * (row["decimal_odds"] - 1) * (1 - policy["commission"])
                  if outcome == row["side"] else -stake)
        turnover += stake
        cumulative += profit
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
        bets.append({"game_id": game_id, "profit": profit, "cumulative_profit": cumulative})
    return {
        "experimental_only": True, "policy_sha256": digest(policy),
        "results_sha256": digest(results), "observations": len(rows),
        "settled_bets": len(bets), "pending_bets": pending, "void_bets": voided,
        "abstentions": abstained, "turnover": turnover, "net_profit": cumulative,
        "roi": cumulative / turnover if turnover else None,
        "max_drawdown_decision_order": drawdown, "bets": bets,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--min-ev", type=float, required=True)
    init.add_argument("--stake", type=float, required=True)
    init.add_argument("--max-age-seconds", type=float, default=300)
    init.add_argument("--commission", type=float, default=0)
    init.add_argument("--bookmaker")
    init.add_argument("--matching-rule", choices=["exact_v1", "exact_or_plus_600s_v1"],
                      default="exact_or_plus_600s_v1")
    init.add_argument("--monthly-requests", type=int, default=300)
    capture = commands.add_parser("record")
    capture.add_argument("snapshot", type=Path)
    evaluate = commands.add_parser("report")
    evaluate.add_argument("results", type=Path)
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.directory, min_ev=args.min_ev, stake=args.stake,
                   max_age_seconds=args.max_age_seconds, commission=args.commission,
                   bookmaker=args.bookmaker, matching_rule=args.matching_rule,
                   monthly_requests=args.monthly_requests)
        output = {"initialized": str(args.directory)}
    elif args.command == "record":
        output = record(args.directory, json.loads(args.snapshot.read_text(encoding="utf-8-sig")))
    else:
        output = report(args.directory, json.loads(args.results.read_text(encoding="utf-8-sig")))
    print(json.dumps(output, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
