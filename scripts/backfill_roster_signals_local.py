"""Backfill LOCAL de señales de plantilla (D-PRE-8, Parte C).

Corre el MISMO paso 1b del ingest job (roster_signals_step) contra el
LocalDataStore, con almacén LOCAL EXPLÍCITO (convención de scripts: el modo se
fija, jamás se hereda del .env). Escribe solo en local:
  - tabla roster_signals de data/nba.sqlite;
  - data/raw/boxscores_preseason/{game_id}.json.

Lee de la red, sin escribir fuera de la laptop:
  - scheduleLeagueV2 en vivo vía CDNClient (el mismo camino del job);
  - boxscores 001 vía CDNClient (S3);
  - PDFs ya archivados del injury report en GCS (solo lectura) para la mitad
    (b), en vez de sondear ak-static otra vez.

Evidencia que imprime: hashes de games / team_game_stats / player_game_stats /
features_v1.parquet ANTES y DESPUÉS (no contaminación sobre los datos reales)
y la cobertura pre-registrada: jugadores con fila de temporada regular 2025-26
que tienen señal de pretemporada 2026 (conjetura >85%).

Uso:
    python scripts/backfill_roster_signals_local.py [--pdf-dates 2026-10-19,2026-10-20]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BUCKET = "predictorsnonprod-nba-predictors"


def _hash_tabla(db: Path, tabla: str) -> str:
    with sqlite3.connect(db) as conn:
        filas = conn.execute(f"SELECT * FROM {tabla} ORDER BY 1, 2").fetchall()
    return hashlib.sha256(repr(filas).encode()).hexdigest()


def hashes(store) -> dict[str, str]:
    h = {t: _hash_tabla(store.db_path, t)
         for t in ("games", "team_game_stats", "player_game_stats")}
    parquet = store.processed_dir / "features_v1.parquet"
    h["features_v1.parquet"] = hashlib.sha256(parquet.read_bytes()).hexdigest()
    return h


def cobertura(store, season_regular: str = "2025-26") -> dict:
    """Jugadores con fila regular en season_regular que tienen señal 001."""
    pgs = store.load_player_game_stats(season=season_regular)
    con_regular = set(pgs["player_id"].astype(int))
    rs = store.load_roster_signals()
    con_001 = set(rs.loc[rs["source"] == "preseason", "player_id"].astype(int))
    # Desglose por rotación: minutos promedio en 2025-26 (los destacados salen de arriba).
    mins = pgs[pgs["minutes"] > 0].groupby("player_id")["minutes"].mean()
    rot = set(mins[mins >= 20].index.astype(int))
    return {
        "denominador_regular_2025_26": len(con_regular),
        "con_senal_001": len(con_regular & con_001),
        "cobertura": round(len(con_regular & con_001) / len(con_regular), 4),
        "rotacion_20min_denominador": len(rot),
        "rotacion_20min_con_senal_001": len(rot & con_001),
        "rotacion_20min_cobertura": round(len(rot & con_001) / len(rot), 4) if rot else None,
        "jugadores_001_sin_fila_2025_26": len(con_001 - con_regular),
    }


def pdfs_archivados(fechas: list[str]) -> list[tuple[str, str, bytes]]:
    """PDFs ya archivados en GCS para esas fechas (lectura)."""
    from google.cloud import storage

    from nba_predictor.config import settings

    gcs = storage.Client(project=settings.gcp_project_id)
    out = []
    for f in fechas:
        for b in gcs.list_blobs(BUCKET, prefix=f"raw/injury_reports/{f}_"):
            suffix = b.name.rsplit("/", 1)[1][len(f) + 1:-len(".pdf")]
            out.append((f, suffix, b.download_as_bytes()))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dates", default="2026-10-19,2026-10-20")
    args = ap.parse_args()

    from nba_predictor.config import settings
    from nba_predictor.ingestion.cdn_client import CDNClient
    from nba_predictor.jobs.roster_signals import roster_signals_step
    from nba_predictor.storage.local import LocalDataStore

    # Almacén LOCAL explícito, nunca get_datastore() (incidente GCS de D-RES-2).
    store = LocalDataStore(db_path=settings.db_path, raw_dir=settings.raw_dir,
                           processed_dir=settings.processed_dir)
    antes = hashes(store)

    cdn = CDNClient()
    _, raw_schedule = cdn.fetch_season_schedule("2026-27")
    pdfs = pdfs_archivados([f for f in args.pdf_dates.split(",") if f])
    print(f"PDFs archivados leídos de GCS: {[f'{d}_{s}' for d, s, _ in pdfs]}")

    observed = datetime.now(timezone.utc)
    # Mitad (a) primero sin PDFs: así load_player_names local ya ve los
    # boxscores 001 recién escritos (novatos) cuando corra la mitad (b).
    r_a = roster_signals_step(store, cdn, raw_schedule, [], observed_at=observed)
    r_b = roster_signals_step(store, cdn, raw_schedule, pdfs, observed_at=observed)
    despues = hashes(store)

    rs = store.load_roster_signals()
    out = {
        "resumen_pretemporada": r_a,
        "resumen_pdf": r_b,
        "hashes_antes": antes,
        "hashes_despues": despues,
        "no_contaminacion": antes == despues,
        "roster_signals_por_fuente": rs["source"].value_counts().to_dict(),
        "partidos_001_con_senal": int(rs.loc[rs["source"] == "preseason", "ref_id"].nunique()),
        "cobertura": cobertura(store),
    }
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
