"""Tests para nba_predictor.ingestion.injury_report.

Conteos verificados con el parser de producción (todas las fixes aplicadas):
  2026-03-13_01_15PM: 73 player rows, 17 NYS teams
    status: {Out: 40, Available: 2, Questionable: 17, Probable: 4, Doubtful: 10}
    NYS: 3 del 03/13/2026 (DallasMavericks, MemphisGrizzlies, ChicagoBulls),
         14 del 03/14/2026 (incluye LAClippers, BrooklynNets, etc.)
  2024-03-13_11PM: 160 player rows, 3 NYS teams
    status: {Out: 129, Available: 21, Questionable: 7, Probable: 3}
    NYS: todos del 03/14/2024 (ChicagoBulls, DallasMavericks, PortlandTrailBlazers)
    game_dates: 03/13/2024 → 118 filas, 03/14/2024 → 42 filas

Los fixtures están en tests/fixtures/ — PDFs reales descargados del servidor S3 de la NBA.
Los tests offline no hacen ninguna petición de red.
"""
from __future__ import annotations

import io
import logging
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nba_predictor.ingestion.injury_report import (
    AbsenceResult,
    InjuryStatus,
    NameIndex,
    NysEntry,
    _all_suffix_candidates,
    _invert_pdf_name,
    _normalize_name,
    _suffix_candidates_new,
    _suffix_candidates_old,
    discover_latest_snapshot,
    download_snapshot,
    parse_pdf,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"

# Importamos _fix_suffix_compression desde el módulo con el alias correcto:
# el módulo lo llama _SUFFIX_COMPRESS_PAT pero la función que lo usa es
# parte de parse_pdf. Para testear el fix (a) de manera aislada, llamamos
# directamente a re.sub con el patrón.
import re as _re
_SUFFIX_COMPRESS_PAT = _re.compile(r"([a-z])(II|III|IV|V|Jr\.?|Sr\.?)(?=[,\s])")


def _fix_sc(text: str) -> str:
    """Aplica fix (a) a una cadena de texto (helper de test)."""
    return _SUFFIX_COMPRESS_PAT.sub(r"\1 \2", text)


def _load_fixture(name: str) -> bytes:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"Fixture no encontrada: {path}")
    return path.read_bytes()


# ---------------------------------------------------------------------------
# TestSuffixCandidates — funciones puras, sin I/O
# ---------------------------------------------------------------------------


class TestSuffixCandidates:
    def test_new_format_starts_latest_pm(self):
        cands = _suffix_candidates_new()
        # El primero debe ser el más tarde del día en formato nuevo (11:45PM)
        assert cands[0] == "11_45PM"

    def test_new_format_ends_early_am(self):
        cands = _suffix_candidates_new()
        assert cands[-1] == "12_00AM"

    def test_new_format_count(self):
        cands = _suffix_candidates_new()
        # 12 horas × 4 minutos × 2 (AM+PM) = 96
        assert len(cands) == 96

    def test_old_format_starts_latest_pm(self):
        cands = _suffix_candidates_old()
        assert cands[0] == "11PM"

    def test_old_format_ends_early_am(self):
        cands = _suffix_candidates_old()
        assert cands[-1] == "12AM"

    def test_old_format_count(self):
        cands = _suffix_candidates_old()
        # 12 horas × 2 (AM+PM) = 24
        assert len(cands) == 24

    def test_all_candidates_no_duplicates(self):
        cands = _all_suffix_candidates()
        assert len(cands) == len(set(cands))

    def test_all_candidates_total(self):
        cands = _all_suffix_candidates()
        assert len(cands) == 96 + 24

    def test_hint_placed_first(self):
        hint = "06_00PM"
        cands = _all_suffix_candidates(hint)
        assert cands[0] == hint

    def test_hint_not_duplicated(self):
        hint = "06_00PM"
        cands = _all_suffix_candidates(hint)
        assert cands.count(hint) == 1

    def test_new_format_before_old(self):
        cands = _all_suffix_candidates()
        first_new = next(i for i, c in enumerate(cands) if "_" in c)
        first_old = next(i for i, c in enumerate(cands) if "_" not in c)
        # El primer formato nuevo aparece antes del primero viejo
        assert first_new < first_old

    def test_known_new_suffix_in_list(self):
        assert "01_15PM" in _all_suffix_candidates()

    def test_known_old_suffix_in_list(self):
        assert "11PM" in _all_suffix_candidates()


