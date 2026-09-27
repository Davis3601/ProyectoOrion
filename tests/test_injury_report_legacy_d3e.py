"""Tests del fix de propagacion del parser legacy (D-RES-3e).

Archivo APARTE del de D-RES-3c a proposito: aquel esta en verde sin una sola
edicion desde la auditoria humana que lo adjudico, y conviene que se vea que
sigue intacto. Aqui viven las guardas de las dos manifestaciones que D-EXP-5
destapo, del invariante interno y del clasificador portable.

Geometria SINTETICA que reproduce las coordenadas MEDIDAS en los PDFs reales
(2018-12-18 paginas 2 y 3). Los PDFs no entran como fixtures y ningun conteo de
aqui es oficial: estas son guardas de REGRESION, y la correccion la establece la
auditoria humana del listado contra el documento (protocolo 13e-1).
"""
from __future__ import annotations

import logging

import pytest

from nba_predictor.config import INJURY_INCOHERENT_WARN_RATE
from nba_predictor.ingestion import injury_report_legacy as legacy
from nba_predictor.ingestion.injury_classify import available_bucket, is_gleague_or_twoway
from nba_predictor.ingestion.injury_report import InjuryStatus


# --------------------------------------------------------------------------
# Andamio: paginas falsas con la geometria real de ITEXT_V1
# --------------------------------------------------------------------------


def _w(text, x0, top):
    return {"text": text, "x0": x0, "top": top}


def _cabecera(top=58.0):
    pares = [("Game", 21.0), ("Date", 42.0), ("Game", 74.0), ("Time", 95.0),
             ("Matchup", 126.0), ("Team", 179.0), ("Player", 269.0),
             ("Name", 292.0), ("Category", 382.0), ("Reason", 495.0),
             ("Current", 607.0), ("Status", 635.0), ("Previous", 720.0),
             ("Status", 751.0)]
    return [_w(t, x, top) for t, x in pares]


class _Pag:
    def __init__(self, words, width=842.0, height=595.0):
        self._words, self.width, self.height = words, width, height

    def extract_words(self):
        return self._words


class _FakePdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _parse(monkeypatch, paginas):
    """Corre parse_pdf_legacy sobre paginas sinteticas, sin tocar pdfplumber real."""
    monkeypatch.setattr(legacy.pdfplumber, "open", lambda _buf: _FakePdf(paginas))
    monkeypatch.setattr(legacy, "parse_pdf", lambda _b: ([], []))
    rows, _nys = legacy.parse_pdf_legacy(b"%PDF-fake", legacy.LAYOUT_ITEXT_V1)
    return rows


# --------------------------------------------------------------------------
# Manifestacion (a): la fecha nueva aterriza en una fila NYS
# --------------------------------------------------------------------------


