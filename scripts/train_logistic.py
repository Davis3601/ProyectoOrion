"""
Entrena y evalúa la regresión logística (Fase 3) con walk-forward.

Duelo de variantes A (ratings ajustados) vs B (ratings crudos).
Reporta:
  1. Tabla comparada por fold: trivial / ELO / logística A / logística B
  2. Fila TOTAL agregada + veredicto vs ELO
  3. Calibración de la variante ganadora vs ELO (mismo formato que evaluate_baselines.py)
  4. Coeficientes estandarizados de la variante ganadora (fold final)

Uso:
    python scripts/train_logistic.py
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


def _fmt_row(
    label: str, n: int,
    cll: float, ell: float, all_: float, bll: float,
    ca: float, ea: float, aa: float, ba: float,
) -> str:
    return (
        f"{label:<12} {n:>5}  "
        f"{cll:>7.5f}  {ell:>7.5f}  {all_:>7.5f}  {bll:>7.5f}  "
        f"{ca:>6.3f}  {ea:>6.3f}  {aa:>6.3f}  {ba:>6.3f}"
    )


def main() -> None:
    from nba_predictor.models.logistic import (
        LogisticResult,
        VARIANT_B_COLS,
        VARIANT_B_CLEAN_COLS,
        aggregate_logistic,
        run_b_clean_experiment,
        walk_forward_logistic,
    )
    from nba_predictor.models.evaluation import _calibration_bins
    from nba_predictor.storage import get_datastore

    ds = get_datastore()

    log.info("Cargando features_v1...")
    features = ds.load_features()
    log.info(f"  {len(features):,} partidos")

    log.info("Cargando games (todos, para ELO)...")
    all_games = ds.load_games()
    log.info(f"  {len(all_games):,} partidos")

    log.info("Walk-forward logístico (6 folds, variantes A y B)...")
    result: LogisticResult = walk_forward_logistic(features, all_games)
    total = aggregate_logistic(result.folds)

    # -----------------------------------------------------------------------
    print()
    print("=" * 88)
    print("RESULTADOS — REGRESIÓN LOGÍSTICA (Fase 3)")
    print("=" * 88)
    hdr = (
        f"{'Fold':<12} {'N':>5}  "
        f"{'Cte-LL':>7}  {'ELO-LL':>7}  {'LogA-LL':>7}  {'LogB-LL':>7}  "
        f"{'Cte-Ac':>6}  {'ELO-Ac':>6}  {'LogA-Ac':>7}  {'LogB-Ac':>7}"
    )
    print(hdr)
    print("-" * 88)

    for f in result.folds:
        print(_fmt_row(
            f.val_season, f.n_games,
            f.const_log_loss, f.elo_log_loss, f.log_a_log_loss, f.log_b_log_loss,
            f.const_accuracy, f.elo_accuracy, f.log_a_accuracy, f.log_b_accuracy,
        ))

    print("-" * 88)
    print(_fmt_row(
        "TOTAL", total.n_games,
        total.const_log_loss, total.elo_log_loss, total.log_a_log_loss, total.log_b_log_loss,
        total.const_accuracy, total.elo_accuracy, total.log_a_accuracy, total.log_b_accuracy,
    ))

    print()
    print("Leyenda: LL = log loss (↓ mejor) | Ac = accuracy (↑ mejor)")
    print("         A = logística con adj ratings | B = logística con raw ratings")

    # -----------------------------------------------------------------------
    # Veredicto
    # -----------------------------------------------------------------------
    print()
    print("=" * 60)
    print("VEREDICTO DEL DUELO")
    print("=" * 60)

    if total.log_a_log_loss <= total.log_b_log_loss:
        winner_label = "A (usa ratings ajustados)"
        winner_ll = total.log_a_log_loss
        winner_probs = total.log_a_probs
        winner_coefs = result.final_coefs_a
    else:
        winner_label = "B (usa ratings crudos)"
        winner_ll = total.log_b_log_loss
        winner_probs = total.log_b_probs
        winner_coefs = result.final_coefs_b

    loser_label = "B" if winner_label.startswith("A") else "A"
    loser_ll = total.log_b_log_loss if winner_label.startswith("A") else total.log_a_log_loss

    print(f"Ganadora: variante {winner_label}")
    print(f"  Log loss: {winner_ll:.5f} (variante {loser_label}: {loser_ll:.5f})")
    print()

    elo_ll = total.elo_log_loss
    vs_elo = elo_ll - winner_ll
    if winner_ll < elo_ll:
        print(f"BATE al ELO: logística={winner_ll:.5f} < ELO={elo_ll:.5f} (mejora={vs_elo:+.5f})")
        print("→ Las 12 features contienen más información que el escalar ELO.")
    else:
        print(f"NO bate al ELO: logística={winner_ll:.5f} ≥ ELO={elo_ll:.5f} (delta={vs_elo:+.5f})")
        print("→ El ELO secuencial sigue siendo superior. Diagnóstico en CLAUDE.md.")

    # -----------------------------------------------------------------------
    # Calibración: ganadora vs ELO
    # -----------------------------------------------------------------------
    print()
    print("=" * 72)
    print(f"CALIBRACIÓN — Variante ganadora ({winner_label}) vs ELO")
    print("=" * 72)
    print(f"{'Bin':>14}  {'LogReg pred':>12}  {'LogReg real':>12}  {'ELO real':>10}  {'N':>6}")
    print("-" * 60)

    y_true = total.y_true
    logreg_bins = _calibration_bins(y_true, winner_probs)
    elo_bins = {round(mp, 4): ar for mp, ar, _ in _calibration_bins(y_true, total.elo_probs)}

    for mean_pred, actual_rate, n in logreg_bins:
        lo = mean_pred - 0.05
        hi = mean_pred + 0.05
        # Buscar el bin ELO más cercano para comparación
        elo_actual = next(
            (ar for mp, ar in elo_bins.items() if abs(mp - mean_pred) < 0.06),
            float("nan"),
        )
        print(
            f"  [{lo:4.2f}-{hi:4.2f}]  {mean_pred:>12.4f}  {actual_rate * 100:>10.2f}%  "
            f"{elo_actual * 100 if not isinstance(elo_actual, float) or elo_actual == elo_actual else float('nan'):>8.2f}%  "
            f"{n:>6,}"
        )

    print()
    print("Calibración perfecta → LogReg pred ≈ LogReg real")
    print("Comparar LogReg real vs ELO real en cada bin: el sesgo de +8-10 pp del ELO")
    print("debería reducirse significativamente (intercepto aprendido fold a fold).")

    # -----------------------------------------------------------------------
    # Coeficientes de la variante ganadora (fold final)
    # -----------------------------------------------------------------------
    print()
    print("=" * 60)
    print(f"COEFICIENTES — Variante ganadora ({winner_label}), fold final")
    print("(features estandarizadas → magnitudes comparables)")
    print("=" * 60)
    print(f"{'Feature':<30}  {'Coef':>10}  {'|Coef|':>8}  {'Dirección'}")
    print("-" * 60)

    for feature, coef in winner_coefs:
        direction = "→ ventaja local" if coef > 0 else "→ ventaja visitante"
        print(f"  {feature:<28}  {coef:>10.4f}  {abs(coef):>8.4f}  {direction}")

    print()
    print("Nota: coeficiente positivo = la diferencia local-visitante alta")
    print("favorece al local (para métricas donde más es mejor).")
    print("Para tov_rate_diff y def_rating_diff, positivo = desventaja local.")

    # -----------------------------------------------------------------------
    # EXPERIMENTO B-LIMPIA
    # Criterio (CLAUDE.md): degradación < 0.001 → B-limpia es la logística oficial
    # -----------------------------------------------------------------------
    print()
    print("=" * 72)
    print("EXPERIMENTO B-LIMPIA (net_rating_diff eliminado — redundancia lineal)")
    print("=" * 72)
    log.info("Corriendo B-limpia walk-forward...")
    b_clean_ll, b_clean_coefs = run_b_clean_experiment(features, all_games)

    b_ll = total.log_b_log_loss
    degradacion = b_clean_ll - b_ll
    UMBRAL = 0.001

    print(f"Variante B     (12 features): LL = {b_ll:.5f}")
    print(f"Variante B-limpia (11 feat.): LL = {b_clean_ll:.5f}")
    print(f"Degradación: {degradacion:+.5f}  (umbral = {UMBRAL})")
    print()

    if degradacion < UMBRAL:
        print(f"VEREDICTO: B-limpia ES la logística oficial (degradación {degradacion:+.5f} < {UMBRAL})")
        print("→ Misma potencia predictiva, coeficientes legibles. Usar VARIANT_B_CLEAN_COLS.")
        oficial_label = "B-limpia"
        oficial_cols = VARIANT_B_CLEAN_COLS
        oficial_coefs = b_clean_coefs
    else:
        print(f"VEREDICTO: Se mantiene B (degradación {degradacion:+.5f} ≥ {UMBRAL})")
        print("→ net_rating_diff aporta señal no capturada por off+def solo. Usar VARIANT_B_COLS.")
        oficial_label = "B"
        oficial_cols = VARIANT_B_COLS
        oficial_coefs = result.final_coefs_b

    print()
    print(f"COEFICIENTES — Logística oficial ({oficial_label}), fold final")
    print("=" * 60)
    print(f"{'Feature':<30}  {'Coef':>10}  {'|Coef|':>8}")
    print("-" * 52)
    for feature, coef in oficial_coefs:
        sign = "+" if coef >= 0 else ""
        print(f"  {feature:<28}  {sign}{coef:.4f}  {abs(coef):.4f}")


if __name__ == "__main__":
    main()