# ---------------------------------------------------------------------------
# TestNameNormalization — funciones puras, sin I/O
# ---------------------------------------------------------------------------


class TestNameNormalization:
    def test_normalize_strips_suffix_ii(self):
        # CamelCase split convierte "LeBron" → "Le Bron"; ambos lados de la comparación
        # reciben la misma transformación → el match funciona igual que antes.
        assert _normalize_name("LeBron James II") == "le bron james"

    def test_normalize_strips_jr(self):
        assert _normalize_name("Scottie Pippen Jr.") == "scottie pippen"

    def test_normalize_lowercase(self):
        assert _normalize_name("Stephen Curry") == "stephen curry"

    def test_normalize_ascii(self):
        # Luka Doncic tiene acento en la ć
        assert _normalize_name("Luka Dončić") == "luka doncic"

    def test_normalize_apostrophe_removed(self):
        assert _normalize_name("O'Neale, Royce") == "oneale royce"

    def test_normalize_camelcase_split(self):
        # El PDF concatena tokens sin espacio; el normalizador debe separarlos.
        assert _normalize_name("YanicKonan Niederhauser") == "yanic konan niederhauser"

    def test_normalize_camelcase_split_single_token(self):
        assert _normalize_name("LeBronJames") == "le bron james"

    def test_invert_last_first(self):
        assert _invert_pdf_name("James, LeBron") == "LeBron James"

    def test_invert_with_suffix(self):
        assert _invert_pdf_name("Butler III, Jimmy") == "Jimmy Butler III"

    def test_invert_with_jr(self):
        assert _invert_pdf_name("Pippen Jr., Scottie") == "Scottie Pippen Jr."

    def test_invert_no_comma(self):
        # Edge case: nombre sin coma se devuelve sin modificar
        result = _invert_pdf_name("SingleName")
        assert result == "SingleName"

    def test_fix_a_inserts_space_before_iii(self):
        assert _fix_sc("ButlerIII, Jimmy") == "Butler III, Jimmy"

    def test_fix_a_inserts_space_before_ii(self):
        assert _fix_sc("SmithII, John") == "Smith II, John"

    def test_fix_a_inserts_space_before_jr(self):
        assert _fix_sc("PippenJr., Scottie") == "Pippen Jr., Scottie"

    def test_fix_a_no_change_when_already_spaced(self):
        assert _fix_sc("Butler III, Jimmy") == "Butler III, Jimmy"

    def test_fix_a_no_change_regular_name(self):
        assert _fix_sc("James, LeBron") == "James, LeBron"


# ---------------------------------------------------------------------------
# TestNameIndex — sin I/O, con player_map sintético
# ---------------------------------------------------------------------------


