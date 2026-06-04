"""Cliente para nba_api con retry, rate limiting y normalización mínima."""
import time

import pandas as pd
from nba_api.stats.endpoints import boxscoretraditionalv2, leaguegamefinder
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


# Tipos de error que justifican reintentar
_RETRYABLE = (TimeoutError, ConnectionError, ConnectionResetError)


class NBAClient:
    """Wrapper minimalista de nba_api con políticas de robustez."""
    
    def __init__(self, request_delay_seconds: float = 0.6):
        """
        Args:
            request_delay_seconds: pausa entre requests. NBA tolera ~30 req/min;
                0.6s es un valor conservador que en la práctica funciona bien.
        """
        self.request_delay = request_delay_seconds
        self._last_request_time = 0.0
    
    def _throttle(self) -> None:
        """Asegura que no llamamos a la API más rápido que request_delay."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_request_time = time.monotonic()
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=10),
        retry=retry_if_exception_type(_RETRYABLE),
        reraise=True,
    )
    def fetch_season_schedule(self, season: str) -> pd.DataFrame:
        """
        Descarga el calendario completo de una temporada de NBA.
        
        Args:
            season: formato '2023-24' (temporada terminando en 2024).
        
        Returns:
            DataFrame con una fila por equipo por partido (cada partido aparece 2 veces).
            Columnas relevantes: GAME_ID, GAME_DATE, TEAM_ID, TEAM_ABBREVIATION,
            MATCHUP, WL, PTS.
        """
        self._throttle()
        finder = leaguegamefinder.LeagueGameFinder(
            season_nullable=season,
            season_type_nullable="Regular Season",
            league_id_nullable="00",  # 00 = NBA
            timeout=30,
        )
        df = finder.get_data_frames()[0]
        return df

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=10),
        retry=retry_if_exception_type(_RETRYABLE),
        reraise=True,
    )
    def fetch_boxscore(self, game_id: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
        """
        Descarga el box score de un partido.

        Args:
            game_id: identificador oficial NBA, e.g. '0022300001'.

        Returns:
            (team_stats, player_stats, raw_payload) donde raw_payload es el dict
            crudo de la API para persistirlo en la capa RAW sin procesar.
        """
        self._throttle()
        box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id, timeout=30)
        team_df = box.team_stats.get_data_frame()
        player_df = box.player_stats.get_data_frame()
        raw = box.get_dict()
        return team_df, player_df, raw