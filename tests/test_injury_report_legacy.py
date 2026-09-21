"""
Tests de CONTRATO del parser legacy (D-RES-3c). NO son tests de conteos.

Por que solo contrato: el protocolo de 13e-1 dice que un conteo de fixture no
se adopta como oficial sin auditoria humana del listado contra el documento.
Los dos PDFs de auditoria (2019-01-15 y 2021-02-10) NO entran aqui como
fixtures todavia; sus conteos se congelaran en una tarea posterior, despues de
que Antonio adjudique los listados.

Lo que si se fija hoy: que el DESPACHO por layout es correcto, que una firma
desconocida falla RUIDOSAMENTE, y que la guarda de cobertura salta. Es decir,
las propiedades que impiden que el modulo falle en silencio — que fue
exactamente el modo de fallo que motivo todo esto.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nba_predictor.ingestion import injury_report_legacy as legacy
from nba_predictor.ingestion.injury_report_legacy import (
    LAYOUT_GEMBOX,
    ParserCoverageError,
    UnknownLayoutError,
    detect_layout,
    parse_pdf_any,
)

FIXTURE_GEMBOX = Path("tests/fixtures/injury_report_2024-03-13_11PM.pdf")


class TestDetectLayout:
    def test_fixture_gembox_se_detecta_como_gembox(self):
        """El PDF ya auditado en 13e-1 sigue siendo GEMBOX: la ruta viva no se
        desvia al parser legacy por accidente."""
        layout = detect_layout(FIXTURE_GEMBOX.read_bytes())
        assert layout.name == "GEMBOX"
        assert layout is LAYOUT_GEMBOX

    def test_encabezado_alterado_lanza_unknown_layout(self, monkeypatch):
        """Firma fuera de la lista blanca -> excepcion, nunca un intento a
        ciegas. El mensaje debe llevar los tokens observados para que el
        siguiente humano no tenga que reabrir el PDF."""
        monkeypatch.setattr(
            legacy, "_header_words",
            lambda page: [{"text": t, "x0": i * 50.0}
                          for i, t in enumerate(("Fecha", "Equipo", "Matchup"))],
        )
        with pytest.raises(UnknownLayoutError) as exc:
            detect_layout(FIXTURE_GEMBOX.read_bytes())
        assert "Fecha" in str(exc.value)
        assert "GEMBOX" in str(exc.value)

    def test_pdf_sin_token_ancla_lanza_unknown_layout(self, monkeypatch):
        """Sin 'Matchup' no hay encabezado que leer: tampoco se adivina."""
        monkeypatch.setattr(legacy, "_header_words", lambda page: [])
        with pytest.raises(UnknownLayoutError):
            detect_layout(FIXTURE_GEMBOX.read_bytes())


class TestBandasConTope:
    def test_la_ultima_banda_cierra_en_el_ancho_de_pagina(self):
        """Ninguna banda sin tope: una banda abierta por la derecha se traga la
        columna siguiente (medido en la encuesta de layouts, 2026-09-13)."""
        for layout in (legacy.LAYOUT_ITEXT_V1, legacy.LAYOUT_ITEXT_V2):
            bands = layout.bands(page_width=842.0)
            assert bands[-1][2] == 842.0
            assert all(a < b for _n, a, b in bands)

    def test_las_bandas_son_contiguas_y_ordenadas(self):
        for layout in (legacy.LAYOUT_ITEXT_V1, legacy.LAYOUT_ITEXT_V2):
            bands = layout.bands(page_width=842.0)
            for (_n1, _a1, fin), (_n2, ini, _b2) in zip(bands, bands[1:]):
                assert fin == ini


class TestGuardaDeCobertura:
    def test_cobertura_insuficiente_lanza(self, monkeypatch):
        """Con monkeypatch, no con PDF real: lo que se fija es la REGLA."""
        monkeypatch.setattr(legacy, "detect_layout", lambda b: legacy.LAYOUT_ITEXT_V2)
        monkeypatch.setattr(legacy, "parse_pdf_legacy", lambda b, lay: ([], []))
        monkeypatch.setattr(legacy, "count_candidate_lines", lambda b: 50)
        with pytest.raises(ParserCoverageError) as exc:
            parse_pdf_any(b"%PDF-falso")
        assert "0" in str(exc.value)
        assert "50" in str(exc.value)

    def test_cobertura_suficiente_no_lanza(self, monkeypatch):
        fila = object()
        monkeypatch.setattr(legacy, "detect_layout", lambda b: legacy.LAYOUT_ITEXT_V2)
        monkeypatch.setattr(legacy, "parse_pdf_legacy", lambda b, lay: ([fila] * 45, []))
        monkeypatch.setattr(legacy, "count_candidate_lines", lambda b: 50)
        rep = parse_pdf_any(b"%PDF-falso")
        assert rep.layout == "ITEXT_V2"
        assert len(rep.rows) == 45

    def test_documento_sin_candidatas_no_dispara_la_guarda(self, monkeypatch):
        """Un PDF sin filas de jugador (todos los equipos en NYS) es legitimo:
        cero de cero no es una perdida de cobertura."""
        monkeypatch.setattr(legacy, "detect_layout", lambda b: legacy.LAYOUT_ITEXT_V2)
        monkeypatch.setattr(legacy, "parse_pdf_legacy", lambda b, lay: ([], []))
        monkeypatch.setattr(legacy, "count_candidate_lines", lambda b: 0)
        rep = parse_pdf_any(b"%PDF-falso")
        assert rep.rows == []


class TestRetrocompatibilidadInjuryRow:
    def test_los_campos_nuevos_son_opcionales(self):
        """La ruta GemBox construye InjuryRow sin category ni previous_status."""
        from nba_predictor.ingestion.injury_report import InjuryRow, InjuryStatus

        fila = InjuryRow(
            game_date="03/13/2024", game_time="07:00(ET)", matchup="BKN@ORL",
            team="BrooklynNets", player_name="Clowney,Noah",
            status=InjuryStatus.OUT, reason="Coach'sDecision",
        )
        assert fila.category is None
        assert fila.previous_status is None


class TestDefectosCorregidosRonda2:
    """Un test por defecto de la ronda 2. Geometria SINTETICA que reproduce la
    del PDF real (coordenadas medidas en 2019-01-15), no conteos oficiales."""

    def _pagina_falsa(self, words, height=595.0, width=842.0):
        class _Pag:
            def __init__(self):
                self.width, self.height = width, height

            def extract_words(self):
                return words

        return _Pag()

    def _w(self, text, x0, top):
        return {"text": text, "x0": x0, "top": top}

    def _cabecera(self):
        """Encabezado ITEXT_V1 en top=58, con sus X0 reales."""
        pares = [("Game", 21.0), ("Date", 42.0), ("Game", 74.0), ("Time", 95.0),
                 ("Matchup", 126.0), ("Team", 179.0), ("Player", 269.0),
                 ("Name", 292.0), ("Category", 382.0), ("Reason", 495.0),
                 ("Current", 607.0), ("Status", 635.0), ("Previous", 720.0),
                 ("Status", 751.0)]
        return [self._w(t, x, 58.0) for t, x in pares]

    def test_razon_multilinea_no_invade_la_fila_anterior(self):
        """Defecto (b). Medido en 2019-01-15 p.2: cuando la razon ocupa dos
        lineas, el generador centra las celdas de una linea (jugador en 135) y
        deja equipo y primera linea de razon 4 pt MAS ARRIBA (131). Anclar la
        banda en el top del jugador metia ese 131 en la fila anterior: Robinson
        heredaba "Milwaukee Bucks" y la razon de Antetokounmpo."""
        words = self._cabecera() + [
            self._w("Robinson,", 268.11, 113.0), self._w("Duncan", 302.24, 113.0),
            self._w("G", 380.89, 113.0), self._w("Out", 606.44, 113.0),
            self._w("Milwaukee", 177.89, 131.0), self._w("Bucks", 215.52, 131.0),
            self._w("Right", 493.67, 131.0), self._w("quadriceps", 512.28, 131.0),
            self._w("Antetokounmpo,", 268.11, 135.0), self._w("Giannis", 325.32, 135.0),
            self._w("Injury/Illness", 380.89, 135.0), self._w("Probable", 606.44, 135.0),
            self._w("hip", 493.67, 139.0), self._w("contusion", 505.71, 139.0),
        ]
        filas = legacy._rows_from_page(self._pagina_falsa(words), legacy.LAYOUT_ITEXT_V1)
        assert len(filas) == 2
        robinson, giannis = filas
        assert robinson["team"] == ""            # su equipo venia de una pagina previa
        assert "quadriceps" not in robinson["reason"]
        assert giannis["team"] == "Milwaukee Bucks"
        assert giannis["reason"] == "Right quadriceps hip contusion"

    def test_bloque_nys_no_se_funde_con_la_fila_de_jugador(self):
        """Defecto (a). Las filas NYS no tienen ancla de jugador, asi que caian
        dentro de la banda del jugador anterior y su equipo se concatenaba
        ("Detroit Pistons Orlando Magic Brooklyn Nets")."""
        words = self._cabecera() + [
            self._w("Williams,", 268.11, 113.0), self._w("Johnathan", 302.24, 113.0),
            self._w("Out", 606.44, 113.0),
            self._w("Detroit", 177.89, 131.0), self._w("Pistons", 200.41, 131.0),
            self._w("NOT", 380.89, 131.0), self._w("YET", 397.05, 131.0),
            self._w("SUBMITTED", 410.55, 131.0),
            self._w("Orlando", 177.89, 149.0), self._w("Magic", 200.41, 149.0),
            self._w("NOT", 380.89, 149.0), self._w("YET", 397.05, 149.0),
            self._w("SUBMITTED", 410.55, 149.0),
        ]
        (fila,) = legacy._rows_from_page(self._pagina_falsa(words), legacy.LAYOUT_ITEXT_V1)
        assert "Detroit" not in fila["team"]
        assert "Orlando" not in fila["team"]
        assert "SUBMITTED" not in fila["category"]
