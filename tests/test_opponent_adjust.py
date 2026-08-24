"""
Tests del Grupo 3 — Ajuste por calidad de oponente
(nba_predictor/features/opponent_adjust.py).

Cinco clases de tests:
  (a) TestReinaNoLeakage     : opp_def=999 en partido K no afecta off_rating_adj de K;
                               pero SÍ el de K+1.
  (b) TestDirectional        : mismo rating crudo, calendario fuerte vs. débil;
                               el equipo con rivales fuertes debe salir con
                               off_rating_adj MAYOR tras el ajuste.
  (c) TestLeagueAvgNoFuture  : plantar off_rating=999 en partido futuro no contamina
                               el league_avg de fechas anteriores.
  (d) TestNumerical          : verificación numérica exacta con caso a mano
                               (window=3, A off=120 def=100, B off=110 def=90).
  (e) TestSanityRealData     : adj ratings tienen media similar a los crudos y
                               correlación alta (>0.8) pero no perfecta (<1.0) con ellos.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from nba_predictor.features.opponent_adjust import (
    _compute_daily_league_avg,
    compute_adjusted_ratings,
    compute_team_adjusted_ratings,
)


# ---------------------------------------------------------------------------
# Helper: construye filas de team_ratings para un equipo con stats constantes
# (excepto donde se especifique override).
# ---------------------------------------------------------------------------

def _rows(
    team_id: int,
    is_home: int,
    n: int,
    off: float,
    def_: float | list[float],
    game_id_prefix: str = "g",
    start: date = date(2016, 10, 1),
    season: str = "2016-17",
) -> list[dict]:
    """Una fila de team_ratings por partido para un equipo."""
    defs = def_ if isinstance(def_, list) else [def_] * n
    return [
        {
            "team_id": team_id,
            "game_id": f"{game_id_prefix}{i}",
            "game_date": start + timedelta(days=i),
            "season": season,
            "is_home": is_home,
            "off_rating": off,
            "def_rating": defs[i],
            "net_rating": off - defs[i],
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# (a) Test reina de no-leakage
# ---------------------------------------------------------------------------

class TestReinaNoLeakage:
    """
    Plantar opp_def_rating=999 en partido K.
    - off_rating_adj EN K se basa en los N partidos PREVIOS → no refleja 999.
    - off_rating_adj EN K+1 debe verse afectado (999 entra en la ventana).

    opp_def_rating al partido K para el equipo LOCAL = def_rating del RIVAL en K.
    Al plantar def_rating=999 para el rival en el partido K, el local "ve" ese
    valor solo desde K+1 en adelante (ventana con shift anti-leakage).
    """

    WINDOW = 3
    EXTREME_GAME = 3

    def _build(self) -> pd.DataFrame:
        n = 6
        defs2 = [108.0] * n
        defs2[self.EXTREME_GAME] = 999.0  # rival con def_rating extremo en g3

        return pd.DataFrame(
            _rows(1, 1, n, off=115.0, def_=105.0)         # equipo local: constante
            + _rows(2, 0, n, off=112.0, def_=defs2)        # rival: def=999 en g3
        )

    def test_extreme_opp_def_absent_from_own_adj(self):
        """off_rating_adj en g3 NO debe reflejar el def_rating=999 del rival."""
        result = compute_adjusted_ratings(self._build(), window=self.WINDOW)
        row_g3 = result[result["game_id"] == f"g{self.EXTREME_GAME}"].iloc[0]

        # Con opp_def_rolling basado en g0,g1,g2 (todos 108), el ajuste es
        # moderado. Si hubiera leakage, off_rating_adj sería drásticamente menor
        # (restaría ~999 del numerador de opp_def).
        # Normal: off_adj ≈ 115 - 108 + league_avg.  Leakage: ≈ 115 - 440 + league_avg.
        assert row_g3["off_rating_adj_diff"] > -200, (
            f"LEAKAGE DETECTADO: off_rating_adj_diff en g3 = "
            f"{row_g3['off_rating_adj_diff']:.2f} "
            f"(sería << -200 si opp_def=999 entrara en la ventana)"
        )

    def test_extreme_opp_def_visible_to_next_game(self):
        """off_rating_adj en g4 debe verse drásticamente afectado (999 en ventana)."""
        result = compute_adjusted_ratings(self._build(), window=self.WINDOW)
        row_g4 = result[result["game_id"] == f"g{self.EXTREME_GAME + 1}"].iloc[0]

        # opp_def_rolling para g4 = mean(108, 108, 999) = 405 → ajuste fuerte
        # off_rating_adj_diff << off_rating_adj_diff normal (≈ 7)
        assert row_g4["off_rating_adj_diff"] < -100, (
            f"El partido posterior al opp_def=999 debe verlo: "
            f"off_rating_adj_diff={row_g4['off_rating_adj_diff']:.2f}, esperado << -100"
        )


# ---------------------------------------------------------------------------
# (b) Calendarios fuertes vs. débiles — test direccional
# ---------------------------------------------------------------------------

class TestDirectional:
    """
    Equipo X (off=115) juega contra rivales FUERTES (def_rating=100).
    Equipo Y (off=115) juega contra rivales DÉBILES (def_rating=120).

    Tras el ajuste, off_rating_adj del equipo X debe ser MAYOR:
    X enfrentó defensas mejores que la media → su ofensiva "real" es mayor.

    off_rating_adj = off_rating - mean_opp_def + league_avg
    X: 115 - 100 + lg_avg  >  Y: 115 - 120 + lg_avg   ✓
    """

    WINDOW = 3

    def _build(self) -> pd.DataFrame:
        n = 5
        start = date(2016, 10, 1)

        # X (id=1) vs rival fuerte (id=2): def=100
        x_rows = _rows(1, 1, n, off=115.0, def_=105.0, game_id_prefix="gX", start=start)
        sx_rows = _rows(2, 0, n, off=107.0, def_=100.0, game_id_prefix="gX", start=start)

        # Y (id=3) vs rival débil (id=4): def=120
        y_rows = _rows(3, 1, n, off=115.0, def_=105.0, game_id_prefix="gY", start=start)
        sy_rows = _rows(4, 0, n, off=107.0, def_=120.0, game_id_prefix="gY", start=start)

        return pd.DataFrame(x_rows + sx_rows + y_rows + sy_rows)

    def test_strong_schedule_gets_higher_adj_off_rating(self):
        team_adj = compute_team_adjusted_ratings(self._build(), window=self.WINDOW)

        x_off = team_adj[team_adj["team_id"] == 1]["off_rating_adj"].dropna()
        y_off = team_adj[team_adj["team_id"] == 3]["off_rating_adj"].dropna()

        assert len(x_off) > 0 and len(y_off) > 0, "No hay filas válidas con window suficiente"
        assert x_off.iloc[-1] > y_off.iloc[-1], (
            f"Equipo con calendario fuerte debería tener off_rating_adj mayor: "
            f"X={x_off.iloc[-1]:.3f}, Y={y_off.iloc[-1]:.3f}"
        )


# ---------------------------------------------------------------------------
# (c) league_avg no usa datos futuros
# ---------------------------------------------------------------------------

class TestLeagueAvgNoFuture:
    """
    Plantar off_rating=999 en el partido MÁS RECIENTE del dataset.
    El league_avg de fechas anteriores no debe reflejar ese valor extremo.
    """

    def _build_team_ratings_with_future_extreme(self) -> pd.DataFrame:
        n = 6
        start = date(2016, 10, 1)
        offs_a = [110.0] * n
        offs_a[-1] = 999.0  # off_rating extremo en el último partido

        rows = []
        for i in range(n):
            d = start + timedelta(days=i)
            rows += [
                {
                    "team_id": 1, "game_id": f"g{i}", "game_date": d,
                    "season": "2016-17", "is_home": 1,
                    "off_rating": offs_a[i], "def_rating": 110.0,
                    "net_rating": offs_a[i] - 110.0,
                },
                {
                    "team_id": 2, "game_id": f"g{i}", "game_date": d,
                    "season": "2016-17", "is_home": 0,
                    "off_rating": 110.0, "def_rating": 110.0,
                    "net_rating": 0.0,
                },
            ]
        return pd.DataFrame(rows)

    def test_future_extreme_does_not_contaminate_past_league_avg(self):
        df = self._build_team_ratings_with_future_extreme()
        daily_avg = _compute_daily_league_avg(df)

        extreme_date = date(2016, 10, 1) + timedelta(days=5)  # último partido
        early_avg = daily_avg[daily_avg["game_date"] < extreme_date]["league_avg"].dropna()

        assert len(early_avg) > 0, "No hay league_avg calculado para fechas anteriores"
        assert (early_avg < 200).all(), (
            f"LEAKAGE en league_avg: valores antes del partido extremo deberían ser "
            f"< 200 (esperado ≈ 110), obtenido max={early_avg.max():.1f}"
        )


# ---------------------------------------------------------------------------
# (d) Verificación numérica exacta
# ---------------------------------------------------------------------------

class TestNumerical:
    """
    Setup:
        Team A (home): off=120, def=100  →  net=20
        Team B (away): off=110, def=90   →  net=20
        window=3, 4 partidos (g0-g3).

    En g3 (primer partido con 3 previos completos):
        opp_def_rolling_A  = mean(90,90,90)   = 90
        opp_off_rolling_A  = mean(110,110,110) = 110
        league_avg         = mean(120,110)/2   = 115

        off_rating_adj_A = 120 - 90  + 115 = 145
        def_rating_adj_A = 100 - 110 + 115 = 105
        net_rating_adj_A = 145 - 105       = 40

        off_rating_adj_B = 110 - 100 + 115 = 125
        def_rating_adj_B = 90  - 120 + 115 = 85
        net_rating_adj_B = 125 - 85        = 40

        off_rating_adj_diff = 145 - 125 = 20
        def_rating_adj_diff = 105 - 85  = 20
        net_rating_adj_diff = 40  - 40  = 0
    """

    WINDOW = 3

    def _build(self) -> pd.DataFrame:
        n = 4
        return pd.DataFrame(
            _rows(1, 1, n, off=120.0, def_=100.0)
            + _rows(2, 0, n, off=110.0, def_=90.0)
        )

    def test_off_rating_adj_diff(self):
        result = compute_adjusted_ratings(self._build(), window=self.WINDOW)
        last = result.iloc[-1]
        assert last["off_rating_adj_diff"] == pytest.approx(20.0, abs=1e-6), (
            f"off_rating_adj_diff = {last['off_rating_adj_diff']:.6f}, esperado 20.0"
        )

    def test_def_rating_adj_diff(self):
        result = compute_adjusted_ratings(self._build(), window=self.WINDOW)
        last = result.iloc[-1]
        assert last["def_rating_adj_diff"] == pytest.approx(20.0, abs=1e-6), (
            f"def_rating_adj_diff = {last['def_rating_adj_diff']:.6f}, esperado 20.0"
        )

    def test_net_rating_adj_diff(self):
        result = compute_adjusted_ratings(self._build(), window=self.WINDOW)
        last = result.iloc[-1]
        assert last["net_rating_adj_diff"] == pytest.approx(0.0, abs=1e-6), (
            f"net_rating_adj_diff = {last['net_rating_adj_diff']:.6f}, esperado 0.0"
        )


# ---------------------------------------------------------------------------
# (e) Sanidad sobre datos reales
# ---------------------------------------------------------------------------

class TestSanityRealData:
    """
    Los ratings ajustados y los crudos deben:
    - Tener media similar (ambas diferencias centradas en 0).
    - Correlación alta (> 0.8): el ajuste es suave, no revierte el ranking.
    - Correlación no perfecta (< 1.0): el ajuste SÍ cambia algo.
    """

    def test_adj_vs_crude_similarity(self):
        from nba_predictor.features.opponent_adjust import build_adjusted_ratings
        from nba_predictor.features.ratings import build_ratings

        crude = build_ratings().dropna()
        adj = build_adjusted_ratings().dropna()

        # Unir por game_id para correlación
        merged = crude[["game_id", "net_rating_diff"]].merge(
            adj[["game_id", "net_rating_adj_diff"]], on="game_id"
        )

        assert len(merged) > 5_000, (
            f"Se esperan >5 000 filas válidas en ambas tablas, obtenido {len(merged)}"
        )

        # Medias similares (ambas ≈ 0 para diffs home-away sin sesgo sistemático)
        mean_crude = merged["net_rating_diff"].mean()
        mean_adj = merged["net_rating_adj_diff"].mean()
        assert abs(mean_adj - mean_crude) < 2.0, (
            f"Las medias difieren más de lo esperado: "
            f"crudo={mean_crude:.3f}, ajustado={mean_adj:.3f}"
        )

        # Correlación alta: el ajuste mantiene el orden relativo
        corr = merged["net_rating_diff"].corr(merged["net_rating_adj_diff"])
        assert corr > 0.8, (
            f"Correlación crudo-ajustado debería ser > 0.8, obtenido {corr:.4f}"
        )

        # No perfecta: el ajuste SÍ hace algo
        assert corr < 1.0, (
            f"Correlación = 1.0 exacto: el ajuste no cambió nada (bug)"
        )