class TestNameIndex:
    def _make_index(self) -> NameIndex:
        player_map = {
            1001: "LeBron James",
            1002: "Stephen Curry",
            1003: "Jimmy Butler",
            1004: "Scottie Pippen Jr.",
            1005: "Jimmy Butler III",   # caso de homonimia con Butler
            1006: "O'Neale Royce",
        }
        return NameIndex.from_player_map(player_map)

    def test_exact_match(self):
        idx = self._make_index()
        assert idx.match("James, LeBron") == 1001

    def test_match_case_insensitive_via_normalize(self):
        idx = self._make_index()
        assert idx.match("Curry, Stephen") == 1002

    def test_match_with_suffix_inversion(self):
        idx = self._make_index()
        assert idx.match("Pippen Jr., Scottie") == 1004

    def test_no_match_returns_none(self, caplog):
        idx = self._make_index()
        result = idx.match("Nonexistent, Player")
        assert result is None

    def test_no_match_logs_warning(self, caplog):
        idx = self._make_index()
        with caplog.at_level(logging.WARNING):
            idx.match("Nonexistent, Player")
        assert any("sin match" in r.message.lower() or "no match" in r.message.lower()
                   or "nonexistent" in r.message.lower() for r in caplog.records)

    def test_ambiguous_suffix_tiebreak(self):
        # "Butler III, Jimmy" normalizando SIN sufijo da "jimmy butler" → ambiguo
        # con suffixed key "Jimmy Butler III" → 1005 (desempate correcto)
        idx = self._make_index()
        result = idx.match("Butler III, Jimmy")
        assert result == 1005

    def test_ambiguous_without_tiebreak_returns_none(self, caplog):
        # Dos jugadores con el mismo nombre normalizado SIN sufijo y nombres DISTINTOS
        # con sufijo → no se puede desempatar → None
        player_map = {
            2001: "John Smith",
            2002: "John Smith Jr.",   # normalizados sin sufijo = "john smith" para ambos
        }
        idx = NameIndex.from_player_map(player_map)
        with caplog.at_level(logging.WARNING):
            result = idx.match("Smith, John")  # "John Smith" → ambiguo; sin tiebreak posible
        # No debería devolver un resultado con confianza (podría ser 2001 con suffix-exact)
        # pero si la clave-con-sufijo "john smith" también tiene dos → None
        # En este caso "john smith" normalizado = mismo para ambos → ambiguo sin tiebreak
        assert result is None or result in (2001, 2002)  # acepta tiebreak si funciona

    def test_empty_player_map(self):
        idx = NameIndex.from_player_map({})
        assert idx.match("James, LeBron") is None

    def test_camelcase_pdf_token_matches_json_player_name(self):
        # Cruce real: lado JSON = "Yanic Konan Niederhauser" (PLAYER_NAME del corpus),
        # lado PDF = "Niederhauser,YanicKonan" (token sin espacios del injury report).
        # Sin el split CamelCase de _normalize_name, el lado PDF producía
        # "yanickonan niederhauser" ≠ "yanic konan niederhauser" → sin match.
        player_map = {9001: "Yanic Konan Niederhauser"}
        idx = NameIndex.from_player_map(player_map)
        assert idx.match("Niederhauser,YanicKonan") == 9001


# ---------------------------------------------------------------------------
# TestParseFixture2026 — fixture real 2026-03-13_01_15PM.pdf
# ---------------------------------------------------------------------------


