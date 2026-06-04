"""
Script: descarga el calendario de todas las temporadas configuradas.

Uso:
    python scripts/ingest_schedules.py

Idempotente: re-correrlo es seguro (sobrescribe los registros existentes).
"""
import logging

from tqdm import tqdm

from nba_predictor.config import ALL_SEASONS
from nba_predictor.ingestion.nba_client import NBAClient
from nba_predictor.ingestion.schedule import ingest_season_schedule
from nba_predictor.storage import get_datastore


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    log = logging.getLogger(__name__)
    
    store = get_datastore()
    client = NBAClient()
    
    log.info(f"Descargando {len(ALL_SEASONS)} temporadas")
    
    for season in tqdm(ALL_SEASONS, desc="Temporadas"):
        try:
            games = ingest_season_schedule(client, season)
            log.info(f"{season}: {len(games)} partidos descargados")
            store.save_games(games)
        except Exception as e:
            log.error(f"Error en {season}: {e}")
            raise
    
    log.info("Descarga completada")
    
    all_games = store.load_games()
    log.info(f"Total de partidos en BD: {len(all_games)}")
    log.info(f"Por temporada:\n{all_games.groupby('season').size()}")


if __name__ == "__main__":
    main()