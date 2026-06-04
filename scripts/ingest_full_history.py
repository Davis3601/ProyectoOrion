"""
Script: descarga box scores de todos los partidos jugados y los persiste en SQLite.

Uso:
    python scripts/ingest_full_history.py

Idempotente: verifica cuáles game_ids ya tienen JSON en data/raw/ y solo descarga
los que faltan, por lo que re-correrlo es seguro después de una interrupción.

Nota de tiempo: ~15 000 partidos × 0.6 s/req ≈ 2.5 horas. Planificarlo como
una tarea de fondo o ejecutarlo en partes usando --season.
"""
import argparse
import logging
from pathlib import Path

from tqdm import tqdm

from nba_predictor.config import ALL_SEASONS, settings
from nba_predictor.ingestion.boxscores import ingest_boxscore
from nba_predictor.ingestion.nba_client import NBAClient
from nba_predictor.storage import get_datastore


def _already_downloaded(raw_dir: Path) -> set[str]:
    """Devuelve los game_ids cuyo JSON crudo ya existe en raw_dir."""
    return {p.stem for p in raw_dir.glob("*.json")}


def _run_season(store, client: NBAClient, season: str, done: set[str]) -> dict:
    """
    Descarga los box scores pendientes de una temporada.

    Returns un dict con contadores para el resumen final.
    """
    games = store.load_games(season=season)
    # Solo partidos ya jugados (home_pts no nulo)
    played = games[games["home_pts"].notna()]
    pending = played[~played["game_id"].isin(done)]

    n_already = len(played) - len(pending)
    n_ok = 0
    n_error = 0

    for _, row in tqdm(pending.iterrows(), total=len(pending), desc=season, leave=False):
        game_id = row["game_id"]
        home_team_id = int(row["home_team_id"])
        try:
            team_stats, player_stats, raw_payload = ingest_boxscore(
                client, game_id, home_team_id
            )
            store.save_team_game_stats(team_stats)
            store.save_player_game_stats(player_stats)
            store.save_raw_boxscore(game_id, raw_payload)
            done.add(game_id)
            n_ok += 1
        except Exception as exc:
            logging.getLogger(__name__).warning(f"  Error en {game_id}: {exc}")
            n_error += 1

    return {"season": season, "ya_tenia": n_already, "descargados": n_ok, "errores": n_error}


def main(seasons: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger(__name__)

    target_seasons = seasons or ALL_SEASONS

    store = get_datastore()
    client = NBAClient(request_delay_seconds=0.6)
    done = _already_downloaded(settings.raw_dir)

    log.info(f"Temporadas a procesar: {target_seasons}")
    log.info(f"Box scores ya en raw_dir: {len(done)}")

    results = []
    for season in tqdm(target_seasons, desc="Temporadas"):
        r = _run_season(store, client, season, done)
        results.append(r)
        log.info(
            f"{season}: {r['descargados']} descargados, "
            f"{r['ya_tenia']} ya existían, {r['errores']} errores"
        )

    total_ok = sum(r["descargados"] for r in results)
    total_err = sum(r["errores"] for r in results)
    log.info(f"Completado — {total_ok} nuevos box scores, {total_err} errores")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Descarga box scores históricos NBA")
    parser.add_argument(
        "--season",
        nargs="+",
        metavar="SEASON",
        help="Una o más temporadas a procesar (e.g. --season 2023-24 2024-25). "
             "Por defecto descarga todas.",
    )
    args = parser.parse_args()
    main(seasons=args.season)