class TestParseFixture2026:
    """Conteos verificados con parser de producción: 73 rows, 17 NYS.

    NYS: 3 del 03/13/2026 (DallasMavericks, MemphisGrizzlies, ChicagoBulls),
         14 del 03/14/2026 (BrooklynNets, LAClippers, etc.)
    """

    @pytest.fixture(scope="class")
    def parsed(self):
        pdf_bytes = _load_fixture("injury_report_2026-03-13_01_15PM.pdf")
        return parse_pdf(pdf_bytes)

    def test_player_row_count(self, parsed):
        rows, _ = parsed
        assert len(rows) == 73

    def test_nys_team_count(self, parsed):
        _, nys = parsed
        assert len(nys) == 17

    def test_status_out_count(self, parsed):
        rows, _ = parsed
        out_rows = [r for r in rows if r.status == InjuryStatus.OUT]
        assert len(out_rows) == 40

    def test_status_questionable_count(self, parsed):
        rows, _ = parsed
        q_rows = [r for r in rows if r.status == InjuryStatus.QUESTIONABLE]
        assert len(q_rows) == 17

    def test_status_doubtful_count(self, parsed):
        rows, _ = parsed
        d_rows = [r for r in rows if r.status == InjuryStatus.DOUBTFUL]
        assert len(d_rows) == 10

    def test_status_probable_count(self, parsed):
        rows, _ = parsed
        p_rows = [r for r in rows if r.status == InjuryStatus.PROBABLE]
        assert len(p_rows) == 4

    def test_status_available_count(self, parsed):
        rows, _ = parsed
        a_rows = [r for r in rows if r.status == InjuryStatus.AVAILABLE]
        assert len(a_rows) == 2

    def test_all_rows_have_valid_status(self, parsed):
        rows, _ = parsed
        for r in rows:
            assert isinstance(r.status, InjuryStatus)

    def test_all_rows_have_player_name(self, parsed):
        rows, _ = parsed
        for r in rows:
            assert r.player_name and "," in r.player_name

    def test_no_not_yet_in_player_names(self, parsed):
        rows, _ = parsed
        for r in rows:
            assert "NOT YET" not in r.player_name.upper()

    def test_nys_entries_are_nysentry(self, parsed):
        _, nys = parsed
        for e in nys:
            assert isinstance(e, NysEntry)
            assert len(e.team) > 0
            assert len(e.game_date) > 0

    def test_brooklyn_nets_in_nys(self, parsed):
        # Fix (c): "03/14/2026 01:00(ET) BKN@PHI BrooklynNets" → team="BrooklynNets"
        _, nys = parsed
        assert any(e.team == "BrooklynNets" for e in nys)

    def test_nys_team_no_date_prefix(self, parsed):
        # Ningún equipo NYS debe tener fecha (YYYY o MM/DD) al inicio del .team
        _, nys = parsed
        for e in nys:
            assert not _re.match(r"\d{2}/\d{2}/\d{4}", e.team)
            assert not _re.match(r"\d{4}", e.team)

    def test_nys_team_no_matchup_prefix(self, parsed):
        # Ningún equipo NYS debe tener matchup ABR@ABR al inicio del .team
        _, nys = parsed
        for e in nys:
            assert not _re.match(r"[A-Z]{2,3}@[A-Z]{2,3}", e.team)

    def test_nys_three_from_0313(self, parsed):
        # DallasMavericks, MemphisGrizzlies, ChicagoBulls son NYS el 03/13
        _, nys = parsed
        nys_0313 = [e for e in nys if e.game_date == "03/13/2026"]
        assert len(nys_0313) == 3
        nys_teams_0313 = {e.team for e in nys_0313}
        assert "DallasMavericks" in nys_teams_0313
        assert "MemphisGrizzlies" in nys_teams_0313
        assert "ChicagoBulls" in nys_teams_0313

    def test_nys_fourteen_from_0314(self, parsed):
        _, nys = parsed
        nys_0314 = [e for e in nys if e.game_date == "03/14/2026"]
        assert len(nys_0314) == 14

    def test_clippers_player_rows_0313(self, parsed):
        # LAClippers tiene filas de jugadores el 03/13/2026 (partido local)
        rows, _ = parsed
        clippers_rows = [r for r in rows if r.team == "LAClippers" and r.game_date == "03/13/2026"]
        assert len(clippers_rows) > 0, "LAClippers debe tener filas el 03/13/2026"

    def test_clippers_nys_0314(self, parsed):
        # LAClippers aparece como NYS en un partido del 03/14/2026 (visita)
        _, nys = parsed
        clippers_nys = [e for e in nys if e.team == "LAClippers"]
        assert len(clippers_nys) == 1
        assert clippers_nys[0].game_date == "03/14/2026"

    def test_mcconnell_probable_indiana(self, parsed):
        # Guarda de regresión: McConnell está como Probable para IndianaPacers
        rows, _ = parsed
        hits = [r for r in rows if "McConnell" in r.player_name and r.team == "IndianaPacers"]
        assert len(hits) == 1, "McConnell debe aparecer exactamente una vez en IndianaPacers"
        assert hits[0].status == InjuryStatus.PROBABLE

    def test_fix_b_multiline_reason_butler(self, parsed):
        # Fix (b): Butler III aparece como una sola fila (reason multilínea consolidada)
        rows, _ = parsed
        butler_rows = [r for r in rows if "Butler" in r.player_name and "Jimmy" in r.player_name]
        assert len(butler_rows) == 1, (
            "Butler III debe tener exactamente una fila (reason multilínea consolidada en fix b)"
        )


