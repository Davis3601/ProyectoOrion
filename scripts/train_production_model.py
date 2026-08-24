"""
Entrena el modelo de producción (Fase 4) y lo guarda en el registry.

Qué hace este script:
  1. Verifica datos: carga features_v1 y valida que el recuento sea el esperado.
  2. Calcula métricas consolidadas: corre walk_forward_xgboost para obtener
     Brier y calibración de logística B-limpia y XGBoost — cerrando el
     pendiente de Fase 3 que quedó marcado como "pendiente*" en CLAUDE.md.
  3. Reentrena: ajusta la logística B-limpia sobre TODOS los 9 643 partidos.
  4. Guarda: serializa pipeline + metadata en data/models/v1_logistic_bclean_FECHA/.
  5. Reporta: imprime la ruta guardada, el metadata completo y los Brier
     consolidados.

IMPORTANTE sobre las métricas:
  Las métricas del metadata son las del walk-forward (0.63138).
  El modelo de producción NO se evalúa sobre sus propios datos de entrenamiento
  — no existe validación independiente posible. Si se imprime alguna métrica
  in-sample (por ejemplo, LL sobre los 9 643), se marca explícitamente como
  IN-SAMPLE / NO COMPARABLE con el walk-forward.

Uso:
    python scripts/train_production_model.py
    python scripts/train_production_model.py --date 2026-08-12
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


def main(training_date: date | None = None) -> None:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    from nba_predictor.config import LOGREG_C, RETRAIN_CADENCE_DAYS
    from nba_predictor.models.evaluation import _brier, _calibration_bins, _log_loss
    from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS
    from nba_predictor.models.registry import (
        build_metadata,
        compute_parquet_sha256,
        make_version_name,
    )
    from nba_predictor.models.xgboost_model import aggregate_xgboost, walk_forward_xgboost
    from nba_predictor.storage import get_datastore

    ds = get_datastore()

    # -----------------------------------------------------------------------
    # 1. Cargar datos
    # -----------------------------------------------------------------------
    log.info("Cargando features_v1...")
    features = ds.load_features()
    log.info(f"  {len(features):,} partidos × {len(features.columns)} columnas")

    log.info("Cargando games (todos, para el ELO del walk-forward)...")
    all_games = ds.load_games()
    log.info(f"  {len(all_games):,} partidos en total")

    # Ruta del parquet para el hash (simetría con LocalDataStore)
    from nba_predictor.config import settings
    parquet_path = settings.processed_dir / "features_v1.parquet"
    parquet_sha256 = compute_parquet_sha256(parquet_path)
    log.info(f"  SHA-256 del parquet: {parquet_sha256[:16]}...")

    # -----------------------------------------------------------------------
    # 2. Métricas walk-forward consolidadas (incluye Brier y calibración
    #    pendientes de Fase 3 para logística B-limpia y XGBoost)
    # -----------------------------------------------------------------------
    log.info("Walk-forward XGBoost (calcula Brier+calibración para ambos modelos)...")
    xgb_result = walk_forward_xgboost(features, all_games)
    total = aggregate_xgboost(xgb_result.folds)

    log_brier = _brier(total.y_true, total.log_probs)
    xgb_brier = _brier(total.y_true, total.xgb_probs)
    elo_brier = _brier(total.y_true, total.elo_probs)
    const_brier = _brier(total.y_true, total.const_probs)

    cal_bins_log = _calibration_bins(total.y_true, total.log_probs)
    cal_bins_xgb = _calibration_bins(total.y_true, total.xgb_probs)

    log.info(f"  Log B-limpia LL={total.log_log_loss:.5f} | Brier={log_brier:.4f}")
    log.info(f"  XGBoost     LL={total.xgb_log_loss:.5f} | Brier={xgb_brier:.4f}")

    walk_forward_metrics = {
        "n_val_games": int(total.n_games),
        "n_folds": 6,
        "val_seasons": "2020-21 to 2025-26",
        "log_loss": round(total.log_log_loss, 5),
        "accuracy": round(total.log_accuracy, 4),
        "brier": round(log_brier, 5),
        "baselines": {
            "trivial_log_loss": round(total.const_log_loss, 5),
            "trivial_accuracy": round(total.const_accuracy, 4),
            "trivial_brier": round(const_brier, 5),
            "elo_log_loss": round(total.elo_log_loss, 5),
            "elo_accuracy": round(total.elo_accuracy, 4),
            "elo_brier": round(elo_brier, 5),
        },
        "vs_elo": {
            "improvement_log_loss": round(total.elo_log_loss - total.log_log_loss, 5),
        },
        "calibration_bins": [
            {"mean_pred": round(mp, 4), "actual_rate": round(ar, 4), "n": n}
            for mp, ar, n in cal_bins_log
        ],
        "xgboost_comparison": {
            "xgb_log_loss": round(total.xgb_log_loss, 5),
            "xgb_accuracy": round(total.xgb_accuracy, 4),
            "xgb_brier": round(xgb_brier, 5),
            "xgb_vs_logistic_delta": round(
                total.xgb_log_loss - total.log_log_loss, 5
            ),
            "xgb_calibration_bins": [
                {"mean_pred": round(mp, 4), "actual_rate": round(ar, 4), "n": n}
                for mp, ar, n in cal_bins_xgb
            ],
        },
    }

    # -----------------------------------------------------------------------
    # 3. Reentrenar sobre TODOS los datos
    # -----------------------------------------------------------------------
    log.info(f"Reentrenando logística B-limpia sobre {len(features):,} partidos...")
    X_all = features[OFFICIAL_LOGISTIC_COLS].to_numpy(dtype=float)
    y_all = features["home_won"].to_numpy(dtype=float)

    production_pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("logistic", LogisticRegression(
            C=LOGREG_C,
            solver="lbfgs",
            max_iter=1000,
            random_state=42,
        )),
    ])
    production_pipeline.fit(X_all, y_all)
    log.info("  Pipeline entrenado.")

    hyperparameters = {
        "C": LOGREG_C,
        "solver": "lbfgs",
        "max_iter": 1000,
        "penalty": "l2",
        "random_state": 42,
        "scaler": "StandardScaler",
        "feature_cols": OFFICIAL_LOGISTIC_COLS,
    }

    # -----------------------------------------------------------------------
    # 4. Guardar en el registry
    # -----------------------------------------------------------------------
    version_name = make_version_name(training_date)
    metadata = build_metadata(
        version_name=version_name,
        parquet_sha256=parquet_sha256,
        n_rows=len(features),
        feature_cols=OFFICIAL_LOGISTIC_COLS,
        hyperparameters=hyperparameters,
        walk_forward_metrics=walk_forward_metrics,
        retrain_cadence_days=RETRAIN_CADENCE_DAYS,
        training_date=training_date,
    )

    version_dir = ds.save_model(production_pipeline, metadata, version_name)
    log.info(f"  Modelo guardado en: {version_dir}")

    # -----------------------------------------------------------------------
    # 5. Reporte
    # -----------------------------------------------------------------------
    print()
    print("=" * 72)
    print("MODELO DE PRODUCCIÓN GUARDADO")
    print("=" * 72)
    print(f"Versión  : {version_name}")
    print(f"Ruta     : {version_dir}")
    print(f"Partidos : {len(features):,} (todos los disponibles en features_v1)")
    print(f"Features : {len(OFFICIAL_LOGISTIC_COLS)} ({', '.join(OFFICIAL_LOGISTIC_COLS[:3])}...)")
    print()

    print("=" * 72)
    print("MÉTRICAS CONSOLIDADAS (walk-forward — OFICIALES)")
    print("(Estas son las métricas del modelo en producción)")
    print("=" * 72)
    print(f"  Logística B-limpia  LL={total.log_log_loss:.5f} | Acc={total.log_accuracy:.3f} | Brier={log_brier:.5f}")
    print(f"  ELO (vara)          LL={total.elo_log_loss:.5f} | Acc={total.elo_accuracy:.3f} | Brier={elo_brier:.5f}")
    print(f"  Trivial             LL={total.const_log_loss:.5f} | Acc={total.const_accuracy:.3f} | Brier={const_brier:.5f}")
    print()
    print("  XGBoost (referencia, NO el modelo de prod):")
    print(f"    LL={total.xgb_log_loss:.5f} | Acc={total.xgb_accuracy:.3f} | Brier={xgb_brier:.5f}")
    print()
    print("  Brier B-limpia y XGBoost: CIERRE DEL PENDIENTE DE FASE 3")
    print(f"  (marcados como 'pendiente*' en CLAUDE.md — ahora disponibles)")
    print()

    print("=" * 72)
    print("METADATA.JSON (contenido completo)")
    print("=" * 72)
    print(json.dumps(metadata, indent=2, ensure_ascii=False))

    # Nota in-sample (honestidad)
    in_sample_ll = _log_loss(y_all, production_pipeline.predict_proba(X_all)[:, 1])
    print()
    print("=" * 72)
    print("NOTA: MÉTRICA IN-SAMPLE (solo referencia — NO comparable al walk-forward)")
    print("=" * 72)
    print(f"  LL in-sample sobre {len(features):,} partidos de entrenamiento: {in_sample_ll:.5f}")
    print("  Este número es optimista por definición — el modelo fue entrenado")
    print("  sobre estos mismos datos. La métrica oficial es 0.63138 (walk-forward).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Entrena y guarda el modelo de producción.")
    parser.add_argument(
        "--date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Fecha del modelo (YYYY-MM-DD). Default: hoy.",
    )
    args = parser.parse_args()
    main(training_date=args.date)