class TestFechaSobreFilaNys:
    """Medido en 2018-12-18: "12/19/2018" aparece UNA vez en todo el PDF, en la
    fila NOT YET SUBMITTED de CLE@CHA. Con las filas NYS recortadas del cuerpo,
    esa fecha no entraba nunca al estado y las 52 filas salian fechadas 12/18,
    aunque 12 de los 16 partidos del documento eran del 19."""

    def _paginas(self):
        words = _cabecera() + [
            # bloque del 12/18, con su fecha propia
            _w("12/18/2018", 20.5, 77.0), _w("07:00", 72.9, 77.0),
            _w("CLE@IND", 125.3, 77.0),
            _w("Cleveland", 177.9, 77.0), _w("Cavaliers", 210.0, 77.0),
            _w("Henson,", 268.1, 77.0), _w("John", 300.0, 77.0),
            _w("Injury/Illness", 380.9, 77.0), _w("Out", 606.4, 77.0),
            # transicion de FECHA sobre una fila NYS
            _w("12/19/2018", 20.5, 95.0), _w("07:00", 72.9, 95.0),
            _w("CLE@CHA", 125.3, 95.0),
            _w("Charlotte", 177.9, 95.0), _w("Hornets", 208.0, 95.0),
            _w("NOT", 380.9, 95.0), _w("YET", 397.1, 95.0),
            _w("SUBMITTED", 410.6, 95.0),
            # fila de jugador del dia siguiente: NO trae fecha propia
            _w("Cleveland", 177.9, 113.0), _w("Cavaliers", 210.0, 113.0),
            _w("Love,", 268.1, 113.0), _w("Kevin", 293.0, 113.0),
            _w("Injury/Illness", 380.9, 113.0), _w("Out", 606.4, 113.0),
        ]
        return [_Pag(words)]

    def test_la_fecha_de_la_fila_nys_entra_al_estado(self, monkeypatch):
        rows = _parse(monkeypatch, self._paginas())
        assert len(rows) == 2
        henson, love = rows
        assert henson.game_date == "12/18/2018"
        assert love.game_date == "12/19/2018", (
            "la fila de jugador posterior al bloque NYS debe heredar la fecha NUEVA"
        )

    def test_matchup_y_hora_tambien_se_actualizan_desde_la_fila_nys(self, monkeypatch):
        rows = _parse(monkeypatch, self._paginas())
        assert rows[1].matchup == "CLE@CHA"

    def test_la_fila_nys_no_emite_fila_de_jugador(self, monkeypatch):
        """El estado se actualiza con ella; el CUERPO sigue siendo solo jugadores."""
        rows = _parse(monkeypatch, self._paginas())
        assert all("Hornets" not in r.player_name for r in rows)
        assert len(rows) == 2

    def test_el_equipo_nys_no_contamina_la_celda_de_equipo(self, monkeypatch):
        """Regresion de la concatenacion ruidosa ("Sacramento Kings LA Clippers")."""
        rows = _parse(monkeypatch, self._paginas())
        assert rows[0].team == "Cleveland Cavaliers"
        assert "Charlotte" not in rows[1].team


# --------------------------------------------------------------------------
# Manifestacion (b): nombre de equipo NYS de DOS lineas
# --------------------------------------------------------------------------


class TestNombreDeEquipoNysEnDosLineas:
    """Medido en 2018-12-18 pagina 3: "Minnesota" (top=275) y "Timberwolves"
    (top=283) con SUBMITTED centrado en 279. El recorte de +-2 pt alrededor de
    SUBMITTED dejaba fuera los dos fragmentos, que caian en la banda de la
    ULTIMA ancla de la pagina (VanVleet) porque esa banda llegaba hasta el pie.
    Resultado: un jugador de Toronto atribuido a Minnesota."""

    def _paginas(self):
        words = _cabecera() + [
            _w("Toronto", 177.9, 167.0), _w("Raptors", 205.0, 167.0),
            _w("VanVleet,", 268.1, 167.0), _w("Fred", 301.0, 167.0),
            _w("Injury/Illness", 380.9, 167.0), _w("Probable", 606.4, 167.0),
            # bloque NYS con nombre de equipo partido en dos lineas
            _w("DET@MIN", 125.3, 257.0),
            _w("Detroit", 177.9, 257.0), _w("Pistons", 203.0, 257.0),
            _w("NOT", 380.9, 257.0), _w("YET", 397.1, 257.0),
            _w("SUBMITTED", 410.6, 257.0),
            _w("Minnesota", 177.9, 275.0),
            _w("NOT", 380.9, 279.0), _w("YET", 397.1, 279.0),
            _w("SUBMITTED", 410.6, 279.0),
            _w("Timberwolves", 177.9, 283.0),
        ]
        return [_Pag(words)]

    def test_los_fragmentos_no_caen_en_la_ultima_fila_de_jugador(self, monkeypatch):
        (fila,) = _parse(monkeypatch, self._paginas())
        assert fila.team == "Toronto Raptors"
        assert "Minnesota" not in fila.team
        assert "Timberwolves" not in fila.team

    def test_la_ultima_fila_no_absorbe_el_resto_de_la_pagina(self, monkeypatch):
        (fila,) = _parse(monkeypatch, self._paginas())
        assert "SUBMITTED" not in (fila.category or "")
        assert "Detroit" not in fila.team


# --------------------------------------------------------------------------
# Cruce de pagina
# --------------------------------------------------------------------------