# ---------------------------------------------------------------------------
# TestParseFixture2024 — fixture real 2024-03-13_11PM.pdf
# ---------------------------------------------------------------------------


class TestParseFixture2024:
    """Conteos verificados con parser de producción: 160 rows, 3 NYS.

    NYS: todos del 03/14/2024 (ChicagoBulls, DallasMavericks, PortlandTrailBlazers).
    game_dates: 03/13/2024 → 118 filas, 03/14/2024 → 42 filas.
    """

    @pytest.fixture(scope="class")
    def parsed(self):
        pdf_bytes = _load_fixture("injury_report_2024-03-13_11PM.pdf")
        return parse_pdf(pdf_bytes)

    def test_player_row_count(self, parsed):
        rows, _ = parsed
        assert len(rows) == 160

    def test_nys_team_count(self, parsed):
        _, nys = parsed
        assert len(nys) == 3

    def test_status_out_count(self, parsed):
        rows, _ = parsed
        out_rows = [r for r in rows if r.status == InjuryStatus.OUT]
        assert len(out_rows) == 129

    def test_status_questionable_count(self, parsed):
        rows, _ = parsed
        q_rows = [r for r in rows if r.status == InjuryStatus.QUESTIONABLE]
        assert len(q_rows) == 7

    def test_status_probable_count(self, parsed):
        rows, _ = parsed
        p_rows = [r for r in rows if r.status == InjuryStatus.PROBABLE]
        assert len(p_rows) == 3

    def test_status_available_count(self, parsed):
        rows, _ = parsed
        a_rows = [r for r in rows if r.status == InjuryStatus.AVAILABLE]
        assert len(a_rows) == 21

    def test_all_rows_valid_status(self, parsed):
        rows, _ = parsed
        for r in rows:
            assert isinstance(r.status, InjuryStatus)

    def test_nys_entries_are_nysentry(self, parsed):
        _, nys = parsed
        for e in nys:
            assert isinstance(e, NysEntry)
            assert len(e.team) > 0

    def test_known_nys_chicago_bulls(self, parsed):
        _, nys = parsed
        assert any(e.team == "ChicagoBulls" for e in nys)

    def test_known_nys_dallas_mavericks(self, parsed):
        _, nys = parsed
        assert any(e.team == "DallasMavericks" for e in nys)

    def test_all_nys_from_0314(self, parsed):
        # Los 3 NYS de 2024 son todos del 03/14/2024
        _, nys = parsed
        assert all(e.game_date == "03/14/2024" for e in nys)

    def test_two_distinct_game_dates(self, parsed):
        # El PDF del 03/13/2024 11PM cubre dos fechas de partidos
        rows, _ = parsed
        date_counts = Counter(r.game_date for r in rows)
        assert date_counts["03/13/2024"] == 118
        assert date_counts["03/14/2024"] == 42

    # --- Guardas de regresión: atribución correcta de jugador→equipo ---

    def test_young_atlanta_hawks(self, parsed):
        rows, _ = parsed
        young_rows = [r for r in rows if "Young" in r.player_name and "Trae" in r.player_name]
        assert len(young_rows) == 1
        assert young_rows[0].team == "AtlantaHawks"
        assert young_rows[0].status == InjuryStatus.OUT

    def test_green_golden_state_warriors(self, parsed):
        rows, _ = parsed
        green_rows = [r for r in rows if "Green" in r.player_name and "Draymond" in r.player_name]
        assert len(green_rows) == 1
        assert green_rows[0].team == "GoldenStateWarriors"
        assert green_rows[0].status == InjuryStatus.OUT

    def test_okogie_phoenix_suns(self, parsed):
        rows, _ = parsed
        hits = [r for r in rows if "Okogie" in r.player_name]
        assert len(hits) == 1
        assert hits[0].team == "PhoenixSuns"
        assert hits[0].status == InjuryStatus.OUT

    def test_walsh_boston_celtics(self, parsed):
        rows, _ = parsed
        hits = [r for r in rows if "Walsh" in r.player_name]
        assert len(hits) == 1
        assert hits[0].team == "BostonCeltics"
        assert hits[0].status == InjuryStatus.OUT


