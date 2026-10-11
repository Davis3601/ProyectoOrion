"""D-PRE-1 / bug 1 — descubrimiento del injury report EN TEMPORADA.

El bug: discover_latest_snapshot arrancaba siempre en 11_45PM y con budget 20
solo llegaba a 07_00PM. A las 15:00 ET (endpoint) el ultimo corte existente es
el de las 03:00 PM (indice 35 de la lista vieja) y a las 08:00 ET (job) el de
las 07:45 AM (indice 64). En temporada el endpoint habria declarado feed caido
los 72 dias de la ventana y el job jamas habria archivado nada. Ninguna
verificacion previa ejercito la condicion real: el spike sondeo fechas PASADAS
completas (todos los cortes ya existen), el offseason no tiene PDFs y el
dry-run uso una fecha FUTURA.

SIMULACION HONESTA DEL SERVIDOR: un corte existe si su hora de creacion es <=
"ahora". La creacion real lleva unos segundos de retraso sobre el sufijo
(/CreationDate D:20260313000004 para el corte 12_00AM, fase 0 de D-RES-2), asi
que el corte del cuarto de hora en curso puede no existir todavia en el minuto
exacto de su sufijo. Por eso los tests exigen "el ultimo corte EXISTENTE en <= 2
sondas" y no "el sufijo del floor": la segunda sonda es el precio maximo de
llegar en el mismo minuto en que el corte se esta generando.

Ningun test toca la red: HEAD simulado con MagicMock.
"""
from __future__ import annotations

import inspect
from datetime import date, datetime, time, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from nba_predictor.ingestion import injury_report as ir
from nba_predictor.ingestion.injury_report import discover_latest_snapshot

ET = ZoneInfo("America/New_York")
CREATION_LAG = timedelta(seconds=4)  # medido: 12_00AM -> creado 00:00:04


def _creation(day: date, suffix: str) -> datetime:
    """Hora ET de creacion de un corte de formato nuevo {HH}_{MM}{AM|PM}."""
    hh, rest = suffix.split("_")
    mm, ampm = int(rest[:2]), rest[2:]
    h = int(hh) % 12 + (12 if ampm == "PM" else 0)
    return datetime.combine(day, time(h, mm), tzinfo=ET) + CREATION_LAG


def _server(day: date, now_et: datetime) -> MagicMock:
    """Sesion falsa: 200 si el corte ya fue creado a la hora now_et, 403 si no.

    Para fechas ANTERIORES a now_et.date() todos los cortes del dia existen.
    """
    sess = MagicMock()

    def head(url: str, timeout: int = 10):  # noqa: ARG001
        resp = MagicMock()
        resp.headers = {"Content-Length": "70000"}
        fname = url.rsplit("/", 1)[-1]  # Injury-Report_YYYY-MM-DD_HH_MMxM.pdf
        d_str, suffix = fname[len("Injury-Report_"):-len(".pdf")].split("_", 1)
        d = date.fromisoformat(d_str)
        if "_" not in suffix:  # formato viejo: no existe en 2026
            resp.status_code = 403
        elif d != day:
            resp.status_code = 403
        else:
            resp.status_code = 200 if _creation(d, suffix) <= now_et else 403
        return resp

    sess.head.side_effect = head
    return sess


def _sufijos_probados(sess: MagicMock) -> list[str]:
    return [
        c.args[0].rsplit("/", 1)[-1].split("_", 2)[2][:-4]
        for c in sess.head.call_args_list
    ]


DIA = date(2026, 3, 13)  # dia de temporada con los 96 cortes archivados


# ---------------------------------------------------------------------------
# (a) endpoint: 15:00 ET
# ---------------------------------------------------------------------------


class TestEndpointQuinceET:
    def test_15_00_30_encuentra_03_00PM_en_una_sonda(self):
        """El request real llega ~15:00:03-15:00:17 ET (cron 19:00:02Z + cold start)."""
        now = datetime(2026, 3, 13, 15, 0, 30, tzinfo=ET)
        sess = _server(DIA, now)
        _, suffix = discover_latest_snapshot("2026-03-13", session=sess, now_et=now)
        assert suffix == "03_00PM"
        assert sess.head.call_count <= 2

    def test_15_00_00_exacto_cae_al_02_45PM_en_dos_sondas(self):
        """En el minuto exacto el corte 03_00PM aun no existe: una sonda de mas."""
        now = datetime(2026, 3, 13, 15, 0, 0, tzinfo=ET)
        sess = _server(DIA, now)
        _, suffix = discover_latest_snapshot("2026-03-13", session=sess, now_et=now)
        assert suffix == "02_45PM"
        assert _sufijos_probados(sess) == ["03_00PM", "02_45PM"]


