"""CloudDataStore: BigQuery (tablas STRUCTURED) + GCS (RAW/FEATURES/MODELS)."""
from __future__ import annotations

import io
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from .base import DataStore

# ---------------------------------------------------------------------------
# Imports opcionales — el módulo se puede importar sin [cloud] instalado.
# Los clientes reales solo se crean si no se inyectan mocks en __init__.
# ---------------------------------------------------------------------------
try:
    from google.cloud import bigquery as _bigquery  # type: ignore[import]
    from google.cloud import storage as _storage    # type: ignore[import]

    _CLOUD_AVAILABLE = True
except ImportError:
    _bigquery = None  # type: ignore[assignment]
    _storage = None  # type: ignore[assignment]
    _CLOUD_AVAILABLE = False


# Expiración automática de las staging tables como red de seguridad ante crash.
# Si el proceso muere entre el MERGE y el delete explícito, BigQuery limpia
# la staging en _STAGING_EXPIRATION_HOURS horas sin intervención manual.
# El delete explícito sigue siendo el mecanismo primario (limpieza inmediata
# en el camino feliz). Los dos mecanismos juntos = sin basura silenciosa.
_STAGING_EXPIRATION_HOURS: int = 1


# ---------------------------------------------------------------------------
# Funciones puras — sin google-cloud, 100% testeables sin mocks
# ---------------------------------------------------------------------------


def _full_table_id(project_id: str, dataset: str, table: str) -> str:
    return f"{project_id}.{dataset}.{table}"


def _staging_table_id(
    project_id: str, dataset: str, table: str, suffix: str
) -> str:
    return f"{project_id}.{dataset}._{table}_staging_{suffix}"


def _gcs_boxscore_path(game_id: str) -> str:
    return f"raw/boxscores/{game_id}.json"


def _gcs_features_path(version: str) -> str:
    return f"features/features_{version}.parquet"


def _gcs_model_path(version_name: str, filename: str) -> str:
    return f"models/{version_name}/{filename}"


def _gcs_injury_report_path(date_str: str, suffix: str) -> str:
    return f"raw/injury_reports/{date_str}_{suffix}.pdf"


def _build_merge_sql(
    target_id: str,
    staging_id: str,
    merge_keys: list[str],
    all_columns: list[str],
) -> str:
    """Genera el SQL MERGE BigQuery para idempotencia tabular.

    Los merge_keys van en ON y NO en UPDATE SET (ya están matcheados).
    Todos los columns aparecen en INSERT.
    Función pura: solo string processing, testeable con assertEqual exacto.
    """
    on_clause = " AND ".join(f"T.{k} = S.{k}" for k in merge_keys)

    update_cols = [c for c in all_columns if c not in merge_keys]
    update_clause = ",\n    ".join(f"T.{c} = S.{c}" for c in update_cols)

    insert_cols = ", ".join(all_columns)
    insert_values = ", ".join(f"S.{c}" for c in all_columns)

    return (
        f"MERGE `{target_id}` AS T\n"
        f"USING `{staging_id}` AS S\n"
        f"ON {on_clause}\n"
        f"WHEN MATCHED THEN\n"
        f"  UPDATE SET\n"
        f"    {update_clause}\n"
        f"WHEN NOT MATCHED THEN\n"
        f"  INSERT ({insert_cols})\n"
        f"  VALUES ({insert_values})"
    )


# Claves de idempotencia por tabla (definición única, referenciada en _save_tabular)
_MERGE_KEYS: dict[str, list[str]] = {
    "teams": ["team_id"],
    "games": ["game_id"],
    "team_game_stats": ["game_id", "team_id"],
    "player_game_stats": ["game_id", "player_id"],
}


# ---------------------------------------------------------------------------
# CloudDataStore
# ---------------------------------------------------------------------------