class TestCruceDePagina:
    def test_un_bloque_partido_entre_paginas_conserva_su_equipo(self, monkeypatch):
        """Medido en 2018-12-18: el bloque de Toronto empieza en la pagina 2 y
        sigue en la 3, donde las filas NO traen celda de equipo."""
        p1 = _Pag(_cabecera() + [
            _w("12/18/2018", 20.5, 77.0), _w("IND@TOR", 125.3, 77.0),
            _w("Toronto", 177.9, 77.0), _w("Raptors", 205.0, 77.0),
            _w("Ibaka,", 268.1, 77.0), _w("Serge", 295.0, 77.0),
            _w("Injury/Illness", 380.9, 77.0), _w("Questionable", 606.4, 77.0),
        ])
        p2 = _Pag(_cabecera() + [
            _w("Lowry,", 268.1, 77.0), _w("Kyle", 292.0, 77.0),
            _w("Injury/Illness", 380.9, 77.0), _w("Questionable", 606.4, 77.0),
        ])
        rows = _parse(monkeypatch, [p1, p2])
        assert [r.team for r in rows] == ["Toronto Raptors", "Toronto Raptors"]
        assert [r.game_date for r in rows] == ["12/18/2018", "12/18/2018"]
        assert [r.matchup for r in rows] == ["IND@TOR", "IND@TOR"]

    def test_una_pagina_que_empieza_con_encabezado_propio_no_hereda(self, monkeypatch):
        """Si la pagina nueva abre bloque, sus valores GANAN sobre el estado."""
        p1 = _Pag(_cabecera() + [
            _w("12/18/2018", 20.5, 77.0), _w("IND@TOR", 125.3, 77.0),
            _w("Toronto", 177.9, 77.0), _w("Raptors", 205.0, 77.0),
            _w("Ibaka,", 268.1, 77.0), _w("Serge", 295.0, 77.0),
            _w("Injury/Illness", 380.9, 77.0), _w("Out", 606.4, 77.0),
        ])
        p2 = _Pag(_cabecera() + [
            _w("12/19/2018", 20.5, 77.0), _w("BKN@CHI", 125.3, 77.0),
            _w("Chicago", 177.9, 77.0), _w("Bulls", 205.0, 77.0),
            _w("White,", 268.1, 77.0), _w("Coby", 293.0, 77.0),
            _w("Injury/Illness", 380.9, 77.0), _w("Out", 606.4, 77.0),
        ])
        rows = _parse(monkeypatch, [p1, p2])
        assert rows[1].team == "Chicago Bulls"
        assert rows[1].game_date == "12/19/2018"
        assert rows[1].matchup == "BKN@CHI"


# --------------------------------------------------------------------------
# Invariante interno: equipo dentro de su matchup
# --------------------------------------------------------------------------


def _fila(team, matchup, player="Doe, John"):
    return legacy.LegacyInjuryRow(
        game_date="12/18/2018", game_time="07:00", matchup=matchup, team=team,
        player_name=player, status=InjuryStatus.OUT, reason="-",
    )


