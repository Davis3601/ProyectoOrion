"""D-PRE-3 — guarda de cobertura del parser del injury report en el endpoint.

El modo de fallo REAL de parse_pdf ante un generador de PDF distinto (D-RES-3,
corpus iTextSharp) es devolver CERO filas sin lanzar. En el endpoint eso
publicaría "Bajas: –" en todos los partidos con feed_down=False: la mentira
silenciosa prohibida por 13e-2.5. La guarda compara las filas del parser contra
un conteo INDEPENDIENTE de líneas con nombre y, si el parser leyó menos del 80%,
el feed se DECLARA caído.

Los conteos de las fixtures (73 y 160) son los OFICIALES auditados a mano en
13e-1: el contador independiente los reproduce exactamente.
"""
from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from nba_predictor.api.daily_predictions import AvailabilityFlag, build_daily_predictions
from nba_predictor.ingestion.future_schedule import ScheduledGame
from nba_predictor.ingestion.injury_report import (
    PARSE_MIN_COVERAGE,
    ParseCoverageError,
    _NAME_LINE_RE,
    check_parse_coverage,
    count_name_lines,
    parse_pdf,
)
from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS

FIX = Path(__file__).parent / "fixtures"
PDFS = {
    "2026": (FIX / "injury_report_2026-03-13_01_15PM.pdf").read_bytes(),
    "2024": (FIX / "injury_report_2024-03-13_11PM.pdf").read_bytes(),
}
PDF_2026 = PDFS["2026"]

_IR = "nba_predictor.ingestion.injury_report"
_CLF = "nba_predictor.features.live_lookup.compute_live_features"


class TestContadorIndependiente:
    @pytest.mark.parametrize("cual, oficial", [("2026", 73), ("2024", 160)])
    def test_reproduce_los_conteos_oficiales_auditados(self, cual, oficial):
        assert count_name_lines(PDFS[cual]) == oficial

    @pytest.mark.parametrize("cual", ["2026", "2024"])
    def test_parser_vigente_no_dispara_la_guarda(self, cual):
        pdf = PDFS[cual]
        rows, _ = parse_pdf(pdf)
        check_parse_coverage(len(rows), count_name_lines(pdf))  # no lanza

    def test_la_regex_legacy_cuenta_cero_en_gembox(self):
        """Por qué no se reutilizó: exige espacio tras la coma y GemBox no lo emite."""
        from nba_predictor.ingestion.injury_report_legacy import count_candidate_lines

        assert count_candidate_lines(PDF_2026) == 0

    @pytest.mark.parametrize("linea", [
        "Allen,Jarrett Out Injury/Illness-RightKnee;Tendonitis",          # GemBox
        "03/13/2026 07:30(ET) CLE@DAL ClevelandCavaliers Allen,Jarrett Out",  # 1a de bloque
        "Robinson, Duncan G League Team Out",                              # iTextSharp
        "McConnell,T.J. Probable",                                         # iniciales
        "Gilgeous-Alexander,Shai Questionable",                            # guion
        "O'Neale,Royce Out",                                               # apóstrofo
    ])
    def test_lineas_con_nombre(self, linea):
        assert _NAME_LINE_RE.search(linea)

    @pytest.mark.parametrize("linea", [
        "ClevelandCavaliers NOT YET SUBMITTED",
        "Injury/Illness-RightKnee;Tendonitis",
        "Page 1 of 4",
        "GameDate GameTime Matchup Team PlayerName CurrentStatus Reason",
    ])
    def test_lineas_sin_nombre(self, linea):
        assert not _NAME_LINE_RE.search(linea)

    def test_pdf_ilegible_no_es_veredicto(self):
        assert count_name_lines(b"%PDF-fake") is None


class TestRegla:
    def test_cero_filas_con_nombres_dispara(self):
        with pytest.raises(ParseCoverageError, match="0 filas de 60"):
            check_parse_coverage(0, 60)

    def test_frontera_80(self):
        assert PARSE_MIN_COVERAGE == 0.8
        check_parse_coverage(48, 60)  # 80% exacto: pasa
        with pytest.raises(ParseCoverageError):
            check_parse_coverage(47, 60)

    @pytest.mark.parametrize("n_lines", [0, None])
    def test_documento_sin_nombres_o_ilegible_no_dispara(self, n_lines):
        check_parse_coverage(0, n_lines)


GAME = ScheduledGame(
    game_id="0022600001", game_date=date(2026, 3, 13),
    home_team_id=1610612742, away_team_id=1610612739,
    home_tricode="DAL", away_tricode="CLE", season="2025-26",
)


def _store() -> MagicMock:
    store = MagicMock()
    store.load_teams.return_value = pd.DataFrame([
        {"team_id": 1610612742, "abbreviation": "DAL", "name": "Dallas Mavericks"},
        {"team_id": 1610612739, "abbreviation": "CLE", "name": "Cleveland Cavaliers"},
    ])
    pipe = MagicMock()
    pipe.predict_proba.return_value = np.array([[0.4, 0.6]])
    store.load_model.return_value = (pipe, {})
    return store


def _run(store, parse_side_effect=None):
    patches = [
        patch(f"{_IR}.discover_latest_snapshot", return_value=("u", "01_15PM")),
        patch(f"{_IR}.download_snapshot", return_value=PDF_2026),
        patch(_CLF, return_value={c: 0.0 for c in OFFICIAL_LOGISTIC_COLS}),
    ]
    if parse_side_effect is not None:
        patches.append(patch(f"{_IR}.parse_pdf", return_value=parse_side_effect))
    for p in patches:
        p.start()
    try:
        return build_daily_predictions(
            date(2026, 3, 13), store, season="2025-26", scheduled_games=[GAME],
            version_name="v1", player_map={1: "Jarrett Allen"}, save_injury_raw=True,
        )
    finally:
        for p in patches:
            p.stop()


class TestEnElEndpoint:
    def test_parser_mudo_se_declara_feed_caido(self):
        """El escenario D-RES-3 en vivo: PDF real con 73 nombres, parser devuelve 0."""
        store = _store()
        result = _run(store, parse_side_effect=([], []))
        assert result.feed_down is True
        assert "0 filas de 73" in result.feed_down_reason
        assert result.games[0].availability_flag == AvailabilityFlag.FEED_DOWN
        assert result.games[0].probability_home == pytest.approx(0.6)  # se predice igual
        store.save_raw_injury_report.assert_called_once()  # el PDF queda archivado

    def test_parser_vigente_sobre_pdf_real_no_degrada(self):
        result = _run(_store())
        assert result.feed_down is False

    def test_guarda_va_tras_parsear_y_tras_archivar(self):
        from nba_predictor.api import daily_predictions as dp

        src = inspect.getsource(dp._fetch_absences)
        i_guard = src.index("check_parse_coverage(len(player_rows), count_name_lines(pdf_bytes))")
        assert src.index("parse_pdf(pdf_bytes)") < i_guard
        assert src.index("save_raw_injury_report") < i_guard
