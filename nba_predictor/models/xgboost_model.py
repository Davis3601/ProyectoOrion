"""
XGBoost — Fase 3.

Responde la pregunta: ¿hay no-linealidad o interacciones que la logística no ve?

Por qué NO estandarizar (a diferencia de la logística):
  Los árboles de decisión particionan el espacio de features con umbrales binarios.
  La escala de las features no afecta QUÉ umbral es óptimo — solo el valor absoluto
  del umbral cambia. Multiplicar una feature por una constante no cambia la
  estructura del árbol ni su capacidad predictiva. En contraste, la logística asigna
  un coeficiente global por feature: features de distinta escala dominan el gradiente
  si no se normalizan. XGBoost no tiene este problema.

Early stopping SIN leakage (cuarta aparición del principio):
  El eval set del early stopping proviene de la ÚLTIMA temporada del train del fold.
  Esa temporada siempre es ANTERIOR a la temporada de validación del fold → cero
  leakage de selección. El set de validación del fold NUNCA entra en el entrenamiento
  ni en el early stopping.

  Ejemplo (fold que valida 2023-24):
    train_seasons = [2016-17..2022-23]  → early_stop = 2022-23
    xgb_train     = [2016-17..2021-22]  → datos que el modelo ve en el fit
    val (fold)    = 2023-24             → solo para evaluar, nunca para entrenar

Expectativa calibrada (ver CLAUDE.md):
  En tabular deportivo con features-diferencia bien diseñadas, la mejora típica
  sobre la logística es +0.002-0.008 LL. Una mejora >0.02 es bandera de auditoría.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from nba_predictor.config import (
    FIRST_VAL_IDX,
    LOGREG_C,
    TRAINING_SEASONS,
    XGB_COLSAMPLE,
    XGB_EARLY_STOP_ROUNDS,
    XGB_LEARNING_RATE,
    XGB_MAX_DEPTH,
    XGB_N_ESTIMATORS,
    XGB_SUBSAMPLE,
)
from nba_predictor.models.baselines import ConstantBaseline, EloBaseline
from nba_predictor.models.evaluation import _accuracy, _brier, _calibration_bins, _log_loss
from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS, _fit_fold


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _make_early_stop_split(
    train_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Divide el train del fold en datos de entrenamiento XGBoost y eval set.

    La ÚLTIMA temporada del train se reserva como eval set para el early
    stopping. Las temporadas restantes son el entrenamiento efectivo del XGBoost.

    Anti-leakage: la última temporada del train es siempre ANTERIOR a la
    temporada de validación del fold (que está en TRAINING_SEASONS[i], mientras
    que la última del train está en TRAINING_SEASONS[i-1]).

    Expuesta para testing directo del criterio de split.
    """
    seasons_sorted = sorted(train_df["season"].unique())
    early_stop_season = seasons_sorted[-1]

    xgb_train_df = train_df[train_df["season"] != early_stop_season].copy()
    xgb_eval_df = train_df[train_df["season"] == early_stop_season].copy()
    return xgb_train_df, xgb_eval_df


def _fit_xgb_fold(
    X_train: pd.DataFrame,
    y_train: np.ndarray,
    X_eval: pd.DataFrame,
    y_eval: np.ndarray,
) -> XGBClassifier:
    """
    Ajusta XGBClassifier con early stopping sobre el eval set.

    Recibe DataFrames (no arrays) para que XGBoost conozca los nombres de las
    features y los importe correctamente con get_booster().get_score().

    X_eval/y_eval: ÚLTIMA TEMPORADA DEL TRAIN del fold.
    NUNCA pasar la temporada de validación del fold aquí.
    """
    model = XGBClassifier(
        max_depth=XGB_MAX_DEPTH,
        learning_rate=XGB_LEARNING_RATE,
        n_estimators=XGB_N_ESTIMATORS,
        subsample=XGB_SUBSAMPLE,
        colsample_bytree=XGB_COLSAMPLE,
        objective="binary:logistic",
        eval_metric="logloss",
        early_stopping_rounds=XGB_EARLY_STOP_ROUNDS,
        random_state=42,
        n_jobs=1,  # n_jobs=1 garantiza reproducibilidad exacta con la semilla fija
        verbosity=0,
    )
    model.fit(
        X_train,
        y_train,
        eval_set=[(X_eval, y_eval)],
        verbose=False,
    )
    return model


