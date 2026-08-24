"""Reconstrucción cloud: sube RAW→GCS y repuebla BigQuery desde GCS.

Diseño y justificaciones (Decisiones 1-4 de Fase 5b en CLAUDE.md):

Fase A — RAW → GCS:
  Sube los 14 429 JSON de data/raw/ a gs://{bucket}/raw/boxscores/{game_id}.json
  con transfer_manager (paralelo; blob a blob sería horas). Idempotente: lista
  blobs existentes antes de subir y omite los ya presentes. Falla ruidosamente
  si el conteo final en GCS no coincide con el local.

Fase B — GCS → BigQuery (leyendo DESDE GCS):
  La reconstrucción descarga boxscores DESDE GCS para probar que la copia en la
  nube es autosuficiente — si BigQuery se puede reconstruir desde el bucket, la
  capa RAW cumple su promesa. Proceso: por temporada, descarga los JSON en
  paralelo con ThreadPoolExecutor, los parsea con los normalizadores de ingesta
  existentes, acumula DataFrames y ejecuta UN save por tabla por temporada.

  EXCEPCIÓN — games y teams se leen del SQLite local (no de GCS):
  Los JSON de boxscores solo contienen stats de jugadores/equipos, no metadata
  del partido (season, game_date, home_won, neutral_site, home_team_id) ni el
  catálogo de equipos. Esa información viene del endpoint de calendario, ya
  ingestada al SQLite. El SQLite es el oracle de la Fase C.

  DEUDA PARCIALMENTE ABIERTA: el RAW en GCS no es autosuficiente para
  reconstruir games desde cero — los schedules (calendario) vienen de un
  endpoint distinto y no se persisten en GCS actualmente. Solución futura:
  persistir los JSON de schedule en raw/schedules/{season}.json en la misma
  Fase A, para que la nube sea verdaderamente autocontenida.

  Batch por temporada: ~36 MERGEs totales (4 tablas × 12 temporadas + 1 teams)
  vs ~57 700 operaciones partido-por-partido. Idempotente: MERGE hace no-op
  para lo ya cargado; re-run seguro.

Fase C — Verificación de equivalencia:
  Criterio de cierre de Decisión 1. Con el SQLite como oracle: (1) conteos BQ
  vs SQLite por tabla — deben ser idénticos; (2) contenido: se normalizan dtypes
  a tipos canónicos para aislar artefactos de motor de diferencias de valor,
  luego pd.testing.assert_frame_equal con check_dtype=False. Una discrepancia de
  VALOR es un bug y falla ruidosamente con las primeras filas discrepantes.

Fase D — Verificación de features (pre-registrada en Decisión 1):
  Reconstruye features_v1 con assemble_features() leyendo de CloudDataStore,
  guarda en GCS y compara contra el parquet local en dos niveles:
    Nivel 1: SHA-256 idéntico → PASS ideal (mismo parquet bit a bit).
    Nivel 2: hash distinto + contenido idéntico → PASS con nota (artefacto
      de serialización de parquet; diferencia de motor, no de datos).
    Contenido distinto → FAIL ruidoso con primeras filas discrepantes.

Uso:
  python scripts/rebuild_cloud.py            # Fases A + B + C en orden
  python scripts/rebuild_cloud.py --upload-only
  python scripts/rebuild_cloud.py --rebuild-only
  python scripts/rebuild_cloud.py --verify-only
  python scripts/rebuild_cloud.py --features-check

Prerequisitos:
  NBA_PREDICTOR_MODE=cloud en .env (o variable de entorno)
  Credenciales GCP configuradas (GOOGLE_APPLICATION_CREDENTIALS o ADC)
  pip install 'nba-predictor[cloud]'
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

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


# ---------------------------------------------------------------------------
# Funciones puras — sin GCP, 100% testeables sin mocks de red
# ---------------------------------------------------------------------------


def _season_from_game_id(game_id: str) -> str:
    """Extrae la temporada de un game_id NBA.

    Formato NBA: {league}{type}{year2d}{seq5d}
    Los caracteres 3-4 (índice base-0) son el año de 2 dígitos del inicio
    de la temporada (la temporada 2014-15 empieza en 2014, year2d='14').

    Ejemplos:
      '0021400001' → '2014-15'
      '0022300001' → '2023-24'
      '0022500001' → '2025-26'
    """
    year2 = int(game_id[3:5])
    full_year = 2000 + year2
    return f"{full_year}-{(full_year + 1) % 100:02d}"


def _resultset_to_df(raw: dict, name: str) -> pd.DataFrame:
    """Convierte un resultSet del JSON crudo de BoxScoreTraditionalV2 a DataFrame.

    Estructura del JSON crudo:
      {"resultSets": [{"name": "TeamStats", "headers": [...], "rowSet": [...]}, ...]}

    Lanza KeyError si el nombre no existe — falla ruidosamente para detectar
    cambios en el esquema de la API.
    """
    rs = next((rs for rs in raw["resultSets"] if rs["name"] == name), None)
    if rs is None:
        available = [rs["name"] for rs in raw["resultSets"]]
        raise KeyError(
            f"resultSet '{name}' no encontrado en el JSON. "
            f"Disponibles: {available}"
        )
    return pd.DataFrame(rs["rowSet"], columns=rs["headers"])


def _normalize_for_compare(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza dtypes de un DataFrame para comparación cross-engine.

    BigQuery devuelve enteros como Int64 (nullable pandas) y fechas como
    datetime.date en columnas object. SQLite devuelve enteros como int64 (o
    float64 cuando hay NULLs) y fechas como datetime.date tras load_games.

    Una diferencia de dtype es un artefacto del motor y se normaliza aquí.
    Una diferencia de VALOR es un bug y se propaga al assert_frame_equal del
    llamador — nunca se silencia aquí.

    Tipos canónicos post-normalización:
    - Enteros (Int64 nullable, int64): → float64 (soporta NaN)
    - Fechas (datetime.date en object, datetime64): → str ISO "YYYY-MM-DD"
    - Flotantes: float64 (sin cambio)
    - Cadenas: object (sin cambio)
    """
    df = df.copy()
    for col in df.columns:
        series = df[col]
        dtype_str = str(series.dtype)

        # Nullable integer de BigQuery (Int64, Int32, …)
        if dtype_str in ("Int64", "Int32", "Int16", "Int8", "UInt64", "UInt32"):
            df[col] = series.astype("float64")

        # Entero regular numpy → float64 para uniformidad con nullable
        elif pd.api.types.is_integer_dtype(series):
            df[col] = series.astype("float64")

        # Datetime64 numpy (e.g. columnas convertidas con pd.to_datetime)
        elif pd.api.types.is_datetime64_any_dtype(series):
            df[col] = series.dt.strftime("%Y-%m-%d")

        # object dtype que puede contener datetime.date (detección por muestra)
        elif dtype_str == "object":
            non_null = series.dropna()
            if len(non_null) > 0 and hasattr(non_null.iloc[0], "strftime"):
                df[col] = series.apply(
                    lambda v: v.strftime("%Y-%m-%d") if v is not None else None
                )
    return df


