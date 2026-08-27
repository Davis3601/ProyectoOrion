"""Abstract interface for NBA data storage."""
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

# CHECK 4 — Simetría: todo save_* tiene su load_* correspondiente.


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

    @abstractmethod
    def get_latest_model_version(self) -> str:
        """Devuelve el version_name más reciente del registry.

        Criterio: orden lexicográfico del nombre de versión (v1_logistic_bclean_YYYY-MM-DD),
        que coincide con orden cronológico gracias al formato ISO 8601 de la fecha.

        Registry vacío o inaccesible → FileNotFoundError con mensaje claro.
        Nunca devuelve None silencioso.
        """
        ...

    # ----- Utilidad -----

    @abstractmethod
    def existing_game_ids(self, season: str) -> set[str]:
        """Check which games are already stored."""
        ...

    # ----- predictions_log (Fase 5b / 13e-2.4) -----

    @abstractmethod
    def save_predictions_log(self, rows: list[dict]) -> None:
        """Anexa filas de evidencia al log de predicciones. APPEND-ONLY.

        Una fila por partido por servida (Decisión 13e-2.4): se loggea CADA
        servida SIN deduplicar — predicted_at_utc las distingue. La servida
        "de record" del día se identifica en el ANÁLISIS, jamás en la escritura.

        NUNCA actualiza ni sobreescribe filas existentes: el grading se computa
        después como JOIN contra resultados (grading = query; log = intocable).
        Por eso este método es la excepción documentada al patrón idempotente
        MERGE/INSERT OR REPLACE del resto del contrato.

        Cada fila lleva las 9 claves del schema cerrado:
            game_id, game_date, home_team, away_team, p_home_win,
            model_version, predicted_at_utc, served_by, absences_applied

        rows vacía → no-op (día sin partidos: nada que registrar).
        """
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