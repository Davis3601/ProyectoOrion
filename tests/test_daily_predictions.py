"""
Tests para nba_predictor/api/daily_predictions.py — 13e-2.

Cubre los escenarios de Decisión 13e-2.5:
    1. Sin partidos → DailyResult vacío + mensaje de descanso.
    2. Feed caído → FEED_DOWN flag + advertencia global en el mensaje.
    3. NYS al invocar → flag NYS por equipo afectado; otros OK.
    4. Fallo duro → excepción propagada (modelo inaccesible).
    5. Solo Out cuenta → Doubtful/Questionable ignorados en ausencias.

Estrategia de mocks:
    - scheduled_games y player_map se pasan directamente (sin CDN).
    - discover_latest_snapshot / download_snapshot / parse_pdf: parche en
      nba_predictor.ingestion.injury_report (módulo fuente).
    - compute_live_features: parche en nba_predictor.features.live_lookup.
    - DataStore: MagicMock con load_teams() y load_model() configurados.
"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from nba_predictor.api.daily_predictions import (
    AvailabilityFlag,
    DailyResult,
    GamePrediction,
    _DISCLAIMER,
    _FEED_DOWN_MSG,
    _et_to_cdmx_str,
    build_daily_predictions,
    format_daily_message,
)
from nba_predictor.ingestion.future_schedule import ScheduledGame
from nba_predictor.ingestion.injury_report import InjuryRow, InjuryStatus, NysEntry
from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS


# ---------------------------------------------------------------------------
# Constantes de test
# ---------------------------------------------------------------------------

TARGET_DATE = date(2026, 10, 15)
TARGET_DATE_MDY = "10/15/2026"
VERSION = "v1_logistic_bclean_2026-08-22"

GAME_BOS_LAL = ScheduledGame(
    game_id="0022600001",
    game_date=TARGET_DATE,
    home_team_id=1610612738,  # BOS
    away_team_id=1610612747,  # LAL
    home_tricode="BOS",
    away_tricode="LAL",
    season="2026-27",
)

GAME_MIA_GSW = ScheduledGame(
    game_id="0022600002",
    game_date=TARGET_DATE,
    home_team_id=1610612748,  # MIA
    away_team_id=1610612744,  # GSW
    home_tricode="MIA",
    away_tricode="GSW",
    season="2026-27",
)

TEAMS_DF = pd.DataFrame([
    {"team_id": 1610612738, "abbreviation": "BOS", "name": "Boston Celtics"},
    {"team_id": 1610612747, "abbreviation": "LAL", "name": "Los Angeles Lakers"},
    {"team_id": 1610612748, "abbreviation": "MIA", "name": "Miami Heat"},
    {"team_id": 1610612744, "abbreviation": "GSW", "name": "Golden State Warriors"},
])

PLAYER_MAP = {
    101: "Jaylen Brown",
    202: "LeBron James",
    303: "Jimmy Butler",
}

_NULL_FEATURES: dict = {col: 0.0 for col in OFFICIAL_LOGISTIC_COLS}

_CLF_PATCH = "nba_predictor.features.live_lookup.compute_live_features"
_IR_MODULE = "nba_predictor.ingestion.injury_report"


# ---------------------------------------------------------------------------
# Factories de fixtures
# ---------------------------------------------------------------------------


def _make_store(proba: float = 0.67) -> MagicMock:
    store = MagicMock()
    store.load_teams.return_value = TEAMS_DF.copy()
    pipeline = MagicMock()
    pipeline.predict_proba.return_value = np.array([[1 - proba, proba]])
    store.load_model.return_value = (pipeline, {"version": VERSION})
    return store


def _make_pdf_row(
    team: str,
    player: str,
    status: InjuryStatus,
    game_date: str = TARGET_DATE_MDY,
) -> InjuryRow:
    return InjuryRow(
        game_date=game_date,
        game_time="07:30(ET)",
        matchup="BOS@LAL",
        team=team,
        player_name=player,
        status=status,
        reason="Injury",
    )


def _make_nys(team: str, game_date: str = TARGET_DATE_MDY) -> NysEntry:
    return NysEntry(team=team, game_date=game_date)


class _FeedPatch:
    """Parchea discover_latest_snapshot / download_snapshot / parse_pdf."""

    def __init__(self, rows: list[InjuryRow], nys: list[NysEntry]):
        self._rows = rows
        self._nys = nys
        self._patches: list = []

    def __enter__(self) -> "_FeedPatch":
        p1 = patch(
            f"{_IR_MODULE}.discover_latest_snapshot",
            return_value=("https://fake.url/Injury.pdf", "01_15PM"),
        )
        p2 = patch(f"{_IR_MODULE}.download_snapshot", return_value=b"%PDF-fake")
        p3 = patch(f"{_IR_MODULE}.parse_pdf", return_value=(self._rows, self._nys))
        self._patches = [p1, p2, p3]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *_) -> None:
        for p in self._patches:
            p.stop()


def _make_gp_ok(
    home: str = "BOS",
    away: str = "LAL",
    prob: float = 0.67,
    home_abs: list[str] | None = None,
    away_abs: list[str] | None = None,
    tip_off_cdmx: str | None = None,
) -> GamePrediction:
    return GamePrediction(
        home_tricode=home,
        away_tricode=away,
        game_date="2026-10-15",
        probability_home=prob,
        home_absences=home_abs or [],
        away_absences=away_abs or [],
        availability_flag=AvailabilityFlag.OK,
        model_version=VERSION,
        tip_off_cdmx=tip_off_cdmx,
    )


def _make_gp_feed_down(home: str = "BOS", away: str = "LAL", prob: float = 0.67) -> GamePrediction:
    return GamePrediction(
        home_tricode=home,
        away_tricode=away,
        game_date="2026-10-15",
        probability_home=prob,
        home_absences=[],
        away_absences=[],
        availability_flag=AvailabilityFlag.FEED_DOWN,
        model_version=VERSION,
    )


def _make_gp_nys(
    home: str = "MIA",
    away: str = "GSW",
    prob: float = 0.54,
    nys_tricodes: list[str] | None = None,
) -> GamePrediction:
    return GamePrediction(
        home_tricode=home,
        away_tricode=away,
        game_date="2026-10-15",
        probability_home=prob,
        home_absences=[],
        away_absences=[],
        availability_flag=AvailabilityFlag.NYS,
        model_version=VERSION,
        nys_tricodes=nys_tricodes or ["GSW"],
    )


# ---------------------------------------------------------------------------
# TestEtToCdmxStr
# ---------------------------------------------------------------------------


class TestEtToCdmxStr:
    """Conversión ET → CDMX."""

    def test_winter_et_minus_one(self):
        """EST (UTC-5) → CDMX (UTC-6): diferencia 1 hora; 19:30 ET → 18:30 CDMX."""
        # Enero: New York en EST (UTC-5), Mexico City en CST (UTC-6)
        dt = datetime(2026, 1, 15, 19, 30)  # 7:30 PM ET naive → EST
        result = _et_to_cdmx_str(dt)
        assert result == "18:30 CDMX"

    def test_summer_et_minus_two(self):
        """EDT (UTC-4) → CDMX (UTC-6): diferencia 2 horas; 19:30 ET → 17:30 CDMX."""
        # Octubre: New York en EDT (UTC-4), Mexico City en CDT-histórico = UTC-5...
        # pero México abolió DST en 2023 → permanentemente UTC-6.
        # Por tanto: 19:30 EDT (UTC-4) → 17:30 UTC-6
        dt = datetime(2026, 10, 15, 19, 30)  # octubre → NY en EDT
        result = _et_to_cdmx_str(dt)
        assert result == "17:30 CDMX"

    def test_no_leading_zero_on_hour(self):
        """La hora no lleva cero adelante: '7:30 CDMX', no '07:30 CDMX'."""
        dt = datetime(2026, 1, 15, 8, 30)  # 8:30 EST → 7:30 CDMX
        result = _et_to_cdmx_str(dt)
        assert result == "7:30 CDMX"

    def test_midnight_minutes(self):
        """Los minutos siempre usan dos dígitos: ':00', ':05', ':30'."""
        dt = datetime(2026, 1, 15, 20, 0)  # 20:00 EST → 19:00 CDMX
        assert _et_to_cdmx_str(dt) == "19:00 CDMX"

        dt2 = datetime(2026, 1, 15, 20, 5)
        assert _et_to_cdmx_str(dt2) == "19:05 CDMX"


# ---------------------------------------------------------------------------
# TestBuildDailyPredictions
# ---------------------------------------------------------------------------


class TestBuildDailyPredictions:

    def test_rest_day_returns_empty_games(self):
        store = _make_store()
        result = build_daily_predictions(
            TARGET_DATE, store, season="2026-27",
            scheduled_games=[], version_name=VERSION,
            player_map=PLAYER_MAP, save_injury_raw=False,
        )
        assert result.games == []
        assert result.feed_down is False
        assert result.model_version is None
        store.load_teams.assert_not_called()
        store.load_model.assert_not_called()

    def test_feed_down_sets_flag_on_all_games(self):
        store = _make_store(proba=0.67)
        with patch(_CLF_PATCH, return_value=_NULL_FEATURES):
            with patch(f"{_IR_MODULE}.discover_latest_snapshot",
                       side_effect=RuntimeError("budget exhausted")):
                result = build_daily_predictions(
                    TARGET_DATE, store, season="2026-27",
                    scheduled_games=[GAME_BOS_LAL], version_name=VERSION,
                    player_map=PLAYER_MAP, save_injury_raw=False,
                )
        assert result.feed_down is True
        assert "budget exhausted" in result.feed_down_reason
        gp = result.games[0]
        assert gp.availability_flag == AvailabilityFlag.FEED_DOWN
        assert gp.nys_tricodes == []

    def test_normal_feed_ok_with_out_absence(self):
        store = _make_store(proba=0.67)
        rows = [_make_pdf_row("BostonCeltics", "Brown,Jaylen", InjuryStatus.OUT)]
        with _FeedPatch(rows, []):
            with patch(_CLF_PATCH, return_value=_NULL_FEATURES):
                result = build_daily_predictions(
                    TARGET_DATE, store, season="2026-27",
                    scheduled_games=[GAME_BOS_LAL], version_name=VERSION,
                    player_map=PLAYER_MAP, save_injury_raw=False,
                )
        gp = result.games[0]
        assert gp.availability_flag == AvailabilityFlag.OK
        assert "Jaylen Brown" in gp.home_absences
        assert gp.away_absences == []

    def test_nys_flag_only_on_affected_team(self):
        store = _make_store(proba=0.55)
        nys = [_make_nys("GoldenStateWarriors")]
        with _FeedPatch([], nys):
            with patch(_CLF_PATCH, return_value=_NULL_FEATURES):
                result = build_daily_predictions(
                    TARGET_DATE, store, season="2026-27",
                    scheduled_games=[GAME_BOS_LAL, GAME_MIA_GSW],
                    version_name=VERSION, player_map=PLAYER_MAP, save_injury_raw=False,
                )
        bos_lal = next(g for g in result.games if g.home_tricode == "BOS")
        mia_gsw = next(g for g in result.games if g.home_tricode == "MIA")
        assert bos_lal.availability_flag == AvailabilityFlag.OK
        assert mia_gsw.availability_flag == AvailabilityFlag.NYS
        assert "GSW" in mia_gsw.nys_tricodes

    def test_only_out_counts_doubtful_ignored(self):
        store = _make_store(proba=0.55)
        rows = [
            _make_pdf_row("LosAngelesLakers", "James,LeBron", InjuryStatus.DOUBTFUL),
            _make_pdf_row("LosAngelesLakers", "Davis,Anthony", InjuryStatus.QUESTIONABLE),
        ]
        with _FeedPatch(rows, []):
            with patch(_CLF_PATCH, return_value=_NULL_FEATURES):
                result = build_daily_predictions(
                    TARGET_DATE, store, season="2026-27",
                    scheduled_games=[GAME_BOS_LAL], version_name=VERSION,
                    player_map=PLAYER_MAP, save_injury_raw=False,
                )
        assert result.games[0].away_absences == []

    def test_nys_for_different_date_not_applied(self):
        store = _make_store(proba=0.55)
        nys = [NysEntry(team="BostonCeltics", game_date="10/16/2026")]
        with _FeedPatch([], nys):
            with patch(_CLF_PATCH, return_value=_NULL_FEATURES):
                result = build_daily_predictions(
                    TARGET_DATE, store, season="2026-27",
                    scheduled_games=[GAME_BOS_LAL], version_name=VERSION,
                    player_map=PLAYER_MAP, save_injury_raw=False,
                )
        assert result.games[0].availability_flag == AvailabilityFlag.OK
        assert result.games[0].nys_tricodes == []

    def test_out_for_different_date_not_applied(self):
        store = _make_store(proba=0.55)
        rows = [_make_pdf_row("BostonCeltics", "Brown,Jaylen", InjuryStatus.OUT,
                              game_date="10/16/2026")]
        with _FeedPatch(rows, []):
            with patch(_CLF_PATCH, return_value=_NULL_FEATURES):
                result = build_daily_predictions(
                    TARGET_DATE, store, season="2026-27",
                    scheduled_games=[GAME_BOS_LAL], version_name=VERSION,
                    player_map=PLAYER_MAP, save_injury_raw=False,
                )
        assert result.games[0].home_absences == []

    def test_save_injury_raw_called_when_enabled(self):
        store = _make_store(proba=0.67)
        with _FeedPatch([], []):
            with patch(_CLF_PATCH, return_value=_NULL_FEATURES):
                build_daily_predictions(
                    TARGET_DATE, store, season="2026-27",
                    scheduled_games=[GAME_BOS_LAL], version_name=VERSION,
                    player_map=PLAYER_MAP, save_injury_raw=True,
                )
        store.save_raw_injury_report.assert_called_once_with(
            "2026-10-15", "01_15PM", b"%PDF-fake"
        )

    def test_save_injury_raw_not_called_when_disabled(self):
        store = _make_store(proba=0.67)
        with _FeedPatch([], []):
            with patch(_CLF_PATCH, return_value=_NULL_FEATURES):
                build_daily_predictions(
                    TARGET_DATE, store, season="2026-27",
                    scheduled_games=[GAME_BOS_LAL], version_name=VERSION,
                    player_map=PLAYER_MAP, save_injury_raw=False,
                )
        store.save_raw_injury_report.assert_not_called()

    def test_save_raw_failure_does_not_set_feed_down(self):
        """Fallo de save_raw_injury_report → feed_down sigue False (best-effort, 13e-2)."""
        store = _make_store(proba=0.67)
        store.save_raw_injury_report.side_effect = OSError("disco lleno")
        with _FeedPatch([], []):
            with patch(_CLF_PATCH, return_value=_NULL_FEATURES):
                result = build_daily_predictions(
                    TARGET_DATE, store, season="2026-27",
                    scheduled_games=[GAME_BOS_LAL], version_name=VERSION,
                    player_map=PLAYER_MAP, save_injury_raw=True,
                )
        assert result.feed_down is False
        assert result.feed_down_reason is None

    def test_save_raw_failure_predictions_intact(self):
        """Fallo de persistencia → las predicciones se sirven completas (200)."""
        store = _make_store(proba=0.67)
        store.save_raw_injury_report.side_effect = RuntimeError("GCS timeout")
        with _FeedPatch([], []):
            with patch(_CLF_PATCH, return_value=_NULL_FEATURES):
                result = build_daily_predictions(
                    TARGET_DATE, store, season="2026-27",
                    scheduled_games=[GAME_BOS_LAL], version_name=VERSION,
                    player_map=PLAYER_MAP, save_injury_raw=True,
                )
        assert len(result.games) == 1
        assert result.games[0].availability_flag == AvailabilityFlag.OK

    def test_save_raw_failure_logs_warning_not_error(self, caplog):
        """Fallo de persistencia → WARNING en el log, no ERROR ni excepción."""
        import logging
        store = _make_store(proba=0.67)
        store.save_raw_injury_report.side_effect = OSError("fallo de escritura")
        with _FeedPatch([], []):
            with patch(_CLF_PATCH, return_value=_NULL_FEATURES):
                with caplog.at_level(logging.WARNING, logger="nba_predictor.api.daily_predictions"):
                    build_daily_predictions(
                        TARGET_DATE, store, season="2026-27",
                        scheduled_games=[GAME_BOS_LAL], version_name=VERSION,
                        player_map=PLAYER_MAP, save_injury_raw=True,
                    )
        warning_records = [r for r in caplog.records if "snapshot" in r.message.lower()]
        assert len(warning_records) >= 1
        assert all(r.levelno == logging.WARNING for r in warning_records)

    def test_model_load_failure_raises(self):
        store = _make_store()
        store.load_model.side_effect = FileNotFoundError("versión no encontrada")
        with _FeedPatch([], []):
            with patch(_CLF_PATCH, return_value=_NULL_FEATURES):
                with pytest.raises(FileNotFoundError, match="versión no encontrada"):
                    build_daily_predictions(
                        TARGET_DATE, store, season="2026-27",
                        scheduled_games=[GAME_BOS_LAL], version_name=VERSION,
                        player_map=PLAYER_MAP, save_injury_raw=False,
                    )

    def test_result_structure_fields(self):
        store = _make_store(proba=0.63)
        with _FeedPatch([], []):
            with patch(_CLF_PATCH, return_value=_NULL_FEATURES):
                result = build_daily_predictions(
                    TARGET_DATE, store, season="2026-27",
                    scheduled_games=[GAME_BOS_LAL], version_name=VERSION,
                    player_map=PLAYER_MAP, save_injury_raw=False,
                )
        gp = result.games[0]
        assert gp.home_tricode == "BOS"
        assert gp.away_tricode == "LAL"
        assert gp.game_date == "2026-10-15"
        assert 0.0 < gp.probability_home < 1.0
        assert isinstance(gp.nys_tricodes, list)
        assert gp.model_version == VERSION


# ---------------------------------------------------------------------------
# TestFormatDailyMessage
# ---------------------------------------------------------------------------


class TestFormatDailyMessage:
    """
    Cada test fija el texto EXACTO del escenario correspondiente (Decisión 13e-2.1).

    Formato consolidado:
    - Encabezado unificado: '🏀 Predicciones NBA · {fecha}' en TODOS los escenarios.
    - Partido: 'VISITANTE @ LOCAL [· HH:MM CDMX]'
    - Probabilidades: 'LOCAL X% — VISITANTE Y%'
    - Disclaimer antes de la línea del modelo.
    - Feed caído: advertencia global, sin líneas de bajas por partido.
    - NYS: advertencia por equipo afectado; otros con líneas de bajas normales.
    """

    # ── Escenario 1: día de descanso ──

    def test_rest_day_exact_text(self):
        result = DailyResult(
            target_date="2026-10-15", games=[],
            feed_down=False, feed_down_reason=None, model_version=None,
        )
        assert format_daily_message(result) == (
            "🏀 Predicciones NBA · 15 oct 2026\n"
            "Sin partidos hoy."
        )

    # ── Escenario 2: normal sin tip-off ──

    def test_normal_no_absences_exact_text(self):
        result = DailyResult(
            target_date="2026-10-15",
            games=[_make_gp_ok(prob=0.67)],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        expected = (
            "🏀 Predicciones NBA · 15 oct 2026\n"
            "\n"
            "LAL @ BOS\n"
            "BOS 67% — LAL 33%\n"
            "Bajas BOS: –\n"
            "Bajas LAL: –\n"
            "\n"
            f"{_DISCLAIMER}\n"
            f"Modelo: {VERSION}"
        )
        assert format_daily_message(result) == expected

    def test_normal_with_home_absence(self):
        result = DailyResult(
            target_date="2026-10-15",
            games=[_make_gp_ok(prob=0.67, home_abs=["Jaylen Brown"])],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        msg = format_daily_message(result)
        assert "Bajas BOS: Jaylen Brown" in msg
        assert "Bajas LAL: –" in msg

    def test_normal_with_tip_off(self):
        """Partido con tip-off: encabezado 'VISITANTE @ LOCAL · HH:MM CDMX'."""
        result = DailyResult(
            target_date="2026-10-15",
            games=[_make_gp_ok(prob=0.67, tip_off_cdmx="19:30 CDMX")],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        msg = format_daily_message(result)
        assert "LAL @ BOS · 19:30 CDMX" in msg
        assert "BOS 67% — LAL 33%" in msg

    def test_normal_without_tip_off_no_cdmx_label(self):
        """Sin tip-off, el encabezado es 'VISITANTE @ LOCAL' sin sufijo."""
        result = DailyResult(
            target_date="2026-10-15",
            games=[_make_gp_ok(prob=0.67)],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        msg = format_daily_message(result)
        assert "LAL @ BOS\n" in msg
        assert "CDMX" not in msg

    def test_normal_two_games_both_present(self):
        result = DailyResult(
            target_date="2026-10-15",
            games=[
                _make_gp_ok(home="BOS", away="LAL", prob=0.67),
                _make_gp_ok(home="MIA", away="GSW", prob=0.54),
            ],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        msg = format_daily_message(result)
        assert "LAL @ BOS" in msg
        assert "BOS 67% — LAL 33%" in msg
        assert "GSW @ MIA" in msg
        assert "MIA 54% — GSW 46%" in msg
        assert msg.endswith(f"Modelo: {VERSION}")

    def test_disclaimer_present_in_normal(self):
        result = DailyResult(
            target_date="2026-10-15",
            games=[_make_gp_ok()],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        assert _DISCLAIMER in format_daily_message(result)

    def test_disclaimer_before_modelo(self):
        result = DailyResult(
            target_date="2026-10-15",
            games=[_make_gp_ok()],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        msg = format_daily_message(result)
        assert msg.index(_DISCLAIMER) < msg.index(f"Modelo: {VERSION}")

    # ── Escenario 3: feed caído ──

    def test_feed_down_exact_text(self):
        result = DailyResult(
            target_date="2026-10-15",
            games=[_make_gp_feed_down(home="BOS", away="LAL", prob=0.67)],
            feed_down=True, feed_down_reason="error", model_version=VERSION,
        )
        expected = (
            "🏀 Predicciones NBA · 15 oct 2026\n"
            f"{_FEED_DOWN_MSG}\n"
            "\n"
            "LAL @ BOS\n"
            "BOS 67% — LAL 33%\n"
            "\n"
            f"{_DISCLAIMER}\n"
            f"Modelo: {VERSION}"
        )
        assert format_daily_message(result) == expected

    def test_feed_down_no_bajas_lines(self):
        result = DailyResult(
            target_date="2026-10-15",
            games=[_make_gp_feed_down()],
            feed_down=True, feed_down_reason="err", model_version=VERSION,
        )
        assert "Bajas" not in format_daily_message(result)

    def test_feed_down_disclaimer_present(self):
        result = DailyResult(
            target_date="2026-10-15",
            games=[_make_gp_feed_down()],
            feed_down=True, feed_down_reason="err", model_version=VERSION,
        )
        assert _DISCLAIMER in format_daily_message(result)

    def test_feed_down_warning_ends_with_de_hoy(self):
        """El warning de feed caído termina en '…de bajas de hoy.'"""
        result = DailyResult(
            target_date="2026-10-15",
            games=[_make_gp_feed_down()],
            feed_down=True, feed_down_reason="err", model_version=VERSION,
        )
        msg = format_daily_message(result)
        assert "de bajas de hoy." in msg

    # ── Escenario 4: NYS ──

    def test_nys_away_shows_warning_not_bajas(self):
        result = DailyResult(
            target_date="2026-10-15",
            games=[_make_gp_nys(home="MIA", away="GSW", nys_tricodes=["GSW"])],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        msg = format_daily_message(result)
        assert "⚠️ Disponibilidad GSW sin confirmar" in msg
        assert "Bajas MIA: –" in msg
        assert "Bajas GSW" not in msg

    def test_nys_home_shows_warning_not_bajas(self):
        result = DailyResult(
            target_date="2026-10-15",
            games=[_make_gp_nys(home="MIA", away="GSW", nys_tricodes=["MIA"])],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        msg = format_daily_message(result)
        assert "⚠️ Disponibilidad MIA sin confirmar" in msg
        assert "Bajas GSW: –" in msg

    def test_nys_both_teams_both_show_warning(self):
        result = DailyResult(
            target_date="2026-10-15",
            games=[_make_gp_nys(home="MIA", away="GSW", nys_tricodes=["MIA", "GSW"])],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        msg = format_daily_message(result)
        assert "⚠️ Disponibilidad MIA sin confirmar" in msg
        assert "⚠️ Disponibilidad GSW sin confirmar" in msg
        assert "Bajas MIA" not in msg
        assert "Bajas GSW" not in msg

    def test_nys_disclaimer_present(self):
        result = DailyResult(
            target_date="2026-10-15",
            games=[_make_gp_nys()],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        assert _DISCLAIMER in format_daily_message(result)

    # ── Escenario 5: mixto OK + NYS ──

    def test_mixed_ok_and_nys(self):
        result = DailyResult(
            target_date="2026-10-15",
            games=[
                _make_gp_ok(home="BOS", away="LAL", prob=0.67, home_abs=["Jaylen Brown"]),
                _make_gp_nys(home="MIA", away="GSW", nys_tricodes=["GSW"]),
            ],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        msg = format_daily_message(result)
        assert "Bajas BOS: Jaylen Brown" in msg
        assert "Bajas LAL: –" in msg
        assert "⚠️ Disponibilidad GSW sin confirmar" in msg
        assert "Bajas MIA: –" in msg
        assert "⚠️ Reporte de lesiones" not in msg  # sin feed_down global

    # ── Rounding ──

    def test_rounding_50_50(self):
        result = DailyResult(
            target_date="2026-10-15", games=[_make_gp_ok(prob=0.50)],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        assert "BOS 50% — LAL 50%" in format_daily_message(result)

    def test_rounding_truncates_correctly(self):
        result = DailyResult(
            target_date="2026-10-15", games=[_make_gp_ok(prob=0.674)],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        assert "BOS 67% — LAL 33%" in format_daily_message(result)

    # ── Meses en español ──

    def test_month_labels_spanish(self):
        for month_num, expected in [
            (1, "ene"), (2, "feb"), (3, "mar"), (4, "abr"),
            (5, "may"), (6, "jun"), (7, "jul"), (8, "ago"),
            (9, "sep"), (10, "oct"), (11, "nov"), (12, "dic"),
        ]:
            d = date(2026, month_num, 1)
            result = DailyResult(
                target_date=d.isoformat(), games=[],
                feed_down=False, feed_down_reason=None, model_version=None,
            )
            assert expected in format_daily_message(result)

    # ── Encabezado unificado ──

    def test_unified_header_rest_day(self):
        """El día de descanso usa '🏀 Predicciones NBA ·', no '🏀 NBA ·'."""
        result = DailyResult(
            target_date="2026-10-15", games=[],
            feed_down=False, feed_down_reason=None, model_version=None,
        )
        assert format_daily_message(result).startswith("🏀 Predicciones NBA · ")

    def test_unified_header_game_day(self):
        result = DailyResult(
            target_date="2026-10-15", games=[_make_gp_ok()],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        assert format_daily_message(result).startswith("🏀 Predicciones NBA · ")

    # ── Ordenamiento por tip-off ──

    def test_games_sorted_by_tip_off_ascending_no_hour_last(self):
        """format_daily_message ordena por tip-off ascendente; sin hora al final.

        La entrada está deliberadamente desordenada para verificar que la función
        reordena, sin depender del orden del DailyResult entrante.
        Hora numérica: "7:30 CDMX" < "17:30 CDMX" (no alfanumérico).
        """
        result = DailyResult(
            target_date="2026-10-21",
            games=[
                _make_gp_ok("LAL", "GSW", 0.55, tip_off_cdmx="20:00 CDMX"),  # 3er partido
                _make_gp_ok("BOS", "MIA", 0.60, tip_off_cdmx="17:30 CDMX"),  # 1er partido
                _make_gp_ok("DEN", "OKC", 0.52),                               # sin hora → al final
            ],
            feed_down=False, feed_down_reason=None, model_version=VERSION,
        )
        msg = format_daily_message(result)
        pos_1730 = msg.index("17:30 CDMX")
        pos_2000 = msg.index("20:00 CDMX")
        pos_den = msg.index("OKC @ DEN")  # sin hora, aparece después de los dos con hora
        assert pos_1730 < pos_2000 < pos_den


# ---------------------------------------------------------------------------
# Impresión de mensajes para auditoría visual
# ---------------------------------------------------------------------------


def print_format_scenarios():
    """Imprime los mensajes de todos los escenarios para auditoría del formato."""
    sep = "─" * 55

    print(f"\n{sep}")
    print("ESCENARIO 1 — Día de descanso")
    print(sep)
    r = DailyResult(
        target_date="2026-10-15", games=[],
        feed_down=False, feed_down_reason=None, model_version=None,
    )
    print(format_daily_message(r))

    print(f"\n{sep}")
    print("ESCENARIO 2 — Normal (2 partidos, baja en BOS, con tip-off)")
    print(sep)
    r2 = DailyResult(
        target_date="2026-10-15",
        games=[
            _make_gp_ok("BOS", "LAL", 0.67, home_abs=["Jaylen Brown"], tip_off_cdmx="19:30 CDMX"),
            _make_gp_ok("MIA", "GSW", 0.54, tip_off_cdmx="20:00 CDMX"),
        ],
        feed_down=False, feed_down_reason=None, model_version=VERSION,
    )
    print(format_daily_message(r2))

    print(f"\n{sep}")
    print("ESCENARIO 3 — Feed caído")
    print(sep)
    r3 = DailyResult(
        target_date="2026-10-15",
        games=[_make_gp_feed_down("BOS", "LAL", 0.67), _make_gp_feed_down("MIA", "GSW", 0.54)],
        feed_down=True, feed_down_reason="RuntimeError: budget exhausted",
        model_version=VERSION,
    )
    print(format_daily_message(r3))

    print(f"\n{sep}")
    print("ESCENARIO 4 — NYS en equipo visitante (GSW)")
    print(sep)
    r4 = DailyResult(
        target_date="2026-10-15",
        games=[
            _make_gp_ok("BOS", "LAL", 0.67, home_abs=["Jaylen Brown"]),
            _make_gp_nys("MIA", "GSW", 0.54, nys_tricodes=["GSW"]),
        ],
        feed_down=False, feed_down_reason=None, model_version=VERSION,
    )
    print(format_daily_message(r4))

    print(f"\n{sep}")
    print("ESCENARIO 5 — NYS en AMBOS equipos de un partido")
    print(sep)
    r5 = DailyResult(
        target_date="2026-10-15",
        games=[_make_gp_nys("MIA", "GSW", 0.54, nys_tricodes=["MIA", "GSW"])],
        feed_down=False, feed_down_reason=None, model_version=VERSION,
    )
    print(format_daily_message(r5))
    print()


if __name__ == "__main__":
    print_format_scenarios()
