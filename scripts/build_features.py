"""
Ensambla la capa FEATURES (Layer 3) y la persiste en Parquet.

Orquestación:
    1. Calcula los 5 grupos de features (Grupos 1-5)
    2. Une los grupos con inner joins validados 1:1 por game_id
    3. Filtra a TRAINING_SEASONS y aplica la regla "ambos equipos ≥15 partidos previos"
    4. Verifica invariante cero-NaN
    5. Guarda en data/processed/features_v1.parquet vía DataStore

Uso:
    python scripts/build_features.py

El script es idempotente: sobreescribe el Parquet anterior si existe.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Asegurar que el proyecto es importable cuando se corre desde la raíz
sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main() -> None:
    from nba_predictor.features.assemble import assemble_features
    from nba_predictor.storage import get_datastore

    log.info("=" * 60)
    log.info("build_features.py — Ensamblado capa FEATURES (Layer 3)")
    log.info("=" * 60)

    features = assemble_features(log=log.info)

    log.info("-" * 60)
    log.info(f"Filas finales             : {len(features):,}")
    log.info(f"Columnas                  : {len(features.columns)}")
    log.info(f"home_won (media)          : {features['home_won'].mean():.4f}")
    log.info(
        f"Período                   : {features['game_date'].min()} → "
        f"{features['game_date'].max()}"
    )

    log.info("")
    log.info("Desglose por temporada (tras todas las exclusiones):")
    for season in sorted(features["season"].unique()):
        mask = features["season"] == season
        hw = features.loc[mask, "home_won"].mean()
        log.info(f"  {season}: {mask.sum():5,} partidos  home_won={hw:.3f}")

    ds = get_datastore()
    ds.save_features(features)
    log.info("")
    log.info("Features guardadas en Parquet (features_v1).")


if __name__ == "__main__":
    main()