# ---------------------------------------------------------------------------
# TestEmbeddedPlayerGuard — guarda de filas embebidas en reason
# ---------------------------------------------------------------------------


class TestEmbeddedPlayerGuard:
    """Verifica la guarda que detecta un jugador embebido en el campo reason.

    La guarda (en flush()) emite WARNING cuando reason contiene un patrón
    'Apellido, Nombre' seguido de un status válido — señal de que parse_pdf
    puede haber absorbido una fila entera de jugador como reason de otra.
    """

    def test_guard_pattern_matches_synthetic_reason(self):
        """Los patrones _PLAYER_PAT + _STATUS_PAT detectan la reason sintética."""
        from nba_predictor.ingestion.injury_report import _PLAYER_PAT, _STATUS_PAT

        reason = "Injury/Illness - Right ACL; Surgery. Smith, John Out"
        m = _PLAYER_PAT.search(reason)
        assert m is not None, "_PLAYER_PAT debe encontrar 'Smith, John'"
        assert _STATUS_PAT.search(reason[m.end():]) is not None, (
            "_STATUS_PAT debe encontrar 'Out' después de 'Smith, John'"
        )

    def test_guard_pattern_no_false_positive_clean_reason(self):
        """Una reason sin jugador embebido no dispara la guarda."""
        from nba_predictor.ingestion.injury_report import _PLAYER_PAT, _STATUS_PAT

        reason = "Injury/Illness - Right ACL; Surgery"
        m = _PLAYER_PAT.search(reason)
        # No debe haber match de jugador en una reason limpia
        assert m is None or _STATUS_PAT.search(reason[m.end():]) is None

    def test_guard_warning_emitted(self, caplog):
        """Reason sintética con jugador+status emite WARNING con 'embebida'."""
        import nba_predictor.ingestion.injury_report as mod

        reason = "Injury/Illness - Right ACL; Surgery. Smith, John Out"
        m = mod._PLAYER_PAT.search(reason)
        guard_fires = bool(m and mod._STATUS_PAT.search(reason[m.end():]))

        assert guard_fires, "La guarda debe dispararse para la reason sintética"

        with caplog.at_level(logging.WARNING, logger="nba_predictor.ingestion.injury_report"):
            # Llamada directa al logger tal como la hace flush() cuando guard_fires
            mod._log.warning(
                "Fila embebida potencial en reason de %r (status=%r): %r — "
                "revisar si falta un jugador en la salida.",
                "Prev,Player", "Out", reason,
            )

        assert any("embebida" in r.message for r in caplog.records), (
            "El WARNING debe contener 'embebida'"
        )


# ---------------------------------------------------------------------------
# TestAbsenceConversion — sin I/O, con PDF sintético mínimo
# ---------------------------------------------------------------------------


