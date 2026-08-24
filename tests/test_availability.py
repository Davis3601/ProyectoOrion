"""
Tests del Grupo 5 — Disponibilidad de jugadores (nba_predictor/features/availability.py).

Cinco clases de tests:
  (a) TestNoLeakage      : el partido actual no entra en su propio rolling.
                           Planta 999 min en g3 y verifica que el rolling de g3
                           es mean(10,20,30)=20, no 999. El partido SIGUIENTE
                           sí refleja el 999 (test K y K+1).
  (b) TestDirectional    : equipo local con su mejor jugador ausente tiene menor
                           disponibilidad que el rival con rotación completa.
  (c) TestTradedPlayer   : jugador traspasado entra al NUMERADOR del nuevo equipo
                           en su primera activación (historial viaja con player_id),
                           pero NO al DENOMINADOR hasta que su aparición quede en
                           la ventana del equipo. Demuestra availability > 1.0 válido.
  (d) TestRange          : en datos reales, availability_diff concentrada en [−0.5, 0.5];
                           sanity sobre row count y columnas.
  (e) TestSanityRealData : mean(availability_diff) ≈ 0 (simetría home/away);
                           cero NaN donde hay historia suficiente.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from nba_predictor.features.availability import (
    build_availability,
    compute_availability,
    compute_team_availability,
)


# ---------------------------------------------------------------------------
# Helper: builder de DataFrames de player_stats sintéticos
# ---------------------------------------------------------------------------

def _pgs(rows: list[dict]) -> pd.DataFrame:
    """Construye un player_stats mínimo; 'season' y 'minutes' son obligatorios."""
    defaults = {"season": "2016-17"}
    return pd.DataFrame([{**defaults, **r} for r in rows])


# ---------------------------------------------------------------------------
# (a) No-leakage — test reina
# ---------------------------------------------------------------------------

class TestNoLeakage:
    """
    Test reina de no-leakage del Grupo 5.

    Un jugador no puede influir en su propio rolling de minutos para el partido
    que se predice. Garantía: shift(1) dentro de groupby(player_id) sobre los
    partidos jugados (minutes > 0).

    Setup: P1 juega g0=10, g1=20, g2=30, g3=999 (valor plantado), g4=30.
    Window = 3.

    - En g3: rolling esperado = mean(10, 20, 30) = 20 — el 999 no se ve.
    - En g4: rolling esperado = mean(20, 30, 999) ≈ 349.67 — g4 sí lo ve.
    """

    @staticmethod
    def _make_stats() -> pd.DataFrame:
        D = date
        games = [
            ("g0", D(2016, 10, 1), 10),
            ("g1", D(2016, 10, 3), 20),
            ("g2", D(2016, 10, 5), 30),
            ("g3", D(2016, 10, 7), 999),  # valor plantado
            ("g4", D(2016, 10, 9), 30),
        ]
        rows = []
        for gid, d, p1_min in games:
            rows.append({
                "game_id": gid, "game_date": d,
                "player_id": 1, "team_id": 1, "is_home": 1, "minutes": p1_min,
            })
            rows.append({
                "game_id": gid, "game_date": d,
                "player_id": 2, "team_id": 2, "is_home": 0, "minutes": 30,
            })
        return _pgs(rows)

    def test_planted_value_not_in_own_rolling(self):
        """
        En g3, el numerador de T1 refleja rolling=20 (mean de g0,g1,g2), no 999.
        Si hubiera leakage: rolling = mean(20, 30, 999) = 349.67.
        """
        stats = self._make_stats()
        result = compute_team_availability(stats, window=3)
        t1_g3 = result[(result["game_id"] == "g3") & (result["team_id"] == 1)].iloc[0]

        assert t1_g3["numerator"] == pytest.approx(20.0, rel=1e-3), (
            f"leakage detectado: numerator={t1_g3['numerator']:.2f}, "
            "esperado 20.0 = mean(10, 20, 30). El 999 no debe aparecer en su propio rolling."
        )

    def test_planted_value_visible_in_subsequent_game(self):
        """
        En g4, el rolling de P1 SÍ incluye el 999 del g3.
        rolling en g4 = mean(20, 30, 999) ≈ 349.67 — los partidos futuros lo ven.
        """
        stats = self._make_stats()
        result = compute_team_availability(stats, window=3)
        t1_g4 = result[(result["game_id"] == "g4") & (result["team_id"] == 1)].iloc[0]

        assert t1_g4["numerator"] > 100.0, (
            f"numerator en g4 = {t1_g4['numerator']:.2f}; "
            "el 999 de g3 DEBE ser visible para el partido siguiente (anti-leakage correcto)"
        )


# ---------------------------------------------------------------------------
# (b) Directional — estrella ausente reduce disponibilidad
# ---------------------------------------------------------------------------

class TestDirectional:
    """
    Equipo local con su mejor jugador ausente tiene menor disponibilidad que el
    rival con rotación completa.

    Setup (window=1):
    - g_pre2, g_pre1: warmup con todos los jugadores (necesarios para que el
      rolling de g0 no sea NaN — se requieren 2 partidos previos).
    - g0: T1 (local) sin P1 (lesionado, no activado); T2 (visitante) completo.

    Cálculo esperado en g0:
      Numerator(T1)   = rolling(P2) + rolling(P3) = 30 + 30 = 60
      Denominator(T1) = rolling(P1) + rolling(P2) + rolling(P3) = 90  [del g_pre1]
      availability(T1) = 60/90 ≈ 0.667

      Numerator(T2)   = 30 + 30 + 30 = 90
      Denominator(T2) = 90
      availability(T2) = 1.0

      availability_diff = 0.667 − 1.0 = −0.333  (desventaja local)
    """

    @staticmethod
    def _make_stats() -> pd.DataFrame:
        D = date
        rows = [
            # g_pre2 y g_pre1: todos juegan 30 min
            *[
                {"game_id": gid, "game_date": d,
                 "player_id": pid, "team_id": tid, "is_home": ih, "minutes": 30}
                for gid, d in [("g_pre2", D(2016, 9, 29)), ("g_pre1", D(2016, 10, 1))]
                for pid, tid, ih in [(1, 1, 1), (2, 1, 1), (3, 1, 1),
                                     (4, 2, 0), (5, 2, 0), (6, 2, 0)]
            ],
            # g0: P1 ausente de T1 (no tiene fila — no activado)
            {"game_id": "g0", "game_date": D(2016, 10, 3),
             "player_id": 2, "team_id": 1, "is_home": 1, "minutes": 30},
            {"game_id": "g0", "game_date": D(2016, 10, 3),
             "player_id": 3, "team_id": 1, "is_home": 1, "minutes": 30},
            {"game_id": "g0", "game_date": D(2016, 10, 3),
             "player_id": 4, "team_id": 2, "is_home": 0, "minutes": 30},
            {"game_id": "g0", "game_date": D(2016, 10, 3),
             "player_id": 5, "team_id": 2, "is_home": 0, "minutes": 30},
            {"game_id": "g0", "game_date": D(2016, 10, 3),
             "player_id": 6, "team_id": 2, "is_home": 0, "minutes": 30},
        ]
        return _pgs(rows)

    def test_missing_star_yields_negative_diff(self):
        """availability_diff < 0 cuando la estrella local está ausente."""
        stats = self._make_stats()
        result = compute_availability(stats, window=1)
        g0 = result[result["game_id"] == "g0"].iloc[0]

        assert g0["availability_diff"] < 0, (
            f"availability_diff = {g0['availability_diff']:.4f}; "
            "esperado negativo: equipo local sin P1 vs visitante completo"
        )

    def test_numerical_values(self):
        """Verifica numerador, denominador y disponibilidad exactos en g0."""
        stats = self._make_stats()
        ta = compute_team_availability(stats, window=1)

        t1 = ta[(ta["game_id"] == "g0") & (ta["team_id"] == 1)].iloc[0]
        t2 = ta[(ta["game_id"] == "g0") & (ta["team_id"] == 2)].iloc[0]

        # T1: P2(30) + P3(30) = 60; denominador P1(30)+P2(30)+P3(30) = 90
        assert t1["numerator"] == pytest.approx(60.0)
        assert t1["denominator"] == pytest.approx(90.0)
        assert t1["availability"] == pytest.approx(60.0 / 90.0, rel=1e-4)

        # T2: rotación completa
        assert t2["numerator"] == pytest.approx(90.0)
        assert t2["denominator"] == pytest.approx(90.0)
        assert t2["availability"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# (c) Jugador traspasado
# ---------------------------------------------------------------------------

class TestTradedPlayer:
    """
    Un jugador traspasado lleva su historial de minutos al nuevo equipo.

    En el partido de su PRIMERA activación para el nuevo equipo:
    - Entra al NUMERADOR con su rolling de su equipo anterior.
    - NO entra al DENOMINADOR (no está en la ventana reciente del nuevo equipo).
    - Consecuencia documentada: availability > 1.0 es un resultado válido.

    Setup (window=1):
    - g0, g1: P1 juega para T1 (local); P2 juega para T2 (visitante).
              → P1 acumula rolling=30 en T1.
    - g2: P1 es traspasado y se activa por primera vez para T2 (visitante).
          T1 tiene solo P3 (debutante, rolling=NaN → contribuye 0).
    """

    @staticmethod
    def _make_stats() -> pd.DataFrame:
        D = date
        rows = [
            # g0: T1 vs T2
            {"game_id": "g0", "game_date": D(2016, 10, 1),
             "player_id": 1, "team_id": 1, "is_home": 1, "minutes": 30},
            {"game_id": "g0", "game_date": D(2016, 10, 1),
             "player_id": 2, "team_id": 2, "is_home": 0, "minutes": 30},
            # g1: T1 vs T2
            {"game_id": "g1", "game_date": D(2016, 10, 3),
             "player_id": 1, "team_id": 1, "is_home": 1, "minutes": 30},
            {"game_id": "g1", "game_date": D(2016, 10, 3),
             "player_id": 2, "team_id": 2, "is_home": 0, "minutes": 30},
            # g2: P1 traspasado → primera activación para T2
            {"game_id": "g2", "game_date": D(2016, 10, 5),
             "player_id": 3, "team_id": 1, "is_home": 1, "minutes": 25},  # debutante
            {"game_id": "g2", "game_date": D(2016, 10, 5),
             "player_id": 1, "team_id": 2, "is_home": 0, "minutes": 25},  # P1 en T2
            {"game_id": "g2", "game_date": D(2016, 10, 5),
             "player_id": 2, "team_id": 2, "is_home": 0, "minutes": 30},
        ]
        return _pgs(rows)

    def test_traded_player_in_numerator_with_prior_rolling(self):
        """
        P1 contribuye al numerador de T2 en g2 con rolling=30 de su historial en T1.
        Numerator(T2) = P1.rolling(30) + P2.rolling(30) = 60.
        """
        stats = self._make_stats()
        ta = compute_team_availability(stats, window=1)
        t2_g2 = ta[(ta["game_id"] == "g2") & (ta["team_id"] == 2)].iloc[0]

        assert t2_g2["numerator"] == pytest.approx(60.0), (
            "P1 (traspasado) debe entrar al numerador con su rolling histórico de T1"
        )

    def test_traded_player_not_in_denominator(self):
        """
        P1 NO está en el denominador de T2 en g2.
        La ventana [g1] de T2 solo contenía P2.
        Denominator(T2) = P2.rolling(30) = 30.
        """
        stats = self._make_stats()
        ta = compute_team_availability(stats, window=1)
        t2_g2 = ta[(ta["game_id"] == "g2") & (ta["team_id"] == 2)].iloc[0]

        assert t2_g2["denominator"] == pytest.approx(30.0), (
            "P1 (traspasado) NO debe estar en el denominador de T2 hasta que "
            "su aparición para ese equipo quede dentro de la ventana"
        )

    def test_availability_exceeds_one_when_trade_player_arrives(self):
        """
        availability(T2) = 60/30 = 2.0 > 1.0 — resultado válido y esperado.
        Indica que el equipo recibe fuerza extra no incluida en la rotación base.
        """
        stats = self._make_stats()
        ta = compute_team_availability(stats, window=1)
        t2_g2 = ta[(ta["game_id"] == "g2") & (ta["team_id"] == 2)].iloc[0]

        assert t2_g2["availability"] > 1.0, (
            f"availability = {t2_g2['availability']:.4f}; "
            "debe superar 1.0 cuando un traspasado entra al numerador pero no al denominador"
        )
        assert t2_g2["availability"] == pytest.approx(2.0, rel=1e-4)


# ---------------------------------------------------------------------------
# (d) Rango — diff concentrada en [-0.5, 0.5] en datos reales
# ---------------------------------------------------------------------------

class TestRange:
    """
    Verificación empírica de rangos de availability_diff en los 14 429 partidos.

    La diff debe estar concentrada alrededor de cero (home ≈ away en promedio
    de disponibilidad). Partidos con diff > |0.5| son raros (lesiones graves
    o rachas inusuales de traspasados).
    """

    def test_diff_mostly_in_narrow_range(self):
        """≥ 85% de los diffs válidos deben caer en [−0.5, 0.5]."""
        result = build_availability()
        valid = result.dropna(subset=["availability_diff"])

        in_range = (valid["availability_diff"].abs() <= 0.5).mean() * 100
        assert in_range >= 85.0, (
            f"Solo {in_range:.1f}% de availability_diff en [−0.5, 0.5]; "
            "se esperan ≥ 85% — revisar si hay outliers en el cálculo"
        )

    def test_output_columns_and_row_count(self):
        """La salida tiene las columnas correctas y una fila por partido."""
        from nba_predictor.features.availability import OUTPUT_COLS

        result = build_availability()
        assert list(result.columns) == OUTPUT_COLS, (
            f"Columnas inesperadas: {list(result.columns)}"
        )
        assert len(result) == 14_429, (
            f"Se esperan 14 429 filas (una por partido), obtenidas {len(result)}"
        )


# ---------------------------------------------------------------------------
# (e) Sanidad en datos reales
# ---------------------------------------------------------------------------

class TestSanityRealData:
    """
    Checks globales sobre los datos reales.

    - Media de availability_diff ≈ 0: no hay ventaja sistemática de local/visitante
      en disponibilidad (por simetría del calendario NBA).
    - Los primeros N partidos de cada equipo tienen NaN (sin historia rolling);
      el resto deben ser válidos.
    """

    def test_mean_diff_approximately_zero(self):
        """
        media(availability_diff) debe ser ≈ 0.
        Una media > |0.05| indica asimetría no esperada o un bug en la convención
        local − visitante.
        """
        result = build_availability()
        valid = result.dropna(subset=["availability_diff"])
        mean_diff = valid["availability_diff"].mean()

        assert abs(mean_diff) < 0.05, (
            f"mean(availability_diff) = {mean_diff:.4f}; "
            "se esperaba ≈ 0 por simetría home/away del calendario NBA"
        )

    def test_nan_only_at_start_of_history(self):
        """
        Los NaN en availability_diff corresponden a los primeros partidos de cada
        equipo (sin historia rolling). La gran mayoría del dataset debe ser válido.
        """
        result = build_availability()
        n_nan = result["availability_diff"].isna().sum()
        n_total = len(result)
        pct_nan = n_nan / n_total * 100

        # Con 30 equipos y ventana=10, los primeros ~10 partidos por equipo
        # pueden ser NaN. 30 × 10 = 300 máximo de pares con NaN (en la práctica menos).
        assert pct_nan < 5.0, (
            f"{n_nan} filas con NaN ({pct_nan:.1f}%); "
            "esperado < 5% — los NaN deben limitarse a los primeros partidos de cada equipo"
        )
