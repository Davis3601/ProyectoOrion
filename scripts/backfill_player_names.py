"""
D-PROD-1d — BACKFILL del catálogo de nombres de jugador.

*** ESCRIBE EN LA NUBE, A PROPÓSITO. *** Destino:
`predictorsnonprod.nba_predictor.players` vía MERGE por player_id (método 20
del DataStore), con CloudDataStore construido EXPLÍCITAMENTE aquí — nunca
get_datastore(), que obedece al .env (lección del incidente GCS del D-RES-2).
Lee los JSON crudos de GCS (raw/boxscores/ legacy y raw/boxscores_live/ CDN).

POR QUÉ EXISTE: la capa STRUCTURED no guarda nombres de jugador. Viven solo en
los JSON crudos, y en Cloud Run el endpoint no tiene esos archivos — por eso
su NameIndex nacía vacío y NINGUNA ausencia hacía match (bug 2 de D-PROD-1c).
Esta es la carga inicial del catálogo; el mantenimiento incremental lo hace el
ingest job en cada corrida.

CORRIDA ÚNICA: después de esto, el job mantiene la tabla al día. Volver a
ejecutarlo es inofensivo (MERGE idempotente por player_id), solo lento.

Uso:
    python scripts/backfill_player_names.py
    python scripts/backfill_player_names.py --limit 200   # humo
    python scripts/backfill_player_names.py --dry-run     # sin escribir
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter

from nba_predictor.config import settings
from nba_predictor.ingestion.injury_report import (
    player_names_from_cdn_payload,
    player_names_from_legacy_payload,
)
from nba_predictor.storage.cloud import CloudDataStore

log = logging.getLogger("backfill_names")

# Prefijos de GCS y la función que sabe leer cada formato. El corpus tiene dos
# generaciones de payload y ninguna es "la buena": legacy es el histórico,
# CDN es 2026+. El CDN se procesa DESPUÉS para que su nombre gane si difieren.
FUENTES: tuple[tuple[str, str], ...] = (
    ("raw/boxscores/", "legacy"),
    ("raw/boxscores_live/", "cdn"),
)


def make_store() -> CloudDataStore:
    """CloudDataStore EXPLÍCITO. Nunca get_datastore(): el modo no se hereda."""
    return CloudDataStore(
        project_id=settings.gcp_project_id,
        dataset=settings.bq_dataset,
        bucket_name=settings.gcs_bucket,
    )


def names_from_blob(raw: bytes, formato: str) -> dict[int, str]:
    """Nombres de UN payload, según su formato. Reusa el parser del paquete."""
    data = json.loads(raw)
    if formato == "cdn":
        return player_names_from_cdn_payload(data)
    return player_names_from_legacy_payload(data)


def main() -> None:
    ap = argparse.ArgumentParser(description="D-PROD-1d: backfill de players")
    ap.add_argument("--limit", type=int, default=None, help="N blobs por fuente (humo)")
    ap.add_argument("--dry-run", action="store_true", help="no escribe en BigQuery")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    store = make_store()
    bucket = store._gcs.bucket(store.bucket_name)

    nombres: dict[int, str] = {}
    por_fuente: Counter[str] = Counter()
    ilegibles = 0

    for prefijo, formato in FUENTES:
        blobs = bucket.list_blobs(prefix=store._gcs_path(prefijo))
        print(f"\nLeyendo {prefijo} (formato {formato})...", flush=True)
        for n, blob in enumerate(blobs, 1):
            if args.limit and n > args.limit:
                break
            if not blob.name.endswith(".json"):
                continue
            try:
                encontrados = names_from_blob(blob.download_as_bytes(), formato)
            except Exception as exc:  # noqa: BLE001 — se cuenta y se sigue
                ilegibles += 1
                log.warning("Blob ilegible %s: %r", blob.name, exc)
                continue
            nombres.update(encontrados)
            por_fuente[prefijo] += len(encontrados)
            if n % 1000 == 0:
                print(f"  {n} blobs | {len(nombres)} jugadores distintos", flush=True)
        print(f"  {prefijo}: {por_fuente[prefijo]} pares leídos", flush=True)

    print(f"\nTOTAL: {len(nombres)} jugadores distintos | {ilegibles} blobs ilegibles")
    muestra = sorted(nombres.items())[:5]
    print("Muestra de 5 (para cotejo humano):")
    for pid, nombre in muestra:
        print(f"  {pid}: {nombre}")

    if args.dry_run:
        print("\n--dry-run: NO se escribió en BigQuery.")
        return

    store.save_player_names(nombres)
    print(f"\nMERGE completado sobre {settings.bq_dataset}.players.")


if __name__ == "__main__":
    main()