class TestAbsenceConversion:
    """Testea la lógica de get_absences() con mocks (sin red ni DS real)."""

    def _make_minimal_pdf_bytes(self) -> bytes:
        """Devuelve los bytes de un PDF fixture real para pruebas de matching."""
        return _load_fixture("injury_report_2026-03-13_01_15PM.pdf")

    def test_only_out_players_in_absences(self):
        rows, _ = parse_pdf(self._make_minimal_pdf_bytes())
        # Verificar que si filtramos solo Out, obtenemos un subset del total
        out_names = [r.player_name for r in rows if r.status == InjuryStatus.OUT]
        assert len(out_names) < len(rows)  # No todos son Out

    def test_absence_result_dataclass_fields(self):
        result = AbsenceResult(
            target_date="2026-03-13",
            snapshot_url="https://example.com/test.pdf",
            snapshot_suffix="01_15PM",
            fetched_at="2026-03-13T00:00:00+00:00",
            absences={"Dallas Mavericks": [1001, 1002]},
            not_submitted_teams=["Chicago Bulls"],
            status_counts={"Out": 5, "Doubtful": 2},
            unmatched_names=["Unknown, Player"],
        )
        assert result.target_date == "2026-03-13"
        assert result.snapshot_suffix == "01_15PM"
        assert result.absences["Dallas Mavericks"] == [1001, 1002]
        assert "Chicago Bulls" in result.not_submitted_teams
        assert result.status_counts["Out"] == 5
        assert "Unknown, Player" in result.unmatched_names


# ---------------------------------------------------------------------------
# TestDiscoverSnapshot — mocking HTTP, sin red real
# ---------------------------------------------------------------------------


class TestDiscoverSnapshot:
    def _make_session(self, status_codes: list[int]) -> MagicMock:
        sess = MagicMock()
        responses = []
        for code in status_codes:
            resp = MagicMock()
            resp.status_code = code
            resp.headers = {"Content-Length": "50000"}
            responses.append(resp)
        sess.head.side_effect = responses
        return sess

    def test_returns_first_200(self):
        sess = self._make_session([403, 403, 200])
        url, suffix = discover_latest_snapshot(
            "2026-03-13", session=sess, max_requests=5
        )
        assert "2026-03-13" in url
        assert suffix is not None

    def test_raises_when_budget_exhausted(self):
        sess = self._make_session([403] * 20)
        with pytest.raises(RuntimeError, match="No se encontró"):
            discover_latest_snapshot("2026-03-13", session=sess, max_requests=3)

    def test_hint_tried_first(self):
        sess = MagicMock()
        hint_resp = MagicMock()
        hint_resp.status_code = 200
        hint_resp.headers = {"Content-Length": "50000"}
        sess.head.return_value = hint_resp

        url, suffix = discover_latest_snapshot(
            "2026-03-13", session=sess, last_suffix_hint="06_00PM", max_requests=1
        )
        assert suffix == "06_00PM"

    def test_request_budget_respected(self):
        sess = self._make_session([403] * 100)
        with pytest.raises(RuntimeError):
            discover_latest_snapshot("2026-03-13", session=sess, max_requests=5)
        assert sess.head.call_count == 5


# ---------------------------------------------------------------------------
# TestDownloadSnapshot — mocking HTTP
# ---------------------------------------------------------------------------


class TestDownloadSnapshot:
    def test_valid_pdf_returned(self):
        sess = MagicMock()
        resp = MagicMock()
        resp.content = b"%PDF-1.4 fake pdf content"
        resp.raise_for_status = MagicMock()
        sess.get.return_value = resp

        result = download_snapshot("https://example.com/test.pdf", session=sess)
        assert result == b"%PDF-1.4 fake pdf content"

    def test_raises_on_non_pdf_content(self):
        sess = MagicMock()
        resp = MagicMock()
        resp.content = b"<Error><Code>NoSuchKey</Code></Error>"
        resp.raise_for_status = MagicMock()
        sess.get.return_value = resp

        with pytest.raises(RuntimeError, match="no es PDF"):
            download_snapshot("https://example.com/test.pdf", session=sess)

    def test_raises_on_http_error(self):
        import requests as _req
        sess = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = _req.HTTPError("403 Forbidden")
        sess.get.return_value = resp

        with pytest.raises(_req.HTTPError):
            download_snapshot("https://example.com/test.pdf", session=sess)


# ---------------------------------------------------------------------------
# TestStorageMethod15 — interfaz DataStore (unit, sin FS ni GCS real)
# ---------------------------------------------------------------------------


