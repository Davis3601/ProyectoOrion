"""
Tests del Grupo 1 — Four Factors (nba_predictor/features/four_factors.py).

Cuatro clases de tests:
    (a) No-leakage: un valor extremo (999) en el partido K no aparece en efg_diff de K.
    (b) Convención de signo: cuando el local tiene mejores stats, los diffs reflejan
        la ventaja con el signo correcto para cada factor.
    (c) Verificación numérica: valores conocidos → fórmulas comprobadas a mano.
    (d) Ratios-sobre-promedios: games con distinto volumen de tiro verifican que
        se usa ratio del promedio (correcto), no promedio de ratios (incorrecto).
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from nba_predictor.features.four_factors import compute_four_factors

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_START = date(2016, 10, 1)
_SEASON = "2016-17"


def _row(
    team_id: int,
    game_id: str,
    game_date: date,
    is_home: int,
    *,
    fgm: float,
    fg3m: float,
    fga: float,
    fta: float,
    oreb: float,
    tov: float,
    opp_dreb: float,
    season: str = _SEASON,
) -> dict:
    """Construye una fila sintética de stats (equipo, partido)."""
    return {
        "team_id": team_id,
        "game_id": game_id,
        "game_date": game_date,
        "season": season,
        "is_home": is_home,
        "fgm": fgm,
        "fg3m": fg3m,
        "fga": fga,
        "fta": fta,
        "oreb": oreb,
        "tov": tov,
        "opp_dreb": opp_dreb,
    }


def _build_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _typical_home(**overrides) -> dict:
    """Stats 'típicas' para el equipo local (team_id=1, is_home=1)."""
    base = dict(fgm=40, fg3m=8, fga=85, fta=22, oreb=10, tov=12, opp_dreb=30)
    return {**base, **overrides}


def _typical_away(**overrides) -> dict:
    """Stats 'típicas' para el equipo visitante (team_id=2, is_home=0)."""
    base = dict(fgm=38, fg3m=7, fga=84, fta=20, oreb=9, tov=13, opp_dreb=28)
    return {**base, **overrides}


def _make_games(
    n: int,
    home_stats_list: list[dict] | None = None,
    away_stats_list: list[dict] | None = None,
    start: date = _START,
) -> pd.DataFrame:
    """
    Crea un DataFrame sintético con n partidos consecutivos.

    Si home_stats_list / away_stats_list es None, usa stats típicas para todos.
    Si es una lista, el índice i de la lista se aplica al partido i.
    """
    rows = []
    for i in range(n):
        gid = f"g{i}"
        gdate = start + timedelta(days=i)
        hs = home_stats_list[i] if home_stats_list else _typical_home()
        aws = away_stats_list[i] if away_stats_list else _typical_away()
        rows.append(_row(1, gid, gdate, is_home=1, **hs))
        rows.append(_row(2, gid, gdate, is_home=0, **aws))
    return _build_df(rows)


# ---------------------------------------------------------------------------
# (a) No-leakage — test reina sobre efg_diff
# ---------------------------------------------------------------------------

class TestNoLeakage:
    """
    Plantar fgm=999 en el partido K del equipo local.
    La efg_diff del partido K debe reflejar solo los partidos 0..K-1 (sin 999).
    La efg_diff del partido K+1 sí debe verse afectada (999 ya es historial).
    """

    WINDOW = 3

    def _build(self, n: int, extreme_at: int) -> pd.DataFrame:
        home_stats = []
        for i in range(n):
            if i == extreme_at:
                home_stats.append(_typical_home(fgm=999))
            else:
                home_stats.append(_typical_home())
        return _make_games(n, home_stats_list=home_stats)

    def test_extreme_value_absent_from_own_efg_diff(self):
        # 7 partidos, fgm=999 en el partido 3.
        # Con WINDOW=3, el partido 3 ve partidos 0, 1, 2 (todos fgm=40) → normal.
        df = self._build(n=7, extreme_at=3)
        result = (
            compute_four_factors(df, window=self.WINDOW)
            .sort_values("game_date")
            .reset_index(drop=True)
        )

        efg_diff_at_3 = result.loc[3, "efg_diff"]

        # Valor esperado: efg basado en fgm=40 (típico), sin el 999 del partido 3
        expected_home_efg = (40 + 0.5 * 8) / 85  # fgm_rolling=40, fg3m_rolling=8, fga_rolling=85
        expected_away_efg = (38 + 0.5 * 7) / 84
        expected_diff = expected_home_efg - expected_away_efg

        assert efg_diff_at_3 == pytest.approx(expected_diff, rel=1e-4), (
            f"LEAKAGE en efg_diff: partido 3 tiene {efg_diff_at_3:.4f}, "
            f"esperado {expected_diff:.4f} (no debe incluir fgm=999)"
        )

    def test_extreme_value_visible_in_next_game(self):
        # El partido 4 mira los partidos 1, 2, 3 (incluyendo fgm=999 del partido 3).
        # La efg_home del partido 4 debe ser anormalmente alta.
        df = self._build(n=7, extreme_at=3)
        result = (
            compute_four_factors(df, window=self.WINDOW)
            .sort_values("game_date")
            .reset_index(drop=True)
        )

        # fgm_rolling en partido 4 = mean(40, 40, 999) = 1079/3 >> 40
        # efg_home debería ser >> normal
        efg_diff_at_4 = result.loc[4, "efg_diff"]
        normal_diff = (40 + 0.5 * 8) / 85 - (38 + 0.5 * 7) / 84

        assert efg_diff_at_4 > normal_diff + 1.0, (
            f"Partido 4 debería ver el 999 en su ventana: "
            f"efg_diff={efg_diff_at_4:.4f}, normal={normal_diff:.4f}"
        )


# ---------------------------------------------------------------------------
# (b) Convención de signo — local claramente superior
# ---------------------------------------------------------------------------

class TestSignConvention:
    """
    Construir un caso donde el local tiene stats marcadamente mejores.
    Verificar que el signo de cada diff es el correcto según LOCAL − VISITANTE:
      - efg_diff     > 0  (local tiene mayor eFG%   → ventaja local)
      - tov_rate_diff< 0  (local pierde MENOS balones; diff negativa es ventaja local
                           bajo la convención LOCAL − VISITANTE — el modelo aprende
                           el coeficiente negativo)
      - oreb_rate_diff> 0 (local tiene mayor OREB%   → ventaja local)
      - ft_rate_diff > 0  (local tiene mayor FT rate → ventaja local)
    """

    WINDOW = 3

    def _build_dominant_home(self) -> pd.DataFrame:
        """
        Local (team_id=1): alto eFG, bajo TOV, alto OREB, alto FT rate.
        Visitante (team_id=2): bajo eFG, alto TOV, bajo OREB, bajo FT rate.
        """
        # 4 partidos: los primeros 3 sirven de historia, el 4º es el objetivo.
        home_stats = [dict(fgm=50, fg3m=12, fga=85, fta=28, oreb=14, tov=8,  opp_dreb=25)] * 4
        away_stats = [dict(fgm=30, fg3m= 5, fga=85, fta=15, oreb= 7, tov=18, opp_dreb=35)] * 4
        return _make_games(4, home_stats_list=home_stats, away_stats_list=away_stats)

    def test_efg_diff_positive_when_home_better(self):
        df = self._build_dominant_home()
        result = compute_four_factors(df, window=self.WINDOW).dropna()
        assert (result["efg_diff"] > 0).all(), (
            f"efg_diff debe ser positiva cuando local tira mejor:\n{result['efg_diff'].tolist()}"
        )

    def test_tov_rate_diff_negative_when_home_loses_fewer_balls(self):
        # Local pierde MENOS balones → tov_rate_home < tov_rate_away
        # → tov_rate_diff = home − away < 0 (convención LOCAL − VISITANTE)
        df = self._build_dominant_home()
        result = compute_four_factors(df, window=self.WINDOW).dropna()
        assert (result["tov_rate_diff"] < 0).all(), (
            f"tov_rate_diff debe ser negativa cuando local pierde menos balones "
            f"(diff = home_TOV% − away_TOV%, y home_TOV% < away_TOV%):\n"
            f"{result['tov_rate_diff'].tolist()}"
        )

    def test_oreb_rate_diff_positive_when_home_better(self):
        df = self._build_dominant_home()
        result = compute_four_factors(df, window=self.WINDOW).dropna()
        assert (result["oreb_rate_diff"] > 0).all(), (
            f"oreb_rate_diff debe ser positiva cuando local tiene mayor OREB%:\n"
            f"{result['oreb_rate_diff'].tolist()}"
        )

    def test_ft_rate_diff_positive_when_home_better(self):
        df = self._build_dominant_home()
        result = compute_four_factors(df, window=self.WINDOW).dropna()
        assert (result["ft_rate_diff"] > 0).all(), (
            f"ft_rate_diff debe ser positiva cuando local tiene mayor FT rate:\n"
            f"{result['ft_rate_diff'].tolist()}"
        )


# ---------------------------------------------------------------------------
# (c) Verificación numérica — fórmulas comprobadas a mano
# ---------------------------------------------------------------------------

class TestNumericalFormulas:
    """
    Datos fijos con los que se puede calcular cada ratio a mano y comparar.
    Ventana = 3 partidos. Los partidos 0, 1, 2 sirven de historia (todos
    idénticos para que la media rolling sea el valor exacto). El partido 3 es
    el objetivo.
    """

    WINDOW = 3

    # Datos del equipo local (constantes → media rolling = el propio valor)
    HOME = dict(fgm=40, fg3m=10, fga=80, fta=24, oreb=10, tov=10, opp_dreb=30)
    # Datos del equipo visitante
    AWAY = dict(fgm=35, fg3m=5,  fga=80, fta=16, oreb=8,  tov=16, opp_dreb=32)

    # Valores esperados (calculados a mano):
    #   efg_home  = (40 + 0.5×10) / 80     = 45/80   = 0.5625
    #   efg_away  = (35 + 0.5×5)  / 80     = 37.5/80 = 0.46875
    #   tov_home  = 10 / (80 + 0.44×24 + 10) = 10/100.56 ≈ 0.09944
    #   tov_away  = 16 / (80 + 0.44×16 + 16) = 16/103.04 ≈ 0.15528
    #   oreb_home = 10 / (10 + 30) = 0.25
    #   oreb_away =  8 / (8  + 32) = 0.20
    #   ft_home   = 24 / 80 = 0.30
    #   ft_away   = 16 / 80 = 0.20

    EXPECTED_EFG_DIFF      = 45 / 80 - 37.5 / 80         # = 0.09375
    EXPECTED_TOV_DIFF      = 10 / 100.56 - 16 / 103.04   # ≈ -0.05584
    EXPECTED_OREB_DIFF     = 10 / 40 - 8 / 40            # = 0.05
    EXPECTED_FT_DIFF       = 24 / 80 - 16 / 80           # = 0.10

    def _build(self) -> pd.DataFrame:
        home_stats = [self.HOME] * 4
        away_stats = [self.AWAY] * 4
        return _make_games(4, home_stats_list=home_stats, away_stats_list=away_stats)

    def _get_target_row(self) -> pd.Series:
        result = compute_four_factors(self._build(), window=self.WINDOW).dropna()
        assert len(result) == 1, f"Se esperaba 1 fila válida, hay {len(result)}"
        return result.iloc[0]

    def test_efg_diff_formula(self):
        row = self._get_target_row()
        assert row["efg_diff"] == pytest.approx(self.EXPECTED_EFG_DIFF, rel=1e-5)

    def test_tov_rate_diff_formula(self):
        row = self._get_target_row()
        assert row["tov_rate_diff"] == pytest.approx(self.EXPECTED_TOV_DIFF, rel=1e-4)

    def test_oreb_rate_diff_formula(self):
        row = self._get_target_row()
        assert row["oreb_rate_diff"] == pytest.approx(self.EXPECTED_OREB_DIFF, rel=1e-5)

    def test_ft_rate_diff_formula(self):
        row = self._get_target_row()
        assert row["ft_rate_diff"] == pytest.approx(self.EXPECTED_FT_DIFF, rel=1e-5)


# ---------------------------------------------------------------------------
# (d) Ratio-sobre-promedios vs. promedio-de-ratios
# ---------------------------------------------------------------------------

class TestRatioOfAverages:
    """
    Verifica que la implementación calcula el ratio SOBRE los promedios rolling
    (correcto), no el promedio de los ratios por partido (incorrecto).

    Caso sintético con volúmenes de tiro distintos:
        Local — juego 0: fgm=20, fga=40  → eFG=50%
        Local — juego 1: fgm=60, fga=100 → eFG=60%

        Promedio de ratios    : (50%+60%)/2         = 55.0 %  ← INCORRECTO
        Ratio del promedio    : (20+60)/(40+100)    ≈ 57.14 % ← CORRECTO

    Visitante tiene fgm=40, fga=70 en ambos juegos → eFG rolling = 40/70 ≈ 57.14%

    Resultado esperado:
        efg_diff correcto  = 40/70 − 40/70 = 0.0
        efg_diff incorrecto= 55% − 57.14% ≈ −0.021  (habría diferencia ≠ 0)
    """

    WINDOW = 2

    def _build(self) -> pd.DataFrame:
        # Minimos tov/fta/oreb para que no haya NaN; opp_dreb consistente.
        minimal = dict(fg3m=0, fta=1, oreb=1, tov=1, opp_dreb=9)

        home_stats = [
            dict(fgm=20, fga=40,  **minimal),  # juego 0
            dict(fgm=60, fga=100, **minimal),  # juego 1
            dict(fgm=40, fga=70,  **minimal),  # juego 2 (objetivo — stats propias no importan)
        ]
        away_stats = [
            dict(fgm=40, fga=70, **minimal),   # juego 0: uniforme
            dict(fgm=40, fga=70, **minimal),   # juego 1
            dict(fgm=40, fga=70, **minimal),   # juego 2
        ]
        return _make_games(3, home_stats_list=home_stats, away_stats_list=away_stats)

    def test_ratio_of_averages_not_average_of_ratios(self):
        """
        Con ratio-del-promedio (correcto): efg_home ≈ 57.14% = efg_away → diff ≈ 0.0
        Con promedio-de-ratios (incorrecto): efg_home = 55% ≠ efg_away → diff ≈ −0.021
        """
        df = self._build()
        result = compute_four_factors(df, window=self.WINDOW).dropna()
        assert len(result) == 1, f"Se esperaba 1 fila válida, hay {len(result)}"

        efg_diff = result.iloc[0]["efg_diff"]

        # Correcto: (20+60)/(40+100) − 40/70 = 40/70 − 40/70 = 0.0
        correct = 40 / 70 - 40 / 70
        # Incorrecto: (50%+60%)/2 − 40/70 = 0.55 − 4/7 ≈ −0.021
        wrong = (20 / 40 + 60 / 100) / 2 - 40 / 70

        assert efg_diff == pytest.approx(correct, abs=1e-6), (
            f"La implementación usa promedio-de-ratios (incorrecto).\n"
            f"  Obtenido: {efg_diff:.6f}\n"
            f"  Esperado (ratio-del-promedio): {correct:.6f}\n"
            f"  Valor incorrecto sería: {wrong:.6f}"
        )


# ---------------------------------------------------------------------------
# Validaciones de interfaz
# ---------------------------------------------------------------------------

class TestInterfaceValidation:

    def test_missing_opp_dreb_raises(self):
        df = pd.DataFrame({
            "team_id": [1], "game_id": ["g0"], "game_date": [_START],
            "season": [_SEASON], "is_home": [1],
            "fgm": [40], "fg3m": [8], "fga": [85], "fta": [22],
            "oreb": [10], "tov": [12],
            # opp_dreb AUSENTE intencionalmente
        })
        with pytest.raises(ValueError, match="opp_dreb"):
            compute_four_factors(df)

    def test_insufficient_history_returns_nan(self):
        """Partidos sin historia suficiente → NaN en todos los diffs."""
        df = _make_games(2)  # Solo 2 partidos, window=10 por defecto
        result = compute_four_factors(df)
        assert result[["efg_diff", "tov_rate_diff", "oreb_rate_diff", "ft_rate_diff"]].isna().all().all()

    def test_output_columns(self):
        df = _make_games(5)
        result = compute_four_factors(df, window=3)
        expected_cols = {"game_id", "season", "game_date", "efg_diff", "tov_rate_diff", "oreb_rate_diff", "ft_rate_diff"}
        assert expected_cols.issubset(set(result.columns))

    def test_one_row_per_game(self):
        """La salida tiene exactamente una fila por partido."""
        n_games = 6
        df = _make_games(n_games)
        result = compute_four_factors(df, window=3)
        assert len(result) == n_games
        assert result["game_id"].nunique() == n_games
