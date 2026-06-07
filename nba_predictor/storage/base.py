"""Abstract interface for NBA data storage."""
from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataStore(ABC):
    """Interface for NBA data storage.
    
    Any implementation (local, cloud) must implement these methods.
    Business logic depends on this interface, not on concrete implementations.
    """
    
    # ----- Write -----

    @abstractmethod
    def save_teams(self, teams: pd.DataFrame) -> None:
        """Saves the teams catalog (30 teams, rarely changes). Idempotent."""
        ...

    @abstractmethod
    def save_games(self, games: pd.DataFrame) -> None:
        """Saves game metadata. Idempotent."""
        ...
    
    @abstractmethod
    def save_team_game_stats(self, stats: pd.DataFrame) -> None:
        ...
    
    @abstractmethod
    def save_player_game_stats(self, stats: pd.DataFrame) -> None:
        ...
    
    @abstractmethod
    def save_raw_boxscore(self, game_id: str, payload: dict) -> None:
        """Saves the raw API response."""
        ...
    
    # ----- Read -----
    
    @abstractmethod
    def load_games(
        self,
        season: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        ...
    
    @abstractmethod
    def load_team_game_stats(
        self,
        season: str | None = None,
        team_id: int | None = None,
    ) -> pd.DataFrame:
        ...

    @abstractmethod
    def load_player_game_stats(
        self,
        season: str | None = None,
        team_id: int | None = None,
        player_id: int | None = None,
    ) -> pd.DataFrame:
        ...
    
    @abstractmethod
    def load_teams(self) -> pd.DataFrame:
        """Returns the teams catalog: team_id, abbreviation, name."""
        ...

    # ----- Utility -----

    @abstractmethod
    def existing_game_ids(self, season: str) -> set[str]:
        """Check which games are already stored."""
        ...
