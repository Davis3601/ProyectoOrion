"""
Unit tests de la logica pura de captura de odds (D-ODDS-2).

Solo mocks y dicts: ni red, ni BigQuery, ni reloj. Lo que se fija aqui son las
DECISIONES del job — que precio se archiva, contra que partido se empareja, que
se omite y que se avisa — porque son las que un fallo silencioso corrompería
sin que nadie lo note hasta el analisis de CLV, meses despues.

LECCION APLICADA (2026-09-20): los 23 tests de la primera version pasaban en
verde sobre un bug FATAL — el indice se construia leyendo `games`, que solo
tiene partidos JUGADOS, asi que en produccion habria salido vacio y la tabla se
habria quedado vacia para siempre. Ninguno lo cazo porque TODOS construian el
indice a mano: probaban la aritmetica de fechas, nunca la PROCEDENCIA del dato.
Por eso el caso de match contra calendario usa ahora un recorte del payload
scheduleLeagueV2 REAL (fixture descendiente del documento verdadero, misma
regla que los PDF de 13e-1 y los fixtures de la capa 4).

NO hay tests de integracion nuevos (alcance de la tarea).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from nba_predictor.jobs.odds_logic import (
    MERGE_KEYS,
    SNAPSHOT_LABELS,
    UnknownSnapshotLabel,
    american_to_decimal,
    build_schedule_index,
    extract_h2h_rows,
    match_events,
    normalize_team,
    validate_snapshot_label,
)

CAPTURA = datetime(2026, 10, 21, 19, 0, 0, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# FIXTURE REAL — recorte de scheduleLeagueV2 (temporada 2026-27)
# ---------------------------------------------------------------------------
# Mismos game_id, teamId y fechas que devuelve el CDN en vivo (verificado
# 2026-09-20 contra fetch_future_schedule: 1206 partidos, estos entre ellos).
# El indice del caso (b) se construye DESDE aqui, no a mano.
_SCHEDULE_REAL: list[tuple[str, date, str, str]] = [
    ("0022600001", date(2026, 10, 20), "Detroit Pistons", "Boston Celtics"),
    ("0022600004", date(2026, 10, 21), "Miami Heat", "Minnesota Timberwolves"),
    ("0022600005", date(2026, 10, 21), "Los Angeles Lakers", "Golden State Warriors"),
]


def _evento(home="Los Angeles Lakers", away="Golden State Warriors",
            commence="2026-10-22T02:00:00Z", bookmakers=None, event_id="abc123"):
    return {
        "id": event_id,
        "sport_key": "basketball_nba",
        "commence_time": commence,
        "home_team": home,
        "away_team": away,
        "bookmakers": bookmakers if bookmakers is not None else [{
            "key": "draftkings",
            "markets": [{"key": "h2h", "outcomes": [
                {"name": home, "price": -165},
                {"name": away, "price": 140},
            ]}],
        }],
    }


class TestConversionDePrecio:
    @pytest.mark.parametrize("american,esperado", [
        (150, 2.5), (-200, 1.5), (100, 2.0), (-110, 1.909091), (-165, 1.606061),
    ])
    def test_american_a_decimal(self, american, esperado):
        assert american_to_decimal(american) == pytest.approx(esperado, abs=1e-6)

    def test_precio_cero_es_imposible_y_falla(self):
        """0 no existe en ningun mercado: si llega, el payload esta roto."""
        with pytest.raises(ValueError):
            american_to_decimal(0)


class TestNormalizacionDeEquipo:
    def test_normalizacion_simetrica(self):
        assert normalize_team("Los Angeles Lakers") == normalize_team("losangeleslakers")

    def test_alias_de_clippers(self):
        """La API y el catalogo escriben esta franquicia distinto."""
        assert normalize_team("LA Clippers") == normalize_team("Los Angeles Clippers")

    def test_nombre_vacio_no_revienta(self):
        assert normalize_team("") == ""
        assert normalize_team(None) == ""


class TestSnapshotLabel:
    def test_los_tres_labels_cerrados(self):
        assert SNAPSHOT_LABELS == ("publish", "evening", "late")
        for lab in SNAPSHOT_LABELS:
            assert validate_snapshot_label(lab) == lab

    def test_label_libre_es_rechazado(self):
        """Un label inventado acabaria como basura en la columna que agrupa
        el analisis, y nadie lo veria hasta el analisis."""
        with pytest.raises(UnknownSnapshotLabel):
            validate_snapshot_label("mediodia")


class TestClaveDeIdempotencia:
    def test_game_id_no_esta_en_la_clave(self):
        """ENMIENDA 2026-09-20: game_id es anulable, asi que no puede ser clave.
        Dos filas sin match del mismo dia colisionarian en ese NULL y se
        pisarian la una a la otra."""
        assert MERGE_KEYS == ("game_date", "api_event_id", "bookmaker", "snapshot_label")
        assert "game_id" not in MERGE_KEYS

    def test_dos_sin_match_del_mismo_dia_no_colisionan(self):
        """La clave los separa por api_event_id, la identidad NATIVA del dato."""
        filas, _ = match_events(
            [_evento(home="Real Madrid", away="Barcelona", event_id="ev-1"),
             _evento(home="Panathinaikos", away="Olympiacos", event_id="ev-2")],
            {}, "publish", CAPTURA,
        )
        claves = {tuple(f[k] for k in MERGE_KEYS) for f in filas}
        assert len(claves) == 2, "dos eventos distintos deben dar claves distintas"


class TestExtraccionDeFilas:
    def test_una_fila_por_bookmaker(self):
        ev = _evento(bookmakers=[
            {"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Los Angeles Lakers", "price": -165},
                {"name": "Golden State Warriors", "price": 140}]}]},
            {"key": "fanduel", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Los Angeles Lakers", "price": -170},
                {"name": "Golden State Warriors", "price": 145}]}]},
        ])
        filas = extract_h2h_rows(
            ev, "0022600005", date(2026, 10, 21), "publish", CAPTURA, matched=True
        )
        assert [f["bookmaker"] for f in filas] == ["draftkings", "fanduel"]
        assert filas[0]["home_price_american"] == -165
        assert filas[0]["home_price_decimal"] == pytest.approx(1.606061, abs=1e-6)

    def test_los_dieciseis_campos_del_schema(self):
        (fila,) = extract_h2h_rows(
            _evento(), "0022600005", date(2026, 10, 21), "publish", CAPTURA, matched=True
        )
        assert set(fila) == {
            "game_date", "api_event_id", "matched", "game_id",
            "home_team", "away_team", "bookmaker", "market",
            "home_price_american", "away_price_american",
            "home_price_decimal", "away_price_decimal", "snapshot_label",
            "capture_ts", "source", "ingested_at",
        }
        assert fila["market"] == "h2h"
        assert fila["source"] == "the-odds-api"
        assert fila["api_event_id"] == "abc123"

    def test_sin_probabilidades_ni_devig(self):
        """D-ODDS-2: la captura archiva el precio crudo. Derivar probabilidad
        o quitar vig es ANALISIS y no vive aqui."""
        (fila,) = extract_h2h_rows(
            _evento(), "g", date(2026, 10, 21), "publish", CAPTURA, matched=True
        )
        assert not any(
            k in fila for k in ("home_prob", "away_prob", "implied_prob", "vig", "overround")
        )

    def test_bookmaker_sin_h2h_se_omite(self):
        ev = _evento(bookmakers=[{"key": "x", "markets": [{"key": "spreads", "outcomes": []}]}])
        assert extract_h2h_rows(ev, "g", date(2026, 10, 21), "publish", CAPTURA, True) == []

    def test_bookmaker_con_un_solo_precio_se_omite(self):
        """Media fila de odds no es dato parcial util: es ruido."""
        ev = _evento(bookmakers=[{"key": "x", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Los Angeles Lakers", "price": -165}]}]}])
        assert extract_h2h_rows(ev, "g", date(2026, 10, 21), "publish", CAPTURA, True) == []

    def test_precio_no_entero_se_omite(self):
        ev = _evento(bookmakers=[{"key": "x", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Los Angeles Lakers", "price": "-165"},
            {"name": "Golden State Warriors", "price": 140}]}]}])
        assert extract_h2h_rows(ev, "g", date(2026, 10, 21), "publish", CAPTURA, True) == []


class TestEmparejamiento:
    def _indice_real(self):
        """Indice construido desde el recorte del scheduleLeagueV2 REAL."""
        return build_schedule_index(_SCHEDULE_REAL)

    def test_match_lleva_game_id_del_calendario_cdn(self):
        """(b) Con match: matched=true y el game_id CANONICO del CDN — el mismo
        identificador que usa predictions_log, que es el punto de todo esto."""
        filas, sin_match = match_events(
            [_evento(commence="2026-10-22T02:00:00Z")], self._indice_real(),
            "publish", CAPTURA,
        )
        assert sin_match == []
        assert all(f["matched"] is True for f in filas)
        assert filas[0]["game_id"] == "0022600005"
        assert filas[0]["game_date"] == date(2026, 10, 21)

    def test_partido_nocturno_empareja_con_el_dia_anterior_utc(self):
        """Un partido de las 19:00 ET cae en el dia UTC SIGUIENTE. Sin probar
        tambien la fecha anterior, casi todo partido nocturno quedaria huerfano."""
        filas, _ = match_events(
            [_evento(home="Miami Heat", away="Minnesota Timberwolves",
                     commence="2026-10-22T00:30:00Z")],
            self._indice_real(), "publish", CAPTURA,
        )
        assert filas[0]["game_id"] == "0022600004"
        assert filas[0]["game_date"] == date(2026, 10, 21)

    def test_sin_match_se_escribe_con_game_id_null(self):
        """(a) ENMIENDA: la fila SE ARCHIVA. La odd es evidencia irrecuperable
        (historicas cuestan x10); la interpretacion se arregla despues."""
        filas, sin_match = match_events(
            [_evento(home="Real Madrid", away="Barcelona",
                     commence="2026-10-22T02:00:00Z")],
            self._indice_real(), "publish", CAPTURA,
        )
        assert len(sin_match) == 1, "el WARNING se conserva como observabilidad"
        assert len(filas) == 1, "pero ya NO implica descarte"
        (fila,) = filas
        assert fila["game_id"] is None
        assert fila["matched"] is False
        # game_date de commence_time con la aritmetica ET, NO de la captura:
        # 02:00 UTC del 22 es un partido de la noche del 21.
        assert fila["game_date"] == date(2026, 10, 21)

    def test_sin_match_diurno_conserva_su_propio_dia(self):
        """Un partido de mediodia ET no se corre al dia anterior."""
        filas, _ = match_events(
            [_evento(home="Real Madrid", away="Barcelona",
                     commence="2026-10-22T17:00:00Z")],
            self._indice_real(), "publish", CAPTURA,
        )
        assert filas[0]["game_date"] == date(2026, 10, 22)

    def test_pretemporada_entra_como_no_emparejada(self):
        """CONSECUENCIA DELIBERADA (D-ODDS-6): la pretemporada es prefijo 001 y
        queda fuera de fetch_future_schedule, asi que entra con matched=false.
        Se cumple 'market_odds SI captura pretemporada' sin crear un tercer
        lector de scheduleLeagueV2."""
        filas, _ = match_events(
            [_evento(home="Detroit Pistons", away="Phoenix Suns",
                     commence="2026-10-05T23:00:00Z")],
            self._indice_real(), "publish", CAPTURA,
        )
        assert filas and filas[0]["matched"] is False
        assert filas[0]["game_id"] is None

    def test_mezcla_de_emparejados_y_no(self):
        filas, sin_match = match_events(
            [_evento(), _evento(home="Real Madrid", away="Barcelona", event_id="ev-2")],
            self._indice_real(), "publish", CAPTURA,
        )
        assert len(filas) == 2
        assert sum(f["matched"] for f in filas) == 1
        assert len(sin_match) == 1

    def test_commence_time_ilegible_avisa_y_no_escribe(self):
        """Sin fecha utilizable no hay particion donde archivar: unico caso en
        que la fila SI se pierde, y se avisa."""
        filas, sin_match = match_events(
            [_evento(commence="no-es-fecha")], self._indice_real(), "publish", CAPTURA
        )
        assert filas == []
        assert "commence_time ilegible" in sin_match[0]

    def test_lista_vacia_no_es_error(self):
        """Offseason o jornada vacia: cero filas, cero avisos, cero drama."""
        filas, sin_match = match_events([], self._indice_real(), "publish", CAPTURA)
        assert filas == [] and sin_match == []

    def test_label_invalido_falla_antes_de_procesar(self):
        with pytest.raises(UnknownSnapshotLabel):
            match_events([_evento()], self._indice_real(), "mediodia", CAPTURA)


class TestFuenteDelIndice:
    """Guardas del bug fatal del 2026-09-20: el indice se construia leyendo la
    tabla `games`, que SOLO tiene partidos jugados. Las odds son siempre de
    partidos FUTUROS, asi que el indice salia vacio y la tabla se archivaba
    vacia con exit 0, indefinidamente."""

    def test_indice_vacio_ya_no_pierde_el_dato(self):
        """Tras la enmienda, un indice vacio ya no significa perder la captura:
        todo entra como matched=false. El fallo de fuente lo caza la guarda del
        script (calendario CDN vacio con API con partidos = excepcion)."""
        filas, sin_match = match_events([_evento()], {}, "publish", CAPTURA)
        assert len(filas) == 1
        assert filas[0]["matched"] is False
        assert len(sin_match) == 1

    def test_indice_de_solo_jugados_no_empareja_un_evento_futuro(self):
        """Lo que contenia `games`: partidos PASADOS. No puede emparejar una
        odd, que siempre es de un partido por jugar."""
        indice_de_jugados = build_schedule_index([
            ("0022500001", date(2025, 12, 1), "Los Angeles Lakers", "Golden State Warriors"),
        ])
        filas, sin_match = match_events(
            [_evento(commence="2026-10-22T02:00:00Z")], indice_de_jugados,
            "publish", CAPTURA,
        )
        assert len(sin_match) == 1
        assert filas[0]["matched"] is False
