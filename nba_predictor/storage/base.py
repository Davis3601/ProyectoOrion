"""Interfaz abstracta para almacenamiento de datos NBA."""
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

# CHECK 4 — Simetría: todo save_* tiene su load_* correspondiente.


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

    @abstractmethod
    def save_features(self, features: pd.DataFrame, version: str = "v1") -> None:
        """Persiste la capa FEATURES (Layer 3) en Parquet. Idempotente: sobreescribe."""
        ...

    @abstractmethod
    def load_features(self, version: str = "v1") -> pd.DataFrame:
        """Carga la capa FEATURES desde Parquet. Lanza FileNotFoundError si no existe."""
        ...

    # ----- Modelos (Fase 4) -----

    @abstractmethod
    def save_model(self, pipeline: Any, metadata: dict, version_name: str) -> Path:
        """
        Serializa pipeline + metadata en el registry de modelos.

        Devuelve la ruta del directorio de la versión guardada.
        Idempotente: sobreescribe si ya existe.
        """
        ...

    @abstractmethod
    def load_model(self, version_name: str) -> tuple[Any, dict]:
        """
        Carga (pipeline, metadata) de una versión del registry.

        Falla ruidosamente con FileNotFoundError si la versión no existe.
        """
        ...

    # ----- Utilidad -----

    @abstractmethod
    def existing_game_ids(self, season: str) -> set[str]:
        """Para saber qué partidos ya tienes."""
        ...

    # ----- Injury Reports (Fase 5b / 13e-1) -----

    @abstractmethod
    def save_raw_injury_report(self, date_str: str, suffix: str, pdf_bytes: bytes) -> None:
        """Persiste el PDF de injury report como bytes (no JSON).

        Ruta canónica: raw/injury_reports/{date_str}_{suffix}.pdf
        date_str formato: YYYY-MM-DD  (ej. "2026-03-13")
        suffix formato:   sufijo de la URL sin extensión (ej. "01_15PM", "11PM")
        Idempotente: sobreescribe si ya existe.
        """
        ...