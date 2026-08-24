"""
Tests de no-leakage para nba_predictor/features/rolling.py.

Cuatro clases de tests, en orden de severidad:
  (a) Test reina: un valor extremo (999) en el partido K NO aparece en la
      media móvil de K mismo, pero SÍ en la de K+1 y posteriores.
  (b) Direccionalidad: ningún partido ve estadísticas de partidos futuros.
  (c) Cruce de temporadas: la ventana cruza el límite de temporada sin
      reiniciarse y sin mezclar equipos.
  (d) Sin historia suficiente → NaN; nunca se imputa.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from nba_predictor.features.rolling import compute_rolling_means


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_df(
    team_id: int,
    n: int,
    value: float = 1.0,
    start: date = date(2016, 10, 1),
    season: str = "2016-17",
    extra_values: dict[int, float] | None = None,
) -> pd.DataFrame:
    """DataFrame sintético de stats de un equipo: una fila por partido."""
    values = [value] * n
    if extra_values:
        for idx, val in extra_values.items():
            values[idx] = val
    return pd.DataFrame(
        {
            "team_id": team_id,
            "game_date": [start + timedelta(days=i) for i in range(n)],
            "season": season,
            "stat": values,
        }
    )


# ---------------------------------------------------------------------------
# (a) Test reina — el valor plantado NO contamina su propia media móvil
# ---------------------------------------------------------------------------

class TestReinaNoLeakage:
    """
    Plantar 999 en el partido K.
    - La media móvil EN K debe reflejar solo K-1, K-2, … → no 999.
    - La media móvil EN K+1 debe incluir 999.
    """

    WINDOW = 3

    def test_extreme_value_absent_from_own_rolling(self):
        # Partidos 0-5: todos 1.0 excepto el partido 3 que vale 999.
        # Con WINDOW=3, el partido 3 necesita ver los partidos 0, 1, 2 (todos 1.0).
        # Si hay leakage, la media en 3 sería (1.0+1.0+999.0)/3 ≈ 333.7 en vez de 1.0.
        df = _make_df(1, n=6, value=1.0, extra_values={3: 999.0})
        result = (
            compute_rolling_means(df, stat_cols=["stat"], window=self.WINDOW)
            .sort_values("game_date")
            .reset_index(drop=True)
        )

        rolling_at_3 = result.loc[3, "stat_rolling"]
        assert rolling_at_3 == pytest.approx(1.0), (
            f"LEAKAGE DETECTADO: la media en el partido 3 es {rolling_at_3!r} "
            f"(debería ser 1.0, no reflejar el 999 plantado ahí)"
        )

    def test_extreme_value_visible_to_next_game(self):
        # El partido 4 tiene ventana [partidos 1, 2, 3] → incluye el 999.
        # Esperado: (1.0 + 1.0 + 999.0) / 3 = 333.666...
        df = _make_df(1, n=7, value=1.0, extra_values={3: 999.0})
        result = (
            compute_rolling_means(df, stat_cols=["stat"], window=self.WINDOW)
            .sort_values("game_date")
            .reset_index(drop=True)
        )

        expected = (1.0 + 1.0 + 999.0) / 3
        rolling_at_4 = result.loc[4, "stat_rolling"]
        assert rolling_at_4 == pytest.approx(expected), (
            f"El partido posterior a 999 debería verlo: "
            f"esperado {expected:.4f}, obtenido {rolling_at_4!r}"
        )


# ---------------------------------------------------------------------------
# (b) Direccionalidad — la ventana nunca mira al futuro
# ---------------------------------------------------------------------------

class TestNoFutureLeakage:
    """
    Plantar 999 al FINAL de la serie. Los partidos anteriores deben tener
    media 1.0 — nunca deben ver el valor del futuro.
    """

    WINDOW = 2

    def test_future_game_does_not_affect_past_rolling(self):
        # Partidos 0-5: todos 1.0 excepto el último (índice 5) que vale 999.
        # Los partidos 2, 3, 4 deben tener media 1.0 (el 999 está en el futuro).
        df = _make_df(1, n=6, value=1.0, extra_values={5: 999.0})
        result = (
            compute_rolling_means(df, stat_cols=["stat"], window=self.WINDOW)
            .sort_values("game_date")
            .reset_index(drop=True)
        )

        for idx in [2, 3, 4]:
            val = result.loc[idx, "stat_rolling"]
            assert val == pytest.approx(1.0), (
                f"LEAKAGE FUTURO en partido {idx}: "
                f"obtenido {val!r}, debería ser 1.0 (el 999 está en el futuro)"
            )


# ---------------------------------------------------------------------------
# (c) Cruce de temporadas
# ---------------------------------------------------------------------------

class TestSeasonCrossing:
    """
    La ventana cruza límites de temporada sin reiniciarse y nunca mezcla equipos.
    """

    WINDOW = 3

    def test_first_game_of_new_season_sees_prior_season(self):
        # 4 partidos en temporada A (valor 2.0), luego 4 en temporada B (valor 4.0).
        # El primer partido de la temporada B (índice 4 tras reset) debe tener
        # media 2.0 (ventana: partidos 1, 2, 3 de temporada A, todos 2.0).
        s1_start = date(2015, 10, 1)
        s2_start = date(2016, 10, 1)

        df = pd.concat(
            [
                pd.DataFrame(
                    {
                        "team_id": 1,
                        "game_date": [s1_start + timedelta(days=i) for i in range(4)],
                        "season": "2015-16",
                        "stat": [2.0] * 4,
                    }
                ),
                pd.DataFrame(
                    {
                        "team_id": 1,
                        "game_date": [s2_start + timedelta(days=i) for i in range(4)],
                        "season": "2016-17",
                        "stat": [4.0] * 4,
                    }
                ),
            ],
            ignore_index=True,
        )

        result = (
            compute_rolling_means(df, stat_cols=["stat"], window=self.WINDOW)
            .sort_values("game_date")
            .reset_index(drop=True)
        )

        # Índice 4 = primer partido de la temporada B.
        # Tras shift(1), su ventana cubre los índices 1, 2, 3 (todos de temporada A = 2.0).
        rolling_first_s2 = result.loc[4, "stat_rolling"]
        assert rolling_first_s2 == pytest.approx(2.0), (
            f"Cruce de temporadas fallido: el primer partido de B debería ver A "
            f"(esperado 2.0, obtenido {rolling_first_s2!r})"
        )

    def test_teams_do_not_cross_contaminate(self):
        # Equipo 1 (valor 10.0) y equipo 2 (valor 20.0), mismas fechas.
        # La ventana de cada equipo debe ser exclusiva: nunca mezclar valores.
        df = pd.concat(
            [
                _make_df(team_id=1, n=6, value=10.0, start=date(2016, 10, 1)),
                _make_df(team_id=2, n=6, value=20.0, start=date(2016, 10, 1)),
            ],
            ignore_index=True,
        )

        result = compute_rolling_means(df, stat_cols=["stat"], window=self.WINDOW)

        team1_rolling = result.loc[result["team_id"] == 1, "stat_rolling"].dropna()
        team2_rolling = result.loc[result["team_id"] == 2, "stat_rolling"].dropna()

        assert team1_rolling.tolist() == pytest.approx([10.0] * len(team1_rolling)), (
            f"Equipo 1 contaminado por equipo 2: {team1_rolling.tolist()}"
        )
        assert team2_rolling.tolist() == pytest.approx([20.0] * len(team2_rolling)), (
            f"Equipo 2 contaminado por equipo 1: {team2_rolling.tolist()}"
        )


# ---------------------------------------------------------------------------
# (d) Sin historia suficiente → NaN
# ---------------------------------------------------------------------------

class TestNaNForInsufficientHistory:
    """
    Los primeros `window` partidos de cada equipo deben retornar NaN.
    El partido en la posición exacta `window` debe retornar una media válida.
    """

    WINDOW = 3

    def test_first_games_return_nan(self):
        # 5 partidos: índices 0, 1, 2 → NaN (menos de WINDOW previos).
        df = _make_df(1, n=self.WINDOW + 2, value=1.0)
        result = (
            compute_rolling_means(df, stat_cols=["stat"], window=self.WINDOW)
            .sort_values("game_date")
            .reset_index(drop=True)
        )

        for i in range(self.WINDOW):
            val = result.loc[i, "stat_rolling"]
            assert pd.isna(val), (
                f"Partido {i} debería ser NaN (solo {i} partidos previos < {self.WINDOW}), "
                f"obtenido {val!r}"
            )

    def test_game_at_full_window_is_valid(self):
        # El partido en índice WINDOW (el primero con WINDOW previos) debe ser válido.
        df = _make_df(1, n=self.WINDOW + 2, value=1.0)
        result = (
            compute_rolling_means(df, stat_cols=["stat"], window=self.WINDOW)
            .sort_values("game_date")
            .reset_index(drop=True)
        )

        val = result.loc[self.WINDOW, "stat_rolling"]
        assert not pd.isna(val), (
            f"Partido {self.WINDOW} debería tener media válida "
            f"(tiene exactamente {self.WINDOW} partidos previos), obtenido NaN"
        )
        assert val == pytest.approx(1.0)

    def test_single_game_returns_nan(self):
        # Caso extremo: un equipo con un solo partido — ningún historial previo.
        df = _make_df(1, n=1, value=5.0)
        result = compute_rolling_means(df, stat_cols=["stat"], window=self.WINDOW)
        assert pd.isna(result.iloc[0]["stat_rolling"]), (
            "Un equipo con un solo partido debe retornar NaN (sin historia previa)"
        )


# ---------------------------------------------------------------------------
# Validaciones de interfaz
# ---------------------------------------------------------------------------

class TestInterfaceValidation:

    def test_missing_team_id_raises(self):
        df = pd.DataFrame({"game_date": [date(2016, 10, 1)], "stat": [1.0]})
        with pytest.raises(ValueError, match="team_id"):
            compute_rolling_means(df, stat_cols=["stat"])

    def test_missing_stat_col_raises(self):
        df = pd.DataFrame({"team_id": [1], "game_date": [date(2016, 10, 1)]})
        with pytest.raises(ValueError, match="no_existe"):
            compute_rolling_means(df, stat_cols=["no_existe"])

    def test_window_zero_raises(self):
        df = _make_df(1, n=3, value=1.0)
        with pytest.raises(ValueError, match="window"):
            compute_rolling_means(df, stat_cols=["stat"], window=0)

    def test_multiple_stat_cols(self):
        # La función debe manejar varias columnas a la vez.
        df = _make_df(1, n=5, value=2.0)
        df["stat2"] = 4.0
        result = compute_rolling_means(df, stat_cols=["stat", "stat2"], window=2)
        assert "stat_rolling" in result.columns
        assert "stat2_rolling" in result.columns

    def test_original_columns_unchanged(self):
        df = _make_df(1, n=5, value=3.0)
        result = compute_rolling_means(df, stat_cols=["stat"], window=2)
        # Las columnas originales no deben modificarse.
        pd.testing.assert_series_equal(result["stat"], df["stat"], check_names=True)

    def test_output_row_order_matches_input(self):
        # Las filas de salida deben estar en el mismo orden que la entrada.
        df = pd.concat(
            [
                _make_df(team_id=2, n=3, value=1.0, start=date(2016, 10, 1)),
                _make_df(team_id=1, n=3, value=2.0, start=date(2016, 10, 1)),
            ],
            ignore_index=True,
        )
        result = compute_rolling_means(df, stat_cols=["stat"], window=2)
        # team_id de la salida debe coincidir con la entrada en el mismo orden.
        assert list(result["team_id"]) == list(df["team_id"])
