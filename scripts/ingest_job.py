"""Cloud Run Job de ingesta incremental diaria — Fase 5b (Decisión 6).

Tres pasos secuenciales:
  1. Ingesta incremental (siempre): partidos nuevos de la temporada actual
     via existing_game_ids → RAW a GCS + stats a BigQuery.
     Fuente: CDN/S3 endpoint (cdn_client.py, Decisión 9).
     - boxscores → raw/boxscores_live/{game_id}.json en GCS
     - schedule  → raw/schedules/scheduleLeagueV2_{fecha}.json en GCS (cada corrida)
  2. Rebuild de features + save_features a GCS (solo si hubo nuevos).
  3. Reentrenamiento B-limpia + versión nueva al registry GCS (solo si
     hoy − fecha_última_versión ≥ RETRAIN_CADENCE_DAYS, o --force-retrain).

Prerrequisito: NBA_PREDICTOR_MODE=cloud (guard en main; exit ≠ 0 si no).
Excepción al guard: --check-endpoints puede correr en cualquier modo.

En offseason el job corre en vacío en el Paso 1 (0 nuevos) y registra
exactamente eso. Los Pasos 2-3 se evalúan con sus condiciones propias;
--force-retrain fuerza el Paso 3 independientemente de la cadencia.

Cada paso loggea su decisión con el motivo completo para facilitar el
diagnóstico post-run en Cloud Logging. Excepción no capturada → exit ≠ 0
(señal nativa de fallo en Cloud Run Jobs).

Uso:
  python scripts/ingest_job.py
  python scripts/ingest_job.py --force-retrain
  python scripts/ingest_job.py --check-endpoints
  NBA_PREDICTOR_MODE=cloud python scripts/ingest_job.py
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from google.cloud import storage as gcs_module


from nba_predictor.jobs.ingest_logic import (  # noqa: E402
    _check_season_guard,
    _latest_model_metadata,
    _season_from_raw_schedule,
    _should_rebuild_features,
    _should_retrain,
)


def _parquet_sha256_from_df(df: pd.DataFrame) -> str:
    """Calcula el SHA-256 del parquet serializado en memoria.

    Mismo mecanismo que rebuild_cloud._sha256_file, pero sobre bytes en RAM
    (el job no escribe el parquet a disco local en modo cloud).
    """
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return hashlib.sha256(buf.getvalue()).hexdigest()


# ---------------------------------------------------------------------------
# Pasos de ejecución
# ---------------------------------------------------------------------------


def _step1_ingest(
    ds: Any,
    client: Any,  # CDNClient
    config_season: str,  # informativo — la temporada efectiva la deriva el CDN
    today: date,
    save_raw_boxscore_fn: Callable[[str, dict], None],
    save_raw_schedule_fn: Callable[[str, dict], None],
) -> int:
    """Ingesta incremental de la temporada activa según el CDN.

    TEMPORADAS — dos conceptos distintos que no hay que confundir:
    - TRAINING_SEASONS (config) es la ventana ESTÁTICA del modelo: fija qué datos
      forman features y walk-forward. Es un contrato del proyecto, no se toca aquí.
    - config_season (TRAINING_SEASONS[-1]) llega como referencia informativa para
      loggear el estado de transición. NO se usa como filtro del schedule.
    - effective_season = seasonYear del CDN: la fuente sabe qué temporada está activa.
      Se usa para existing_game_ids, filtro del schedule y save_games.

    Guard ruidoso: si por algún bug el filtro efectivo ≠ cdn_season Y hay partidos
    jugados en el CDN → RuntimeError (nunca "0 nuevos" silencioso con datos presentes).

    Returns n_new: partidos nuevos ingestados (0 en offseason o BD al día).
    """
    log.info("PASO 1 — Ingesta incremental CDN (config_season=%s)", config_season)

    log.info("  Descargando calendario CDN/S3...")
    # Primera llamada con config_season para obtener el raw_payload con el seasonYear
    # real del CDN. Si hay desajuste de temporada, schedule_tmp tendrá 0 filas pero
    # raw_schedule siempre contiene el seasonYear canónico.
    schedule_tmp, raw_schedule = client.fetch_season_schedule(config_season)

    # Derivar la temporada efectiva del CDN — la fuente sabe qué temporada es
    cdn_season = _season_from_raw_schedule(raw_schedule)
    if cdn_season is None:
        raise RuntimeError(
            "No se pudo derivar seasonYear del schedule CDN (clave leagueSchedule.seasonYear "
            "ausente). Revisar el payload en raw/schedules/."
        )

    if cdn_season != config_season:
        log.info(
            "  CDN temporada=%s difiere de config=%s — usando CDN como fuente canónica.",
            cdn_season, config_season,
        )
        # Re-fetch filtrado por la temporada real del CDN.
        # Coste: 1 llamada extra en el periodo de transición de temporada (una vez al año).
        schedule_tmp, _ = client.fetch_season_schedule(cdn_season)

    effective_season = cdn_season
    log.info("  Temporada efectiva de ingesta: %s", effective_season)

    # Guard de profundidad: filtro efectivo siempre debe coincidir con cdn_season.
    # Con derivación correcta esto nunca falla; protege contra regresiones futuras.
    has_played = bool(len(schedule_tmp) > 0 and schedule_tmp["home_pts"].notna().any())
    should_err, guard_msg = _check_season_guard(effective_season, cdn_season, has_played)
    log.info("  Guard de temporada: %s", guard_msg)
    if should_err:
        raise RuntimeError(f"Guard de temporada activado: {guard_msg}")

    log.info("  Persistiendo schedule crudo en GCS...")
    save_raw_schedule_fn(today.isoformat(), raw_schedule)
    log.info("  Schedule crudo guardado (raw/schedules/scheduleLeagueV2_%s.json)", today.isoformat())

    existing_ids = ds.existing_game_ids(effective_season)
    log.info("  %d partidos ya en el store para %s", len(existing_ids), effective_season)

    # home_pts no nulo identifica partidos jugados con resultado final
    completed = schedule_tmp[schedule_tmp["home_pts"].notna()].copy()
    log.info("  %d partidos jugados en el calendario CDN", len(completed))

    new_games = completed[~completed["game_id"].isin(existing_ids)]
    n_new = len(new_games)

    if n_new == 0:
        log.info("  → 0 partidos nuevos. Normal en offseason o si la BD está al día.")
        return 0

    log.info("  → %d partidos nuevos a ingestar", n_new)

    all_team_stats: list[pd.DataFrame] = []
    all_player_stats: list[pd.DataFrame] = []

    for _, row in new_games.sort_values("game_date").iterrows():
        game_id = str(row["game_id"])
        log.info("    Descargando boxscore CDN %s...", game_id)
        team_stats, player_stats, raw_payload = client.fetch_boxscore(game_id)
        save_raw_boxscore_fn(game_id, raw_payload)
        all_team_stats.append(team_stats)
        all_player_stats.append(player_stats)

    # Un MERGE por tabla (no por partido) — eficiente en BQ
    ds.save_games(new_games.reset_index(drop=True))
    ds.save_team_game_stats(pd.concat(all_team_stats, ignore_index=True))
    ds.save_player_game_stats(pd.concat(all_player_stats, ignore_index=True))

    log.info("  ✓ %d partidos ingestados y guardados", n_new)
    return n_new


def _step2_features(ds: Any) -> str | None:
    """Reconstruye features_v1 desde BigQuery y sube el parquet a GCS.

    Returns
    -------
    SHA-256 del parquet generado (para usarlo en el metadata del Paso 3),
    o None si este paso no fue ejecutado (no debería ocurrir si se llama solo
    cuando n_new > 0).
    """
    from nba_predictor.features.assemble import assemble_features

    log.info("PASO 2 — Rebuild de features")
    log.info("  Ensamblando features desde BigQuery...")
    features = assemble_features(log=log.info)
    log.info("  %d × %d features ensambladas", len(features), len(features.columns))

    parquet_sha256 = _parquet_sha256_from_df(features)
    ds.save_features(features)
    log.info("  ✓ features_v1.parquet subido a GCS (SHA-256: %s...)", parquet_sha256[:16])
    return parquet_sha256


def _step3_retrain(
    ds: Any,
    gcs_client: Any,
    bucket_name: str,
    gcs_prefix: str,
    today: date,
    force: bool,
) -> None:
    """Reentrenamiento condicional de la logística B-limpia.

    Lee la fecha del último modelo del registry GCS para evaluar la cadencia.
    Si corresponde reentrenar, entrena sobre todos los datos actuales y guarda
    la nueva versión con las métricas walk-forward HEREDADAS del modelo anterior
    (no recalculadas — el walk-forward XGBoost tarda ~20 min y sus valores se
    mueven lento entre reentrenamientos semanales).
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    from nba_predictor.config import LOGREG_C, RETRAIN_CADENCE_DAYS
    from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS
    from nba_predictor.models.registry import build_metadata, make_version_name

    log.info("PASO 3 — Evaluando reentrenamiento")

    # Fecha del último modelo en GCS → fuente de la cadencia
    last_meta = _latest_model_metadata(gcs_client, bucket_name, gcs_prefix)
    last_training_date: date | None = None
    if last_meta is not None:
        last_training_date = date.fromisoformat(last_meta["training_date"])

    should, reason = _should_retrain(last_training_date, today, RETRAIN_CADENCE_DAYS, force)
    log.info("  Decisión de reentrenamiento: %s", reason)

    if not should:
        return

    log.info("  Cargando features desde GCS...")
    features = ds.load_features()
    log.info("  %d partidos para entrenamiento", len(features))

    # SHA-256 del parquet en memoria (la copia en GCS puede diferir en bits
    # por artefactos de serialización; aceptamos esta discrepancia como en Fase D)
    parquet_sha256 = _parquet_sha256_from_df(features)

    log.info("  Reentrenando logística B-limpia sobre %d partidos...", len(features))
    X_all = features[OFFICIAL_LOGISTIC_COLS].to_numpy(dtype=float)
    y_all = features["home_won"].to_numpy(dtype=float)

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("logistic", LogisticRegression(
            C=LOGREG_C,
            solver="lbfgs",
            max_iter=1000,
            random_state=42,
        )),
    ])
    pipeline.fit(X_all, y_all)
    log.info("  Pipeline entrenado.")

    # Métricas walk-forward HEREDADAS del modelo anterior (no recalculadas).
    # La cadencia semanal no justifica el costo de un walk-forward completo (~20 min).
    # Las métricas cambian solo con nuevas features o arquitectura, no con datos
    # adicionales dentro de la misma temporada ya terminada.
    if last_meta is not None:
        walk_forward_metrics = last_meta["walk_forward_metrics"]
        log.info(
            "  Métricas walk-forward heredadas de %s (LL=%s, Brier=%s)",
            last_meta["version"],
            walk_forward_metrics["log_loss"],
            walk_forward_metrics["brier"],
        )
    else:
        # Primer reentrenamiento: no hay métricas previas. Se usan las del walk-forward
        # conocido de la B-limpia (CLAUDE.md Fase 3, valores fijos del experimento).
        walk_forward_metrics = {
            "log_loss": 0.63138,
            "accuracy": 0.645,
            "brier": 0.22064,
            "n_val_games": 5823,
        }
        log.info("  PRIMER entrenamiento: usando métricas walk-forward de referencia")

    hyperparameters = {
        "C": LOGREG_C,
        "solver": "lbfgs",
        "max_iter": 1000,
        "penalty": "l2",
        "random_state": 42,
        "scaler": "StandardScaler",
        "feature_cols": OFFICIAL_LOGISTIC_COLS,
    }

    version_name = make_version_name(today)
    metadata = build_metadata(
        version_name=version_name,
        parquet_sha256=parquet_sha256,
        n_rows=len(features),
        feature_cols=OFFICIAL_LOGISTIC_COLS,
        hyperparameters=hyperparameters,
        walk_forward_metrics=walk_forward_metrics,
        retrain_cadence_days=RETRAIN_CADENCE_DAYS,
        training_date=today,
    )

    ds.save_model(pipeline, metadata, version_name)
    log.info("  ✓ Modelo guardado: %s", version_name)