class CloudDataStore(DataStore):
    """DataStore sobre BigQuery (tablas STRUCTURED) + GCS (RAW/FEATURES/MODELS).

    Idempotencia tabular: MERGE vía staging UUID con doble protección contra
    basura silenciosa (expiración automática + delete explícito).

    Rutas GCS canónicas (Decisión 4), con `gcs_prefix` para aislamiento de
    entornos (e.g. gcs_prefix="integration_test/" en tests de integración):
      {prefix}raw/boxscores/{game_id}.json
      {prefix}features/features_{version}.parquet
      {prefix}models/{version_name}/model.joblib + metadata.json

    Parámetros `_bq_client` y `_gcs_client`: puntos de inyección para
    tests unitarios. Permiten usar mocks sin tener google-cloud instalado.
    """

    def __init__(
        self,
        project_id: str,
        dataset: str,
        bucket_name: str,
        gcs_prefix: str = "",
        _bq_client: Any = None,
        _gcs_client: Any = None,
    ) -> None:
        if not project_id:
            raise ValueError(
                "gcp_project_id no puede estar vacío en modo cloud. "
                "Configura NBA_PREDICTOR_GCP_PROJECT_ID."
            )
        if not bucket_name:
            raise ValueError(
                "gcs_bucket no puede estar vacío en modo cloud. "
                "Configura NBA_PREDICTOR_GCS_BUCKET."
            )

        self.project_id = project_id
        self.dataset = dataset
        self.bucket_name = bucket_name
        self.gcs_prefix = gcs_prefix

        if _bq_client is not None or _gcs_client is not None:
            self._bq = _bq_client
            self._gcs = _gcs_client
        else:
            if not _CLOUD_AVAILABLE:
                raise ImportError(
                    "google-cloud-bigquery y google-cloud-storage son necesarios "
                    "para cloud mode. Instala con: pip install 'nba-predictor[cloud]'"
                )
            self._bq = _bigquery.Client(project=project_id)
            self._gcs = _storage.Client(project=project_id)

    def _gcs_path(self, path: str) -> str:
        """Aplica el prefijo de entorno a una ruta GCS canónica.

        En producción gcs_prefix="" → rutas sin cambio.
        En tests de integración gcs_prefix="integration_test/" → aislamiento
        completo del bucket real sin necesitar un segundo bucket.
        """
        return f"{self.gcs_prefix}{path}"

    # ------------------------------------------------------------------
    # Tablas BigQuery — MERGE + staging con doble protección anti-basura
    # ------------------------------------------------------------------

    def _save_tabular(self, df: pd.DataFrame, table_name: str) -> None:
        """Carga df a staging y ejecuta MERGE contra el destino. Idempotente.

        Paridad con LocalDataStore: save sobre almacén vacío funciona sin
        provisioning manual. SQLite crea las tablas en el primer INSERT OR
        REPLACE; BigQuery MERGE en cambio falla con 404 si el destino no
        existe. Por eso se ejecuta CREATE TABLE IF NOT EXISTS ... AS SELECT *
        FROM staging WHERE FALSE antes del MERGE: mismo esquema que la staging
        recién cargada, cero filas, idempotente.

        Doble protección contra staging huérfana:
        1. Expiración automática (_STAGING_EXPIRATION_HOURS) configurada
           ANTES del MERGE: red de seguridad si el proceso muere durante
           la operación larga.
        2. Delete explícito DESPUÉS del MERGE: limpieza inmediata en el
           camino feliz, sin esperar a la expiración.
        """
        if df.empty:
            return

        suffix = uuid.uuid4().hex[:12]
        staging_id = _staging_table_id(
            self.project_id, self.dataset, table_name, suffix
        )
        target_id = _full_table_id(self.project_id, self.dataset, table_name)

        # job_config real cuando google-cloud está disponible; None en tests
        # (MagicMock acepta cualquier argumento, incluido None)
        job_config = None
        if _bigquery is not None:
            job_config = _bigquery.LoadJobConfig(
                autodetect=True,
                write_disposition=_bigquery.WriteDisposition.WRITE_TRUNCATE,
                create_disposition=_bigquery.CreateDisposition.CREATE_IF_NEEDED,
            )

        load_job = self._bq.load_table_from_dataframe(
            df, staging_id, job_config=job_config
        )
        load_job.result()

        # Fijar expiración ANTES del MERGE para que la red de seguridad esté
        # activa durante la operación más larga (en producción puede tardar
        # varios segundos por millones de filas).
        expiration = datetime.now(timezone.utc) + timedelta(hours=_STAGING_EXPIRATION_HOURS)
        staging_table = self._bq.get_table(staging_id)
        staging_table.expires = expiration
        self._bq.update_table(staging_table, ["expires"])

        # Garantizar que el destino existe antes del MERGE.
        # AS SELECT * FROM staging WHERE FALSE hereda el esquema exacto de la
        # staging recién creada sin insertar ninguna fila.
        create_sql = (
            f"CREATE TABLE IF NOT EXISTS `{target_id}`"
            f" AS SELECT * FROM `{staging_id}` WHERE FALSE"
        )
        self._bq.query(create_sql).result()

        merge_sql = _build_merge_sql(
            target_id=target_id,
            staging_id=staging_id,
            merge_keys=_MERGE_KEYS[table_name],
            all_columns=list(df.columns),
        )
        self._bq.query(merge_sql).result()

        # Delete explícito: limpieza inmediata sin esperar al TTL
        self._bq.delete_table(staging_id, not_found_ok=True)

    def save_teams(self, teams: pd.DataFrame) -> None:
        self._save_tabular(teams, "teams")

    def save_games(self, games: pd.DataFrame) -> None:
        self._save_tabular(games, "games")

    def save_team_game_stats(self, stats: pd.DataFrame) -> None:
        self._save_tabular(stats, "team_game_stats")

    def save_player_game_stats(self, stats: pd.DataFrame) -> None:
        self._save_tabular(stats, "player_game_stats")

    # ------------------------------------------------------------------
    # RAW boxscores → GCS
    # ------------------------------------------------------------------

    def save_raw_boxscore(self, game_id: str, payload: dict) -> None:
        self._gcs.bucket(self.bucket_name).blob(
            self._gcs_path(_gcs_boxscore_path(game_id))
        ).upload_from_string(
            json.dumps(payload, ensure_ascii=False),
            content_type="application/json",
        )

    # ------------------------------------------------------------------
    # Lectura BigQuery
    # ------------------------------------------------------------------

    def _bq_query_df(self, sql: str) -> pd.DataFrame:
        return self._bq.query(sql).to_dataframe()

    def load_games(
        self,
        season: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        table = _full_table_id(self.project_id, self.dataset, "games")
        clauses: list[str] = []
        if season:
            clauses.append(f"season = '{season}'")
        if start_date:
            clauses.append(f"game_date >= '{start_date.isoformat()}'")
        if end_date:
            clauses.append(f"game_date <= '{end_date.isoformat()}'")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM `{table}` {where} ORDER BY game_date"
        df = self._bq_query_df(sql)
        if not df.empty and "game_date" in df.columns:
            df["game_date"] = pd.to_datetime(df["game_date"]).dt.date
        return df

    def load_team_game_stats(
        self,
        season: str | None = None,
        team_id: int | None = None,
    ) -> pd.DataFrame:
        """JOIN a games se construye SOLO cuando se pasa season — team_game_stats no tiene
        columna de temporada propia. Sin filtro de season, consulta la tabla directa para no
        depender de que games exista (paridad de contrato: load no falla por tablas ajenas
        que la consulta no necesita)."""
        table = _full_table_id(self.project_id, self.dataset, "team_game_stats")
        if season:
            games_table = _full_table_id(self.project_id, self.dataset, "games")
            clauses: list[str] = [f"g.season = '{season}'"]
            if team_id is not None:
                clauses.append(f"t.team_id = {team_id}")
            where = "WHERE " + " AND ".join(clauses)
            sql = (
                f"SELECT t.* FROM `{table}` t"
                f" JOIN `{games_table}` g USING (game_id)"
                f" {where}"
                f" ORDER BY g.game_date"
            )
        else:
            clauses_direct: list[str] = []
            if team_id is not None:
                clauses_direct.append(f"team_id = {team_id}")
            where_direct = ("WHERE " + " AND ".join(clauses_direct)) if clauses_direct else ""
            sql = f"SELECT * FROM `{table}` {where_direct}"
        return self._bq_query_df(sql)

    def load_player_game_stats(
        self,
        season: str | None = None,
        team_id: int | None = None,
        player_id: int | None = None,
    ) -> pd.DataFrame:
        """JOIN a games se construye SOLO cuando se pasa season — player_game_stats no tiene
        columna de temporada propia. Sin filtro de season, consulta la tabla directa para no
        depender de que games exista (paridad de contrato: load no falla por tablas ajenas
        que la consulta no necesita)."""
        table = _full_table_id(self.project_id, self.dataset, "player_game_stats")
        if season:
            games_table = _full_table_id(self.project_id, self.dataset, "games")
            clauses: list[str] = [f"g.season = '{season}'"]
            if team_id is not None:
                clauses.append(f"p.team_id = {team_id}")
            if player_id is not None:
                clauses.append(f"p.player_id = {player_id}")
            where = "WHERE " + " AND ".join(clauses)
            sql = (
                f"SELECT p.* FROM `{table}` p"
                f" JOIN `{games_table}` g USING (game_id)"
                f" {where}"
                f" ORDER BY g.game_date"
            )
        else:
            clauses_direct: list[str] = []
            if team_id is not None:
                clauses_direct.append(f"team_id = {team_id}")
            if player_id is not None:
                clauses_direct.append(f"player_id = {player_id}")
            where_direct = ("WHERE " + " AND ".join(clauses_direct)) if clauses_direct else ""
            sql = f"SELECT * FROM `{table}` {where_direct}"
        return self._bq_query_df(sql)

    def load_teams(self) -> pd.DataFrame:
        table = _full_table_id(self.project_id, self.dataset, "teams")
        return self._bq_query_df(
            f"SELECT * FROM `{table}` ORDER BY team_id"
        )

    def existing_game_ids(self, season: str) -> set[str]:
        table = _full_table_id(self.project_id, self.dataset, "games")
        sql = f"SELECT game_id FROM `{table}` WHERE season = '{season}'"
        rows = self._bq.query(sql).result()
        return {row.game_id for row in rows}

    # ------------------------------------------------------------------
    # Features → GCS Parquet
    # ------------------------------------------------------------------

    def save_features(self, features: pd.DataFrame, version: str = "v1") -> None:
        buf = io.BytesIO()
        features.to_parquet(buf, index=False)
        buf.seek(0)
        self._gcs.bucket(self.bucket_name).blob(
            self._gcs_path(_gcs_features_path(version))
        ).upload_from_file(buf, content_type="application/octet-stream")

    def load_features(self, version: str = "v1") -> pd.DataFrame:
        gcs_path = self._gcs_path(_gcs_features_path(version))
        blob = self._gcs.bucket(self.bucket_name).blob(gcs_path)
        buf = io.BytesIO()
        try:
            blob.download_to_file(buf)
        except Exception as exc:
            raise FileNotFoundError(
                f"Features versión '{version}' no encontradas en GCS: "
                f"gs://{self.bucket_name}/{gcs_path}"
            ) from exc
        buf.seek(0)
        return pd.read_parquet(buf)

    # ------------------------------------------------------------------
    # Modelos → GCS (model.joblib + metadata.json)
    # ------------------------------------------------------------------

    def save_model(self, pipeline: Any, metadata: dict, version_name: str) -> Path:
        bucket = self._gcs.bucket(self.bucket_name)

        model_buf = io.BytesIO()
        joblib.dump(pipeline, model_buf)
        model_buf.seek(0)
        bucket.blob(
            self._gcs_path(_gcs_model_path(version_name, "model.joblib"))
        ).upload_from_file(model_buf, content_type="application/octet-stream")

        meta_bytes = json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8")
        bucket.blob(
            self._gcs_path(_gcs_model_path(version_name, "metadata.json"))
        ).upload_from_string(meta_bytes, content_type="application/json")

        return Path(version_name)

    # ------------------------------------------------------------------
    # Injury Reports → GCS (PDF bytes)
    # ------------------------------------------------------------------

    def save_raw_injury_report(self, date_str: str, suffix: str, pdf_bytes: bytes) -> None:
        self._gcs.bucket(self.bucket_name).blob(
            self._gcs_path(_gcs_injury_report_path(date_str, suffix))
        ).upload_from_string(pdf_bytes, content_type="application/pdf")

    def load_model(self, version_name: str) -> tuple[Any, dict]:
        bucket = self._gcs.bucket(self.bucket_name)

        model_blob = bucket.blob(
            self._gcs_path(_gcs_model_path(version_name, "model.joblib"))
        )
        meta_blob = bucket.blob(
            self._gcs_path(_gcs_model_path(version_name, "metadata.json"))
        )

        model_buf = io.BytesIO()
        try:
            model_blob.download_to_file(model_buf)
        except Exception as exc:
            raise FileNotFoundError(
                f"Modelo '{version_name}' no encontrado en GCS: "
                f"gs://{self.bucket_name}/{self._gcs_path('models/' + version_name + '/')}"
            ) from exc
        model_buf.seek(0)
        loaded_pipeline = joblib.load(model_buf)

        meta_buf = io.BytesIO()
        meta_blob.download_to_file(meta_buf)
        meta_buf.seek(0)
        metadata = json.loads(meta_buf.read().decode("utf-8"))

        return loaded_pipeline, metadata