# ---------------------------------------------------------------------------
# (b) job: 08:00 ET
# ---------------------------------------------------------------------------


class TestJobOchoET:
    def test_08_00_00_exacto_encuentra_07_45AM(self):
        now = datetime(2026, 3, 13, 8, 0, 0, tzinfo=ET)
        sess = _server(DIA, now)
        _, suffix = discover_latest_snapshot("2026-03-13", session=sess, now_et=now)
        assert suffix == "07_45AM"
        assert sess.head.call_count <= 2

    def test_08_00_15_encuentra_08_00AM(self):
        """La corrida real archiva tras el paso 1 (~12:00:15Z = 08:00:15 ET)."""
        now = datetime(2026, 3, 13, 8, 0, 15, tzinfo=ET)
        sess = _server(DIA, now)
        _, suffix = discover_latest_snapshot("2026-03-13", session=sess, now_et=now)
        assert suffix == "08_00AM"
        assert sess.head.call_count == 1

    def test_reloj_aware_en_utc_se_convierte_a_et(self):
        """12:00:15Z es 08:00:15 ET en EDT: un datetime aware se normaliza a ET."""
        now_utc = datetime(2026, 3, 13, 12, 0, 15, tzinfo=ZoneInfo("UTC"))
        sess = _server(DIA, now_utc.astimezone(ET))
        _, suffix = discover_latest_snapshot("2026-03-13", session=sess, now_et=now_utc)
        assert suffix == "08_00AM"

    def test_reloj_naive_es_error_ruidoso(self):
        """Un naive no dice en que zona esta: adivinar es el bug de la capa 3."""
        with pytest.raises(ValueError, match="zona horaria"):
            discover_latest_snapshot(
                "2026-03-13", session=MagicMock(), now_et=datetime(2026, 3, 13, 15, 0)
            )


# ---------------------------------------------------------------------------
# (c) fecha pasada, (d) fecha futura
# ---------------------------------------------------------------------------


class TestFechaRelativa:
    def test_fecha_pasada_conserva_arranque_en_11_45PM(self):
        now = datetime(2026, 3, 14, 8, 0, 15, tzinfo=ET)
        sess = _server(DIA, now)
        _, suffix = discover_latest_snapshot("2026-03-13", session=sess, now_et=now)
        assert suffix == "11_45PM"
        assert sess.head.call_count == 1

    def test_fecha_futura_cero_sondas_y_razon_explicita(self):
        now = datetime(2026, 3, 13, 15, 0, 30, tzinfo=ET)
        sess = MagicMock()
        with pytest.raises(RuntimeError, match="FUTURA"):
            discover_latest_snapshot("2026-03-14", session=sess, now_et=now)
        assert sess.head.call_count == 0

    def test_sin_reloj_comportamiento_historico_intacto(self):
        """now_et=None conserva la lista de siempre (los callers sin reloj no cambian)."""
        assert ir.plan_snapshot_candidates("2026-03-13", None) == ir._all_suffix_candidates()


# ---------------------------------------------------------------------------
# (e) presupuesto
# ---------------------------------------------------------------------------