def _extract_importances(
    model: XGBClassifier,
    feature_cols: list[str],
) -> list[tuple[str, float]]:
    """
    Importancias por gain (ganancia media por split), ordenadas desc.

    Gain mide cuánto reduce el error cada feature en promedio cada vez que
    se usa como criterio de split → más informativo que 'weight' (frecuencia).
    Features nunca usadas como split reciben gain=0.
    """
    score_dict = model.get_booster().get_score(importance_type="gain")
    pairs = [(col, score_dict.get(col, 0.0)) for col in feature_cols]
    return sorted(pairs, key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# Dataclasses de resultado
# ---------------------------------------------------------------------------

@dataclass
class XGBFoldResult:
    """Resultado de un fold: trivial + ELO + logística oficial + XGBoost."""

    val_season: str
    train_seasons: list[str]
    n_games: int
    n_trees: int          # árboles usados tras el early stopping
    # Log loss (↓ mejor)
    const_log_loss: float
    elo_log_loss: float
    log_log_loss: float   # logística oficial (B-limpia)
    xgb_log_loss: float
    # Accuracy con umbral 0.5 (↑ mejor)
    const_accuracy: float
    elo_accuracy: float
    log_accuracy: float
    xgb_accuracy: float
    # Arrays crudos para agregación
    y_true: np.ndarray = field(repr=False)
    const_probs: np.ndarray = field(repr=False)
    elo_probs: np.ndarray = field(repr=False)
    log_probs: np.ndarray = field(repr=False)
    xgb_probs: np.ndarray = field(repr=False)


@dataclass
class XGBResult:
    """Resultado completo del walk-forward XGBoost."""

    folds: list[XGBFoldResult]
    # Importancias por gain del fold final (mayor conjunto de entrenamiento)
    final_importances: list[tuple[str, float]]


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------

def walk_forward_xgboost(
    features: pd.DataFrame,
    all_games: pd.DataFrame,
    feature_cols: list[str] | None = None,
    C: float = LOGREG_C,
) -> XGBResult:
    """
    Walk-forward idéntico al de la logística + XGBoost.

    Calcula los 4 modelos por fold (trivial, ELO, logística oficial, XGBoost)
    para comparabilidad exacta. Mismo esquema de 6 folds.

    La logística usa TODOS los datos de train de cada fold (StandardScaler + LR).
    El XGBoost usa los datos de train MENOS la última temporada (que va al
    eval set del early stopping).

    Parameters
    ----------
    features     : features_v1 con FEATURES_V1_COLS.
    all_games    : Todos los partidos para pre-calcular el ELO.
    feature_cols : Columnas de features a usar. Default: OFFICIAL_LOGISTIC_COLS.
    C            : Regularización L2 de la logística. Default: LOGREG_C.
    """
    if feature_cols is None:
        feature_cols = OFFICIAL_LOGISTIC_COLS

    # Pre-calcular predicciones ELO en un único pase cronológico
    elo = EloBaseline()
    all_eval_ids: frozenset[str] = frozenset(features["game_id"])
    elo_preds: dict[str, float] = elo.compute_predictions(all_games, all_eval_ids)

    results: list[XGBFoldResult] = []
    final_importances: list[tuple[str, float]] = []

    for i in range(FIRST_VAL_IDX, len(TRAINING_SEASONS)):
        train_seasons = TRAINING_SEASONS[:i]
        val_season = TRAINING_SEASONS[i]

        train_df = features[features["season"].isin(train_seasons)]
        val_df = features[features["season"] == val_season].sort_values("game_date")

        if val_df.empty:
            continue

        y_train_full = train_df["home_won"].to_numpy(dtype=float)
        y_true = val_df["home_won"].to_numpy(dtype=float)

        # --- Baseline trivial ---
        const = ConstantBaseline()
        const.fit(train_df)
        const_probs = const.predict_proba(val_df)

        # --- ELO ---
        missing = [gid for gid in val_df["game_id"] if gid not in elo_preds]
        if missing:
            raise RuntimeError(
                f"ELO sin predicción para {len(missing)} game_ids en fold {val_season}: "
                f"{missing[:5]}"
            )
        elo_probs = np.array([elo_preds[gid] for gid in val_df["game_id"]])

        # --- Logística oficial (usa TODOS los datos de train, con scaler) ---
        X_train_log = train_df[feature_cols].to_numpy(dtype=float)
        X_val_log = val_df[feature_cols].to_numpy(dtype=float)
        scaler, lr = _fit_fold(X_train_log, y_train_full, C=C)
        log_probs = lr.predict_proba(scaler.transform(X_val_log))[:, 1]

        # --- XGBoost (usa train minus última temporada para early stopping) ---
        xgb_train_df, xgb_eval_df = _make_early_stop_split(train_df)

        X_xgb_train = xgb_train_df[feature_cols]   # DataFrame → feature names preservados
        y_xgb_train = xgb_train_df["home_won"].to_numpy(dtype=float)
        X_xgb_eval = xgb_eval_df[feature_cols]
        y_xgb_eval = xgb_eval_df["home_won"].to_numpy(dtype=float)
        X_val_xgb = val_df[feature_cols]

        xgb_model = _fit_xgb_fold(X_xgb_train, y_xgb_train, X_xgb_eval, y_xgb_eval)
        xgb_probs = xgb_model.predict_proba(X_val_xgb)[:, 1]
        n_trees = xgb_model.best_iteration + 1

        final_importances = _extract_importances(xgb_model, feature_cols)

        results.append(XGBFoldResult(
            val_season=val_season,
            train_seasons=list(train_seasons),
            n_games=len(val_df),
            n_trees=n_trees,
            const_log_loss=_log_loss(y_true, const_probs),
            elo_log_loss=_log_loss(y_true, elo_probs),
            log_log_loss=_log_loss(y_true, log_probs),
            xgb_log_loss=_log_loss(y_true, xgb_probs),
            const_accuracy=_accuracy(y_true, const_probs),
            elo_accuracy=_accuracy(y_true, elo_probs),
            log_accuracy=_accuracy(y_true, log_probs),
            xgb_accuracy=_accuracy(y_true, xgb_probs),
            y_true=y_true,
            const_probs=const_probs,
            elo_probs=elo_probs,
            log_probs=log_probs,
            xgb_probs=xgb_probs,
        ))

    return XGBResult(folds=results, final_importances=final_importances)


def aggregate_xgboost(folds: list[XGBFoldResult]) -> XGBFoldResult:
    """
    Agrega todos los folds en un XGBFoldResult global.

    Métricas sobre la concatenación de predicciones (no media ponderada por fold).
    n_trees se reporta como la media redondeada — solo informativo.
    """
    y_true = np.concatenate([f.y_true for f in folds])
    const_all = np.concatenate([f.const_probs for f in folds])
    elo_all = np.concatenate([f.elo_probs for f in folds])
    log_all = np.concatenate([f.log_probs for f in folds])
    xgb_all = np.concatenate([f.xgb_probs for f in folds])

    return XGBFoldResult(
        val_season="TOTAL",
        train_seasons=[],
        n_games=sum(f.n_games for f in folds),
        n_trees=round(sum(f.n_trees for f in folds) / len(folds)),  # media
        const_log_loss=_log_loss(y_true, const_all),
        elo_log_loss=_log_loss(y_true, elo_all),
        log_log_loss=_log_loss(y_true, log_all),
        xgb_log_loss=_log_loss(y_true, xgb_all),
        const_accuracy=_accuracy(y_true, const_all),
        elo_accuracy=_accuracy(y_true, elo_all),
        log_accuracy=_accuracy(y_true, log_all),
        xgb_accuracy=_accuracy(y_true, xgb_all),
        y_true=y_true,
        const_probs=const_all,
        elo_probs=elo_all,
        log_probs=log_all,
        xgb_probs=xgb_all,
    )
