from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Project configuration. Reads from .env and environment variables."""
    
    model_config = SettingsConfigDict(env_file=".env", env_prefix="NBA_PREDICTOR_")
    
    mode: Literal["local", "cloud"] = "local"
    data_dir: Path = Path("./data")
    db_path: Path = Path("./data/nba.sqlite")
    
    # Only used in mode='cloud'
    gcp_project_id: str = ""
    gcs_bucket: str = ""
    bq_dataset: str = "nba_predictor"
    
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"
    
    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"


settings = Settings()


# Project Seasons — canonical source (see inventory in CLAUDE.md)
# Use these constants throughout the code; never define local lists.
WARMUP_SEASONS: tuple[str, ...] = ("2014-15", "2015-16")

TRAINING_SEASONS: tuple[str, ...] = (
    "2016-17", "2017-18", "2018-19", "2019-20", "2020-21",
    "2021-22", "2022-23", "2023-24", "2024-25", "2025-26",
)

# All seasons to be downloaded (warmup + training)
ALL_SEASONS: tuple[str, ...] = WARMUP_SEASONS + TRAINING_SEASONS