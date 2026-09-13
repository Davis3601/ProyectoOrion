"""The Odds API v4: captura local NBA y registro explícito de una casa.

API key solo desde ODDS_API_KEY; sin .env, GCP ni ejecución de apuestas.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from nba_predictor.research.paper_trading import record, timestamp, write_new

ENDPOINT = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
TEAM_NAMES = dict(line.split("|", 1) for line in """ATL|Atlanta Hawks
BOS|Boston Celtics
BKN|Brooklyn Nets
CHA|Charlotte Hornets
CHI|Chicago Bulls
CLE|Cleveland Cavaliers
DAL|Dallas Mavericks
DEN|Denver Nuggets
DET|Detroit Pistons
GSW|Golden State Warriors
HOU|Houston Rockets
IND|Indiana Pacers
LAC|Los Angeles Clippers
LAL|Los Angeles Lakers
MEM|Memphis Grizzlies
MIA|Miami Heat
MIL|Milwaukee Bucks
MIN|Minnesota Timberwolves
NOP|New Orleans Pelicans
NYK|New York Knicks
OKC|Oklahoma City Thunder
ORL|Orlando Magic
PHI|Philadelphia 76ers
PHX|Phoenix Suns
POR|Portland Trail Blazers
SAC|Sacramento Kings
SAS|San Antonio Spurs
TOR|Toronto Raptors
UTA|Utah Jazz
WAS|Washington Wizards""".splitlines())


def fetch_odds(api_key: str, *, region: str = "us", opener=urlopen) -> dict:
    """Una petición, sin reintentos automáticos que consuman créditos extra.

    Errores sanitizados: ni URL autenticada ni cuerpo del error salen a consola.
    No enviar predicciones ni datos del repositorio al proveedor.
    """
    if not api_key.strip():
        raise ValueError("Configura ODDS_API_KEY en el entorno local")
    if region not in {"us", "us2", "uk", "eu", "au"}:
        raise ValueError("Región no soportada")
    params = {"regions": region, "markets": "h2h", "oddsFormat": "decimal",
              "dateFormat": "iso"}
    url = ENDPOINT + "?" + urlencode(params | {"apiKey": api_key})
    try:
        with opener(url, timeout=30) as response:
            payload = json.load(response)
            quota = {name: response.headers.get(name) for name in (
                "x-requests-remaining", "x-requests-used", "x-requests-last")}
    except HTTPError as exc:
        raise RuntimeError(f"The Odds API respondió HTTP {exc.code}; revisa clave/cuota") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise RuntimeError("No se pudo obtener JSON de The Odds API") from None
    if not isinstance(payload, list) or any(not isinstance(event, dict) for event in payload):
        raise ValueError("Respuesta inesperada: se esperaba una lista de eventos")
    return {"provider": "the-odds-api.com", "endpoint": ENDPOINT, "parameters": params,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "quota": quota, "events": payload}


def single(items: list[dict], key: str, value: str) -> dict:
    matches = [item for item in items if item.get(key) == value]
    if len(matches) != 1:
        raise ValueError(f"Se esperaba exactamente una coincidencia para {key}={value}")
    return matches[0]


def build_snapshot(capture: dict, prediction: dict, *, event_id: str, bookmaker: str,
                   schedule: dict | None = None, allow_ten_minute_offset: bool = False) -> dict:
    """Cruce estricto: ID proveedor + equipos + hora exacta del calendario NBA.

    prediction es una fila de predictions_log exportada, enriquecida con
    tip_off_utc del calendario. No inferimos game_id ni inventamos timestamps.
    """
    if (capture.get("provider") != "the-odds-api.com"
            or capture.get("parameters", {}).get("oddsFormat") != "decimal"
            or capture.get("parameters", {}).get("markets") != "h2h"):
        raise ValueError("Captura incompatible")
    event = single(capture["events"], "id", event_id)
    if event.get("sport_key") != "basketball_nba":
        raise ValueError("El evento no es NBA")
    for side in ("home", "away"):
        expected = TEAM_NAMES.get(prediction[f"{side}_team"])
        if expected is None or expected != event[f"{side}_team"]:
            raise ValueError(f"El equipo {side} no coincide; revisar mapeo explícitamente")
    match_metadata = {"matching_rule": "exact_v1", "offset_seconds": 0}
    if allow_ten_minute_offset and schedule is None:
        raise ValueError("El modo +10 minutos requiere calendario NBA")
    if schedule is not None:
        from nba_predictor.research.reconcile_odds import reconcile
        audit = reconcile(capture, schedule, allow_ten_minute_offset=allow_ten_minute_offset)
        match = single(audit["rows"], "provider_event_id", event_id)
        if match["status"] not in {"matched", "matched_offset"}:
            raise ValueError("Evento sin coincidencia ?nica en el calendario")
        if (match["game_id"] != prediction["game_id"] or
                timestamp(match["nba_tip_off_utc"]) != timestamp(prediction["tip_off_utc"])):
            raise ValueError("Predicci?n no corresponde al ID/horario NBA verificado")
        match_metadata = {"matching_rule": audit["matching_rule"],
                          "offset_seconds": match["offset_seconds"]}
    elif timestamp(prediction["tip_off_utc"]) != timestamp(event["commence_time"]):
        raise ValueError("La hora del calendario no coincide con el proveedor")
    book = single(event["bookmakers"], "key", bookmaker)
    market = single(book["markets"], "key", "h2h")
    outcomes = market["outcomes"]
    if len(outcomes) != 2:
        raise ValueError("Moneyline requiere exactamente dos resultados")
    # Preferir sello del mercado; el sello de descarga nunca sustituye al de cuota.
    updated = market.get("last_update") or book["last_update"]
    if timestamp(updated) > timestamp(capture["fetched_at_utc"]):
        raise ValueError("Cuota posterior a la captura")
    return {**prediction, **match_metadata, "bookmaker": bookmaker,
            "nba_tip_off_utc": prediction["tip_off_utc"],
            "provider_tip_off_utc": event["commence_time"],
            "source": f"{ENDPOINT}#event={event_id}",
            "provider_event_id": event_id,
            "fetched_at_utc": capture["fetched_at_utc"],
            "market": "moneyline_including_overtime", "quoted_at_utc": updated,
            "home_decimal_odds": single(outcomes, "name", event["home_team"])["price"],
            "away_decimal_odds": single(outcomes, "name", event["away_team"])["price"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--region", choices=["us", "us2", "uk", "eu", "au"], default="us")
    register = commands.add_parser("record")
    register.add_argument("--capture", type=Path, required=True)
    register.add_argument("--prediction", type=Path, required=True)
    register.add_argument("--event-id", required=True)
    register.add_argument("--bookmaker", required=True)
    register.add_argument("--directory", type=Path, required=True)
    register.add_argument("--schedule", type=Path)
    register.add_argument("--allow-ten-minute-offset", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "capture":
            # Detectar destino existente antes de gastar créditos; escritura exclusiva.
            args.output.parent.mkdir(parents=True, exist_ok=True)
            if args.output.exists():
                raise FileExistsError("La captura ya existe; usa otro nombre")
            data = fetch_odds(os.environ.get("ODDS_API_KEY", ""), region=args.region)
            write_new(args.output, data)
            print(json.dumps({"file": str(args.output), "events": len(data["events"]),
                              "quota": data["quota"]}))
        else:
            data = json.loads(args.capture.read_text(encoding="utf-8-sig"))
            prediction = json.loads(args.prediction.read_text(encoding="utf-8-sig"))
            snapshot = build_snapshot(data, prediction, event_id=args.event_id,
                                      bookmaker=args.bookmaker,
                                      schedule=json.loads(args.schedule.read_text(
                                          encoding="utf-8-sig")) if args.schedule else None,
                                      allow_ten_minute_offset=args.allow_ten_minute_offset)
            row = record(args.directory, snapshot)
            print(json.dumps(row, indent=2, allow_nan=False))
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
