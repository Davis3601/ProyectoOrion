"""Tests unitarios de la lógica pura de ingest_job (Decisión 6, Fase 5b).

Cubre las funciones que toman decisiones sobre si ejecutar cada paso,
la consulta GCS de la versión más reciente del registry, la derivación de
temporada desde el CDN y el guard contra desajustes silenciosos. Sin
dependencias de GCP, nba_api ni settings — todo mockeado o pura.
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from nba_predictor.jobs.ingest_logic import (
    _archive_injury_report,
    _check_season_guard,
    _latest_model_metadata,
    _season_from_raw_schedule,
    _should_rebuild_features,
    _should_retrain,
)


# ---------------------------------------------------------------------------
# _should_retrain — 4 casos de la especificación (Decisión 6)
# ---------------------------------------------------------------------------


class TestShouldRetrain:
    def test_no_previous_model_returns_true(self):
        """Sin modelo previo en el registry → siempre reentrenar."""
        ok, reason = _should_retrain(None, date(2026, 10, 1), 7, False)
        assert ok is True
        assert "sin modelo previo" in reason.lower()

    def test_force_flag_overrides_cadence(self):
        """--force-retrain ignora la cadencia, incluso si fue ayer."""
        ok, reason = _should_retrain(date(2026, 9, 30), date(2026, 10, 1), 7, True)
        assert ok is True
        assert "force" in reason.lower()

    def test_within_cadence_no_retrain(self):
        """3 días desde el último reentrenamiento con cadencia 7 → no reentrenar."""
        ok, reason = _should_retrain(date(2026, 9, 28), date(2026, 10, 1), 7, False)
        assert ok is False
        assert "3" in reason
        assert "sin reentrenamiento" in reason.lower()

    def test_at_cadence_boundary_retrains(self):
        """Exactamente 7 días (>= cadencia) → reentrenar."""
        ok, reason = _should_retrain(date(2026, 9, 24), date(2026, 10, 1), 7, False)
        assert ok is True
        assert "7" in reason


# ---------------------------------------------------------------------------
# _should_rebuild_features — 2 casos
# ---------------------------------------------------------------------------


class TestShouldRebuildFeatures:
    def test_zero_new_no_rebuild(self):
        """0 partidos nuevos → no hay razón para reconstruir el parquet."""
        ok, reason = _should_rebuild_features(0)
        assert ok is False
        assert "0" in reason

    def test_positive_new_rebuilds(self):
        """Cualquier número de partidos nuevos → reconstruir features."""
        ok, reason = _should_rebuild_features(5)
        assert ok is True
        assert "5" in reason


# ---------------------------------------------------------------------------
# _latest_model_metadata — consulta GCS mockeada
# ---------------------------------------------------------------------------


class TestLatestModelMetadata:
    def _make_blob(self, path: str, payload: dict):
        """Crea un mock de Blob GCS con download_as_text que devuelve JSON."""
        blob = MagicMock()
        blob.name = path
        blob.download_as_text.return_value = json.dumps(payload)
        return blob

    def test_no_blobs_returns_none(self):
        """Sin modelos en GCS → None (primer run o bucket vacío)."""
        gcs_client = MagicMock()
        gcs_client.bucket.return_value.list_blobs.return_value = []

        result = _latest_model_metadata(gcs_client, "my-bucket", "")
        assert result is None

    def test_returns_latest_by_version_name(self):
        """Con varias versiones, devuelve la más reciente (máx lexicográfico)."""
        old_meta = {"training_date": "2026-09-10", "version": "v1_logistic_bclean_2026-09-10"}
        new_meta = {"training_date": "2026-10-01", "version": "v1_logistic_bclean_2026-10-01"}

        blobs = [
            self._make_blob(
                "models/v1_logistic_bclean_2026-09-10/metadata.json", old_meta
            ),
            self._make_blob(
                "models/v1_logistic_bclean_2026-10-01/metadata.json", new_meta
            ),
            self._make_blob(
                "models/v1_logistic_bclean_2026-09-10/model.joblib", {}
            ),
        ]

        gcs_client = MagicMock()
        gcs_client.bucket.return_value.list_blobs.return_value = blobs

        result = _latest_model_metadata(gcs_client, "my-bucket", "")
        assert result["training_date"] == "2026-10-01"

    def test_gcs_prefix_forwarded_to_list_blobs(self):
        """El prefijo del bucket se antepone al path del listado."""
        gcs_client = MagicMock()
        bucket_mock = MagicMock()
        gcs_client.bucket.return_value = bucket_mock
        bucket_mock.list_blobs.return_value = []

        _latest_model_metadata(gcs_client, "my-bucket", "myprefix/")

        call_kwargs = bucket_mock.list_blobs.call_args
        prefix_used = call_kwargs[1].get("prefix") or call_kwargs[0][0]
        assert prefix_used.startswith("myprefix/")


# ---------------------------------------------------------------------------
# _season_from_raw_schedule — derivación de temporada desde el JSON del CDN
# ---------------------------------------------------------------------------


def _make_raw_schedule(season_year: str | None = "2026-27") -> dict:
    """Construye un payload mínimo del schedule CDN con el seasonYear dado."""
    if season_year is None:
        return {"leagueSchedule": {"gameDates": []}}
    return {"leagueSchedule": {"seasonYear": season_year, "gameDates": []}}


class TestSeasonFromRawSchedule:
    def test_extracts_season_from_valid_payload(self):
        """Payload canónico CDN → devuelve el seasonYear exacto."""
        raw = _make_raw_schedule("2026-27")
        assert _season_from_raw_schedule(raw) == "2026-27"

    def test_current_season_format(self):
        """Formato de temporada en transición (offseason → nueva temporada)."""
        raw = _make_raw_schedule("2025-26")
        assert _season_from_raw_schedule(raw) == "2025-26"

    def test_missing_season_year_returns_none(self):
        """Si seasonYear está ausente → None (guard debe fallar ruidosamente)."""
        raw = _make_raw_schedule(None)
        assert _season_from_raw_schedule(raw) is None

    def test_missing_league_schedule_key_returns_none(self):
        """Payload sin clave leagueSchedule → None."""
        assert _season_from_raw_schedule({}) is None

    def test_empty_season_year_returns_none(self):
        """seasonYear vacío ('') se trata como ausente → None."""
        raw = {"leagueSchedule": {"seasonYear": "", "gameDates": []}}
        assert _season_from_raw_schedule(raw) is None


# ---------------------------------------------------------------------------
# _check_season_guard — guard contra '0 nuevos' silencioso
# ---------------------------------------------------------------------------


class TestCheckSeasonGuard:
    def test_match_no_played_games_ok(self):
        """
        Temporadas coinciden y no hay partidos jugados → OK.
        Caso típico: offseason, CDN sirve la misma temporada que config.
        """
        should_err, msg = _check_season_guard("2026-27", "2026-27", has_played_games=False)
        assert should_err is False
        assert "ok" in msg.lower() or "coinciden" in msg.lower()

    def test_match_with_played_games_ok(self):
        """
        Temporadas coinciden y hay partidos jugados → OK.
        Caso típico: temporada en curso, pipeline funcionando correctamente.
        """
        should_err, msg = _check_season_guard("2026-27", "2026-27", has_played_games=True)
        assert should_err is False
        assert "ok" in msg.lower() or "coinciden" in msg.lower()

    def test_mismatch_no_played_games_warning(self):
        """
        Desajuste de temporada pero sin partidos jugados en el CDN → no error.
        Caso: pretemporada, CDN actualizó a 2026-27 pero config aún dice 2025-26.
        Inofensivo: no hay datos que perder.
        """
        should_err, msg = _check_season_guard("2025-26", "2026-27", has_played_games=False)
        assert should_err is False
        assert "aviso" in msg.lower() or "inofensivo" in msg.lower()

    def test_mismatch_with_played_games_error(self):
        """
        Desajuste de temporada Y hay partidos jugados → RuntimeError.
        Caso crítico: octubre 2026, CDN sirve 2026-27 con partidos,
        pero alguien revirtió el código a filtrar por config_season='2025-26'
        → el job diría '0 nuevos' silenciosamente perdiendo toda la temporada.
        """
        should_err, msg = _check_season_guard("2025-26", "2026-27", has_played_games=True)
        assert should_err is True
        assert "mismatch" in msg.lower() or "crítico" in msg.lower()
        # El mensaje debe mencionar ambas temporadas para ayudar en el diagnóstico
        assert "2025-26" in msg
        assert "2026-27" in msg


# ---------------------------------------------------------------------------
# _archive_injury_report — Decisión 4 del feed (best-effort, sin parsear)
# ---------------------------------------------------------------------------


class TestArchiveInjuryReport:
    """Verifica el comportamiento best-effort de _archive_injury_report.

    La función encapsula discover + download + save_raw; cualquier excepción
    produce WARNING y retorna False — nunca lanza. Tests con mocks totales:
    sin acceso a red, sin GCP.
    """

    def _make_store(self) -> MagicMock:
        store = MagicMock()
        store.save_raw_injury_report.return_value = None
        return store

    def test_success_calls_save_raw_injury_report(self):
        """Flujo feliz: discover → download → save_raw con argumentos correctos."""
        # _archive_injury_report importa lazy desde nba_predictor.ingestion.injury_report;
        # hay que parchear en el módulo de origen.
        import nba_predictor.ingestion.injury_report as _ir_mod
        from unittest.mock import patch

        store = self._make_store()
        pdf_bytes = b"%PDF fake content"

        with (
            patch.object(_ir_mod, "discover_latest_snapshot", return_value=("https://example.com/report.pdf", "01_15PM")),
            patch.object(_ir_mod, "download_snapshot", return_value=pdf_bytes),
        ):
            result = _archive_injury_report(store, "2026-10-21")

        assert result is True
        store.save_raw_injury_report.assert_called_once_with(
            "2026-10-21", "01_15PM", pdf_bytes
        )

    def test_discover_failure_returns_false_no_raise(self):
        """RuntimeError en discover → WARNING, retorna False, no lanza."""
        import nba_predictor.ingestion.injury_report as _ir_mod
        from unittest.mock import patch

        store = self._make_store()
        with patch.object(
            _ir_mod,
            "discover_latest_snapshot",
            side_effect=RuntimeError("budget agotado"),
        ):
            result = _archive_injury_report(store, "2026-10-21")

        assert result is False
        store.save_raw_injury_report.assert_not_called()

    def test_download_failure_returns_false_no_raise(self):
        """Error de red en download → WARNING, retorna False, no lanza."""
        import nba_predictor.ingestion.injury_report as _ir_mod
        from unittest.mock import patch
        import requests

        store = self._make_store()
        with (
            patch.object(
                _ir_mod,
                "discover_latest_snapshot",
                return_value=("https://example.com/report.pdf", "01_15PM"),
            ),
            patch.object(
                _ir_mod,
                "download_snapshot",
                side_effect=requests.exceptions.ConnectionError("timeout"),
            ),
        ):
            result = _archive_injury_report(store, "2026-10-21")

        assert result is False
        store.save_raw_injury_report.assert_not_called()

    def test_save_failure_returns_false_no_raise(self):
        """Fallo al escribir en el store → WARNING, retorna False, no lanza."""
        import nba_predictor.ingestion.injury_report as _ir_mod
        from unittest.mock import patch

        store = self._make_store()
        store.save_raw_injury_report.side_effect = OSError("disco lleno")
        pdf_bytes = b"%PDF fake content"

        with (
            patch.object(
                _ir_mod,
                "discover_latest_snapshot",
                return_value=("https://example.com/report.pdf", "01_15PM"),
            ),
            patch.object(_ir_mod, "download_snapshot", return_value=pdf_bytes),
        ):
            result = _archive_injury_report(store, "2026-10-21")

        assert result is False

    def test_failure_emits_warning_not_error(self, caplog):
        """El fallo produce WARNING en el log, no ERROR ni excepción."""
        import logging
        import nba_predictor.ingestion.injury_report as _ir_mod
        from unittest.mock import patch

        store = self._make_store()
        with (
            patch.object(
                _ir_mod,
                "discover_latest_snapshot",
                side_effect=RuntimeError("no existe el PDF"),
            ),
            caplog.at_level(logging.WARNING, logger="nba_predictor.jobs.ingest_logic"),
        ):
            _archive_injury_report(store, "2026-10-21")

        assert any("injury report" in r.message.lower() for r in caplog.records)
        assert all(r.levelno <= logging.WARNING for r in caplog.records if "injury" in r.message.lower())
