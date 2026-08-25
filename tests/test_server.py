"""
Tests del servidor FastAPI — capa thin sobre daily_predictions.py.

Prueba rutas, códigos HTTP y el contrato de respuesta {message, data}.
Los tests de lógica de negocio (escenarios de degradación, formato del mensaje)
están en test_daily_predictions.py — aquí solo interesa el comportamiento del
servidor como capa HTTP.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock, patch

import nba_predictor.api.server as _server
from fastapi.testclient import TestClient
from nba_predictor.api.daily_predictions import (
    AvailabilityFlag,
    DailyResult,
    GamePrediction,
)

VERSION = "v1_logistic_bclean_2026-08-22"
TARGET_DATE = "2026-10-15"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gp(home: str = "BOS", away: str = "LAL", feed_down: bool = False) -> GamePrediction:
    return GamePrediction(
        home_tricode=home,
        away_tricode=away,
        game_date=TARGET_DATE,
        probability_home=0.67,
        home_absences=[],
        away_absences=[],
        availability_flag=AvailabilityFlag.FEED_DOWN if feed_down else AvailabilityFlag.OK,
        model_version=VERSION,
    )


def _make_result(games=None, feed_down: bool = False) -> DailyResult:
    games = games or []
    return DailyResult(
        target_date=TARGET_DATE,
        games=games,
        feed_down=feed_down,
        feed_down_reason="error" if feed_down else None,
        model_version=VERSION if games else None,
    )


def _mock_store() -> MagicMock:
    store = MagicMock()
    pipeline = MagicMock()
    pipeline.predict_proba.return_value = [[0.33, 0.67]]
    store.load_model.return_value = (pipeline, {"version": VERSION})
    return store


@contextmanager
def _running_client(raise_server_exceptions: bool = True):
    """TestClient con startup mockeado (sin acceso a disco ni modelo real)."""
    with (
        patch("nba_predictor.api.server.get_datastore", return_value=_mock_store()),
        patch("nba_predictor.api.server._discover_latest_version", return_value=VERSION),
    ):
        with TestClient(_server.app, raise_server_exceptions=raise_server_exceptions) as c:
            yield c


# ---------------------------------------------------------------------------
# /predictions/today
# ---------------------------------------------------------------------------


class TestPredictionsToday:
    def test_normal_200_message_and_data_present(self):
        """Caso normal: 200 con message y data; data contiene games."""
        result = _make_result(games=[_make_gp()])
        with _running_client() as client:
            with patch("nba_predictor.api.server.build_daily_predictions", return_value=result):
                resp = client.get(f"/predictions/today?date={TARGET_DATE}")
        assert resp.status_code == 200
        body = resp.json()
        assert "message" in body
        assert "data" in body
        assert len(body["data"]["games"]) == 1

    def test_rest_day_200_sin_partidos(self):
        """Día de descanso → 200 con el mensaje de descanso en message."""
        result = _make_result(games=[])
        with _running_client() as client:
            with patch("nba_predictor.api.server.build_daily_predictions", return_value=result):
                resp = client.get(f"/predictions/today?date={TARGET_DATE}")
        assert resp.status_code == 200
        body = resp.json()
        assert "Sin partidos hoy" in body["message"]
        assert body["data"]["games"] == []

    def test_feed_down_200_con_flag(self):
        """Feed caído → 200 con feed_down=true en data y aviso en message."""
        result = _make_result(games=[_make_gp(feed_down=True)], feed_down=True)
        with _running_client() as client:
            with patch("nba_predictor.api.server.build_daily_predictions", return_value=result):
                resp = client.get(f"/predictions/today?date={TARGET_DATE}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["feed_down"] is True
        assert "Reporte de lesiones" in body["message"]

    def test_hard_failure_returns_500(self):
        """Excepción no manejada del núcleo → 500 (fallo duro, no se traga)."""
        with _running_client(raise_server_exceptions=False) as client:
            with patch(
                "nba_predictor.api.server.build_daily_predictions",
                side_effect=RuntimeError("schedule inaccesible"),
            ):
                resp = client.get(f"/predictions/today?date={TARGET_DATE}")
        assert resp.status_code == 500

    def test_date_query_param_forwarded_correctly(self):
        """?date=YYYY-MM-DD se parsea y llega como date al núcleo."""
        result = _make_result(games=[_make_gp()])
        with _running_client() as client:
            with patch(
                "nba_predictor.api.server.build_daily_predictions", return_value=result
            ) as mock_bdp:
                resp = client.get("/predictions/today?date=2026-11-01")
        assert resp.status_code == 200
        assert mock_bdp.call_args.kwargs["target_date"] == date(2026, 11, 1)

    def test_invalid_date_returns_422(self):
        """?date inválido → 422 sin invocar el núcleo."""
        with _running_client() as client:
            resp = client.get("/predictions/today?date=no-es-fecha")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_ok_con_model_version(self):
        """/health → 200 con status=ok y model_version cuando el startup tuvo éxito."""
        with _running_client() as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["model_version"] == VERSION

    def test_health_503_cuando_version_ausente(self):
        """Si version_name no está en app.state → 503, no 'ok' falso."""
        with _running_client() as client:
            original = _server.app.state.version_name
            _server.app.state.version_name = None
            try:
                resp = client.get("/health")
            finally:
                _server.app.state.version_name = original
        assert resp.status_code == 503
