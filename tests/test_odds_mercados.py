"""D-ODDS-7 — captura de spreads y totals junto al h2h, y presupuesto de creditos.

FIXTURE REAL: tests/fixtures/odds_h2h_spreads_totals_2026-10-27.json es la
respuesta de The Odds API del 2026-10-27 (markets=h2h,spreads,totals, mismos
bookmakers que produccion), recortada a 3 eventos SIN editar su contenido. Los
casos "evento solo con h2h" y "bookmaker sin totals" se DERIVAN de ella quitando
el mercado en el test — jamas se inventa un payload (regla de 13e-1 y capa 4:
el fixture desciende del documento verdadero).

El presupuesto de creditos se prueba con monkeypatch de requests.get: lo que
se fija es que el job lee x-requests-used de la cabecera y decide con los
umbrales pre-registrados (400 aviso, 450 solo h2h).
"""
from __future__ import annotations

import copy
import inspect
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from nba_predictor.jobs.odds_logic import (
    CREDITS_DEGRADE_USED,
    CREDITS_WARN_USED,
    MANUAL_LABEL,
    MARKETS_FULL,
    MARKETS_H2H_ONLY,
    MERGE_KEYS,
    SPREAD_TOTAL_COLUMNS,
    UnknownSnapshotLabel,
    choose_markets,
    extract_h2h_rows,
    parse_quota,
    validate_snapshot_label,
)

FIXTURE = Path(__file__).parent / "fixtures" / "odds_h2h_spreads_totals_2026-10-27.json"
CAPTURA = datetime(2026, 10, 27, 20, 15, tzinfo=timezone.utc)


@pytest.fixture
def eventos() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _evento(eventos, event_id):
    return copy.deepcopy(next(e for e in eventos if e["id"] == event_id))


BOS = "9ad8cc650802c0c5c3c23a1f32c3e0f6"  # Brooklyn Nets @ Boston Celtics
LAL = "82afe4e028b0fbbd9f6cf92697bec6fc"  # Portland Trail Blazers @ Los Angeles Lakers


def _filas(ev):
    return extract_h2h_rows(ev, "g", date(2026, 10, 27), "evening", CAPTURA, matched=True)


