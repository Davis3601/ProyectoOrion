"""D-PRE-1 / bug 4 — market_odds es PRE-PARTIDO por definición.

En octubre (EDT) los snapshots evening (19:45 ET) y late (23:15 ET) caen
después del tipoff de casi todos los partidos. Si The Odds API lista eventos
en curso, el MERGE por (game_date, api_event_id, bookmaker, snapshot_label)
sobrescribiría la odd PREVIA de días anteriores con una odd EN VIVO, y el
análisis de CLV compararía contra un precio que no es de cierre. No está
verificado qué devuelve la API para eventos iniciados; en vez de esperar a
verlo, se convierte en invariante: un evento con commence_time <= capture_ts
NO se escribe jamás, y se cuenta.

Frontera exacta: commence_time == capture_ts se EXCLUYE. En el instante del
tipoff la odd ya no es pre-partido.
"""
from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta, timezone

from nba_predictor.jobs.odds_logic import (
    exclude_started,
    extract_h2h_rows,
    match_events,
    parse_commence,
)

CAPTURA = datetime(2026, 10, 21, 3, 15, 0, tzinfo=timezone.utc)  # late del 20-oct

_SCHEDULE = {
    (date(2026, 10, 20), "detroitpistons", "bostonceltics"): "0022600001",
    (date(2026, 10, 21), "losangeleslakers", "goldenstatewarriors"): "0022600005",
}


def _evento(commence: str, home="Detroit Pistons", away="Boston Celtics", eid="e1"):
    return {
        "id": eid,
        "commence_time": commence,
        "home_team": home,
        "away_team": away,
        "bookmakers": [{
            "key": "draftkings",
            "markets": [{"key": "h2h", "outcomes": [
                {"name": home, "price": -150},
                {"name": away, "price": 130},
            ]}],
        }],
    }


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestExcludeStarted:
    def test_evento_iniciado_se_excluye(self):
        ev = _evento(_iso(CAPTURA - timedelta(hours=3)))
        pendientes, iniciados = exclude_started([ev], CAPTURA)
        assert pendientes == []
        assert len(iniciados) == 1

    def test_evento_futuro_se_incluye(self):
        ev = _evento(_iso(CAPTURA + timedelta(minutes=1)))
        pendientes, iniciados = exclude_started([ev], CAPTURA)
        assert pendientes == [ev]
        assert iniciados == []

    def test_frontera_exacta_se_excluye(self):
        ev = _evento(_iso(CAPTURA))
        pendientes, iniciados = exclude_started([ev], CAPTURA)
        assert pendientes == []
        assert len(iniciados) == 1

    def test_contador_y_descripcion_legible(self):
        evs = [
            _evento(_iso(CAPTURA - timedelta(hours=2)), eid="a"),
            _evento(_iso(CAPTURA - timedelta(minutes=5)), "Los Angeles Lakers",
                    "Golden State Warriors", eid="b"),
            _evento(_iso(CAPTURA + timedelta(hours=20)), eid="c"),
        ]
        pendientes, iniciados = exclude_started(evs, CAPTURA)
        assert [e["id"] for e in pendientes] == ["c"]
        assert len(iniciados) == 2
        assert any("Golden State Warriors @ Los Angeles Lakers" in d for d in iniciados)

    def test_commence_ilegible_no_se_decide_aqui(self):
        """Sin hora no se sabe si empezó: sigue al matching, que ya lo avisa y no escribe."""
        ev = _evento("no-es-fecha")
        pendientes, iniciados = exclude_started([ev], CAPTURA)
        assert pendientes == [ev] and iniciados == []
        filas, avisos = match_events(pendientes, _SCHEDULE, "late", CAPTURA)
        assert filas == [] and "ilegible" in avisos[0]

    def test_capture_ts_naive_es_error(self):
        import pytest

        with pytest.raises(ValueError, match="zona"):
            exclude_started([], datetime(2026, 10, 21, 3, 15))


class TestCommencePersistido:
    def test_parse_commence_utc_aware(self):
        assert parse_commence("2026-10-20T23:30:00Z") == datetime(
            2026, 10, 20, 23, 30, tzinfo=timezone.utc
        )
        assert parse_commence("basura") is None

    def test_fila_lleva_commence_time(self):
        (fila,) = extract_h2h_rows(
            _evento("2026-10-20T23:30:00Z"), "0022600001", date(2026, 10, 20),
            "publish", CAPTURA, matched=True,
        )
        assert fila["commence_time"] == datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc)

    def test_fila_sin_match_tambien_lo_lleva(self):
        filas, _ = match_events(
            [_evento("2026-10-25T23:00:00Z", "Utah Jazz", "Phoenix Suns")],
            _SCHEDULE, "publish", CAPTURA,
        )
        assert filas and filas[0]["matched"] is False
        assert filas[0]["commence_time"] == datetime(2026, 10, 25, 23, tzinfo=timezone.utc)


class TestCableadoDelJob:
    def test_merge_escribe_la_columna_como_timestamp(self):
        import scripts.odds_capture_job as job

        src = inspect.getsource(job.merge_rows)
        assert '"commence_time"' in src
        assert '"commence_time": "TIMESTAMP"' in src

    def test_main_filtra_antes_de_emparejar_con_el_mismo_reloj(self):
        import scripts.odds_capture_job as job

        src = "".join(inspect.getsource(job.main).split())  # sin espacios ni saltos
        assert "exclude_started(" in src
        assert src.index("exclude_started(") < src.index("match_events(")
        assert "match_events(pendientes," in src
        assert "label,capture_ts)" in src