# ---------------------------------------------------------------------------
# Modo diagnóstico de endpoints
# ---------------------------------------------------------------------------


def _run_check_endpoints() -> None:
    """
    Prueba conectividad CDN/S3 sin tocar GCS ni BigQuery.

    Para cada URL base configurada en CDNClient, intenta fetch del schedule
    y de un boxscore conocido (DIAGNOSTIC_GAME_ID), reporta OK/403/timeout
    con latencia. Exit 0 si al menos una base es completamente funcional;
    exit 1 si ninguna (señal de fallo para Cloud Run diagnostics).
    """
    from nba_predictor.ingestion.cdn_client import (
        CDNClient,
        DIAGNOSTIC_GAME_ID,
        INJURY_REPORT_DIAG_URL,
    )

    log.info("=== CHECK ENDPOINTS (game_id diagnóstico: %s) ===", DIAGNOSTIC_GAME_ID)
    client = CDNClient()

    # ── Diagnóstico 1+2: CDN/S3 (schedule + boxscore por cada URL base) ─────
    results = client.run_diagnostics(DIAGNOSTIC_GAME_ID)

    any_full_ok = False
    for base_url, base_result in results.items():
        sched_ok = base_result.get("schedule", {}).get("ok", False)
        box_ok = base_result.get("boxscore", {}).get("ok", False)
        sched_ms = base_result.get("schedule", {}).get("latency_ms", "?")
        box_ms = base_result.get("boxscore", {}).get("latency_ms", "?")
        status = "COMPLETO OK" if (sched_ok and box_ok) else "PARCIAL/FALLO"
        log.info(
            "  Base %s: %s (schedule %dms, boxscore %dms)",
            base_url, status, sched_ms, box_ms,
        )
        if sched_ok and box_ok:
            any_full_ok = True

    # ── Diagnóstico 3: servidor de injury reports (ak-static.cms.nba.com) ───
    # Servidor independiente de CDN_BASE_URLS — no hay fallback dual-URL.
    # Resultado informativo: no afecta exit code (la API del injury report
    # no está integrada al pipeline todavía; este check verifica accesibilidad
    # desde datacenter antes de implementar 13e-1).
    log.info("--- Injury Report PDF server ---")
    log.info("  URL diagnóstico: %s", INJURY_REPORT_DIAG_URL)
    ir = client.diagnose_injury_report(INJURY_REPORT_DIAG_URL)
    head = ir.get("head", {})
    get_r = ir.get("get", {})
    log.info(
        "  HEAD: status=%s  Content-Length=%s  %dms",
        head.get("status", "?"), head.get("content_length", "?"), head.get("latency_ms", 0),
    )
    log.info(
        "  GET:  status=%s  Content-Type=%s  first_bytes=%s  is_pdf=%s  %dms",
        get_r.get("status", "?"), get_r.get("content_type", "?"),
        get_r.get("first_bytes", "?"), get_r.get("is_pdf", "?"), get_r.get("latency_ms", 0),
    )
    if ir.get("accessible"):
        log.info("  → Injury report server: ACCESIBLE desde esta IP ✓")
    else:
        log.warning(
            "  → Injury report server: NO ACCESIBLE — "
            "verificar si el servidor restringe IPs de datacenter (ver 13e-1)"
        )

    if any_full_ok:
        log.info("=== CHECK ENDPOINTS OK — al menos una base CDN/S3 funcional. exit=0 ===")
        sys.exit(0)
    else:
        log.error("=== CHECK ENDPOINTS FAIL — ninguna base CDN/S3 funcional. exit=1 ===")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main(force_retrain: bool = False, today: date | None = None) -> None:
    from nba_predictor.config import TRAINING_SEASONS, settings
    from nba_predictor.ingestion.cdn_client import CDNClient
    from nba_predictor.storage import get_datastore

    if settings.mode != "cloud":
        raise ValueError(
            f"ingest_job requiere NBA_PREDICTOR_MODE=cloud (actual: mode='{settings.mode}'). "
            "El job lee y escribe de GCS/BigQuery; en modo local no tiene sentido ejecutarlo."
        )

    today = today or date.today()
    log.info(
        "=== ingest_job START  fecha=%s  force-retrain=%s ===",
        today.isoformat(), force_retrain,
    )

    try:
        from google.cloud import storage as gcs_lib
    except ImportError as exc:
        raise ImportError(
            "google-cloud-storage no instalado. "
            "Ejecuta: pip install 'nba-predictor[cloud]'"
        ) from exc

    ds = get_datastore()
    cdn_client = CDNClient()
    gcs_client = gcs_lib.Client(project=settings.gcp_project_id)
    gcs_prefix = getattr(ds, "gcs_prefix", "")
    bucket_name = settings.gcs_bucket

    def _save_raw_boxscore(game_id: str, payload: dict) -> None:
        """Persiste el JSON CDN del boxscore en raw/boxscores_live/."""
        path = f"{gcs_prefix}raw/boxscores_live/{game_id}.json"
        gcs_client.bucket(bucket_name).blob(path).upload_from_string(
            json.dumps(payload), content_type="application/json"
        )

    def _save_raw_schedule(date_str: str, payload: dict) -> None:
        """Persiste el JSON CDN del schedule en raw/schedules/."""
        path = f"{gcs_prefix}raw/schedules/scheduleLeagueV2_{date_str}.json"
        gcs_client.bucket(bucket_name).blob(path).upload_from_string(
            json.dumps(payload), content_type="application/json"
        )

    # Referencia de config — informativa para loggear transiciones de temporada.
    # La temporada efectiva de ingesta la deriva el CDN en _step1_ingest.
    # TRAINING_SEASONS sigue siendo la ventana estática del modelo (no se altera aquí).
    current_season = TRAINING_SEASONS[-1]

    # ── Paso 1: ingesta incremental ─────────────────────────────────────────
    n_new = _step1_ingest(
        ds=ds,
        client=cdn_client,
        config_season=current_season,
        today=today,
        save_raw_boxscore_fn=_save_raw_boxscore,
        save_raw_schedule_fn=_save_raw_schedule,
    )

    # ── Paso 2: rebuild de features ─────────────────────────────────────────
    do_rebuild, rebuild_reason = _should_rebuild_features(n_new)
    log.info("PASO 2 — %s", rebuild_reason)
    if do_rebuild:
        _step2_features(ds)

    # ── Paso 3: reentrenamiento ─────────────────────────────────────────────
    _step3_retrain(
        ds=ds,
        gcs_client=gcs_client,
        bucket_name=bucket_name,
        gcs_prefix=gcs_prefix,
        today=today,
        force=force_retrain,
    )

    log.info("=== ingest_job END  exit=0 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Cloud Run Job de ingesta incremental diaria (Decisión 6 Fase 5b)."
    )
    parser.add_argument(
        "--force-retrain",
        action="store_true",
        help="Fuerza el reentrenamiento independientemente de la cadencia.",
    )
    parser.add_argument(
        "--check-endpoints",
        action="store_true",
        help=(
            "Modo diagnóstico: prueba conectividad CDN/S3 para cada URL base "
            "configurada y termina (exit 0 si ≥1 base funcional, exit 1 si ninguna). "
            "No toca GCS ni BigQuery. Puede usarse en cualquier mode."
        ),
    )
    args = parser.parse_args()
    if args.check_endpoints:
        _run_check_endpoints()
    else:
        main(force_retrain=args.force_retrain)
