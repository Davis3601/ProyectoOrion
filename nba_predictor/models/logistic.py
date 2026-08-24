"""
Regresión logística — Fase 3.

Duelo de variantes (ver "Fase 3 — Decisión de la regresión logística" en CLAUDE.md):
  Variante A: todas las features MENOS los 3 ratings crudos (off/def/net_rating_diff).
              Usa los ratings ajustados por oponente (_adj). Doce features.
  Variante B: todas las features MENOS los 3 ratings ajustados (_adj).
              Usa los ratings crudos. Doce features.
La variante ganadora es la de menor log loss walk-forward agregado. El resultado
informa si el ajuste por oponente (Grupo 3) paga su costo de complejidad.

Anti-leakage del scaler (tercera aparición del principio del proyecto):
  StandardScaler se ajusta SOLO con el train de cada fold. Las medias y
  desviaciones estándar del scaler nunca ven datos de validación.
  _fit_fold() solo recibe X_train — es imposible que vea X_val.

Regularización:
  L2 con C=LOGREG_C (config.py, default 1.0). Afinar C con búsqueda anidada
  es iteración posterior; la señal "¿le gano al ELO?" no depende de eso.

Conexión conceptual (ver CLAUDE.md):
  La logística es la extensión natural del ELO: mismo modelo probabilístico
  (sigmoide), pero con 12 features libres en lugar de un escalar con coeficiente
  fijo. El intercepto aprenderá la ventaja de local real del período de train
  (corrigiendo el sesgo de +8-10 pp del ELO).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from nba_predictor.config import FIRST_VAL_IDX, LOGREG_C, TRAINING_SEASONS
from nba_predictor.features.assemble import FEATURES_V1_COLS
from nba_predictor.models.baselines import ConstantBaseline, EloBaseline
from nba_predictor.models.evaluation import (
    _accuracy,
    _brier,
    _calibration_bins,
    _log_loss,
)

# ---------------------------------------------------------------------------
# Definición de variantes — una sola fuente de verdad
# ---------------------------------------------------------------------------

_ID_AND_TARGET: frozenset[str] = frozenset({"game_id", "season", "game_date", "home_won"})
_ALL_FEATURE_COLS: list[str] = [c for c in FEATURES_V1_COLS if c not in _ID_AND_TARGET]

# Grupo 2 — ratings crudos (excluidos en A, incluidos en B)
_RAW_RATING_COLS: frozenset[str] = frozenset({
    "off_rating_diff", "def_rating_diff", "net_rating_diff",
})

# Grupo 3 — ratings ajustados por oponente (incluidos en A, excluidos en B)
_ADJ_RATING_COLS: frozenset[str] = frozenset({
    "off_rating_adj_diff", "def_rating_adj_diff", "net_rating_adj_diff",
})

# Públicos para tests y scripts
VARIANT_A_COLS: list[str] = [c for c in _ALL_FEATURE_COLS if c not in _RAW_RATING_COLS]
VARIANT_B_COLS: list[str] = [c for c in _ALL_FEATURE_COLS if c not in _ADJ_RATING_COLS]

# Variante B-limpia: B sin net_rating_diff.
# net_rating_diff = off_rating_diff − def_rating_diff → redundancia lineal exacta.
# Conservar off y def es suficiente; eliminar net hace los coeficientes legibles.
# (Experimento — ver "Fase 3 — Decisión de limpieza" en CLAUDE.md)
VARIANT_B_CLEAN_COLS: list[str] = [c for c in VARIANT_B_COLS if c != "net_rating_diff"]

# Logística oficial — CONFIRMADA por el experimento B-limpia (train_logistic.py).
# Degradación B-limpia vs B: -0.00000 LL (< umbral 0.001 → B-limpia es oficial).
# Coeficientes recuperados: off_rating_diff +0.59 / def_rating_diff -0.36 (legibles).
OFFICIAL_LOGISTIC_COLS: list[str] = VARIANT_B_CLEAN_COLS


# ---------------------------------------------------------------------------
# Pipeline por fold
# ---------------------------------------------------------------------------

def _fit_fold(
    X_train: np.ndarray,
    y_train: np.ndarray,
    C: float = LOGREG_C,
) -> tuple[StandardScaler, LogisticRegression]:
    """
    Ajusta StandardScaler → LogisticRegression L2 sobre datos de train.

    Expuesta como función pública para testing de anti-leakage.
    Solo recibe X_train: el scaler NUNCA puede ver datos de validación.

    Returns
    -------
    (scaler, lr): el scaler ya ajustado; lr ya entrenado con X_train escalado.
    """
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_train)
    lr = LogisticRegression(
        C=C,
        solver="lbfgs",
        max_iter=1000,
        random_state=42,
    )
    lr.fit(X_scaled, y_train)
    return scaler, lr


def _extract_coefs(
    lr: LogisticRegression,
    feature_cols: list[str],
) -> list[tuple[str, float]]:
    """
    Coeficientes estandarizados ordenados por |magnitud| descendente.

    Con features estandarizadas, los coeficientes son directamente comparables:
    el más grande en valor absoluto es la feature con mayor influencia marginal.
    """
    coefs = lr.coef_[0]  # shape (n_features,) para clasificación binaria
    pairs = list(zip(feature_cols, coefs.tolist()))
    return sorted(pairs, key=lambda x: abs(x[1]), reverse=True)


# ---------------------------------------------------------------------------
# Dataclasses de resultado
# ---------------------------------------------------------------------------

@dataclass
class LogisticFoldResult:
    """Resultado de un fold: métricas de trivial + ELO + logística A + logística B."""

    val_season: str
    train_seasons: list[str]
    n_games: int
    const_p: float
    # Log loss (métrica primaria; ↓ mejor)
    const_log_loss: float
    elo_log_loss: float
    log_a_log_loss: float
    log_b_log_loss: float
    # Brier score (↓ mejor)
    const_brier: float
    elo_brier: float
    log_a_brier: float
    log_b_brier: float
    # Accuracy con umbral 0.5 (↑ mejor)
    const_accuracy: float
    elo_accuracy: float
    log_a_accuracy: float
    log_b_accuracy: float
    # Arrays crudos para agregación — excluidos del repr para no saturar consola
    y_true: np.ndarray = field(repr=False)
    const_probs: np.ndarray = field(repr=False)
    elo_probs: np.ndarray = field(repr=False)
    log_a_probs: np.ndarray = field(repr=False)
    log_b_probs: np.ndarray = field(repr=False)


@dataclass
class LogisticResult:
    """Resultado completo del walk-forward logístico."""

    folds: list[LogisticFoldResult]
    # Coeficientes del fold final (mayor conjunto de entrenamiento → más representativos)
    # (feature_name, coef) ordenados por |coef| descendente
    final_coefs_a: list[tuple[str, float]]
    final_coefs_b: list[tuple[str, float]]


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------

def walk_forward_logistic(
    features: pd.DataFrame,
    all_games: pd.DataFrame,
    C: float = LOGREG_C,
) -> LogisticResult:
    """
    Walk-forward idéntico al de los baselines, con la logística añadida.

    Mismo esquema de folds que walk_forward_evaluate (evaluation.py):
      Fold 0: train 2016-17..2019-20 → val 2020-21
      ...
      Fold 5: train 2016-17..2024-25 → val 2025-26

    Anti-leakage garantizado:
      - ELO: predicción registrada antes de actualizar (mismo pase único).
      - Trivial: fit solo con train_df.
      - Scaler: _fit_fold recibe solo X_train.

    Parameters
    ----------
    features  : features_v1 con columnas FEATURES_V1_COLS.
    all_games : Todos los partidos (warmup + training) para el ELO.
    C         : Parámetro de regularización L2. Default: LOGREG_C.

    Returns
    -------
    LogisticResult con folds + coeficientes del fold final.
    """
    # Pre-calcular predicciones ELO en un único pase cronológico
    elo = EloBaseline()
    all_eval_ids: frozenset[str] = frozenset(features["game_id"])
    elo_preds: dict[str, float] = elo.compute_predictions(all_games, all_eval_ids)

    results: list[LogisticFoldResult] = []
    final_coefs_a: list[tuple[str, float]] = []
    final_coefs_b: list[tuple[str, float]] = []

    for i in range(FIRST_VAL_IDX, len(TRAINING_SEASONS)):
        train_seasons = TRAINING_SEASONS[:i]
        val_season = TRAINING_SEASONS[i]

        train_df = features[features["season"].isin(train_seasons)]
        val_df = features[features["season"] == val_season].sort_values("game_date")

        if val_df.empty:
            continue

        y_train = train_df["home_won"].to_numpy(dtype=float)
        y_true = val_df["home_won"].to_numpy(dtype=float)

        # --- Baseline trivial ---
        const = ConstantBaseline()
        const.fit(train_df)
        const_probs = const.predict_proba(val_df)

        # --- ELO (predicciones pre-computadas) ---
        missing = [gid for gid in val_df["game_id"] if gid not in elo_preds]
        if missing:
            raise RuntimeError(
                f"ELO sin predicción para {len(missing)} game_ids en fold {val_season}: "
                f"{missing[:5]}"
            )
        elo_probs = np.array([elo_preds[gid] for gid in val_df["game_id"]])

        # --- Logística A: usa adj, excluye raw ratings ---
        X_train_a = train_df[VARIANT_A_COLS].to_numpy(dtype=float)
        X_val_a = val_df[VARIANT_A_COLS].to_numpy(dtype=float)
        scaler_a, lr_a = _fit_fold(X_train_a, y_train, C=C)
        log_a_probs = lr_a.predict_proba(scaler_a.transform(X_val_a))[:, 1]

        # --- Logística B: usa raw, excluye adj ---
        X_train_b = train_df[VARIANT_B_COLS].to_numpy(dtype=float)
        X_val_b = val_df[VARIANT_B_COLS].to_numpy(dtype=float)
        scaler_b, lr_b = _fit_fold(X_train_b, y_train, C=C)
        log_b_probs = lr_b.predict_proba(scaler_b.transform(X_val_b))[:, 1]

        # Guardar coeficientes del último fold procesado
        final_coefs_a = _extract_coefs(lr_a, VARIANT_A_COLS)
        final_coefs_b = _extract_coefs(lr_b, VARIANT_B_COLS)

        results.append(LogisticFoldResult(
            val_season=val_season,
            train_seasons=list(train_seasons),
            n_games=len(val_df),
            const_p=float(const.p_home_win),
            const_log_loss=_log_loss(y_true, const_probs),
            elo_log_loss=_log_loss(y_true, elo_probs),
            log_a_log_loss=_log_loss(y_true, log_a_probs),
            log_b_log_loss=_log_loss(y_true, log_b_probs),
            const_brier=_brier(y_true, const_probs),
            elo_brier=_brier(y_true, elo_probs),
            log_a_brier=_brier(y_true, log_a_probs),
            log_b_brier=_brier(y_true, log_b_probs),
            const_accuracy=_accuracy(y_true, const_probs),
            elo_accuracy=_accuracy(y_true, elo_probs),
            log_a_accuracy=_accuracy(y_true, log_a_probs),
            log_b_accuracy=_accuracy(y_true, log_b_probs),
            y_true=y_true,
            const_probs=const_probs,
            elo_probs=elo_probs,
            log_a_probs=log_a_probs,
            log_b_probs=log_b_probs,
        ))

    return LogisticResult(
        folds=results,
        final_coefs_a=final_coefs_a,
        final_coefs_b=final_coefs_b,
    )


def run_b_clean_experiment(
    features: pd.DataFrame,
    all_games: pd.DataFrame,
    C: float = LOGREG_C,
) -> tuple[float, list[tuple[str, float]]]:
    """
    Experimento B-limpia: walk-forward con VARIANT_B_CLEAN_COLS (sin net_rating_diff).

    net_rating_diff = off_rating_diff − def_rating_diff → redundancia lineal exacta.
    Eliminarla hace los coeficientes legibles sin perder información real.

    Criterio de decisión (ver CLAUDE.md "Fase 3 — Decisión de limpieza"):
      Si log_loss_agregado(B-limpia) − log_loss_agregado(B) < 0.001 → B-limpia oficial.
      Si se degrada ≥ 0.001 → mantener B y documentar por qué.

    Returns
    -------
    (log_loss_agregado, coefs_fold_final):
        log_loss_agregado : float — LL sobre todos los folds concatenados.
        coefs_fold_final  : [(feature, coef)] ordenados por |coef| desc.
    """
    elo = EloBaseline()
    all_eval_ids: frozenset[str] = frozenset(features["game_id"])
    elo_preds: dict[str, float] = elo.compute_predictions(all_games, all_eval_ids)

    all_y_true: list[np.ndarray] = []
    all_probs: list[np.ndarray] = []
    final_coefs: list[tuple[str, float]] = []

    for i in range(FIRST_VAL_IDX, len(TRAINING_SEASONS)):
        train_seasons = TRAINING_SEASONS[:i]
        val_season = TRAINING_SEASONS[i]

        train_df = features[features["season"].isin(train_seasons)]
        val_df = features[features["season"] == val_season].sort_values("game_date")

        if val_df.empty:
            continue

        y_train = train_df["home_won"].to_numpy(dtype=float)
        y_true = val_df["home_won"].to_numpy(dtype=float)

        X_train = train_df[VARIANT_B_CLEAN_COLS].to_numpy(dtype=float)
        X_val = val_df[VARIANT_B_CLEAN_COLS].to_numpy(dtype=float)
        scaler, lr = _fit_fold(X_train, y_train, C=C)
        probs = lr.predict_proba(scaler.transform(X_val))[:, 1]

        all_y_true.append(y_true)
        all_probs.append(probs)
        final_coefs = _extract_coefs(lr, VARIANT_B_CLEAN_COLS)

    y_all = np.concatenate(all_y_true)
    p_all = np.concatenate(all_probs)
    return _log_loss(y_all, p_all), final_coefs


def aggregate_logistic(folds: list[LogisticFoldResult]) -> LogisticFoldResult:
    """
    Agrega todos los folds en un LogisticFoldResult global.

    Métricas calculadas sobre la concatenación de predicciones (no como media
    ponderada de métricas por fold) — igual que aggregate() en evaluation.py.
    """
    y_true = np.concatenate([f.y_true for f in folds])
    const_all = np.concatenate([f.const_probs for f in folds])
    elo_all = np.concatenate([f.elo_probs for f in folds])
    log_a_all = np.concatenate([f.log_a_probs for f in folds])
    log_b_all = np.concatenate([f.log_b_probs for f in folds])

    total_games = sum(f.n_games for f in folds)
    weighted_const_p = sum(f.const_p * f.n_games for f in folds) / total_games

    return LogisticFoldResult(
        val_season="TOTAL",
        train_seasons=[],
        n_games=total_games,
        const_p=weighted_const_p,
        const_log_loss=_log_loss(y_true, const_all),
        elo_log_loss=_log_loss(y_true, elo_all),
        log_a_log_loss=_log_loss(y_true, log_a_all),
        log_b_log_loss=_log_loss(y_true, log_b_all),
        const_brier=_brier(y_true, const_all),
        elo_brier=_brier(y_true, elo_all),
        log_a_brier=_brier(y_true, log_a_all),
        log_b_brier=_brier(y_true, log_b_all),
        const_accuracy=_accuracy(y_true, const_all),
        elo_accuracy=_accuracy(y_true, elo_all),
        log_a_accuracy=_accuracy(y_true, log_a_all),
        log_b_accuracy=_accuracy(y_true, log_b_all),
        y_true=y_true,
        const_probs=const_all,
        elo_probs=elo_all,
        log_a_probs=log_a_all,
        log_b_probs=log_b_all,
    )
