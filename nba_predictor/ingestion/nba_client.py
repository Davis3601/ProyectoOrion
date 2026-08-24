"""Client for nba_api with retry, rate limiting, and minimal normalization."""
import time

import pandas as pd
from nba_api.stats.endpoints import boxscoretraditionalv2, leaguegamefinder
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


# Error types that justify a retry
_RETRYABLE = (TimeoutError, ConnectionError, ConnectionResetError)


class NBAClient:
    """Minimalistic wrapper for nba_api with robustness policies."""
    
    def __init__(self, request_delay_seconds: float = 0.6):
        """
        Args:
            request_delay_seconds: pause between requests. NBA tolerates ~30 req/min;
                0.6s is a conservative value that works well in practice.
        """
        self.request_delay = request_delay_seconds
        self._last_request_time = 0.0
    
    def _throttle(self) -> None:
        """Ensures that we don't call the API faster than request_delay."""
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
        Downloads the full schedule for an NBA season.
        
        Args:
            season: format '2023-24' (season ending in 2024).
        
        Returns:
            DataFrame with one row per team per game (each game appears twice).
            Relevant columns: GAME_ID, GAME_DATE, TEAM_ID, TEAM_ABBREVIATION,
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
        Downloads the box score for a game.

        Args:
            game_id: official NBA identifier, e.g., '0022300001'.

        Returns:
            (team_stats, player_stats, raw_payload) where raw_payload is the raw
            API dictionary to persist in the RAW layer without processing.
        """
        self._throttle()
        box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id, timeout=30)
        team_df = box.team_stats.get_data_frame()
        player_df = box.player_stats.get_data_frame()
        raw = box.get_dict()
        return team_df, player_df, raw
