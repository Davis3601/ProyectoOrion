"""
Tests de CloudDataStore: unitarios (siempre) + integración (pytest -m integration).

Diseño unit:
- FakeGCSClient en memoria → roundtrips reales sin GCP instalado.
- MagicMock para BigQuery → verifica llamadas correctas al adapter.
- Sin google-cloud instalado: CloudDataStore recibe clientes inyectados.

Diseño integración:
- Dataset de prueba: nba_predictor_test.
- Prefijo GCS: integration_test/ (Decisión 5 — no tocar raw/, features/, models/).
- Fixture cleanup corre ANTES y DESPUÉS de cada test (no hereda basura de corridas
  interrumpidas). Si GCP no está configurado, se saltan con mensaje claro.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from nba_predictor.storage.cloud import (
    CloudDataStore,
    _STAGING_EXPIRATION_HOURS,
    _build_merge_sql,
    _full_table_id,
    _gcs_boxscore_path,
    _gcs_features_path,
    _gcs_model_path,
    _staging_table_id,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Fake GCS (almacena bytes en memoria para roundtrips)
# ---------------------------------------------------------------------------


class FakeBlob:
    def __init__(self) -> None:
        self._data: bytes | None = None

    def upload_from_string(self, data: str | bytes, content_type: str = "") -> None:
        self._data = data if isinstance(data, bytes) else data.encode("utf-8")

    def upload_from_file(self, file_obj: object, content_type: str = "") -> None:
        self._data = file_obj.read()  # type: ignore[union-attr]

    def download_to_file(self, file_obj: object) -> None:
        if self._data is None:
            raise Exception("404: Blob not found")
        file_obj.write(self._data)  # type: ignore[union-attr]

    def exists(self) -> bool:
        return self._data is not None


class FakeBucket:
    def __init__(self) -> None:
        self._blobs: dict[str, FakeBlob] = {}

    def blob(self, path: str) -> FakeBlob:
        if path not in self._blobs:
            self._blobs[path] = FakeBlob()
        return self._blobs[path]


class FakeGCSClient:
    def __init__(self) -> None:
        self._buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        if name not in self._buckets:
            self._buckets[name] = FakeBucket()
        return self._buckets[name]


# ---------------------------------------------------------------------------
# Fixtures unitarios
# ---------------------------------------------------------------------------

PROJECT = "test-project"
DATASET = "test_dataset"
BUCKET = "test-bucket"


@pytest.fixture
def fake_gcs() -> FakeGCSClient:
    return FakeGCSClient()


@pytest.fixture
def mock_bq() -> MagicMock:
    return MagicMock()


@pytest.fixture
def store(fake_gcs: FakeGCSClient, mock_bq: MagicMock) -> CloudDataStore:
    return CloudDataStore(
        project_id=PROJECT,
        dataset=DATASET,
        bucket_name=BUCKET,
        _bq_client=mock_bq,
        _gcs_client=fake_gcs,
    )


@pytest.fixture
def gcs_only_store(fake_gcs: FakeGCSClient) -> CloudDataStore:
    """Store solo con GCS real (fake) para tests de features/modelos."""
    return CloudDataStore(
        project_id=PROJECT,
        dataset=DATASET,
        bucket_name=BUCKET,
        _bq_client=MagicMock(),
        _gcs_client=fake_gcs,
    )


# ---------------------------------------------------------------------------
# Funciones puras — GCS paths
# ---------------------------------------------------------------------------


class TestGCSPaths:
    def test_boxscore_path(self) -> None:
        assert _gcs_boxscore_path("0021400001") == "raw/boxscores/0021400001.json"

    def test_features_path(self) -> None:
        assert _gcs_features_path("v1") == "features/features_v1.parquet"
        assert _gcs_features_path("v2") == "features/features_v2.parquet"

    def test_model_path_joblib(self) -> None:
        assert (
            _gcs_model_path("v1_logistic_bclean_2026-08-12", "model.joblib")
            == "models/v1_logistic_bclean_2026-08-12/model.joblib"
        )

    def test_model_path_metadata(self) -> None:
        assert (
            _gcs_model_path("v1_logistic_bclean_2026-08-12", "metadata.json")
            == "models/v1_logistic_bclean_2026-08-12/metadata.json"
        )

    def test_full_table_id(self) -> None:
        assert _full_table_id("proj", "ds", "games") == "proj.ds.games"

    def test_staging_table_id(self) -> None:
        result = _staging_table_id("proj", "ds", "games", "abc123")
        assert result == "proj.ds._games_staging_abc123"

    def test_gcs_prefix_applied(self) -> None:
        """_gcs_path antepone el prefijo de entorno a la ruta canónica."""
        s = CloudDataStore(
            project_id="p",
            dataset="d",
            bucket_name="b",
            gcs_prefix="integration_test/",
            _bq_client=MagicMock(),
            _gcs_client=MagicMock(),
        )
        assert s._gcs_path("raw/boxscores/x.json") == "integration_test/raw/boxscores/x.json"

    def test_gcs_prefix_empty_by_default(self) -> None:
        s = CloudDataStore(
            project_id="p",
            dataset="d",
            bucket_name="b",
            _bq_client=MagicMock(),
            _gcs_client=MagicMock(),
        )
        assert s._gcs_path("raw/boxscores/x.json") == "raw/boxscores/x.json"


# ---------------------------------------------------------------------------
# Función pura — _build_merge_sql
# ---------------------------------------------------------------------------


class TestBuildMergeSQL:
    def test_teams_single_key(self) -> None:
        sql = _build_merge_sql(
            target_id="proj.ds.teams",
            staging_id="proj.ds._teams_staging_abc",
            merge_keys=["team_id"],
            all_columns=["team_id", "abbreviation", "name"],
        )
        expected = (
            "MERGE `proj.ds.teams` AS T\n"
            "USING `proj.ds._teams_staging_abc` AS S\n"
            "ON T.team_id = S.team_id\n"
            "WHEN MATCHED THEN\n"
            "  UPDATE SET\n"
            "    T.abbreviation = S.abbreviation,\n"
            "    T.name = S.name\n"
            "WHEN NOT MATCHED THEN\n"
            "  INSERT (team_id, abbreviation, name)\n"
            "  VALUES (S.team_id, S.abbreviation, S.name)"
        )
        assert sql == expected

    def test_games_single_key(self) -> None:
        cols = ["game_id", "season", "game_date", "home_won"]
        sql = _build_merge_sql(
            target_id="p.d.games",
            staging_id="p.d._games_staging_xyz",
            merge_keys=["game_id"],
            all_columns=cols,
        )
        # game_id en ON, NO en UPDATE SET
        assert "ON T.game_id = S.game_id" in sql
        assert "T.game_id = S.game_id" not in sql.split("WHEN MATCHED")[1]
        # Resto de columnas en UPDATE SET
        assert "T.season = S.season" in sql
        assert "T.game_date = S.game_date" in sql
        assert "T.home_won = S.home_won" in sql
        # INSERT incluye todas
        assert "INSERT (game_id, season, game_date, home_won)" in sql
        assert "VALUES (S.game_id, S.season, S.game_date, S.home_won)" in sql

    def test_team_game_stats_composite_key(self) -> None:
        cols = ["game_id", "team_id", "fgm", "fga"]
        sql = _build_merge_sql(
            target_id="p.d.team_game_stats",
            staging_id="p.d._team_game_stats_staging_xyz",
            merge_keys=["game_id", "team_id"],
            all_columns=cols,
        )
        assert "ON T.game_id = S.game_id AND T.team_id = S.team_id" in sql
        # game_id y team_id NO en UPDATE SET
        matched_part = sql.split("WHEN MATCHED")[1].split("WHEN NOT MATCHED")[0]
        assert "T.game_id" not in matched_part
        assert "T.team_id" not in matched_part
        assert "T.fgm = S.fgm" in matched_part

    def test_player_game_stats_composite_key(self) -> None:
        cols = ["game_id", "player_id", "team_id", "minutes"]
        sql = _build_merge_sql(
            target_id="p.d.player_game_stats",
            staging_id="p.d._player_game_stats_staging_xyz",
            merge_keys=["game_id", "player_id"],
            all_columns=cols,
        )
        assert "ON T.game_id = S.game_id AND T.player_id = S.player_id" in sql
        assert "T.team_id = S.team_id" in sql
        assert "T.minutes = S.minutes" in sql


# ---------------------------------------------------------------------------
# Validación de config
# ---------------------------------------------------------------------------


class TestConfigValidation:
    def test_empty_project_id_raises(self) -> None:
        with pytest.raises(ValueError, match="gcp_project_id"):
            CloudDataStore(
                project_id="",
                dataset="ds",
                bucket_name="bucket",
                _bq_client=MagicMock(),
                _gcs_client=MagicMock(),
            )

    def test_empty_bucket_name_raises(self) -> None:
        with pytest.raises(ValueError, match="gcs_bucket"):
            CloudDataStore(
                project_id="proj",
                dataset="ds",
                bucket_name="",
                _bq_client=MagicMock(),
                _gcs_client=MagicMock(),
            )

    def test_valid_config_ok(self) -> None:
        store = CloudDataStore(
            project_id="p",
            dataset="d",
            bucket_name="b",
            _bq_client=MagicMock(),
            _gcs_client=MagicMock(),
        )
        assert store.project_id == "p"
        assert store.dataset == "d"
        assert store.bucket_name == "b"


# ---------------------------------------------------------------------------
# save_raw_boxscore
# ---------------------------------------------------------------------------


class TestSaveRawBoxscore:
    def test_uploads_to_correct_path(
        self, store: CloudDataStore, fake_gcs: FakeGCSClient
    ) -> None:
        payload = {"gameId": "0021400001", "score": 110}
        store.save_raw_boxscore("0021400001", payload)

        bucket = fake_gcs.bucket(BUCKET)
        blob = bucket.blob("raw/boxscores/0021400001.json")
        assert blob.exists()
        data = json.loads(blob._data.decode("utf-8"))  # type: ignore[union-attr]
        assert data["gameId"] == "0021400001"
        assert data["score"] == 110

    def test_path_is_game_id_based(
        self, store: CloudDataStore, fake_gcs: FakeGCSClient
    ) -> None:
        store.save_raw_boxscore("GAME_XYZ", {"x": 1})
        assert fake_gcs.bucket(BUCKET).blob("raw/boxscores/GAME_XYZ.json").exists()


# ---------------------------------------------------------------------------
# save_features / load_features — roundtrip con FakeGCS
# ---------------------------------------------------------------------------


class TestFeaturesRoundtrip:
    def _sample_features(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "efg_diff": [0.05, -0.03],
                "tov_rate_diff": [0.01, 0.02],
                "home_won": [1, 0],
            }
        )

    def test_roundtrip_default_version(
        self, gcs_only_store: CloudDataStore, fake_gcs: FakeGCSClient
    ) -> None:
        df = self._sample_features()
        gcs_only_store.save_features(df)

        loaded = gcs_only_store.load_features()
        pd.testing.assert_frame_equal(df, loaded, check_like=True)

    def test_roundtrip_custom_version(
        self, gcs_only_store: CloudDataStore, fake_gcs: FakeGCSClient
    ) -> None:
        df = self._sample_features()
        gcs_only_store.save_features(df, version="v2")

        loaded = gcs_only_store.load_features(version="v2")
        pd.testing.assert_frame_equal(df, loaded, check_like=True)

    def test_stored_at_correct_gcs_path(
        self, gcs_only_store: CloudDataStore, fake_gcs: FakeGCSClient
    ) -> None:
        gcs_only_store.save_features(self._sample_features(), version="v1")
        assert fake_gcs.bucket(BUCKET).blob("features/features_v1.parquet").exists()

    def test_load_missing_raises_file_not_found(
        self, gcs_only_store: CloudDataStore
    ) -> None:
        with pytest.raises(FileNotFoundError, match="v99"):
            gcs_only_store.load_features(version="v99")


# ---------------------------------------------------------------------------
# save_model / load_model — roundtrip con FakeGCS
# ---------------------------------------------------------------------------


class TestModelRoundtrip:
    def _make_pipeline(self) -> Pipeline:
        return Pipeline([("scaler", StandardScaler()), ("lr", LogisticRegression())])

    def _sample_metadata(self) -> dict:
        return {
            "model_type": "logistic_bclean",
            "log_loss": 0.63138,
            "features": ["efg_diff", "tov_rate_diff"],
        }

    def test_roundtrip(
        self, gcs_only_store: CloudDataStore, fake_gcs: FakeGCSClient
    ) -> None:
        pipeline = self._make_pipeline()
        meta = self._sample_metadata()

        result_path = gcs_only_store.save_model(pipeline, meta, "v1_test")
        assert isinstance(result_path, Path)
        assert str(result_path) == "v1_test"

        loaded_pipeline, loaded_meta = gcs_only_store.load_model("v1_test")
        assert loaded_meta == meta
        assert isinstance(loaded_pipeline, Pipeline)

    def test_stored_at_correct_gcs_paths(
        self, gcs_only_store: CloudDataStore, fake_gcs: FakeGCSClient
    ) -> None:
        gcs_only_store.save_model(self._make_pipeline(), {}, "my_version")
        bucket = fake_gcs.bucket(BUCKET)
        assert bucket.blob("models/my_version/model.joblib").exists()
        assert bucket.blob("models/my_version/metadata.json").exists()

    def test_load_missing_raises_file_not_found(
        self, gcs_only_store: CloudDataStore
    ) -> None:
        with pytest.raises(FileNotFoundError, match="missing_version"):
            gcs_only_store.load_model("missing_version")


# ---------------------------------------------------------------------------
# save_* BigQuery — verifica MERGE + expiración con MagicMock
# ---------------------------------------------------------------------------


class TestSaveTabular:
    def test_save_games_calls_merge(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        df = pd.DataFrame(
            {"game_id": ["001", "002"], "season": ["2024-25", "2024-25"]}
        )
        store.save_games(df)

        # load_table_from_dataframe debe haberse llamado
        assert mock_bq.load_table_from_dataframe.called
        staging_id = mock_bq.load_table_from_dataframe.call_args.args[1]
        assert "_games_staging_" in staging_id
        assert PROJECT in staging_id
        assert DATASET in staging_id

        # Entre los queries debe haber exactamente un MERGE (puede haber
        # otros queries antes, e.g. CREATE TABLE IF NOT EXISTS)
        queries = [c.args[0] for c in mock_bq.query.call_args_list]
        merge_queries = [q for q in queries if "MERGE" in q]
        assert len(merge_queries) == 1
        assert "games" in merge_queries[0]

        # delete_table debe limpiar la staging con el mismo ID
        deleted_id = mock_bq.delete_table.call_args.args[0]
        assert deleted_id == staging_id

    def test_staging_expiration_is_set(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        """La staging table debe tener expiración configurada ANTES del MERGE.

        Verifica el doble mecanismo anti-basura: expiración automática como
        red de seguridad ante crash, más delete explícito en camino feliz.
        """
        df = pd.DataFrame({"game_id": ["001"], "season": ["2024-25"]})
        store.save_games(df)

        # get_table → update_table con ["expires"] deben haberse llamado
        assert mock_bq.get_table.called, "get_table no llamado — expiración no configurada"
        staging_id = mock_bq.load_table_from_dataframe.call_args.args[1]
        assert mock_bq.get_table.call_args.args[0] == staging_id

        assert mock_bq.update_table.called, "update_table no llamado — expiración no fijada"
        update_fields = mock_bq.update_table.call_args.args[1]
        assert update_fields == ["expires"]

        # El objeto de tabla debe tener .expires como datetime (con timezone)
        table_obj = mock_bq.update_table.call_args.args[0]
        assert isinstance(table_obj.expires, datetime), (
            f"staging_table.expires debe ser datetime, obtuvo {type(table_obj.expires)}"
        )

    def test_expiration_set_before_merge(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        """La expiración debe configurarse ANTES del query de MERGE.

        El MERGE puede tardar varios segundos en producción — si el proceso
        muere durante él, la red de seguridad debe estar activa.
        """
        df = pd.DataFrame({"game_id": ["001"], "season": ["2024-25"]})
        store.save_games(df)

        method_names = [call[0] for call in mock_bq.method_calls]
        idx_update = method_names.index("update_table")
        idx_query = method_names.index("query")
        assert idx_update < idx_query, (
            "update_table (expiración) debe ocurrir antes de query (MERGE); "
            f"ocurrió en posición {idx_update} vs query en {idx_query}"
        )

    def test_target_table_created_before_merge(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        """CREATE TABLE IF NOT EXISTS destino debe ejecutarse antes del MERGE.

        BigQuery MERGE falla con 404 si la tabla destino no existe.
        LocalDataStore crea automáticamente en el primer INSERT OR REPLACE.
        El CREATE TABLE IF NOT EXISTS garantiza paridad de contrato.
        """
        df = pd.DataFrame({"game_id": ["001"], "season": ["2024-25"]})
        store.save_games(df)

        queries = [c.args[0] for c in mock_bq.query.call_args_list]
        create_queries = [q for q in queries if "CREATE TABLE IF NOT EXISTS" in q]
        merge_queries = [q for q in queries if "MERGE" in q]

        assert len(create_queries) == 1, "Debe haber exactamente un CREATE TABLE IF NOT EXISTS"
        assert len(merge_queries) == 1, "Debe haber exactamente un MERGE"

        # El CREATE debe referenciar el destino y la staging como fuente
        staging_id = mock_bq.load_table_from_dataframe.call_args.args[1]
        target_id = _full_table_id(PROJECT, DATASET, "games")
        assert target_id in create_queries[0], "CREATE debe apuntar al destino"
        assert staging_id in create_queries[0], "CREATE usa staging como fuente de esquema"

        # El orden importa: CREATE antes que MERGE
        create_idx = queries.index(create_queries[0])
        merge_idx = queries.index(merge_queries[0])
        assert create_idx < merge_idx, (
            f"CREATE TABLE IF NOT EXISTS (pos {create_idx}) debe preceder al MERGE (pos {merge_idx})"
        )

    def test_staging_expiration_constant_is_positive(self) -> None:
        assert _STAGING_EXPIRATION_HOURS >= 1

    def test_save_teams_uses_team_id_key(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        df = pd.DataFrame(
            {"team_id": [1, 2], "abbreviation": ["LAL", "BOS"], "name": ["Lakers", "Celtics"]}
        )
        store.save_teams(df)
        merge_sql = next(
            q.args[0] for q in mock_bq.query.call_args_list if "MERGE" in q.args[0]
        )
        assert "ON T.team_id = S.team_id" in merge_sql

    def test_save_team_game_stats_composite_key(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        df = pd.DataFrame(
            {"game_id": ["001"], "team_id": [1], "fgm": [40]}
        )
        store.save_team_game_stats(df)
        merge_sql = next(
            q.args[0] for q in mock_bq.query.call_args_list if "MERGE" in q.args[0]
        )
        assert "T.game_id = S.game_id AND T.team_id = S.team_id" in merge_sql

    def test_save_player_game_stats_composite_key(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        df = pd.DataFrame(
            {"game_id": ["001"], "player_id": [99], "team_id": [1], "minutes": [30.0]}
        )
        store.save_player_game_stats(df)
        merge_sql = next(
            q.args[0] for q in mock_bq.query.call_args_list if "MERGE" in q.args[0]
        )
        assert "T.game_id = S.game_id AND T.player_id = S.player_id" in merge_sql

    def test_empty_df_is_noop(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        store.save_games(pd.DataFrame())
        assert not mock_bq.load_table_from_dataframe.called

    def test_staging_ids_are_unique(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        df = pd.DataFrame({"game_id": ["001"], "season": ["2024-25"]})
        store.save_games(df)
        store.save_games(df)
        ids = [
            call.args[1]
            for call in mock_bq.load_table_from_dataframe.call_args_list
        ]
        assert ids[0] != ids[1], "Cada llamada debe generar un staging ID único"


# ---------------------------------------------------------------------------
# existing_game_ids
# ---------------------------------------------------------------------------


class TestExistingGameIds:
    def test_returns_set_from_bq(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        row1, row2 = MagicMock(), MagicMock()
        row1.game_id = "0021400001"
        row2.game_id = "0021400002"
        mock_bq.query.return_value.result.return_value = [row1, row2]

        ids = store.existing_game_ids("2014-15")
        assert ids == {"0021400001", "0021400002"}

    def test_sql_contains_season_filter(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        mock_bq.query.return_value.result.return_value = []
        store.existing_game_ids("2023-24")
        sql = mock_bq.query.call_args.args[0]
        assert "2023-24" in sql
        assert "game_id" in sql

    def test_empty_season_returns_empty_set(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        mock_bq.query.return_value.result.return_value = []
        assert store.existing_game_ids("2099-00") == set()


# ---------------------------------------------------------------------------
# load_games / load_teams (SQL construction)
# ---------------------------------------------------------------------------


class TestLoadQueries:
    def _setup_empty(self, mock_bq: MagicMock) -> None:
        mock_bq.query.return_value.to_dataframe.return_value = pd.DataFrame()

    def test_load_games_no_filter(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        self._setup_empty(mock_bq)
        store.load_games()
        sql = mock_bq.query.call_args.args[0]
        assert "games" in sql
        assert "WHERE" not in sql

    def test_load_games_season_filter(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        self._setup_empty(mock_bq)
        store.load_games(season="2024-25")
        sql = mock_bq.query.call_args.args[0]
        assert "2024-25" in sql

    def test_load_games_date_range(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        self._setup_empty(mock_bq)
        store.load_games(
            start_date=date(2024, 10, 1), end_date=date(2024, 10, 31)
        )
        sql = mock_bq.query.call_args.args[0]
        assert "2024-10-01" in sql
        assert "2024-10-31" in sql

    def test_load_games_converts_game_date(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        mock_bq.query.return_value.to_dataframe.return_value = pd.DataFrame(
            {"game_id": ["001"], "season": ["2024-25"], "game_date": ["2024-10-25"]}
        )
        df = store.load_games()
        assert isinstance(df["game_date"].iloc[0], date)

    def test_load_teams_no_filter(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        self._setup_empty(mock_bq)
        store.load_teams()
        sql = mock_bq.query.call_args.args[0]
        assert "teams" in sql

    def test_load_team_game_stats_joins_games(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        self._setup_empty(mock_bq)
        store.load_team_game_stats(season="2024-25", team_id=1610612747)
        sql = mock_bq.query.call_args.args[0]
        assert "team_game_stats" in sql
        assert "JOIN" in sql
        assert "2024-25" in sql
        assert "1610612747" in sql

    def test_load_player_game_stats_joins_games(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        self._setup_empty(mock_bq)
        store.load_player_game_stats(player_id=2544)
        sql = mock_bq.query.call_args.args[0]
        assert "player_game_stats" in sql
        assert "2544" in sql

    def test_load_team_game_stats_no_season_no_join(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        """Sin season, el SQL no debe contener JOIN — games puede no existir todavía."""
        self._setup_empty(mock_bq)
        store.load_team_game_stats(team_id=1610612747)
        sql = mock_bq.query.call_args.args[0]
        assert "team_game_stats" in sql
        assert "JOIN" not in sql
        assert "1610612747" in sql

    def test_load_player_game_stats_no_season_no_join(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        """Sin season, el SQL no debe contener JOIN — games puede no existir todavía."""
        self._setup_empty(mock_bq)
        store.load_player_game_stats(player_id=2544)
        sql = mock_bq.query.call_args.args[0]
        assert "player_game_stats" in sql
        assert "JOIN" not in sql
        assert "2544" in sql

    def test_load_player_game_stats_with_season_has_join(
        self, store: CloudDataStore, mock_bq: MagicMock
    ) -> None:
        """Con season, el SQL debe JOIN a games para filtrar por temporada."""
        self._setup_empty(mock_bq)
        store.load_player_game_stats(season="2024-25", player_id=2544)
        sql = mock_bq.query.call_args.args[0]
        assert "player_game_stats" in sql
        assert "JOIN" in sql
        assert "2024-25" in sql
        assert "2544" in sql


# ---------------------------------------------------------------------------
# Helpers de limpieza para integración
# ---------------------------------------------------------------------------

_INT_GCS_PREFIX = "integration_test/"
_INT_DATASET = "nba_predictor_test"
_INT_TABLES = ["games", "teams", "team_game_stats", "player_game_stats"]


def _clean_bq(store: CloudDataStore) -> None:
    """Borra las tablas del dataset de prueba. not_found_ok → tolerante a primera corrida."""
    for tbl in _INT_TABLES:
        table_id = f"{store.project_id}.{store.dataset}.{tbl}"
        store._bq.delete_table(table_id, not_found_ok=True)


def _clean_gcs(store: CloudDataStore) -> None:
    """Borra todos los blobs bajo el prefijo de prueba."""
    bucket = store._gcs.bucket(store.bucket_name)
    blobs = list(bucket.list_blobs(prefix=_INT_GCS_PREFIX))
    for blob in blobs:
        try:
            blob.delete()
        except Exception:
            pass  # Ya borrado por otra corrida concurrente


# ---------------------------------------------------------------------------
# Tests de integración — requieren credenciales GCP reales
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIntegration:
    """Tests contra GCP real: dataset nba_predictor_test, prefijo integration_test/.

    Para correr: pytest -m integration
    Requiere: gcp_project_id y gcs_bucket configurados en settings/.env.
    Si no están configurados, los tests se saltan con mensaje explicativo.
    """

    @pytest.fixture
    def store(self) -> CloudDataStore:
        from nba_predictor.config import settings

        if not settings.gcp_project_id or not settings.gcs_bucket:
            pytest.skip(
                "Integración GCP no configurada. "
                "Define NBA_PREDICTOR_GCP_PROJECT_ID y NBA_PREDICTOR_GCS_BUCKET."
            )
        return CloudDataStore(
            project_id=settings.gcp_project_id,
            dataset=_INT_DATASET,
            bucket_name=settings.gcs_bucket,
            gcs_prefix=_INT_GCS_PREFIX,
        )

    @pytest.fixture(autouse=True)
    def cleanup(self, store: CloudDataStore) -> object:
        """Limpieza antes + después para no heredar basura de corridas interrumpidas."""
        # Crear dataset de prueba si no existe
        try:
            store._bq.create_dataset(
                f"{store.project_id}.{store.dataset}", exists_ok=True
            )
        except Exception:
            pass

        _clean_bq(store)
        _clean_gcs(store)
        yield
        _clean_bq(store)
        _clean_gcs(store)

    def test_games_idempotencia_roundtrip(self, store: CloudDataStore) -> None:
        """Test estrella: save×2 + save modificado → load sin duplicados, valor final correcto.

        Verifica la garantía central de la Decisión 3: el MERGE + staging
        produce idempotencia real contra el motor BigQuery, no solo contra mocks.
        """
        df = pd.DataFrame(
            {
                "game_id": ["INTTEST001", "INTTEST002", "INTTEST003"],
                "season": ["9999-00"] * 3,
                "game_date": ["2999-01-01", "2999-01-02", "2999-01-03"],
                "home_team_id": [1, 2, 3],
                "away_team_id": [2, 3, 1],
                "home_pts": [100, 95, 108],
                "away_pts": [95, 100, 102],
                "home_won": [1, 0, 1],
                "neutral_site": [0, 0, 0],
            }
        )
        store.save_games(df)
        store.save_games(df)  # Segundo save: idempotente, no debe duplicar

        df_mod = df.copy()
        df_mod.loc[df_mod["game_id"] == "INTTEST001", "home_pts"] = 120
        store.save_games(df_mod)  # Tercer save con una fila modificada

        result = store.load_games(season="9999-00")

        assert len(result) == 3, (
            f"Esperaba 3 filas (sin duplicados), obtuvo {len(result)}"
        )
        row = result[result["game_id"] == "INTTEST001"].iloc[0]
        assert int(row["home_pts"]) == 120, (
            f"El valor modificado debe persistir: esperaba 120, obtuvo {row['home_pts']}"
        )
        unmodified = result[result["game_id"] == "INTTEST002"].iloc[0]
        assert int(unmodified["home_pts"]) == 95

    def test_team_game_stats_clave_compuesta_roundtrip(
        self, store: CloudDataStore
    ) -> None:
        """Idempotencia con clave compuesta (game_id, team_id).

        Un mismo (game_id, team_id) guardado dos veces → una sola fila.
        Modificar la fila y guardar → valor nuevo, sin duplicados.
        """
        df = pd.DataFrame(
            {
                "game_id": ["INTTEST001", "INTTEST001"],
                "team_id": [1, 2],
                "is_home": [1, 0],
                "fgm": [40, 38],
                "fga": [88, 90],
                "fta": [20, 18],
            }
        )
        store.save_team_game_stats(df)
        store.save_team_game_stats(df)  # Idempotente

        df_mod = df.copy()
        df_mod.loc[df_mod["team_id"] == 1, "fgm"] = 45
        store.save_team_game_stats(df_mod)

        result = store.load_team_game_stats()
        assert len(result) == 2, f"Esperaba 2 filas, obtuvo {len(result)}"
        row = result[result["team_id"] == 1].iloc[0]
        assert int(row["fgm"]) == 45

    def test_raw_boxscore_gcs_roundtrip(self, store: CloudDataStore) -> None:
        """save_raw_boxscore escribe al GCS con la ruta canónica + prefijo de entorno."""
        game_id = "INTTEST_RAW_001"
        payload = {"gameId": game_id, "test": True, "score": 42, "nested": {"a": 1}}

        store.save_raw_boxscore(game_id, payload)

        bucket = store._gcs.bucket(store.bucket_name)
        from nba_predictor.storage.cloud import _gcs_boxscore_path
        gcs_path = store._gcs_path(_gcs_boxscore_path(game_id))
        data = json.loads(bucket.blob(gcs_path).download_as_text())

        assert data == payload, (
            f"Payload recuperado no coincide con el guardado.\n"
            f"Esperado: {payload}\nObtenido: {data}"
        )

    def test_features_gcs_roundtrip(self, store: CloudDataStore) -> None:
        """save_features / load_features: roundtrip exacto incluyendo dtypes."""
        features = pd.DataFrame(
            {
                "efg_diff": [0.05, -0.03, 0.01],
                "tov_rate_diff": [0.01, 0.02, -0.005],
                "off_rating_diff": [3.2, -1.1, 0.5],
                "home_won": [1, 0, 1],
            }
        )
        store.save_features(features, version="inttest")
        loaded = store.load_features(version="inttest")

        pd.testing.assert_frame_equal(
            features, loaded, check_dtype=True,
            obj="features parquet GCS roundtrip",
        )

    def test_model_gcs_roundtrip(self, store: CloudDataStore) -> None:
        """save_model / load_model: pipeline sklearn + metadata recuperados intactos."""
        X = np.array([[1.0, 2.0], [3.0, 4.0], [-1.0, 0.5], [2.0, -1.0]])
        y = np.array([1, 0, 1, 0])

        pipeline = Pipeline(
            [("scaler", StandardScaler()), ("lr", LogisticRegression(random_state=42))]
        )
        pipeline.fit(X, y)

        metadata = {
            "version": "inttest_model",
            "model_type": "logistic",
            "features": ["f1", "f2"],
            "log_loss": 0.65432,
        }

        store.save_model(pipeline, metadata, "inttest_model")
        loaded_pipeline, loaded_meta = store.load_model("inttest_model")

        assert loaded_meta == metadata

        # Las predicciones del pipeline recuperado deben ser idénticas
        proba_original = pipeline.predict_proba(X)
        proba_loaded = loaded_pipeline.predict_proba(X)
        np.testing.assert_array_almost_equal(
            proba_original, proba_loaded, decimal=10,
            err_msg="Las probabilidades del pipeline cargado difieren del original",
        )


# ---------------------------------------------------------------------------
# Tests de get_latest_model_version (Método 16 — ambos adapters)
# ---------------------------------------------------------------------------


class TestGetLatestModelVersion:
    """Método 16 del contrato DataStore: versión más reciente del registry."""

    # ── LocalDataStore ──────────────────────────────────────────────────────

    def test_local_returns_latest_when_multiple_versions(self, tmp_path: Path):
        """LocalDataStore retorna la versión más reciente (mayor fecha ISO)."""
        from nba_predictor.models.registry import VERSION_PREFIX
        from nba_predictor.storage.local import LocalDataStore

        ds = LocalDataStore(
            db_path=tmp_path / "nba.sqlite",
            raw_dir=tmp_path / "raw",
            processed_dir=tmp_path / "processed",
        )
        # Crear dos versiones en el registry local
        (ds._models_dir / f"{VERSION_PREFIX}_2026-08-15").mkdir(parents=True)
        (ds._models_dir / f"{VERSION_PREFIX}_2026-08-22").mkdir(parents=True)

        assert ds.get_latest_model_version() == f"{VERSION_PREFIX}_2026-08-22"

    def test_local_empty_registry_raises(self, tmp_path: Path):
        """LocalDataStore lanza FileNotFoundError si el registry está vacío."""
        from nba_predictor.storage.local import LocalDataStore

        ds = LocalDataStore(
            db_path=tmp_path / "nba.sqlite",
            raw_dir=tmp_path / "raw",
            processed_dir=tmp_path / "processed",
        )
        # _models_dir no existe: no se ha guardado ningún modelo
        with pytest.raises(FileNotFoundError, match="registry"):
            ds.get_latest_model_version()

    # ── CloudDataStore ──────────────────────────────────────────────────────

    def test_cloud_returns_latest_from_gcs(self):
        """CloudDataStore extrae la versión más reciente de los blobs de GCS."""
        from nba_predictor.models.registry import VERSION_PREFIX

        v_old = f"{VERSION_PREFIX}_2026-08-15"
        v_new = f"{VERSION_PREFIX}_2026-08-22"

        class _Blob:
            def __init__(self, name: str) -> None:
                self.name = name

        mock_gcs = MagicMock()
        mock_gcs.bucket.return_value.list_blobs.return_value = [
            _Blob(f"models/{v_old}/model.joblib"),
            _Blob(f"models/{v_old}/metadata.json"),
            _Blob(f"models/{v_new}/model.joblib"),
            _Blob(f"models/{v_new}/metadata.json"),
        ]

        ds = CloudDataStore(
            project_id=PROJECT,
            dataset=DATASET,
            bucket_name=BUCKET,
            _bq_client=MagicMock(),
            _gcs_client=mock_gcs,
        )
        assert ds.get_latest_model_version() == v_new

    def test_cloud_empty_registry_raises(self):
        """CloudDataStore lanza FileNotFoundError si GCS no tiene versiones."""
        mock_gcs = MagicMock()
        mock_gcs.bucket.return_value.list_blobs.return_value = []

        ds = CloudDataStore(
            project_id=PROJECT,
            dataset=DATASET,
            bucket_name=BUCKET,
            _bq_client=MagicMock(),
            _gcs_client=mock_gcs,
        )
        with pytest.raises(FileNotFoundError):
            ds.get_latest_model_version()
