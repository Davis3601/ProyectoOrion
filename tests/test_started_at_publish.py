"""D-PRE-2 — partidos ya iniciados al publicar.

Hallazgo B de D-PRE-1: 7 de 481 partidos (6 en la ventana) empiezan antes o
en el mismo minuto de la publicación de las 13:00 CDMX. Para ellos la
predicción se publica igual, pero DECLARADA (13e-2.5): una línea congelada
bajo el encabezado del partido. El resto del mensaje, byte a byte intacto.

El tip-off que decide es el REAL (gameDateTimeUTC). gameDateTimeEst jamás:
su Z es decorativo y la hora es ET (capa 4). La hora TBD de la NBA Cup no
enciende nada: su 00:00 ET de relleno (05:00Z) parecería "ya empezado".

FIXTURE REAL: los tres partidos del 2026-10-20 y uno TBD, recortados del
scheduleLeagueV2_2026-10-11.json que el ingest job archivó en
gs://predictorsnonprod-nba-predictors/raw/schedules/ (mismos gameId, horas y
textos). El recorte de test_future_schedule.py empezaba en el 21 y por eso
nadie vio el día 20.
"""
from __future__ import annotations

import inspect
from datetime import date, datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from nba_predictor.api.daily_predictions import (
    AvailabilityFlag,
    DailyResult,
    GamePrediction,
    count_tipoff_tbd,
    format_daily_message,
    format_players_message,
    started_at_publish,
)
from nba_predictor.ingestion.future_schedule import fetch_future_schedule

CDMX = ZoneInfo("America/Mexico_City")
VERSION = "v1_logistic_bclean_2026-10-10"
DISCLAIMER = "Predicciones estadísticas — no constituyen consejo de apuestas."
MARCADOR = "⏱ Iniciado al publicar · predicción sin información del partido en curso"

_REAL_2026_10_20: dict = {
    "leagueSchedule": {
        "seasonYear": "2026-27",
        "gameDates": [
            {"games": [
                {"gameId": "0022600001", "gameStatus": 1, "gameStatusText": "3:00 pm ET",
                 "gameDateEst": "2026-10-20T00:00:00Z",
                 "gameDateTimeEst": "2026-10-20T15:00:00Z",
                 "gameDateTimeUTC": "2026-10-20T19:00:00Z",
                 "homeTeam": {"teamId": 1610612765, "teamTricode": "DET"},
                 "awayTeam": {"teamId": 1610612738, "teamTricode": "BOS"}},
                {"gameId": "0022600002", "gameStatus": 1, "gameStatusText": "7:00 pm ET",
                 "gameDateEst": "2026-10-20T00:00:00Z",
                 "gameDateTimeEst": "2026-10-20T19:00:00Z",
                 "gameDateTimeUTC": "2026-10-20T23:00:00Z",
                 "homeTeam": {"teamId": 1610612752, "teamTricode": "NYK"},
                 "awayTeam": {"teamId": 1610612755, "teamTricode": "PHI"}},
                {"gameId": "0022600003", "gameStatus": 1, "gameStatusText": "9:30 pm ET",
                 "gameDateEst": "2026-10-20T00:00:00Z",
                 "gameDateTimeEst": "2026-10-20T21:30:00Z",
                 "gameDateTimeUTC": "2026-10-21T01:30:00Z",
                 "homeTeam": {"teamId": 1610612759, "teamTricode": "SAS"},
                 "awayTeam": {"teamId": 1610612760, "teamTricode": "OKC"}},
            ]},
            {"games": [
                {"gameId": "0022601201", "gameStatus": 1, "gameStatusText": "TBD",
                 "gameDateEst": "2026-12-04T00:00:00Z",
                 "gameDateTimeEst": "2026-12-04T00:00:00Z",
                 "gameDateTimeUTC": "2026-12-04T05:00:00Z",
                 "homeTeam": {"teamId": 0, "teamTricode": ""},
                 "awayTeam": {"teamId": 0, "teamTricode": ""}},
            ]},
        ],
    }
}


def _schedule() -> list:
    client = MagicMock()
    client.fetch_season_schedule.return_value = (MagicMock(), _REAL_2026_10_20)
    return fetch_future_schedule("2026-27", from_date=date(2026, 10, 1), cdn_client=client)


def _gp(started: bool | None) -> GamePrediction:
    return GamePrediction(
        home_tricode="DET", away_tricode="BOS", game_date="2026-10-20",
        probability_home=0.41, home_absences=[], away_absences=[],
        availability_flag=AvailabilityFlag.OK, model_version=VERSION,
        tip_off_cdmx="13:00 CDMX", started_at_publish=started,
    )


def _result(*gps: GamePrediction) -> DailyResult:
    return DailyResult(target_date="2026-10-20", games=list(gps), feed_down=False,
                       feed_down_reason=None, model_version=VERSION)


class TestParseoDelSchedule:
    def test_tipoff_utc_real_y_no_el_est(self):
        g = {x.game_id: x for x in _schedule()}["0022600001"]
        assert g.tip_off_utc == datetime(2026, 10, 20, 19, 0, tzinfo=timezone.utc)
        assert g.tip_off_et == datetime(2026, 10, 20, 15, 0)  # display, sin tocar
        assert g.tip_off_tbd is False

    def test_tbd_sin_hora_utc(self):
        g = {x.game_id: x for x in _schedule()}["0022601201"]
        assert g.tip_off_tbd is True
        assert g.tip_off_utc is None


