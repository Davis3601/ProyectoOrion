"""
Evaluación walk-forward de los baselines — Fase 3.

Esquema de folds (ver "Reglas de validación" en CLAUDE.md):
    Fold 0: train 2016-17..2019-20  → val 2020-21
    Fold 1: train 2016-17..2020-21  → val 2021-22
    Fold 2: train 2016-17..2021-22  → val 2022-23
    Fold 3: train 2016-17..2022-23  → val 2023-24
    Fold 4: train 2016-17..2023-24  → val 2024-25
    Fold 5: train 2016-17..2024-25  → val 2025-26

Por qué el primer fold arranca en 2020-21 (y no en 2017-18):
- La primera temporada disponible es 2016-17. Validar 2017-18 con solo una
  temporada de entrenamiento da una constante con muy poca data y un ELO
  con solo un año de calentamiento. El contrato fija 4 temporadas como
  mínimo de entrenamiento: es el balance entre tener suficiente historia para
  que la constante sea estable y aún tener múltiples folds de validación.

Anti-leakage garantizado por diseño:
- ConstantBaseline: fit se llama SOLO con train_df (temporadas anteriores).
- EloBaseline: se pre-computan predicciones para todos los game_ids de
  features_v1 en un solo pase. El ELO registra cada predicción antes de
  actualizar → temporalmente correcto por construcción.
- Nunca hay acceso a datos de validación durante el fit.

Comparabilidad con modelos futuros:
- La evaluación es SOLO sobre los game_ids de features_v1 (9 643 partidos),
  aunque el ELO internamente procese todos los 14 429 partidos.
- Esto garantiza que el log loss de los baselines es comparable al log loss
  de cualquier modelo entrenado sobre features_v1.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from nba_predictor.config import FIRST_VAL_IDX, TRAINING_SEASONS
from nba_predictor.models.baselines import ConstantBaseline, EloBaseline


# ---------------------------------------------------------------------------
# Métricas (implementadas sin sklearn — dependencia mínima)
# ---------------------------------------------------------------------------

def _log_loss(y_true: np.ndarray, y_pred: np.ndarray, eps: float = 1e-15) -> float:
    """Log loss binario (métrica primaria del contrato)."""
    p = np.clip(y_pred, eps, 1.0 - eps)
    return float(-np.mean(y_true * np.log(p) + (1.0 - y_true) * np.log(1.0 - p)))


def _brier(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Brier score = MSE entre probabilidades predichas y outcomes binarios."""
    return float(np.mean((y_pred - y_true) ** 2))


