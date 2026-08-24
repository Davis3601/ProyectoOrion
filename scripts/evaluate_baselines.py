"""
Evalúa los dos baselines del contrato con walk-forward por temporadas.

Imprime:
    1. Tabla de resultados por fold (log loss, Brier, accuracy)
    2. Fila agregada TOTAL
    3. Curva de calibración del ELO (bins de probabilidad predicha vs tasa real)

Los resultados de este script son LA VARA OFICIAL que los modelos de Fase 3
deben superar. Guardados en CLAUDE.md bajo "Fase 3 — Vara oficial".

Uso:
    python scripts/evaluate_baselines.py
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


def _fmt_row(label: str, n: int, cp: float, cll: float, cb: float, ca: float,
             ell: float, eb: float, ea: float) -> str:
    return (
        f"{label:<12} {n:>5}  "
        f"{cp:>6.4f}  {cll:>7.5f}  {cb:>6.4f}  {ca:>6.3f}  "
        f"{ell:>7.5f}  {eb:>6.4f}  {ea:>6.3f}"
    )


def main() -> None:
    from nba_predictor.models.evaluation import aggregate, walk_forward_evaluate
    from nba_predictor.storage import get_datastore

    ds = get_datastore()

    log.info("Cargando features_v1...")
    features = ds.load_features()
    log.info(f"  {len(features):,} partidos")

    log.info("Cargando games (todos, para ELO)...")
    all_games = ds.load_games()
    log.info(f"  {len(all_games):,} partidos")

    log.info("Evaluando baselines (walk-forward, 6 folds)...")
    folds = walk_forward_evaluate(features, all_games)
    total = aggregate(folds)

    # -----------------------------------------------------------------------
    print()
    print("=" * 80)
    print("RESULTADOS DE BASELINES — VARA OFICIAL (Fase 3)")
    print("=" * 80)
    hdr = (
        f"{'Fold':<12} {'N':>5}  "
        f"{'Cte-p':>6}  {'Cte-LL':>7}  {'Cte-Br':>6}  {'Cte-Ac':>6}  "
        f"{'ELO-LL':>7}  {'ELO-Br':>6}  {'ELO-Ac':>6}"
    )
    print(hdr)
    print("-" * 80)

    for f in folds:
        print(_fmt_row(
            f.val_season, f.n_games, f.const_p,
            f.const_log_loss, f.const_brier, f.const_accuracy,
            f.elo_log_loss, f.elo_brier, f.elo_accuracy,
        ))

    print("-" * 80)
    print(_fmt_row(
        "TOTAL", total.n_games, total.const_p,
        total.const_log_loss, total.const_brier, total.const_accuracy,
        total.elo_log_loss, total.elo_brier, total.elo_accuracy,
    ))

    print()
    print("Leyenda: Cte = baseline trivial (constante por fold) | ELO = baseline ELO")
    print("         LL = log loss (↓ mejor) | Br = Brier (↓ mejor) | Ac = accuracy (↑ mejor)")

    # -----------------------------------------------------------------------
    print()
    print("=" * 60)
    print("CALIBRACIÓN ELO (agregado — 10 bins de probabilidad predicha)")
    print("=" * 60)
    print(f"{'Bin centro':>12}  {'Pred media':>10}  {'Real %':>8}  {'N':>6}")
    print("-" * 42)
    for mean_pred, actual_rate, n in total.elo_calib_bins:
        lo = mean_pred - 0.05
        hi = mean_pred + 0.05
        bar_len = int(round(actual_rate * 20))
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(
            f"  [{lo:4.2f}-{hi:4.2f}]  {mean_pred:10.4f}  {actual_rate*100:7.2f}%  {n:6,}  {bar}"
        )

    print()
    print("Interpretación: calibración perfecta → Pred media ≈ Real %")
    print(f"Mejora ELO sobre trivial (log loss): "
          f"{total.const_log_loss:.5f} − {total.elo_log_loss:.5f} = "
          f"{total.const_log_loss - total.elo_log_loss:+.5f}")
    print(f"Mejora ELO sobre trivial (accuracy):  "
          f"{total.elo_accuracy:.3f} − {total.const_accuracy:.3f} = "
          f"{total.elo_accuracy - total.const_accuracy:+.3f}")


if __name__ == "__main__":
    main()
