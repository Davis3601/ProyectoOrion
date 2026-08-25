"""Lógica pura del Cloud Run Job de ingesta — testable sin GCP (Fase 5b, Decisión 6).

Extraída de scripts/ingest_job.py para que los unit tests puedan importarla
desde el paquete sin depender de que scripts/ esté en sys.path (problema
estructural: pytest CLI no añade CWD a sys.path, a diferencia de python -m pytest).

NOTA IMPORTANTE sobre temporadas:
  - TRAINING_SEASONS en config.py es la ventana ESTÁTICA del modelo de entrenamiento.
    Fija qué datos forman features y walk-forward. Es un contrato del proyecto y no
    se modifica aquí.
  - La temporada de INGESTA (Paso 1 del job) se deriva del seasonYear del CDN schedule
    (ver _season_from_raw_schedule). La fuente sabe qué temporada está activa; config
    no lo sabe en transiciones de temporada.
  - Ambos conceptos son distintos. Confundirlos produce el fallo silencioso que el
    fix de 2026-08-15 corrige.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

_log = logging.getLogger(__name__)


def _should_retrain(
    last_training_date: date | None,
    today: date,
    cadence_days: int,
    force: bool,
) -> tuple[bool, str]:
    """Decide si corresponde reentrenar el modelo.

    La cadencia viene siempre de config (RETRAIN_CADENCE_DAYS); nunca de otro
    origen. La fecha del último reentrenamiento se lee del metadata del registry
    en GCS — si no hay modelo previo, se entrena incondicionalmente.
    """
    if force:
        return True, "--force-retrain activado: reentrenamiento forzado"
    if last_training_date is None:
        return True, "sin modelo previo en el registry — primer reentrenamiento"
    days_since = (today - last_training_date).days
    if days_since >= cadence_days:
        return True, (
            f"último reentrenamiento hace {days_since} días "
            f"(cadencia {cadence_days} días) → reentrenando"
        )
    return False, (
        f"último reentrenamiento hace {days_since} días "
        f"(cadencia {cadence_days} días) → sin reentrenamiento"
    )


def _should_rebuild_features(n_new: int) -> tuple[bool, str]:
    """Decide si corresponde reconstruir las features.

    Solo tiene sentido si hay datos nuevos — los rolling windows absorberán
    los nuevos partidos. Sin datos nuevos, el parquet no cambia.
    """
    if n_new > 0:
        return True, f"{n_new} partidos nuevos → rebuild de features necesario"
    return False, "0 partidos nuevos → features sin cambios, rebuild omitido"


def _latest_model_metadata(
    gcs_client: Any,
    bucket_name: str,
    gcs_prefix: str,
) -> dict | None:
    """Lista el registry en GCS y devuelve el metadata de la versión más reciente.

    El nombre de versión codifica la fecha (v1_logistic_bclean_YYYY-MM-DD), así que
    el máximo lexicográfico identifica la versión más reciente sin parsear fechas.

    gcs_prefix: prefijo de paths en el bucket (desde CloudDataStore.gcs_prefix),
    con barra final si no está vacío.
    """
    from nba_predictor.models.registry import VERSION_PREFIX

    prefix = f"{gcs_prefix}models/{VERSION_PREFIX}_"
    blobs = list(gcs_client.bucket(bucket_name).list_blobs(prefix=prefix))
    meta_blobs = [b for b in blobs if b.name.endswith("/metadata.json")]
    if not meta_blobs:
        return None
    latest_blob = max(meta_blobs, key=lambda b: b.name)
    return json.loads(latest_blob.download_as_text())


def _season_from_raw_schedule(raw: dict) -> str | None:
    """Extrae el seasonYear del payload crudo del CDN schedule.

    El CDN siempre sirve la temporada activa. Esta función es la fuente canónica
    de la temporada de ingesta — NO TRAINING_SEASONS[-1] de config, que es
    la ventana estática del modelo de entrenamiento (conceptos distintos).

    El payload CDN tiene la estructura:
      {"leagueSchedule": {"seasonYear": "2026-27", "gameDates": [...]}}

    Devuelve None si la estructura es inesperada (guard: el caller debe fallar
    ruidosamente en ese caso).
    """
    return raw.get("leagueSchedule", {}).get("seasonYear") or None


def _archive_injury_report(
    store: Any,
    date_str: str,
    max_requests: int = 20,
) -> bool:
    """Archiva el snapshot del PDF de injury report para date_str.

    Best-effort (Decisión 4 del feed): discover → GET → save_raw.
    Sin parsear, sin extraer ausencias, sin tocar features.
    Cualquier excepción se loggea como WARNING y retorna False —
    el fallo NUNCA bloquea la ingesta de boxscores (misión crítica del job).

    Returns True si se archivó con éxito, False si hubo cualquier error.
    """
    try:
        from nba_predictor.ingestion.injury_report import (
            discover_latest_snapshot,
            download_snapshot,
        )

        url, suffix = discover_latest_snapshot(date_str, max_requests=max_requests)
        pdf_bytes = download_snapshot(url)
        store.save_raw_injury_report(date_str, suffix, pdf_bytes)
        _log.info(
            "  ✓ Injury report archivado: %s_%s (%d bytes)",
            date_str, suffix, len(pdf_bytes),
        )
        return True
    except Exception as exc:
        _log.warning(
            "  Injury report NO archivado para %s: %s — "
            "la ingesta continúa sin interrupción.",
            date_str, exc,
        )
        return False


def _check_season_guard(
    filter_season: str,
    cdn_season: str,
    has_played_games: bool,
) -> tuple[bool, str]:
    """Guard contra silenciosos '0 nuevos' por desajuste de temporada en el filtro.

    Con derivación correcta (filter_season siempre == cdn_season después del fix
    de 2026-08-15), este guard NUNCA falla en operación normal. Es defensa en
    profundidad: si alguien revierte el código a filtrar por config_season en lugar
    de cdn_season, y hay partidos jugados, el job falla ruidosamente en lugar de
    silenciar el problema.

    Parámetros
    ----------
    filter_season : temporada que se usó efectivamente como filtro del schedule.
    cdn_season    : temporada que el CDN reporta en el payload (leagueSchedule.seasonYear).
    has_played_games : si el schedule (filtrado a filter_season) tiene partidos con
                       resultado final (home_pts no nulo / gameStatus==3).

    Devuelve (should_error: bool, message: str).
    El caller lanza RuntimeError si should_error es True.
    """
    if filter_season == cdn_season:
        return False, f"temporada OK: filtro y CDN coinciden en {cdn_season!r}"
    if has_played_games:
        return True, (
            f"MISMATCH CRÍTICO: filtrado por {filter_season!r} pero CDN sirve "
            f"{cdn_season!r} y hay partidos jugados en el CDN — habría '0 nuevos' "
            f"silencioso con datos presentes. Usa temporada CDN o actualiza "
            f"TRAINING_SEASONS en config."
        )
    return False, (
        f"aviso: filtrado por {filter_season!r} pero CDN sirve {cdn_season!r} "
        f"(sin partidos jugados aún — inofensivo por ahora)."
    )