class TestParserSobrePayloadReal:
    def test_evento_con_los_tres_mercados(self, eventos):
        filas = {f["bookmaker"]: f for f in _filas(_evento(eventos, BOS))}
        assert set(filas) == {"fanduel", "draftkings", "betmgm"}
        fd = filas["fanduel"]
        # Valores literales del payload real.
        assert (fd["home_price_american"], fd["away_price_american"]) == (-620, 460)
        assert (fd["spread_home"], fd["spread_home_price"]) == (-12.5, -114.0)
        assert (fd["spread_away"], fd["spread_away_price"]) == (12.5, -106.0)
        assert (fd["total_line"], fd["over_price"], fd["under_price"]) == (222.5, -114.0, -106.0)
        assert fd["market"] == "h2h"

    def test_cada_bookmaker_con_su_propia_linea(self, eventos):
        """POR@LAL: FanDuel -2.5 / 234.5, DraftKings y BetMGM -1.5 / 233.5. Las
        columnas son del MISMO bookmaker que el h2h de su fila, jamas mezcladas."""
        filas = {f["bookmaker"]: f for f in _filas(_evento(eventos, LAL))}
        assert filas["fanduel"]["spread_home"] == -2.5
        assert filas["fanduel"]["total_line"] == 234.5
        assert filas["draftkings"]["spread_home"] == -1.5
        assert filas["betmgm"]["total_line"] == 233.5

    def test_evento_solo_con_h2h(self, eventos):
        ev = _evento(eventos, BOS)
        for bm in ev["bookmakers"]:
            bm["markets"] = [m for m in bm["markets"] if m["key"] == "h2h"]
        filas = _filas(ev)
        assert len(filas) == 3
        for f in filas:
            assert f["home_price_american"] is not None
            assert all(f[c] is None for c in SPREAD_TOTAL_COLUMNS)

    def test_bookmaker_sin_totals(self, eventos, caplog):
        ev = _evento(eventos, BOS)
        bm = next(b for b in ev["bookmakers"] if b["key"] == "betmgm")
        bm["markets"] = [m for m in bm["markets"] if m["key"] != "totals"]
        filas = {f["bookmaker"]: f for f in _filas(ev)}
        mgm = filas["betmgm"]
        assert (mgm["total_line"], mgm["over_price"], mgm["under_price"]) == (None, None, None)
        assert mgm["spread_home"] == -12.5  # el resto del bookmaker intacto
        assert filas["draftkings"]["total_line"] == 222.5  # los otros intactos
        # Ausencia = normal (pre-registro): sin WARNING.
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_spread_home_es_menos_spread_away_en_cada_bookmaker(self, eventos):
        for ev in eventos:
            for f in _filas(copy.deepcopy(ev)):
                assert f["spread_home"] == -f["spread_away"], (ev["id"], f["bookmaker"])

    def test_precios_como_float(self, eventos):
        """La API publica enteros americanos; las 7 columnas son FLOAT64 y el
        parser entrega float siempre (lineas como -12.5 lo exigen de todos modos)."""
        for ev in eventos:
            for f in _filas(copy.deepcopy(ev)):
                for c in SPREAD_TOTAL_COLUMNS:
                    assert isinstance(f[c], float), (c, f[c])

    def test_spreads_incompleto_va_a_null_con_aviso(self, eventos, caplog):
        ev = _evento(eventos, BOS)
        bm = next(b for b in ev["bookmakers"] if b["key"] == "fanduel")
        spreads = next(m for m in bm["markets"] if m["key"] == "spreads")
        spreads["outcomes"] = spreads["outcomes"][:1]
        fd = next(f for f in _filas(ev) if f["bookmaker"] == "fanduel")
        assert fd["spread_home"] is None and fd["spread_away"] is None
        assert fd["total_line"] == 222.5
        assert any("spreads incompleto" in r.getMessage() for r in caplog.records)

    def test_bookmaker_sin_h2h_se_omite_aunque_tenga_spreads(self, eventos):
        """La fila esta anclada al h2h (evidencia del CLV): sin h2h no hay fila."""
        ev = _evento(eventos, BOS)
        bm = next(b for b in ev["bookmakers"] if b["key"] == "fanduel")
        bm["markets"] = [m for m in bm["markets"] if m["key"] != "h2h"]
        assert {f["bookmaker"] for f in _filas(ev)} == {"draftkings", "betmgm"}


class TestClaveSinCambio:
    def test_merge_sql_usa_exactamente_la_clave_pre_registrada(self):
        """Lee el SQL del MERGE: el ON es exactamente los cuatro campos."""
        import scripts.odds_capture_job as job

        assert MERGE_KEYS == ("game_date", "api_event_id", "bookmaker", "snapshot_label")
        src = inspect.getsource(job.merge_rows)
        assert 'on = " AND ".join(f"T.{k} = S.{k}" for k in MERGE_KEYS)' in src
        assert "ON {on}" in src
        on = " AND ".join(f"T.{k} = S.{k}" for k in MERGE_KEYS)
        assert on == (
            "T.game_date = S.game_date AND T.api_event_id = S.api_event_id AND "
            "T.bookmaker = S.bookmaker AND T.snapshot_label = S.snapshot_label"
        )

    def test_merge_escribe_las_siete_columnas_como_float64(self):
        import scripts.odds_capture_job as job

        src = inspect.getsource(job.merge_rows)
        assert "*SPREAD_TOTAL_COLUMNS" in src
        assert '**{c: "FLOAT64" for c in SPREAD_TOTAL_COLUMNS}' in src
        assert SPREAD_TOTAL_COLUMNS == (
            "spread_home", "spread_home_price", "spread_away", "spread_away_price",
            "total_line", "over_price", "under_price",
        )


class TestLabelManual:
    def test_manual_es_valido_y_los_tres_programados_siguen_cerrados(self):
        assert validate_snapshot_label(MANUAL_LABEL) == "manual"
        with pytest.raises(UnknownSnapshotLabel):
            validate_snapshot_label("mediodia")


# ---------------------------------------------------------------------------
# Presupuesto de creditos
# ---------------------------------------------------------------------------