def _build_report(results: dict[str, dict]) -> str:
    """Construye el reporte final de verificación de equivalencia.

    results: dict keyed por tabla con campos:
      local_count (int), cloud_count (int), ok (bool), error (str | None)

    Devuelve un string multi-línea listo para logging.
    """
    lines: list[str] = [
        "",
        "=" * 60,
        "REPORTE DE EQUIVALENCIA  BQ vs SQLite",
        "=" * 60,
    ]
    all_ok = True
    for table, r in results.items():
        icon = "✅" if r["ok"] else "❌"
        line = (
            f"  {icon}  {table}: "
            f"local={r['local_count']:,}  cloud={r['cloud_count']:,}"
        )
        if r.get("error"):
            line += f"  — {r['error']}"
        lines.append(line)
        if not r["ok"]:
            all_ok = False
    veredicto = (
        "RESULTADO: ✅ EQUIVALENCIA EXACTA"
        if all_ok
        else "RESULTADO: ❌ DISCREPANCIAS — NO MARCAR COMO ÉXITO"
    )
    lines += ["=" * 60, veredicto, "=" * 60, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fase A — Subida RAW → GCS
# ---------------------------------------------------------------------------


def phase_a_upload_raw(
    local_raw_dir: Path,
    bucket,
    gcs_prefix: str,
    max_workers: int = 16,
) -> None:
    """Sube los JSON crudos locales a GCS con transfer_manager (paralelo).

    Idempotente: lista blobs existentes una sola vez (O(1) vs O(n) checks
    individuales) y omite los ya presentes. Falla ruidosamente si el conteo
    final en GCS no coincide con el local.
    """
    from google.cloud.storage import transfer_manager

    gcs_raw_prefix = f"{gcs_prefix}raw/boxscores/"
    local_files = sorted(local_raw_dir.glob("*.json"))
    n_local = len(local_files)
    log.info(f"[Fase A] {n_local:,} JSON locales en {local_raw_dir}")

    # Una llamada de listado para obtener todos los blobs existentes
    existing_game_ids: set[str] = {
        blob.name.removeprefix(gcs_raw_prefix).removesuffix(".json")
        for blob in bucket.list_blobs(prefix=gcs_raw_prefix)
        if blob.name.endswith(".json")
    }
    to_upload = [p for p in local_files if p.stem not in existing_game_ids]
    log.info(
        f"[Fase A] En GCS: {len(existing_game_ids):,} blobs. "
        f"A subir: {len(to_upload):,}"
    )

    if to_upload:
        results = transfer_manager.upload_many_from_filenames(
            bucket,
            filenames=[p.name for p in to_upload],
            source_directory=str(local_raw_dir),
            blob_name_prefix=gcs_raw_prefix,
            max_workers=max_workers,
        )
        errors = [
            (to_upload[i].name, r)
            for i, r in enumerate(results)
            if isinstance(r, Exception)
        ]
        if errors:
            sample = ", ".join(f"{fn}: {exc}" for fn, exc in errors[:3])
            raise RuntimeError(
                f"[Fase A] {len(errors)} uploads fallaron. "
                f"Muestra: {sample}"
            )
        log.info(f"[Fase A] {len(to_upload):,} JSON subidos exitosamente.")

    # Verificación final: conteo GCS debe igualar conteo local
    final_count = sum(
        1
        for blob in bucket.list_blobs(prefix=gcs_raw_prefix)
        if blob.name.endswith(".json")
    )
    if final_count != n_local:
        raise RuntimeError(
            f"[Fase A] ❌ Conteo GCS ({final_count:,}) ≠ local ({n_local:,}). "
            "Revisar uploads fallidos antes de continuar."
        )
    log.info(
        f"[Fase A] ✅ Verificación: {final_count:,} blobs GCS == {n_local:,} local."
    )


# ---------------------------------------------------------------------------
# Fase B — Repoblado BigQuery desde GCS
# ---------------------------------------------------------------------------


def _download_raw_from_gcs(
    game_id: str,
    bucket,
    gcs_prefix: str,
) -> tuple[str, dict]:
    """Descarga y deserializa un JSON de boxscore desde GCS. Thread-safe."""
    blob = bucket.blob(f"{gcs_prefix}raw/boxscores/{game_id}.json")
    return game_id, json.loads(blob.download_as_bytes())


def _parse_season_stats(
    game_rows: list[tuple[str, int]],
    bucket,
    gcs_prefix: str,
    max_workers: int = 16,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Descarga JSONs de GCS en paralelo y parsea team/player stats de una temporada.

    game_rows: lista de (game_id, home_team_id).
    Devuelve (team_stats_df, player_stats_df) con todas las filas concatenadas.

    home_team_id se pasa desde el SQLite local porque los JSON de boxscores
    no incluyen quién es el equipo local — esa información viene de la tabla
    games (calendario), no del box score.
    """
    from nba_predictor.ingestion.boxscores import (
        _normalize_player_stats,
        _normalize_team_stats,
    )

    team_frames: list[pd.DataFrame] = []
    player_frames: list[pd.DataFrame] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs: dict = {
            pool.submit(_download_raw_from_gcs, gid, bucket, gcs_prefix): (gid, htid)
            for gid, htid in game_rows
        }
        for fut in as_completed(futs):
            gid, htid = futs[fut]
            try:
                _, raw = fut.result()
                raw_team_df = _resultset_to_df(raw, "TeamStats")
                raw_player_df = _resultset_to_df(raw, "PlayerStats")
                team_frames.append(_normalize_team_stats(raw_team_df, gid, htid))
                player_frames.append(
                    _normalize_player_stats(raw_player_df, gid, htid)
                )
            except Exception as exc:
                errors.append(f"{gid}: {exc}")

    if errors:
        raise RuntimeError(
            f"{len(errors)} partidos fallaron al procesar. "
            f"Muestra: {errors[:3]}"
        )

    return (
        pd.concat(team_frames, ignore_index=True),
        pd.concat(player_frames, ignore_index=True),
    )


def phase_b_rebuild_bq(
    local_store,
    cloud_store,
    bucket,
    gcs_prefix: str,
    max_workers: int = 16,
) -> None:
    """Repuebla BigQuery leyendo boxscores desde GCS y schedule desde SQLite local.

    Ver módulo docstring para justificación de la excepción games/teams.
    """
    from nba_predictor.config import ALL_SEASONS

    teams_df = local_store.load_teams()
    cloud_store.save_teams(teams_df)
    log.info(f"[Fase B] save_teams: {len(teams_df)} equipos")

    for season in ALL_SEASONS:
        log.info(f"[Fase B] Temporada {season}...")
        games_df = local_store.load_games(season=season)
        cloud_store.save_games(games_df)
        log.info(f"  save_games: {len(games_df):,} partidos")

        game_rows = list(
            zip(
                games_df["game_id"].tolist(),
                games_df["home_team_id"].tolist(),
            )
        )
        team_stats, player_stats = _parse_season_stats(
            game_rows, bucket, gcs_prefix, max_workers=max_workers
        )
        cloud_store.save_team_game_stats(team_stats)
        log.info(f"  save_team_game_stats: {len(team_stats):,} filas")
        cloud_store.save_player_game_stats(player_stats)
        log.info(f"  save_player_game_stats: {len(player_stats):,} filas")

    log.info("[Fase B] ✅ Repoblado completo.")


# ---------------------------------------------------------------------------
# Fase D — Verificación de features (helpers puros + fase)
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """SHA-256 de un archivo leído en bloques. Función pura, sin GCP."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _compare_features_content(
    local_df: pd.DataFrame,
    cloud_df: pd.DataFrame,
) -> tuple[bool, str | None]:
    """Compara contenido de dos features DataFrames, normaliza dtypes.

    Ordena ambos por game_id (clave única de features_v1), normaliza dtypes
    con _normalize_for_compare y compara con assert_frame_equal(check_dtype=False).

    Devuelve (True, None) si son equivalentes, o (False, mensaje_error).
    Una diferencia de dtype es artefacto del motor y se normaliza; una
    diferencia de VALOR se reporta con las primeras filas discrepantes.
    """
    if len(local_df) != len(cloud_df):
        return (
            False,
            f"conteo diferente: local={len(local_df):,}, cloud={len(cloud_df):,}",
        )

    local_norm = (
        _normalize_for_compare(local_df)
        .sort_values("game_id")
        .reset_index(drop=True)
    )
    cloud_norm = (
        _normalize_for_compare(cloud_df)
        .sort_values("game_id")
        .reset_index(drop=True)
    )

    try:
        pd.testing.assert_frame_equal(local_norm, cloud_norm, check_dtype=False)
        return True, None
    except AssertionError as exc:
        try:
            mask = (local_norm != cloud_norm).any(axis=1)
            n_diff = int(mask.sum())
            sample = local_norm.loc[mask, "game_id"].head(5).tolist()
            return (
                False,
                f"{n_diff:,} filas con valores distintos. game_ids muestra: {sample}",
            )
        except Exception:
            return False, str(exc)[:300]


def phase_features_check(
    cloud_store,
    local_parquet: Path,
    tmp_local_parquet: Path,
) -> bool:
    """Fase D: verifica equivalencia del build de features en cloud vs oracle local.

    Ejecuta assemble_features() leyendo de CloudDataStore (modo cloud activado
    globalmente), guarda el resultado en GCS y en un archivo temporal local, y
    compara contra el parquet local de referencia en dos niveles:

    Nivel 1: SHA-256 idéntico → PASS ideal (misma serialización bit a bit).
    Nivel 2: hash distinto + contenido idéntico → PASS con nota (artefacto
      de serialización — pandas/pyarrow no garantiza determinismo cross-versión).
    Contenido distinto → FAIL ruidoso con primeras filas discrepantes.

    El archivo temporal queda en tmp_local_parquet para inspección post-run;
    el usuario puede borrarlo manualmente si no lo necesita.
    """
    from nba_predictor.features.assemble import assemble_features

    log.info("[Fase D] Ejecutando assemble_features() desde CloudDataStore...")
    cloud_features = assemble_features(log=log.info)
    n_rows, n_cols = len(cloud_features), len(cloud_features.columns)
    log.info(f"[Fase D] {n_rows:,} filas × {n_cols} columnas")

    # Guardar en GCS (save_features vía CloudDataStore)
    cloud_store.save_features(cloud_features)
    log.info("[Fase D] Features guardadas en GCS (features/features_v1.parquet)")

    # Serializar localmente con el mismo to_parquet(index=False) para SHA-256
    cloud_features.to_parquet(tmp_local_parquet, index=False)
    log.info(f"[Fase D] Copia temporal local: {tmp_local_parquet}")

    # Nivel 1: SHA-256
    sha_local = _sha256_file(local_parquet)
    sha_cloud = _sha256_file(tmp_local_parquet)
    log.info(f"[Fase D] SHA-256 oracle : {sha_local}")
    log.info(f"[Fase D] SHA-256 cloud  : {sha_cloud}")

    if sha_local == sha_cloud:
        log.info(
            "[Fase D] ✅ NIVEL 1 — SHA-256 idéntico. "
            "Equivalencia perfecta: mismo parquet bit a bit."
        )
        return True

    log.info(
        "[Fase D] SHA-256 difieren — hash distinto puede ser artefacto de "
        "serialización. Verificando contenido (Nivel 2)..."
    )

    # Nivel 2: contenido
    local_features = pd.read_parquet(local_parquet)
    ok, error_msg = _compare_features_content(local_features, cloud_features)

    if ok:
        log.info(
            "[Fase D] ✅ NIVEL 2 — Contenido idéntico. PASS con nota: "
            "la diferencia de hash es un artefacto de serialización de parquet "
            "(no determinismo cross-versión de pandas/pyarrow), no una "
            "discrepancia de datos."
        )
        return True

    log.error(f"[Fase D] ❌ NIVEL 2 — Contenido diferente: {error_msg}")
    log.error(
        "[Fase D] FAIL: discrepancia real entre features cloud y oracle local. "
        "Investigar antes de marcar la reconstrucción como exitosa."
    )
    return False


# ---------------------------------------------------------------------------
# Fase C — Verificación de equivalencia
# ---------------------------------------------------------------------------

# Claves de ordenación para comparación determinista por tabla
_SORT_KEYS: dict[str, list[str]] = {
    "teams": ["team_id"],
    "games": ["game_id"],
    "team_game_stats": ["game_id", "team_id"],
    "player_game_stats": ["game_id", "player_id"],
}

# Columnas canónicas en orden de esquema (detecta diferencias de columna
# antes de que assert_frame_equal confunda columna vs valor)
_TABLE_COLS: dict[str, list[str]] = {
    "teams": ["team_id", "abbreviation", "name"],
    "games": [
        "game_id", "season", "season_type", "game_date",
        "home_team_id", "away_team_id",
        "home_pts", "away_pts", "home_won", "neutral_site",
    ],
    "team_game_stats": [
        "game_id", "team_id", "is_home",
        "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
        "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "plus_minus",
    ],
    "player_game_stats": [
        "game_id", "player_id", "team_id", "is_home", "minutes", "started",
        "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
        "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "plus_minus",
    ],
}


def phase_c_verify(local_store, cloud_store) -> bool:
    """Verifica equivalencia exacta entre BigQuery y SQLite (oracle).

    Para cada tabla:
      1. Conteo BQ == conteo SQLite (falla ruidosamente si no coincide).
      2. Contenido: normaliza dtypes cross-engine, ordena por claves de MERGE,
         compara con assert_frame_equal(check_dtype=False). Una diferencia de
         valor es un bug; se reportan las primeras filas discrepantes con sus
         claves para facilitar el diagnóstico.

    Devuelve True si todas las tablas son equivalentes.
    """
    results: dict[str, dict] = {}

    for table, sort_keys in _SORT_KEYS.items():
        log.info(f"[Fase C] Verificando {table}...")
        cols = _TABLE_COLS[table]

        if table == "teams":
            local_df = local_store.load_teams()
            cloud_df = cloud_store.load_teams()
        elif table == "games":
            local_df = local_store.load_games()
            cloud_df = cloud_store.load_games()
        elif table == "team_game_stats":
            local_df = local_store.load_team_game_stats()
            cloud_df = cloud_store.load_team_game_stats()
        else:
            local_df = local_store.load_player_game_stats()
            cloud_df = cloud_store.load_player_game_stats()

        local_n, cloud_n = len(local_df), len(cloud_df)

        if local_n != cloud_n:
            log.error(f"  ❌ {table}: conteo local={local_n:,} ≠ cloud={cloud_n:,}")
            results[table] = {
                "local_count": local_n,
                "cloud_count": cloud_n,
                "ok": False,
                "error": f"conteos diferentes ({local_n:,} vs {cloud_n:,})",
            }
            continue

        # Seleccionar solo columnas canónicas presentes en ambos DataFrames
        shared_cols = [c for c in cols if c in local_df.columns and c in cloud_df.columns]

        local_norm = (
            _normalize_for_compare(local_df[shared_cols])
            .sort_values(sort_keys)
            .reset_index(drop=True)
        )
        cloud_norm = (
            _normalize_for_compare(cloud_df[shared_cols])
            .sort_values(sort_keys)
            .reset_index(drop=True)
        )

        try:
            pd.testing.assert_frame_equal(
                local_norm, cloud_norm, check_dtype=False, check_like=False
            )
            log.info(f"  ✅ {table}: {local_n:,} filas — equivalencia exacta")
            results[table] = {
                "local_count": local_n,
                "cloud_count": cloud_n,
                "ok": True,
                "error": None,
            }
        except AssertionError as exc:
            # Reportar primeras filas discrepantes con sus claves
            try:
                mask = (local_norm != cloud_norm).any(axis=1)
                n_diff = int(mask.sum())
                sample_keys = (
                    local_norm.loc[mask, sort_keys].head(5).to_dict("records")
                )
                error_msg = (
                    f"{n_diff:,} filas con diferencias de valor. "
                    f"Primeras claves: {sample_keys}"
                )
            except Exception:
                error_msg = str(exc)[:200]
            log.error(f"  ❌ {table}: {error_msg}")
            results[table] = {
                "local_count": local_n,
                "cloud_count": cloud_n,
                "ok": False,
                "error": error_msg,
            }

    report = _build_report(results)
    log.info(report)
    return all(r["ok"] for r in results.values())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Reconstrucción cloud: RAW→GCS + GCS→BigQuery + verificación.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--upload-only", action="store_true",
        help="Solo Fase A: sube JSON locales a GCS."
    )
    group.add_argument(
        "--rebuild-only", action="store_true",
        help="Solo Fase B: repuebla BigQuery desde GCS."
    )
    group.add_argument(
        "--verify-only", action="store_true",
        help="Solo Fase C: verifica equivalencia BQ vs SQLite."
    )
    group.add_argument(
        "--features-check", action="store_true",
        help=(
            "Solo Fase D: reconstruye features desde CloudDataStore, "
            "guarda en GCS y compara contra el parquet local (verificación "
            "pre-registrada de la Decisión 1)."
        ),
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    from nba_predictor.config import settings
    from nba_predictor.storage.cloud import CloudDataStore
    from nba_predictor.storage.local import LocalDataStore

    if settings.mode != "cloud":
        sys.exit(
            "ERROR: Este script requiere NBA_PREDICTOR_MODE=cloud. "
            "Añade NBA_PREDICTOR_MODE=cloud a tu .env antes de ejecutar."
        )

    try:
        from google.cloud import storage as gcs_lib
    except ImportError:
        sys.exit(
            "ERROR: google-cloud-storage no instalado. "
            "Instala con: pip install 'nba-predictor[cloud]'"
        )

    local_store = LocalDataStore(
        db_path=settings.db_path,
        raw_dir=settings.raw_dir,
        processed_dir=settings.processed_dir,
    )
    cloud_store = CloudDataStore(
        project_id=settings.gcp_project_id,
        dataset=settings.bq_dataset,
        bucket_name=settings.gcs_bucket,
    )
    gcs_client = gcs_lib.Client(project=settings.gcp_project_id)
    bucket = gcs_client.bucket(settings.gcs_bucket)
    gcs_prefix = ""  # producción: sin prefijo; tests de integración usan "integration_test/"

    # Fases A+B+C por defecto; --features-check es independiente y siempre explícito
    run_abc = not any(
        [args.upload_only, args.rebuild_only, args.verify_only, args.features_check]
    )

    if args.upload_only or run_abc:
        log.info("=" * 60)
        log.info("FASE A — Subida RAW → GCS")
        log.info("=" * 60)
        phase_a_upload_raw(settings.raw_dir, bucket, gcs_prefix)

    if args.rebuild_only or run_abc:
        log.info("=" * 60)
        log.info("FASE B — Repoblado BigQuery desde GCS")
        log.info("=" * 60)
        phase_b_rebuild_bq(local_store, cloud_store, bucket, gcs_prefix)

    if args.verify_only or run_abc:
        log.info("=" * 60)
        log.info("FASE C — Verificación de equivalencia BQ vs SQLite")
        log.info("=" * 60)
        ok = phase_c_verify(local_store, cloud_store)
        if not ok:
            sys.exit(1)

    if args.features_check:
        log.info("=" * 60)
        log.info("FASE D — Verificación de features (pre-registrada Decisión 1)")
        log.info("=" * 60)
        local_parquet = settings.data_dir / "features_v1.parquet"
        if not local_parquet.exists():
            local_parquet = settings.processed_dir / "features_v1.parquet"
        if not local_parquet.exists():
            sys.exit(
                f"ERROR: No se encuentra el oracle local features_v1.parquet. "
                f"Buscado en {settings.data_dir / 'features_v1.parquet'} "
                f"y {settings.processed_dir / 'features_v1.parquet'}"
            )
        tmp_parquet = settings.data_dir / "_features_cloud_check.parquet"
        ok = phase_features_check(cloud_store, local_parquet, tmp_parquet)
        if not ok:
            sys.exit(1)

    log.info("rebuild_cloud.py finalizado.")


if __name__ == "__main__":
    main()
