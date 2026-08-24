from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Project configuration. Reads from .env and environment variables."""
    
    model_config = SettingsConfigDict(env_file=".env", env_prefix="NBA_PREDICTOR_")
    
    mode: Literal["local", "cloud"] = "local"
    data_dir: Path = Path("./data")
    db_path: Path = Path("./data/nba.sqlite")
    
    # Estos solo se usan en mode='cloud'
    gcp_project_id: str = "predictorsnonprod"
    gcs_bucket: str = "predictorsnonprod-nba-predictors"
    bq_dataset: str = "nba_predictor"
    
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"
    
    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"


settings = Settings()


# Hiperparámetro de ventana móvil — nunca hardcodear en módulos de features.
# Se experimentará con 5/15/20 en la fase de iteración midiendo log loss OOS.
ROLLING_WINDOW_GAMES: int = 10

# Cap de días de descanso. Más de una semana no añade frescura marginal
# y los ~100 días de offseason distorsionarían rest_diff.
REST_DAYS_CAP: int = 7

# Temporadas del proyecto — fuente canónica (ver inventario en CLAUDE.md)
# Usar estas constantes en todo el código; nunca definir listas locales.
WARMUP_SEASONS: tuple[str, ...] = ("2014-15", "2015-16")

TRAINING_SEASONS: tuple[str, ...] = (
    "2016-17", "2017-18", "2018-19", "2019-20", "2020-21",
    "2021-22", "2022-23", "2023-24", "2024-25", "2025-26",
)

# All seasons to be downloaded (warmup + training)
ALL_SEASONS: tuple[str, ...] = WARMUP_SEASONS + TRAINING_SEASONS

# ---------------------------------------------------------------------------
# ELO Baseline — parámetros CERRADOS (Fase 3, ver CLAUDE.md)
# K, HOME_ADV y DIVISOR son interdependientes: no cambiar uno sin repensar
# los otros. FiveThirtyEight usa valores similares para NBA.
# ---------------------------------------------------------------------------
ELO_K: int = 20                      # K-factor; memoria efectiva ~10-15 partidos
ELO_HOME_ADV: int = 100              # Ventaja ELO al local; ≈64% entre iguales; 0 en neutral
ELO_DIVISOR: int = 400               # Divisor logístico (convención)
ELO_SEASON_CARRYOVER: float = 0.75   # 75/25 hacia regression_mean entre temporadas
ELO_REGRESSION_MEAN: float = 1505.0  # Media de regresión ≈1500
ELO_INITIAL_RATING: float = 1500.0   # Rating inicial de todos los equipos en 2014-15

# Walk-forward: primer fold valida TRAINING_SEASONS[FIRST_VAL_IDX]
# "Primer fold valida 2020-21 (entrenando 2016-17..2019-20)" → índice 4
FIRST_VAL_IDX: int = 4

# ---------------------------------------------------------------------------
# Regresión logística — parámetros (Fase 3, ver CLAUDE.md)
# C = 1/λ: mayor C = menos regularización. L2 por defecto.
# Afinar con búsqueda anidada es iteración posterior al duelo A/B.
# ---------------------------------------------------------------------------
LOGREG_C: float = 1.0

# ---------------------------------------------------------------------------
# XGBoost — parámetros iniciales (Fase 3, ver CLAUDE.md)
# Todos en config.py para no hardcodear en el módulo del modelo.
# El primer XGBoost responde "¿hay no-linealidad?" — el tuning fino es posterior.
# ---------------------------------------------------------------------------
XGB_MAX_DEPTH: int = 3            # interacciones pares/tríos; evita memorizar con ~9.6k filas
XGB_LEARNING_RATE: float = 0.05   # shrinkage conservador
XGB_N_ESTIMATORS: int = 1000      # techo; early stopping decide el número real
XGB_SUBSAMPLE: float = 0.8        # 80% de filas por árbol (ligero bagging)
XGB_COLSAMPLE: float = 0.8        # 80% de features por árbol
XGB_EARLY_STOP_ROUNDS: int = 50   # si no mejora 50 rondas consecutivas, para

# ---------------------------------------------------------------------------
# Registry de modelos — Fase 4 (ver CLAUDE.md)
# Cadencia documental hasta Cloud Scheduler en Fase 5b.
# ---------------------------------------------------------------------------
RETRAIN_CADENCE_DAYS: int = 7     # reentrenamiento semanal