class TestPresupuesto:
    def test_budget_default_nunca_excede_20(self):
        now = datetime(2026, 3, 13, 15, 0, 30, tzinfo=ET)
        sess = _server(date(2026, 3, 12), now)  # el servidor no tiene NADA de hoy
        with pytest.raises(RuntimeError):
            discover_latest_snapshot("2026-03-13", session=sess, now_et=now)
        assert sess.head.call_count == 20

    def test_veinte_sondas_son_cinco_horas_desde_la_hora_actual(self):
        now = datetime(2026, 3, 13, 15, 0, 30, tzinfo=ET)
        sess = _server(date(2026, 3, 12), now)
        with pytest.raises(RuntimeError) as exc:
            discover_latest_snapshot("2026-03-13", session=sess, now_et=now)
        probados = _sufijos_probados(sess)
        assert probados[0] == "03_00PM"
        assert probados[-1] == "10_15AM"
        assert "11_45PM" not in probados  # jamas sondea el futuro del dia
        assert "03_00PM" in str(exc.value)  # la razon muestra las sondas reales

    def test_madrugada_lista_corta_no_inventa_cortes(self):
        """A las 00:20 ET solo hay dos cuartos posibles: 12_15AM y 12_00AM."""
        now = datetime(2026, 3, 13, 0, 20, tzinfo=ET)
        assert ir.plan_snapshot_candidates("2026-03-13", now) == ["12_15AM", "12_00AM"]

    def test_hoy_era_nueva_formato_nuevo(self):
        now = datetime(2026, 3, 13, 13, 7, tzinfo=ET)
        assert ir.plan_snapshot_candidates("2026-03-13", now)[:3] == [
            "01_00PM", "12_45PM", "12_30PM",
        ]

    def test_hoy_era_vieja_formato_viejo(self):
        """Antes de la frontera 2025-12-22 el sufijo es por hora (D-RES-2)."""
        now = datetime(2025, 12, 21, 13, 7, tzinfo=ET)
        assert ir.plan_snapshot_candidates("2025-12-21", now)[:3] == ["01PM", "12PM", "11AM"]


# ---------------------------------------------------------------------------
# (f) job: ayer completo + hoy hasta la hora actual
# ---------------------------------------------------------------------------


class TestArchivoDelJob:
    def test_fechas_de_archivo_son_ayer_y_hoy_en_et(self):
        from nba_predictor.jobs.ingest_logic import injury_archive_dates

        now_utc = datetime(2026, 10, 22, 12, 0, 15, tzinfo=ZoneInfo("UTC"))
        assert injury_archive_dates(now_utc) == ["2026-10-21", "2026-10-22"]

    def test_ayer_encuentra_el_ultimo_corte_de_ayer(self):
        now = datetime(2026, 3, 14, 8, 0, 15, tzinfo=ET)
        sess = _server(DIA, now)
        _, suffix = discover_latest_snapshot("2026-03-13", session=sess, now_et=now)
        assert suffix == "11_45PM"

    def test_archive_pasa_el_reloj_a_discover(self):
        from nba_predictor.jobs.ingest_logic import _archive_injury_report

        now = datetime(2026, 10, 22, 8, 0, 15, tzinfo=ET)
        store = MagicMock()
        with (
            patch.object(ir, "discover_latest_snapshot", return_value=("u", "07_45AM")) as d,
            patch.object(ir, "download_snapshot", return_value=b"%PDF-x"),
        ):
            assert _archive_injury_report(store, "2026-10-22", now_et=now) is True
        assert d.call_args.kwargs["now_et"] == now

    def test_job_inyecta_reloj_et_y_archiva_ayer_y_hoy(self):
        """Guarda de call site: el reloj entra por el borde (main), jamas por la funcion."""
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "scripts" / "ingest_job.py"
        src = path.read_text(encoding="utf-8")
        assert "injury_archive_dates(" in src
        assert "now_et=now_et" in src


# ---------------------------------------------------------------------------
# Endpoint: el reloj viaja server -> build_daily_predictions -> discover
# ---------------------------------------------------------------------------


class TestEndpointCableado:
    def test_server_inyecta_reloj_et(self):
        from nba_predictor.api import server

        src = inspect.getsource(server.predictions_today)
        assert "now_et=" in src

    def test_fetch_absences_pasa_now_et(self):
        from nba_predictor.api import daily_predictions as dp

        src = inspect.getsource(dp._fetch_absences)
        assert "now_et=now_et" in src
        assert "now_et" in inspect.signature(dp.build_daily_predictions).parameters

    def test_la_funcion_pura_jamas_lee_el_reloj(self):
        """Regla de la capa 3: el reloj se inyecta, no se consulta dentro."""
        src = inspect.getsource(ir.plan_snapshot_candidates) + inspect.getsource(
            ir.discover_latest_snapshot
        )
        assert ".now(" not in src
        assert "today(" not in src