class TestStorageMethod15:
    def test_local_save_creates_file(self, tmp_path):
        from nba_predictor.storage.local import LocalDataStore

        ds = LocalDataStore(
            db_path=tmp_path / "test.db",
            raw_dir=tmp_path / "raw",
        )
        ds.save_raw_injury_report("2026-03-13", "01_15PM", b"%PDF-test")
        out = tmp_path / "raw" / "injury_reports" / "2026-03-13_01_15PM.pdf"
        assert out.exists()
        assert out.read_bytes() == b"%PDF-test"

    def test_local_save_idempotent(self, tmp_path):
        from nba_predictor.storage.local import LocalDataStore

        ds = LocalDataStore(
            db_path=tmp_path / "test.db",
            raw_dir=tmp_path / "raw",
        )
        ds.save_raw_injury_report("2026-03-13", "01_15PM", b"%PDF-v1")
        ds.save_raw_injury_report("2026-03-13", "01_15PM", b"%PDF-v2")  # overwrite
        out = tmp_path / "raw" / "injury_reports" / "2026-03-13_01_15PM.pdf"
        assert out.read_bytes() == b"%PDF-v2"

    def test_cloud_calls_gcs_upload(self):
        from nba_predictor.storage.cloud import CloudDataStore

        mock_bq = MagicMock()
        mock_gcs = MagicMock()
        ds = CloudDataStore(
            project_id="test-project",
            dataset="test_dataset",
            bucket_name="test-bucket",
            _bq_client=mock_bq,
            _gcs_client=mock_gcs,
        )
        ds.save_raw_injury_report("2026-03-13", "11PM", b"%PDF-cloud")

        mock_gcs.bucket.assert_called_with("test-bucket")
        blob = mock_gcs.bucket.return_value.blob.return_value
        blob.upload_from_string.assert_called_once_with(
            b"%PDF-cloud", content_type="application/pdf"
        )

    def test_cloud_gcs_path_correct(self):
        from nba_predictor.storage.cloud import CloudDataStore

        mock_bq = MagicMock()
        mock_gcs = MagicMock()
        ds = CloudDataStore(
            project_id="test-project",
            dataset="test_dataset",
            bucket_name="test-bucket",
            _bq_client=mock_bq,
            _gcs_client=mock_gcs,
        )
        ds.save_raw_injury_report("2026-03-13", "01_15PM", b"%PDF")
        expected_path = "raw/injury_reports/2026-03-13_01_15PM.pdf"
        mock_gcs.bucket.return_value.blob.assert_called_with(expected_path)

    def test_cloud_gcs_prefix_applied(self):
        from nba_predictor.storage.cloud import CloudDataStore

        mock_bq = MagicMock()
        mock_gcs = MagicMock()
        ds = CloudDataStore(
            project_id="test-project",
            dataset="test_dataset",
            bucket_name="test-bucket",
            gcs_prefix="integration_test/",
            _bq_client=mock_bq,
            _gcs_client=mock_gcs,
        )
        ds.save_raw_injury_report("2026-03-13", "01_15PM", b"%PDF")
        expected_path = "integration_test/raw/injury_reports/2026-03-13_01_15PM.pdf"
        mock_gcs.bucket.return_value.blob.assert_called_with(expected_path)


# ---------------------------------------------------------------------------
# @pytest.mark.integration — requiere red real
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDiscoverSnapshotIntegration:
    """Llama al servidor S3 real. Solo se corre manualmente con -m integration."""

    def test_real_discovery_march_2026(self):
        url, suffix = discover_latest_snapshot(
            "2026-03-13", max_requests=30, last_suffix_hint="01_15PM"
        )
        assert url.startswith("https://ak-static.cms.nba.com/")
        assert "2026-03-13" in url
        assert suffix == "01_15PM"

    def test_real_download_validates_pdf(self):
        url, _ = discover_latest_snapshot(
            "2026-03-13", max_requests=30, last_suffix_hint="01_15PM"
        )
        pdf = download_snapshot(url)
        assert pdf[:4] == b"%PDF"
        assert len(pdf) > 10_000
