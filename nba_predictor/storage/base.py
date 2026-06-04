"""Interfaz abstracta para almacenamiento de datos NBA."""
from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataStore(ABC):
    """Interfaz para almacenamiento de datos NBA.
    
    Cualquier implementación (local, cloud) debe implementar estos métodos.
    El código de negocio depende de esta interfaz, no de implementaciones concretas.
    """
    
    # ----- Escritura -----

    @abstractmethod
    def save_teams(self, teams: pd.DataFrame) -> None:
        """Guarda el catálogo de equipos (30 equipos, raramente cambia). Idempotente."""
        ...

    @abstractmethod
    def save_games(self, games: pd.DataFrame) -> None:
        """Guarda metadata de partidos. Idempotente."""
        ...
    
    @abstractmethod
    def save_team_game_stats(self, stats: pd.DataFrame) -> None:
        ...
    
    @abstractmethod
    def save_player_game_stats(self, stats: pd.DataFrame) -> None:
        ...
    
    @abstractmethod
    def save_raw_boxscore(self, game_id: str, payload: dict) -> None:
        """Guarda la respuesta cruda de la API."""
        ...
    
    # ----- Lectura -----
    
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
        """Devuelve el catálogo de equipos: team_id, abbreviation, name."""
        ...

    # ----- Utilidad -----

    @abstractmethod
    def existing_game_ids(self, season: str) -> set[str]:
        """Para saber qué partidos ya tienes."""
        ...