class TestReglaDeIniciado:
    def test_sanity_real_20_oct_cron_13_00_02_solo_bos_det(self):
        cron = datetime(2026, 10, 20, 13, 0, 2, tzinfo=CDMX)
        dia = [g for g in _schedule() if g.game_date == date(2026, 10, 20)]
        marcados = {g.game_id: started_at_publish(g, cron) for g in dia}
        assert marcados == {"0022600001": True, "0022600002": False, "0022600003": False}

    def test_frontera_exacta_enciende(self):
        g = {x.game_id: x for x in _schedule()}["0022600001"]
        assert started_at_publish(g, datetime(2026, 10, 20, 13, 0, 0, tzinfo=CDMX)) is True

    def test_un_segundo_antes_apagado(self):
        g = {x.game_id: x for x in _schedule()}["0022600001"]
        assert started_at_publish(g, datetime(2026, 10, 20, 12, 59, 59, tzinfo=CDMX)) is False

    def test_tbd_no_determinable(self):
        g = {x.game_id: x for x in _schedule()}["0022601201"]
        # 05:00Z de relleno: leída como real, "ya empezó" a las 13:00 CDMX del 4-dic.
        assert started_at_publish(g, datetime(2026, 12, 4, 13, 0, 2, tzinfo=CDMX)) is None

    def test_sin_reloj_no_determinable(self):
        g = {x.game_id: x for x in _schedule()}["0022600001"]
        assert started_at_publish(g, None) is None

    def test_reloj_naive_es_error(self):
        g = {x.game_id: x for x in _schedule()}["0022600001"]
        with pytest.raises(ValueError, match="zona"):
            started_at_publish(g, datetime(2026, 10, 20, 13, 0, 2))

    def test_contador_tbd(self):
        assert count_tipoff_tbd(_schedule()) == 1


class TestMensaje:
    def test_rama_encendida_texto_exacto(self):
        assert format_daily_message(_result(_gp(True))) == (
            "🏀 Predicciones NBA · 20 oct 2026\n"
            "\n"
            "BOS @ DET · 13:00 CDMX\n"
            f"{MARCADOR}\n"
            "DET 41% — BOS 59%\n"
            "Bajas DET: –\n"
            "Bajas BOS: –\n"
            "\n"
            f"{DISCLAIMER}\n"
            f"Modelo: {VERSION}"
        )

    @pytest.mark.parametrize("estado", [False, None])
    def test_rama_apagada_byte_a_byte(self, estado):
        base = GamePrediction(
            home_tricode="DET", away_tricode="BOS", game_date="2026-10-20",
            probability_home=0.41, home_absences=[], away_absences=[],
            availability_flag=AvailabilityFlag.OK, model_version=VERSION,
            tip_off_cdmx="13:00 CDMX",
        )  # sin el campo: el mensaje vigente
        assert format_daily_message(_result(_gp(estado))) == format_daily_message(_result(base))
        assert "⏱" not in format_daily_message(_result(_gp(estado)))

    def test_encendida_solo_inserta_una_linea(self):
        on = format_daily_message(_result(_gp(True))).split("\n")
        off = format_daily_message(_result(_gp(False))).split("\n")
        assert len(on) == len(off) + 1
        on.remove(MARCADOR)
        assert on == off

    def test_solo_el_partido_iniciado_lleva_marcador(self):
        otro = GamePrediction(
            home_tricode="NYK", away_tricode="PHI", game_date="2026-10-20",
            probability_home=0.55, home_absences=[], away_absences=[],
            availability_flag=AvailabilityFlag.OK, model_version=VERSION,
            tip_off_cdmx="17:00 CDMX", started_at_publish=False,
        )
        msg = format_daily_message(_result(_gp(True), otro))
        assert msg.count(MARCADOR) == 1
        assert msg.index(MARCADOR) < msg.index("PHI @ NYK")

    def test_marcador_seguro_en_html(self):
        assert not any(c in MARCADOR for c in "<>&")

    def test_mensaje_2_no_cambia(self):
        assert format_players_message(_result(_gp(True))) == format_players_message(
            _result(_gp(False))
        )


class TestCableado:
    def test_server_inyecta_now_cdmx_desde_el_mismo_instante(self):
        from nba_predictor.api import server

        src = inspect.getsource(server.predictions_today)
        assert "now_cdmx=ahora.astimezone(" in src
        assert "now_et=ahora.astimezone(" in src
        assert src.count("datetime.now(") == 2  # el instante del request + el stamp de logs

    def test_build_pasa_el_reloj_a_la_regla(self):
        from nba_predictor.api import daily_predictions as dp

        assert "now_cdmx" in inspect.signature(dp.build_daily_predictions).parameters
        assert "started_at_publish(sg, now_cdmx)" in inspect.getsource(dp.build_daily_predictions)

    def test_la_regla_jamas_lee_el_reloj(self):
        src = inspect.getsource(started_at_publish)
        assert ".now(" not in src and "today(" not in src