class TestUmbrales:
    def test_parse_quota_ignora_mayusculas(self):
        assert parse_quota({"X-Requests-Used": "84", "x-requests-remaining": "416"}) == (84, 416)

    def test_parse_quota_ilegible_es_none(self):
        assert parse_quota({}) == (None, None)
        assert parse_quota({"x-requests-used": "abc"}) == (None, None)

    @pytest.mark.parametrize(
        "used, mercados, nivel",
        [
            (0, MARKETS_FULL, "ok"),
            (399, MARKETS_FULL, "ok"),
            (400, MARKETS_FULL, "warning"),
            (449, MARKETS_FULL, "warning"),
            (450, MARKETS_H2H_ONLY, "degradado"),
            (500, MARKETS_H2H_ONLY, "degradado"),
            (None, MARKETS_H2H_ONLY, "degradado"),
        ],
    )
    def test_choose_markets(self, used, mercados, nivel):
        assert choose_markets(used) == (mercados, nivel)

    def test_umbrales_pre_registrados(self):
        assert (CREDITS_WARN_USED, CREDITS_DEGRADE_USED) == (400, 450)
        assert MARKETS_FULL == ("h2h", "spreads", "totals")


class _Resp:
    def __init__(self, headers, payload=None):
        self.headers = headers
        self._payload = payload if payload is not None else []

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture
def job(monkeypatch):
    import scripts.odds_capture_job as job

    monkeypatch.setattr(job, "_api_key", lambda: "CLAVE-FALSA")
    return job


def _patch_get(monkeypatch, job, used, llamadas):
    def fake_get(url, params=None, timeout=None):
        llamadas.append((url, dict(params or {})))
        return _Resp({"x-requests-used": str(used),
                      "x-requests-remaining": str(500 - used),
                      "x-requests-last": "0"})
    monkeypatch.setattr(job.requests, "get", fake_get)


class TestContadorDeCreditosEnElJob:
    def test_bajo_umbral_pide_los_tres_mercados(self, job, monkeypatch, caplog):
        llamadas: list = []
        _patch_get(monkeypatch, job, 84, llamadas)
        with caplog.at_level(logging.INFO):
            assert job.choose_markets_from_api() == MARKETS_FULL
        assert llamadas[0][0] == job.SPORTS_URL  # la consulta GRATUITA
        assert "x-requests-used=84" in caplog.text
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_400_avisa_y_sigue_con_tres_mercados(self, job, monkeypatch, caplog):
        _patch_get(monkeypatch, job, 400, [])
        assert job.choose_markets_from_api() == MARKETS_FULL
        avisos = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("400 >= 400" in m for m in avisos)
        assert not any("DEGRADACION" in m for m in avisos)

    def test_450_degrada_a_h2h_y_lo_declara(self, job, monkeypatch, caplog):
        _patch_get(monkeypatch, job, 450, [])
        assert job.choose_markets_from_api() == MARKETS_H2H_ONLY
        assert "DEGRADACION DECLARADA" in caplog.text

    def test_consulta_de_cuota_caida_degrada_a_h2h(self, job, monkeypatch, caplog):
        def caida(url, params=None, timeout=None):
            raise job.requests.ConnectionError("red caida apiKey=CLAVE-FALSA")
        monkeypatch.setattr(job.requests, "get", caida)
        assert job.choose_markets_from_api() == MARKETS_H2H_ONLY
        assert "DEGRADACION DECLARADA" in caplog.text
        assert "CLAVE-FALSA" not in caplog.text  # la clave jamas al log

    def test_fetch_odds_pide_los_mercados_elegidos_y_loggea_consumo(
        self, job, monkeypatch, caplog
    ):
        llamadas: list = []
        _patch_get(monkeypatch, job, 87, llamadas)
        with caplog.at_level(logging.INFO):
            job.fetch_odds(MARKETS_FULL)
        url, params = llamadas[0]
        assert url == job.ODDS_API_URL
        assert params["markets"] == "h2h,spreads,totals"
        assert "tras la captura" in caplog.text and "x-requests-used=87" in caplog.text
        assert "CLAVE-FALSA" not in caplog.text

    def test_fetch_odds_degradado_pide_solo_h2h(self, job, monkeypatch):
        llamadas: list = []
        _patch_get(monkeypatch, job, 460, llamadas)
        job.fetch_odds(MARKETS_H2H_ONLY)
        assert llamadas[0][1]["markets"] == "h2h"
