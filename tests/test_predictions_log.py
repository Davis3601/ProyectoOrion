"""
Tests de predictions_log — la EVIDENCIA del criterio de comercialización (13e-2.4).

Cubre las cuatro propiedades que el diseño CERRADO exige:
    1. Escritura en AMBAS implementaciones del Repository (SQLite y BigQuery).
    2. Una fila por partido servido, con los 9 campos del schema.
    3. Append-only: dos servidas del mismo partido = dos filas, jamás un update.
    4. Best-effort: fallo de escritura → 200 íntegro + WARNING (nunca 500).
    5. Día sin partidos → cero filas, sin error.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import nba_predictor.api.server as _server
from nba_predictor.api.daily_predictions import (
    AvailabilityFlag,
    DailyResult,
    GamePrediction,
)
from nba_predictor.api.predictions_log import (
    PREDICTIONS_LOG_FIELDS,
    build_log_rows,
    resolve_model_version,
    resolve_served_by,
    write_predictions_log,
)
from nba_predictor.storage.cloud import (
    _MERGE_KEYS,
    _PREDICTIONS_LOG_SCHEMA,
    _PREDICTIONS_LOG_TABLE,
    CloudDataStore,
)
from nba_predictor.storage.local import LocalDataStore

VERSION = "v1_logistic_bclean_2026-08-22"
SHA = "13358021f558f62de8cdb5acf2e6cf953ae474772b17e7d0613444e112aee7d2"
TARGET_DATE = "2026-10-21"
STAMP = datetime(2026, 10, 21, 19, 0, 2, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gp(
    home: str = "BOS",
    away: str = "LAL",
    game_id: str = "0022600001",
    prob: float = 0.6712,
    home_ids: list[int] | None = None,
    away_ids: list[int] | None = None,
) -> GamePrediction:
    return GamePrediction(
        home_tricode=home,
        away_tricode=away,
        game_date=TARGET_DATE,
        probability_home=prob,
        home_absences=[],
        away_absences=[],
        availability_flag=AvailabilityFlag.OK,
        model_version=VERSION,
        game_id=game_id,
        home_absence_ids=home_ids or [],
        away_absence_ids=away_ids or [],
    )


def _make_result(games=None) -> DailyResult:
    games = games if games is not None else []
    return DailyResult(
        target_date=TARGET_DATE,
        games=games,
        feed_down=False,
        feed_down_reason=None,
        model_version=VERSION if games else None,
    )


def _rows(*games) -> list[dict]:
    return build_log_rows(
        _make_result(list(games)),
        served_by="predictions-api-00005-abc",
        model_version=f"{VERSION}@{SHA}",
        predicted_at_utc=STAMP,
    )


def _mock_store() -> MagicMock:
    store = MagicMock()
    pipeline = MagicMock()
    pipeline.predict_proba.return_value = [[0.33, 0.67]]
    store.load_model.return_value = (
        pipeline,
        {"version": VERSION, "training_data": {"parquet_sha256": SHA}},
    )
    store.get_latest_model_version.return_value = VERSION
    return store


@contextmanager
def _running_client(store: MagicMock):
    with patch("nba_predictor.api.server.get_datastore", return_value=store):
        with TestClient(_server.app) as c:
            yield c


# ---------------------------------------------------------------------------
# (b) Una fila por partido con los 9 campos correctos
# ---------------------------------------------------------------------------


class TestBuildLogRows:
    def test_una_fila_por_partido(self):
        rows = _rows(_make_gp(game_id="0022600001"), _make_gp(game_id="0022600002"))
        assert len(rows) == 2
        assert [r["game_id"] for r in rows] == ["0022600001", "0022600002"]

    def test_los_nueve_campos_del_schema_exactos(self):
        """Ni uno más ni uno menos: el schema está CERRADO."""
        (row,) = _rows(_make_gp())
        assert tuple(row.keys()) == PREDICTIONS_LOG_FIELDS
        assert len(PREDICTIONS_LOG_FIELDS) == 9

    def test_valores_de_la_fila(self):
        (row,) = _rows(_make_gp(home="BOS", away="LAL", prob=0.6712))
        assert row["game_id"] == "0022600001"
        assert row["game_date"] == TARGET_DATE
        assert row["home_team"] == "BOS"
        assert row["away_team"] == "LAL"
        assert row["p_home_win"] == 0.6712
        assert row["model_version"] == f"{VERSION}@{SHA}"
        assert row["predicted_at_utc"] == STAMP
        assert row["served_by"] == "predictions-api-00005-abc"

    def test_absences_applied_lleva_player_ids_por_equipo(self):
        (row,) = _rows(_make_gp(home_ids=[201939, 1628369], away_ids=[]))
        assert json.loads(row["absences_applied"]) == {
            "home": [201939, 1628369],
            "away": [],
        }

    def test_timestamp_es_utc_y_unico_por_servida(self):
        rows = _rows(_make_gp(game_id="1"), _make_gp(game_id="2"))
        assert rows[0]["predicted_at_utc"] == rows[1]["predicted_at_utc"]
        assert rows[0]["predicted_at_utc"].tzinfo is not None
        assert rows[0]["predicted_at_utc"].utcoffset().total_seconds() == 0

    def test_timestamp_por_defecto_es_ahora_en_utc(self):
        before = datetime.now(timezone.utc)
        (row,) = build_log_rows(
            _make_result([_make_gp()]), served_by="local", model_version=VERSION
        )
        after = datetime.now(timezone.utc)
        assert before <= row["predicted_at_utc"] <= after

    def test_sin_resultado_del_partido_en_el_schema(self):
        """El grading es un JOIN posterior; el log jamás guarda el marcador."""
        (row,) = _rows(_make_gp())
        assert not any(
            k in row for k in ("home_won", "result", "final_score", "graded")
        )


class TestResolveHelpers:
    def test_served_by_desde_k_revision(self, monkeypatch):
        monkeypatch.setenv("K_REVISION", "predictions-api-00007-xyz")
        assert resolve_served_by() == "predictions-api-00007-xyz"

    def test_served_by_default_local(self, monkeypatch):
        monkeypatch.delenv("K_REVISION", raising=False)
        assert resolve_served_by() == "local"

    def test_model_version_es_id_mas_hash(self):
        meta = {"training_data": {"parquet_sha256": SHA}}
        assert resolve_model_version(VERSION, meta) == f"{VERSION}@{SHA}"

    def test_model_version_sin_hash_degrada_al_id(self):
        assert resolve_model_version(VERSION, {}) == VERSION
        assert resolve_model_version(VERSION, None) == VERSION

    def test_model_version_sin_version_es_none(self):
        assert resolve_model_version(None, {}) is None


# ---------------------------------------------------------------------------
# (a) Escritura en ambas implementaciones del Repository
# ---------------------------------------------------------------------------


class TestLocalDataStoreWrite:
    @pytest.fixture()
    def store(self, tmp_path) -> LocalDataStore:
        return LocalDataStore(
            db_path=tmp_path / "nba.sqlite",
            raw_dir=tmp_path / "raw",
            processed_dir=tmp_path / "processed",
        )

    def _read(self, store: LocalDataStore) -> list[sqlite3.Row]:
        with sqlite3.connect(store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                "SELECT * FROM predictions_log ORDER BY game_id"
            ).fetchall()

    def test_escribe_una_fila_por_partido(self, store):
        store.save_predictions_log(_rows(_make_gp(game_id="1"), _make_gp(game_id="2")))
        rows = self._read(store)
        assert len(rows) == 2
        assert [r["game_id"] for r in rows] == ["1", "2"]

    def test_los_nueve_campos_persisten(self, store):
        store.save_predictions_log(_rows(_make_gp(home_ids=[201939])))
        (row,) = self._read(store)
        assert set(row.keys()) == set(PREDICTIONS_LOG_FIELDS)
        assert row["home_team"] == "BOS"
        assert row["away_team"] == "LAL"
        assert row["p_home_win"] == pytest.approx(0.6712)
        assert row["model_version"] == f"{VERSION}@{SHA}"
        assert row["served_by"] == "predictions-api-00005-abc"
        assert json.loads(row["absences_applied"])["home"] == [201939]

    def test_predicted_at_utc_se_guarda_como_iso8601(self, store):
        store.save_predictions_log(_rows(_make_gp()))
        (row,) = self._read(store)
        assert row["predicted_at_utc"] == STAMP.isoformat()
        # Round-trip: el texto vuelve a ser el mismo instante UTC.
        assert datetime.fromisoformat(row["predicted_at_utc"]) == STAMP

    def test_append_only_dos_servidas_dos_filas(self, store):
        """Misma predicción servida dos veces = dos hechos. Jamás un UPSERT."""
        store.save_predictions_log(_rows(_make_gp()))
        store.save_predictions_log(
            build_log_rows(
                _make_result([_make_gp()]),
                served_by="predictions-api-00005-abc",
                model_version=f"{VERSION}@{SHA}",
                predicted_at_utc=datetime(2026, 10, 21, 20, 30, tzinfo=timezone.utc),
            )
        )
        rows = self._read(store)
        assert len(rows) == 2
        assert {r["predicted_at_utc"] for r in rows} == {
            STAMP.isoformat(),
            datetime(2026, 10, 21, 20, 30, tzinfo=timezone.utc).isoformat(),
        }

    def test_lista_vacia_es_noop(self, store):
        store.save_predictions_log([])
        assert self._read(store) == []


class TestCloudDataStoreWrite:
    def _store(self, bq: MagicMock) -> CloudDataStore:
        return CloudDataStore(
            project_id="proj",
            dataset="nba_predictor",
            bucket_name="bucket",
            _bq_client=bq,
            _gcs_client=MagicMock(),
        )

    def test_carga_append_a_la_tabla_correcta(self):
        bq = MagicMock()
        self._store(bq).save_predictions_log(_rows(_make_gp(game_id="1"), _make_gp(game_id="2")))

        bq.load_table_from_dataframe.assert_called_once()
        df, table_id = bq.load_table_from_dataframe.call_args[0][:2]
        assert table_id == f"proj.nba_predictor.{_PREDICTIONS_LOG_TABLE}"
        assert len(df) == 2
        assert list(df.columns) == list(PREDICTIONS_LOG_FIELDS)

    def test_no_usa_merge_ni_staging(self):
        """Append-only: MERGE tiene rama UPDATE, y el log es intocable."""
        bq = MagicMock()
        self._store(bq).save_predictions_log(_rows(_make_gp()))
        bq.query.assert_not_called()
        bq.delete_table.assert_not_called()
        assert _PREDICTIONS_LOG_TABLE not in _MERGE_KEYS

    def test_espera_a_que_el_job_termine(self):
        bq = MagicMock()
        self._store(bq).save_predictions_log(_rows(_make_gp()))
        bq.load_table_from_dataframe.return_value.result.assert_called_once()

    def test_lista_vacia_no_toca_bigquery(self):
        bq = MagicMock()
        self._store(bq).save_predictions_log([])
        bq.load_table_from_dataframe.assert_not_called()

    def test_schema_declarado_cubre_los_nueve_campos(self):
        assert [name for name, _ in _PREDICTIONS_LOG_SCHEMA] == list(PREDICTIONS_LOG_FIELDS)
        types = dict(_PREDICTIONS_LOG_SCHEMA)
        assert types["p_home_win"] == "FLOAT"
        assert types["predicted_at_utc"] == "TIMESTAMP"
        assert types["game_date"] == "DATE"


# ---------------------------------------------------------------------------
# (c) Best-effort en el wrapper
# ---------------------------------------------------------------------------


class TestWriteBestEffort:
    def test_fallo_no_propaga_y_deja_warning(self, caplog):
        store = MagicMock()
        store.save_predictions_log.side_effect = RuntimeError("BigQuery caído")
        with caplog.at_level(logging.WARNING):
            ok = write_predictions_log(store, _rows(_make_gp()))
        assert ok is False
        assert "predictions_log" in caplog.text

    def test_exito_devuelve_true(self):
        store = MagicMock()
        assert write_predictions_log(store, _rows(_make_gp())) is True

    def test_sin_filas_no_llama_al_store(self):
        store = MagicMock()
        assert write_predictions_log(store, []) is True
        store.save_predictions_log.assert_not_called()


# ---------------------------------------------------------------------------
# Integración con el endpoint: (c) 200 íntegro, (d) día sin partidos
# ---------------------------------------------------------------------------


class TestEndpointIntegration:
    def test_servida_normal_escribe_una_fila_por_partido(self):
        store = _mock_store()
        result = _make_result([_make_gp(game_id="1"), _make_gp(game_id="2")])
        with _running_client(store) as client:
            with patch("nba_predictor.api.server.build_daily_predictions", return_value=result):
                resp = client.get(f"/predictions/today?date={TARGET_DATE}")

        assert resp.status_code == 200
        store.save_predictions_log.assert_called_once()
        rows = store.save_predictions_log.call_args[0][0]
        assert len(rows) == 2
        assert all(tuple(r.keys()) == PREDICTIONS_LOG_FIELDS for r in rows)

    def test_model_version_registrado_es_id_mas_hash(self):
        store = _mock_store()
        result = _make_result([_make_gp()])
        with _running_client(store) as client:
            with patch("nba_predictor.api.server.build_daily_predictions", return_value=result):
                client.get(f"/predictions/today?date={TARGET_DATE}")
        (rows,) = store.save_predictions_log.call_args[0]
        assert rows[0]["model_version"] == f"{VERSION}@{SHA}"

    def test_served_by_desde_k_revision(self, monkeypatch):
        monkeypatch.setenv("K_REVISION", "predictions-api-00009-zzz")
        store = _mock_store()
        result = _make_result([_make_gp()])
        with _running_client(store) as client:
            with patch("nba_predictor.api.server.build_daily_predictions", return_value=result):
                client.get(f"/predictions/today?date={TARGET_DATE}")
        (rows,) = store.save_predictions_log.call_args[0]
        assert rows[0]["served_by"] == "predictions-api-00009-zzz"

    def test_fallo_de_escritura_sirve_200_integro_con_warning(self, caplog):
        """Contrato best-effort (CERRADO 2026-08-26): el log falla, el canal no."""
        store = _mock_store()
        store.save_predictions_log.side_effect = RuntimeError("BigQuery caído")
        result = _make_result([_make_gp()])

        with caplog.at_level(logging.WARNING):
            with _running_client(store) as client:
                with patch(
                    "nba_predictor.api.server.build_daily_predictions", return_value=result
                ):
                    resp = client.get(f"/predictions/today?date={TARGET_DATE}")

        assert resp.status_code == 200
        body = resp.json()
        assert "message" in body and "data" in body
        assert len(body["data"]["games"]) == 1
        assert "BOS" in body["message"]
        assert "predictions_log" in caplog.text

    def test_dia_sin_partidos_cero_filas_sin_error(self):
        """Heartbeat de descanso: nada que registrar, y nada que falle."""
        store = _mock_store()
        with _running_client(store) as client:
            with patch(
                "nba_predictor.api.server.build_daily_predictions",
                return_value=_make_result([]),
            ):
                resp = client.get(f"/predictions/today?date={TARGET_DATE}")

        assert resp.status_code == 200
        assert "Sin partidos hoy" in resp.json()["message"]
        store.save_predictions_log.assert_not_called()

    def test_la_escritura_no_altera_la_respuesta(self):
        """El payload servido es idéntico con y sin fallo de log."""
        result = _make_result([_make_gp()])

        ok_store = _mock_store()
        with _running_client(ok_store) as client:
            with patch("nba_predictor.api.server.build_daily_predictions", return_value=result):
                ok_body = client.get(f"/predictions/today?date={TARGET_DATE}").json()

        ko_store = _mock_store()
        ko_store.save_predictions_log.side_effect = RuntimeError("boom")
        with _running_client(ko_store) as client:
            with patch("nba_predictor.api.server.build_daily_predictions", return_value=result):
                ko_body = client.get(f"/predictions/today?date={TARGET_DATE}").json()

        assert ok_body == ko_body