def _accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Accuracy: predicción = 1 si p ≥ 0.5, 0 si no."""
    return float(np.mean((y_pred >= 0.5) == y_true))


def _calibration_bins(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    n_bins: int = 10,
) -> list[tuple[float, float, int]]:
    """
    Curva de calibración: (probabilidad_media_predicha, tasa_real, n_partidos) por bin.

    Bins equiespaciados en [0, 1]. Bins vacíos se omiten.
    Para calibración perfecta, probabilidad_media ≈ tasa_real en cada bin.
    """
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    result: list[tuple[float, float, int]] = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (y_pred >= lo) & (y_pred < hi)
        if mask.any():
            result.append((
                float(y_pred[mask].mean()),
                float(y_true[mask].mean()),
                int(mask.sum()),
            ))
    return result


# ---------------------------------------------------------------------------
# Resultado de un fold
# ---------------------------------------------------------------------------

@dataclass
class FoldResult:
    """
    Resultado de un fold del walk-forward.

    Almacena métricas precalculadas y los arrays crudos (y_true, *_probs)
    para poder agregar folds correctamente concatenando predicciones.
    """
    val_season: str
    train_seasons: list[str]
    n_games: int
    # Constante del baseline trivial en este fold
    const_p: float
    # Métricas por baseline
    const_log_loss: float
    const_brier: float
    const_accuracy: float
    elo_log_loss: float
    elo_brier: float
    elo_accuracy: float
    # Datos de calibración del ELO (trivial tiene un solo punto: no aplica)
    elo_calib_bins: list[tuple[float, float, int]]
    # Arrays crudos para agregación (excluidos del repr para no saturar la consola)
    y_true: np.ndarray = field(repr=False)
    const_probs: np.ndarray = field(repr=False)
    elo_probs: np.ndarray = field(repr=False)


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------

def walk_forward_evaluate(
    features: pd.DataFrame,
    all_games: pd.DataFrame,
) -> list[FoldResult]:
    """
    Evalúa ambos baselines con walk-forward por temporadas.

    6 folds: primer fold valida 2020-21 (TRAINING_SEASONS[4]),
    último fold valida 2025-26 (TRAINING_SEASONS[9]).

    Parameters
    ----------
    features  : features_v1 — columnas game_id, season, home_won (mínimo).
    all_games : Todos los partidos de la tabla games (warmup + training),
                con columnas game_id, season, game_date, home_team_id,
                away_team_id, home_won, neutral_site.

    Returns
    -------
    Lista de FoldResult, uno por fold, en orden cronológico de validación.
    """
    # Pre-computar predicciones ELO para todos los game_ids de features_v1
    # en un único pase cronológico. El fold split se hace después sobre el dict.
    elo = EloBaseline()
    all_eval_ids: frozenset[str] = frozenset(features["game_id"])
    elo_preds: dict[str, float] = elo.compute_predictions(all_games, all_eval_ids)

    results: list[FoldResult] = []

    for i in range(FIRST_VAL_IDX, len(TRAINING_SEASONS)):
        train_seasons = TRAINING_SEASONS[:i]
        val_season = TRAINING_SEASONS[i]

        train_df = features[features["season"].isin(train_seasons)]
        val_df = features[features["season"] == val_season].sort_values("game_date")

        if val_df.empty:
            continue

        # ----- Constant baseline -----
        const = ConstantBaseline()
        const.fit(train_df)
        const_probs = const.predict_proba(val_df)

        # ----- ELO baseline -----
        # Usa predicciones pre-computadas; no hay re-fit por fold.
        missing = [gid for gid in val_df["game_id"] if gid not in elo_preds]
        if missing:
            raise RuntimeError(
                f"ELO no tiene predicción para {len(missing)} game_ids del fold "
                f"{val_season}. Primeros: {missing[:5]}"
            )
        elo_probs = np.array([elo_preds[gid] for gid in val_df["game_id"]])

        y_true = val_df["home_won"].to_numpy(dtype=float)

        results.append(FoldResult(
            val_season=val_season,
            train_seasons=list(train_seasons),
            n_games=len(val_df),
            const_p=float(const.p_home_win),
            const_log_loss=_log_loss(y_true, const_probs),
            const_brier=_brier(y_true, const_probs),
            const_accuracy=_accuracy(y_true, const_probs),
            elo_log_loss=_log_loss(y_true, elo_probs),
            elo_brier=_brier(y_true, elo_probs),
            elo_accuracy=_accuracy(y_true, elo_probs),
            elo_calib_bins=_calibration_bins(y_true, elo_probs),
            y_true=y_true,
            const_probs=const_probs,
            elo_probs=elo_probs,
        ))

    return results


def aggregate(folds: list[FoldResult]) -> FoldResult:
    """
    Agrega todos los folds en un FoldResult global.

    Las métricas se calculan sobre la concatenación de todas las predicciones
    (no como media ponderada de métricas por fold) — esto es lo correcto para
    log loss y Brier.
    """
    y_true = np.concatenate([f.y_true for f in folds])
    const_all = np.concatenate([f.const_probs for f in folds])
    elo_all = np.concatenate([f.elo_probs for f in folds])

    # La "constante" agregada es la media ponderada de las constantes por fold
    total_games = sum(f.n_games for f in folds)
    weighted_const_p = sum(f.const_p * f.n_games for f in folds) / total_games

    return FoldResult(
        val_season="TOTAL",
        train_seasons=[],
        n_games=total_games,
        const_p=weighted_const_p,
        const_log_loss=_log_loss(y_true, const_all),
        const_brier=_brier(y_true, const_all),
        const_accuracy=_accuracy(y_true, const_all),
        elo_log_loss=_log_loss(y_true, elo_all),
        elo_brier=_brier(y_true, elo_all),
        elo_accuracy=_accuracy(y_true, elo_all),
        elo_calib_bins=_calibration_bins(y_true, elo_all),
        y_true=y_true,
        const_probs=const_all,
        elo_probs=elo_all,
    )