class TestInvarianteDeMatchup:
    def test_fila_coherente_queda_marcada_como_tal(self):
        filas = [_fila("Toronto Raptors", "IND@TOR")]
        legacy._apply_matchup_invariant(filas, "ITEXT_V1")
        assert filas[0].coherent is True

    def test_fila_incoherente_se_marca_y_se_avisa_jamas_se_descarta(self, caplog):
        """El caso VanVleet. La fila SIGUE en la lista: un parser que la borrara
        o la corrigiera en silencio mentiria igual que uno que calla."""
        filas = [_fila("Minnesota Timberwolves", "IND@TOR", "VanVleet, Fred")]
        with caplog.at_level(logging.WARNING):
            legacy._apply_matchup_invariant(filas, "ITEXT_V1")
        assert len(filas) == 1, "la fila incoherente no se descarta"
        assert filas[0].team == "Minnesota Timberwolves", "no se corrige en silencio"
        assert filas[0].coherent is False
        assert "Invariante de matchup violado" in caplog.text
        assert "VanVleet, Fred" in caplog.text

    def test_equipo_desconocido_no_es_evaluable_y_no_se_marca(self, caplog):
        """Un nombre fuera de la referencia deja la fila SIN veredicto: el
        invariante avisa, nunca inventa. Es el caso de las celdas con dos
        nombres concatenados, que ya fallan ruidosamente por otra via."""
        filas = [_fila("Timberwolves Dallas Mavericks", "DAL@MIN")]
        with caplog.at_level(logging.WARNING):
            legacy._apply_matchup_invariant(filas, "ITEXT_V1")
        assert filas[0].coherent is True
        assert "Invariante de matchup violado" not in caplog.text

    def test_matchup_vacio_no_es_evaluable(self):
        filas = [_fila("Toronto Raptors", "")]
        legacy._apply_matchup_invariant(filas, "ITEXT_V1")
        assert filas[0].coherent is True

    def test_aviso_agregado_cuando_la_tasa_supera_el_umbral(self, caplog):
        buenas = [_fila("Toronto Raptors", "IND@TOR") for _ in range(50)]
        mala = _fila("Minnesota Timberwolves", "IND@TOR")
        with caplog.at_level(logging.WARNING):
            legacy._apply_matchup_invariant([*buenas, mala], "ITEXT_V1")
        assert "por encima del umbral" in caplog.text

    def test_sin_aviso_agregado_por_debajo_del_umbral(self, caplog):
        n = int(2 / INJURY_INCOHERENT_WARN_RATE)      # tasa = 1/n < umbral
        filas = [_fila("Toronto Raptors", "IND@TOR") for _ in range(n)]
        filas.append(_fila("Minnesota Timberwolves", "IND@TOR"))
        with caplog.at_level(logging.WARNING):
            legacy._apply_matchup_invariant(filas, "ITEXT_V1")
        assert "por encima del umbral" not in caplog.text

    def test_la_fila_legacy_es_una_injury_row(self):
        """La subclase existe para no tocar injury_report.py; debe seguir
        sirviendo donde se espera una InjuryRow."""
        from nba_predictor.ingestion.injury_report import InjuryRow
        assert isinstance(_fila("Toronto Raptors", "IND@TOR"), InjuryRow)


# --------------------------------------------------------------------------
# Clasificador portable de Available
# --------------------------------------------------------------------------


class TestClasificadorPortable:
    @pytest.mark.parametrize(
        "category, reason, esperado",
        [
            # ITEXT_V1: la etiqueta vive en Category y la razon es "-"
            ("G League Team", "-", True),
            ("G League - On Assignment", "-", True),
            ("G League - Two-Way", "-", True),
            ("Injury/Illness", "Left knee soreness", False),
            ("Personal Reasons", "-", False),
            # ITEXT_V2 y GemBox: no hay Category, la razon lo dice todo
            (None, "G League - On Assignment", True),
            (None, "Two-Way", True),
            (None, "Injury/Illness - Left ankle", False),
            (None, "", False),
            (None, None, False),
        ],
    )
    def test_lee_category_cuando_existe_y_razon_cuando_no(self, category, reason, esperado):
        assert is_gleague_or_twoway(category, reason) is esperado

    def test_el_caso_que_rompia_la_portabilidad(self):
        """La fila cuya Category dice G-League y cuya razon no lo menciona: la
        inferencia por razon la perdia, y con ella el 47% de las filas
        Available con Category del corpus legacy (medido en D-EXP-5)."""
        assert is_gleague_or_twoway("G League Team", "-") is True
        assert is_gleague_or_twoway(None, "-") is False

    def test_la_category_manda_sobre_la_razon(self):
        """Cuando el documento trae etiqueta EXPLICITA, no se infiere del texto
        libre: la etiqueta del documento gana."""
        assert is_gleague_or_twoway("Injury/Illness", "G League mention") is False

    def test_available_bucket_devuelve_las_dos_etiquetas_de_la_particion(self):
        assert available_bucket("G League Team", "-") == "gleague_o_twoway"
        assert available_bucket(None, "Left knee") == "resto"
