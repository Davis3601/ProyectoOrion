"""
Entrena y evalúa XGBoost (Fase 3) con walk-forward.

Tabla comparada: trivial / ELO / logística oficial (B-limpia) / XGBoost.
Reporta además:
  - Número de árboles por fold (donde paró el early stopping)
  - Calibración del XGBoost vs logística vs ELO
  - Importancias por gain (fold final)
  - Veredicto honesto vs ELO y vs logística

EXPECTATIVA DOCUMENTADA (ver CLAUDE.md):
  La mejora típica sobre logística en tabular deportivo es +0.002-0.008 LL.
  Si la mejora es >0.02, PARAR y reportar como bandera de auditoría.

Uso:
    python scripts/train_xgboost.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

AUDIT_THRESHOLD = 0.02  # mejora sobre logística que dispara auditoría de leakage


def _fmt_row(
    label: str, n: int, trees: int,
    cll: float, ell: float, lll: float, xll: float,
    ca: float, ea: float, la: float, xa: float,
) -> str:
    trees_str = f"{trees:4d}" if trees > 0 else "   —"
    return (
        f"{label:<12} {n:>5}  {trees_str}  "
        f"{cll:>7.5f}  {ell:>7.5f}  {lll:>7.5f}  {xll:>7.5f}  "
        f"{ca:>6.3f}  {ea:>6.3f}  {la:>6.3f}  {xa:>6.3f}"
    )


def main() -> None:
    from nba_predictor.models.xgboost_model import (
        XGBResult,
        aggregate_xgboost,
        walk_forward_xgboost,
    )
    from nba_predictor.models.evaluation import _calibration_bins
    from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS
    from nba_predictor.config import XGB_N_ESTIMATORS
    from nba_predictor.storage import get_datastore

    ds = get_datastore()

    log.info("Cargando features_v1...")
    features = ds.load_features()
    log.info(f"  {len(features):,} partidos")

    log.info("Cargando games (todos, para ELO)...")
    all_games = ds.load_games()
    log.info(f"  {len(all_games):,} partidos")

    log.info(f"Walk-forward XGBoost (6 folds, {len(OFFICIAL_LOGISTIC_COLS)} features)...")
    result: XGBResult = walk_forward_xgboost(features, all_games)
    total = aggregate_xgboost(result.folds)

    # -----------------------------------------------------------------------
    print()
    print("=" * 96)
    print("RESULTADOS — XGBOOST vs BASELINES (Fase 3)")
    print("=" * 96)
    hdr = (
        f"{'Fold':<12} {'N':>5}  {'Árb.':>4}  "
        f"{'Cte-LL':>7}  {'ELO-LL':>7}  {'Log-LL':>7}  {'XGB-LL':>7}  "
        f"{'Cte-Ac':>6}  {'ELO-Ac':>6}  {'Log-Ac':>6}  {'XGB-Ac':>6}"
    )
    print(hdr)
    print("-" * 96)

    for f in result.folds:
        print(_fmt_row(
            f.val_season, f.n_games, f.n_trees,
            f.const_log_loss, f.elo_log_loss, f.log_log_loss, f.xgb_log_loss,
            f.const_accuracy, f.elo_accuracy, f.log_accuracy, f.xgb_accuracy,
        ))

    print("-" * 96)
    print(_fmt_row(
        "TOTAL", total.n_games, total.n_trees,
        total.const_log_loss, total.elo_log_loss, total.log_log_loss, total.xgb_log_loss,
        total.const_accuracy, total.elo_accuracy, total.log_accuracy, total.xgb_accuracy,
    ))

    print()
    print("Leyenda: Árb. = árboles usados (early stopping) | Log = logística B-limpia")
    print("         LL = log loss (↓ mejor) | Ac = accuracy (↑ mejor)")

    # -----------------------------------------------------------------------
    # Veredictos
    # -----------------------------------------------------------------------
    print()
    print("=" * 72)
    print("VEREDICTOS")
    print("=" * 72)

    xgb_ll = total.xgb_log_loss
    elo_ll = total.elo_log_loss
    log_ll = total.log_log_loss

    mejora_vs_elo = elo_ll - xgb_ll
    mejora_vs_log = log_ll - xgb_ll

    # vs ELO
    if xgb_ll < elo_ll:
        print(f"vs ELO    : BATE — XGB={xgb_ll:.5f} < ELO={elo_ll:.5f} (delta={mejora_vs_elo:+.5f})")
    else:
        print(f"vs ELO    : NO bate — XGB={xgb_ll:.5f} ≥ ELO={elo_ll:.5f} (delta={mejora_vs_elo:+.5f})")

    # vs logística
    if xgb_ll < log_ll:
        print(f"vs LogReg : MEJORA — XGB={xgb_ll:.5f} < Log={log_ll:.5f} (delta={mejora_vs_log:+.5f})")
    else:
        print(f"vs LogReg : NO mejora — XGB={xgb_ll:.5f} ≥ Log={log_ll:.5f} (delta={mejora_vs_log:+.5f})")

    # Auditoría
    if mejora_vs_log > AUDIT_THRESHOLD:
        print()
        print(f"*** BANDERA DE AUDITORÍA: mejora vs logística = {mejora_vs_log:.5f} > {AUDIT_THRESHOLD}")
        print("    Investigar leakage antes de celebrar el resultado.")
    elif mejora_vs_log > 0:
        print(f"→ Mejora en rango esperado ({mejora_vs_log:.5f} < {AUDIT_THRESHOLD}): sin bandera de auditoría.")

    # Árboles efectivos
    trees_str = ", ".join(str(f.n_trees) for f in result.folds)
    print(f"\nÁrboles por fold: [{trees_str}] — ceiling={XGB_N_ESTIMATORS}")
    if any(f.n_trees == XGB_N_ESTIMATORS for f in result.folds):
        print("⚠ Algún fold llegó al ceiling — considerar aumentar N_ESTIMATORS o early_stop_rounds.")

    # -----------------------------------------------------------------------
    # Calibración: XGBoost vs logística vs ELO
    # -----------------------------------------------------------------------
    y_true = total.y_true
    print()
    print("=" * 88)
    print("CALIBRACIÓN — XGBoost vs Logística vs ELO (agregado, 10 bins)")
    print("=" * 88)
    print(f"{'Bin':>14}  {'XGB pred':>9}  {'XGB real':>9}  {'Log real':>9}  {'ELO real':>9}  {'N':>6}")
    print("-" * 64)

    xgb_bins = _calibration_bins(y_true, total.xgb_probs)
    log_bins = {round(mp * 20): ar for mp, ar, _ in _calibration_bins(y_true, total.log_probs)}
    elo_bins = {round(mp * 20): ar for mp, ar, _ in _calibration_bins(y_true, total.elo_probs)}

    for mean_pred, actual_rate, n in xgb_bins:
        lo = mean_pred - 0.05
        hi = mean_pred + 0.05
        key = round(mean_pred * 20)
        log_real = log_bins.get(key, float("nan"))
        elo_real = elo_bins.get(key, float("nan"))
        log_str = f"{log_real * 100:8.2f}%" if log_real == log_real else "       —"
        elo_str = f"{elo_real * 100:8.2f}%" if elo_real == elo_real else "       —"
        print(
            f"  [{lo:4.2f}-{hi:4.2f}]  {mean_pred:9.4f}  {actual_rate * 100:8.2f}%  "
            f"{log_str}  {elo_str}  {n:6,}"
        )

    print()
    print("Calibración perfecta → XGB pred ≈ XGB real")

    # -----------------------------------------------------------------------
    # Importancias por gain (fold final)
    # -----------------------------------------------------------------------
    print()
    print("=" * 60)
    print("IMPORTANCIAS POR GAIN — fold final (B-limpia, 11 features)")
    print("(gain = reducción media del error por split; ↑ más informativa)")
    print("=" * 60)
    print(f"{'Feature':<30}  {'Gain':>10}  {'% total':>8}")
    print("-" * 52)

    total_gain = sum(g for _, g in result.final_importances)
    for feature, gain in result.final_importances:
        pct = gain / total_gain * 100 if total_gain > 0 else 0.0
        bar = "█" * int(pct / 2)
        print(f"  {feature:<28}  {gain:>10.2f}  {pct:>7.1f}%  {bar}")

    print()
    print("Nota: features con gain=0 no fueron usadas como criterio de split.")


if __name__ == "__main__":
    main()
