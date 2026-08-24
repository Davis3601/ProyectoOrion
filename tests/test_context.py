"""
Tests del Grupo 4 — Features de contexto (nba_predictor/features/context.py).

Cuatro clases de tests:
  (a) TestRestDaysAndB2b   : casos sintéticos con fechas conocidas.
                             lunes→martes = b2b (rest=0);
                             lunes→miércoles = 1 día de descanso;
                             descanso asimétrico: local b2b, visitante 2 días.
  (b) TestCap              : gap de 100 días → capeado en REST_DAYS_CAP.
  (c) TestFirstGameNaN     : primer partido histórico del equipo → NaN.
  (d) TestSanityRealData   : datos reales — % b2b en [20%, 40%];
                             distribución de rest_days concentrada en 0-3.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from nba_predictor.config import REST_DAYS_CAP
from nba_predictor.features.context import build_context, compute_context, compute_team_rest


# ---------------------------------------------------------------------------
# Helper: construye un DataFrame de partidos mínimo para tests sintéticos
# ---------------------------------------------------------------------------

def _games(*rows: dict) -> pd.DataFrame:
    """DataFrame de partidos con defaults de columnas opcionales."""
    defaults = {"season": "2016-17", "neutral_site": 0}
    return pd.DataFrame([{**defaults, **r} for r in rows])


# ---------------------------------------------------------------------------
# (a) Fechas conocidas — rest_days y b2b
# ---------------------------------------------------------------------------

class TestRestDaysAndB2b:
    """
    Verificación con fechas exactas del calendario.
    Lunes 2016-10-03, martes 2016-10-04, miércoles 2016-10-05, jueves 2016-10-06.
    """

    def test_back_to_back_both_teams(self):
        """lunes→martes: gap=1d → rest_days=0 → b2b=1 para ambos equipos."""
        games = _games(
            {"game_id": "g0", "game_date": date(2016, 10, 3),  # lun
             "home_team_id": 1, "away_team_id": 2},
            {"game_id": "g1", "game_date": date(2016, 10, 4),  # mar — ambos b2b
             "home_team_id": 1, "away_team_id": 2},
        )
        result = compute_context(games)
        g1 = result[result["game_id"] == "g1"].iloc[0]

        assert g1["home_b2b"] == 1.0
        assert g1["away_b2b"] == 1.0
        assert g1["rest_diff"] == pytest.approx(0.0)

    def test_one_rest_day_both_teams(self):
        """lunes→miércoles: gap=2d → rest_days=1 → no b2b."""
        games = _games(
            {"game_id": "g0", "game_date": date(2016, 10, 3),  # lun
             "home_team_id": 1, "away_team_id": 2},
            {"game_id": "g1", "game_date": date(2016, 10, 5),  # mié — 1 día de descanso
             "home_team_id": 1, "away_team_id": 2},
        )
        result = compute_context(games)
        g1 = result[result["game_id"] == "g1"].iloc[0]

        assert g1["home_b2b"] == 0.0
        assert g1["away_b2b"] == 0.0
        assert g1["rest_diff"] == pytest.approx(0.0)

        # Verificar rest_days directamente en la función de nivel equipo
        team_rest = compute_team_rest(games)
        team1_g1 = team_rest[(team_rest["team_id"] == 1) & (team_rest["game_id"] == "g1")].iloc[0]
        assert team1_g1["rest_days"] == pytest.approx(1.0)

    def test_asymmetric_rest_home_b2b_away_rested(self):
        """
        Escenario asimétrico:
          g0 (sáb): equipo 2 (local) vs equipo 3 (visit) — ambos debutan
          g1 (lun): equipo 1 (local) vs equipo 3 (visit) — equipo 1 debuta;
                    equipo 3: sáb→lun = gap 2d → rest_days = 1
          g2 (mar): equipo 1 (local) vs equipo 2 (visit)
                    equipo 1: lun→mar = gap 1d → rest_days = 0 → b2b = 1
                    equipo 2: sáb→mar = gap 3d → rest_days = 2 → b2b = 0
                    rest_diff = 0 − 2 = −2
        """
        games = _games(
            {"game_id": "g0", "game_date": date(2016, 10, 1),  # sáb
             "home_team_id": 2, "away_team_id": 3},
            {"game_id": "g1", "game_date": date(2016, 10, 3),  # lun
             "home_team_id": 1, "away_team_id": 3},
            {"game_id": "g2", "game_date": date(2016, 10, 4),  # mar
             "home_team_id": 1, "away_team_id": 2},
        )
        result = compute_context(games)
        g2 = result[result["game_id"] == "g2"].iloc[0]

        assert g2["home_b2b"] == 1.0, "equipo 1 jugó ayer → b2b"
        assert g2["away_b2b"] == 0.0, "equipo 2 tiene 2 días de descanso"
        assert g2["rest_diff"] == pytest.approx(-2.0)

    def test_neutral_site_pass_through(self):
        """neutral_site se copia directo de la columna en games."""
        games = _games(
            {"game_id": "g0", "game_date": date(2016, 10, 1),
             "home_team_id": 1, "away_team_id": 2, "neutral_site": 1},
            {"game_id": "g1", "game_date": date(2016, 10, 2),
             "home_team_id": 1, "away_team_id": 2, "neutral_site": 0},
        )
        result = compute_context(games)
        assert result[result["game_id"] == "g0"].iloc[0]["neutral_site"] == 1
        assert result[result["game_id"] == "g1"].iloc[0]["neutral_site"] == 0


# ---------------------------------------------------------------------------
# (b) Cap en REST_DAYS_CAP
# ---------------------------------------------------------------------------

class TestCap:
    """Gap > REST_DAYS_CAP (e.g. 100 días) se recorta a REST_DAYS_CAP."""

    def test_large_gap_capped(self):
        """100 días de gap → rest_days = REST_DAYS_CAP."""
        # date(2016,10,1) a date(2017,1,9) = 100 días
        games = _games(
            {"game_id": "g0", "game_date": date(2016, 10, 1),
             "home_team_id": 1, "away_team_id": 2},
            {"game_id": "g1", "game_date": date(2017,  1,  9),
             "home_team_id": 1, "away_team_id": 2},
        )
        team_rest = compute_team_rest(games)
        team1_g1 = team_rest[
            (team_rest["team_id"] == 1) & (team_rest["game_id"] == "g1")
        ].iloc[0]

        raw_gap = (date(2017, 1, 9) - date(2016, 10, 1)).days  # 100
        assert raw_gap - 1 > REST_DAYS_CAP, "sanity: el gap raw supera el cap"
        assert team1_g1["rest_days"] == pytest.approx(float(REST_DAYS_CAP))

    def test_just_at_cap_not_truncated(self):
        """rest_days = REST_DAYS_CAP exacto (gap = CAP+1) → no cambia."""
        gap_days = REST_DAYS_CAP + 1  # produce rest_days = CAP exacto
        start = date(2016, 10, 1)
        end_date = date(start.year, start.month, start.day + gap_days)
        # Calculamos manualmente para evitar confusión de meses
        import datetime
        end_date = start + datetime.timedelta(days=gap_days)

        games = _games(
            {"game_id": "g0", "game_date": start,
             "home_team_id": 1, "away_team_id": 2},
            {"game_id": "g1", "game_date": end_date,
             "home_team_id": 1, "away_team_id": 2},
        )
        team_rest = compute_team_rest(games)
        team1_g1 = team_rest[
            (team_rest["team_id"] == 1) & (team_rest["game_id"] == "g1")
        ].iloc[0]
        assert team1_g1["rest_days"] == pytest.approx(float(REST_DAYS_CAP))


# ---------------------------------------------------------------------------
# (c) Primer partido histórico → NaN
# ---------------------------------------------------------------------------

class TestFirstGameNaN:
    """El primer partido de un equipo en el dataset no tiene partido anterior."""

    def _single_game(self) -> pd.DataFrame:
        return _games(
            {"game_id": "g0", "game_date": date(2016, 10, 1),
             "home_team_id": 1, "away_team_id": 2},
        )

    def test_rest_days_nan_for_first_game(self):
        team_rest = compute_team_rest(self._single_game())
        for tid in (1, 2):
            row = team_rest[(team_rest["team_id"] == tid) & (team_rest["game_id"] == "g0")].iloc[0]
            assert pd.isna(row["rest_days"]), f"team {tid}: rest_days debería ser NaN"

    def test_is_b2b_nan_for_first_game(self):
        team_rest = compute_team_rest(self._single_game())
        for tid in (1, 2):
            row = team_rest[(team_rest["team_id"] == tid) & (team_rest["game_id"] == "g0")].iloc[0]
            assert pd.isna(row["is_b2b"]), f"team {tid}: is_b2b debería ser NaN"

    def test_rest_diff_nan_when_first_game(self):
        """rest_diff también es NaN si alguno de los equipos no tiene partido previo."""
        result = compute_context(self._single_game())
        row = result[result["game_id"] == "g0"].iloc[0]
        assert pd.isna(row["rest_diff"])
        assert pd.isna(row["home_b2b"])
        assert pd.isna(row["away_b2b"])

    def test_second_game_has_valid_rest(self):
        """Tras el primer partido, el segundo del mismo equipo sí tiene rest_days válido."""
        games = _games(
            {"game_id": "g0", "game_date": date(2016, 10, 1),
             "home_team_id": 1, "away_team_id": 2},
            {"game_id": "g1", "game_date": date(2016, 10, 3),
             "home_team_id": 1, "away_team_id": 2},
        )
        team_rest = compute_team_rest(games)
        team1_g1 = team_rest[
            (team_rest["team_id"] == 1) & (team_rest["game_id"] == "g1")
        ].iloc[0]
        assert not pd.isna(team1_g1["rest_days"])
        assert team1_g1["rest_days"] == pytest.approx(1.0)  # gap=2d → rest=1


# ---------------------------------------------------------------------------
# (d) Sanidad sobre datos reales
# ---------------------------------------------------------------------------

class TestSanityRealData:
    """
    Checks empíricos sobre los 14 429 partidos descargados:
    - Frecuencia de b2b (al menos un equipo en b2b) ∈ [20%, 40%].
    - ≥ 80% de rest_diff en rango [−3, 3] (schedules típicos NBA).
    - Partidos con neutral_site=1 entre 50 y 150 (burbuja COVID 2020 = 88).
    """

    def test_b2b_frequency_in_realistic_range(self):
        result = build_context()
        valid = result.dropna(subset=["home_b2b", "away_b2b"])
        has_b2b = (valid["home_b2b"] == 1) | (valid["away_b2b"] == 1)
        pct = has_b2b.mean() * 100
        assert 20.0 <= pct <= 40.0, (
            f"Frecuencia de b2b (al menos un equipo): {pct:.1f}% "
            f"fuera del rango esperado [20%, 40%]"
        )

    def test_rest_diff_concentrated_in_short_range(self):
        result = build_context()
        valid = result.dropna(subset=["rest_diff"])
        in_range = (valid["rest_diff"].abs() <= 3).mean() * 100
        assert in_range >= 80.0, (
            f"Solo {in_range:.1f}% de rest_diff en [−3, 3]; "
            f"se esperan ≥80% (schedule NBA estándar)"
        )

    def test_neutral_site_count_matches_bubble(self):
        result = build_context()
        n_neutral = (result["neutral_site"] == 1).sum()
        # Burbuja COVID 2020 = 88 partidos marcados como neutral_site
        assert 50 <= n_neutral <= 150, (
            f"Partidos neutral_site=1: {n_neutral}; se esperan 50-150 "
            f"(burbuja COVID 2020 = 88)"
        )

    def test_output_columns_and_row_count(self):
        result = build_context()
        from nba_predictor.features.context import OUTPUT_COLS
        assert list(result.columns) == OUTPUT_COLS
        assert len(result) == 14_429, (
            f"Se esperan 14 429 partidos, obtenidos {len(result)}"
        )
