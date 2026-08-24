"""
Tests para nba_predictor/features/assemble.py.

Tests (a)–(d) usan DataFrames sintéticos (sin BD) vía assemble_from_dfs.
Test (e) corre contra la BD real y verifica propiedades estadísticas.

Estructura:
    (a) TestBothTeamsRule      — regla "ambos equipos ≥15 partidos previos"
    (b) TestWarmupExclusion    — warmup (2014-15, 2015-16) ausentes del set final
    (c) TestNanInvariant       — NaN sobreviviente levanta ValueError
    (d) TestColumnOrder        — columnas exactas en orden features_v1
    (e) TestSanityRealData     — propiedades estadísticas en datos reales
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from nba_predictor.features.assemble import (
    FEATURES_V1_COLS,
    _FEATURE_COLS,
    assemble_features,
    assemble_from_dfs,
)

# ---------------------------------------------------------------------------
# Helpers de datos sintéticos
# ---------------------------------------------------------------------------

_G1 = ["efg_diff", "tov_rate_diff", "oreb_rate_diff", "ft_rate_diff"]
_G2 = ["off_rating_diff", "def_rating_diff", "net_rating_diff"]
_G3 = ["off_rating_adj_diff", "def_rating_adj_diff", "net_rating_adj_diff"]
_G4 = ["rest_diff", "home_b2b", "away_b2b", "neutral_site"]
_G5 = ["availability_diff"]


def _season_base(season: str) -> date:
    """Oct 1 del primer año de la temporada, e.g. '2016-17' → date(2016, 10, 1)."""
    return date(int(season.split("-")[0]), 10, 1)


def _feature_row(game_id: str, season: str, game_date: date) -> dict:
    """Fila con valores numéricos válidos (sin NaN) para todas las features."""
    return {
        "game_id": game_id,
        "season": season,
        "game_date": game_date,
        # Grupo 1
        "efg_diff": 0.01,
        "tov_rate_diff": -0.01,
        "oreb_rate_diff": 0.01,
        "ft_rate_diff": 0.01,
        # Grupo 2
        "off_rating_diff": 0.5,
        "def_rating_diff": -0.5,
        "net_rating_diff": 1.0,
        # Grupo 3
        "off_rating_adj_diff": 0.5,
        "def_rating_adj_diff": -0.5,
        "net_rating_adj_diff": 1.0,
        # Grupo 4
        "rest_diff": 0.0,
        "home_b2b": 0,
        "away_b2b": 0,
        "neutral_site": 0,
        # Grupo 5
        "availability_diff": 0.01,
    }


def _split_groups(
    rows: list[dict],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Separa una lista de filas de features en los 5 DataFrames de grupo."""
    df = pd.DataFrame(rows)
    base = ["game_id", "season", "game_date"]
    return (
        df[base + _G1],
        df[base + _G2],
        df[base + _G3],
        df[base + _G4],
        df[base + _G5],
    )


def _games_df(
    home_prior: int,
    away_prior: int,
    test_game_id: str,
    season: str = "2016-17",
    home_team: int = 1,
    away_team: int = 2,
) -> pd.DataFrame:
    """
    DataFrame de games donde:
    - home_team juega home_prior partidos (vs equipo 99) ANTES del test game.
    - away_team juega away_prior partidos (vs equipo 99) ANTES del test game.
    - test_game_id: home_team vs away_team, fecha posterior a todos los previos.

    Los IDs de los previos son únicos por prefijo h{i}_{test_game_id} /
    a{i}_{test_game_id}, evitando colisiones entre múltiples llamadas.
    """
    base = _season_base(season)
    test_date = base + timedelta(days=max(home_prior, away_prior) + 5)
    rows = []
    for i in range(home_prior):
        rows.append(
            dict(
                game_id=f"h{i}_{test_game_id}",
                season=season,
                game_date=base + timedelta(days=i),
                home_team_id=home_team,
                away_team_id=99,
                home_won=1,
            )
        )
    for i in range(away_prior):
        rows.append(
            dict(
                game_id=f"a{i}_{test_game_id}",
                season=season,
                game_date=base + timedelta(days=i),
                home_team_id=99,
                away_team_id=away_team,
                home_won=1,
            )
        )
    rows.append(
        dict(
            game_id=test_game_id,
            season=season,
            game_date=test_date,
            home_team_id=home_team,
            away_team_id=away_team,
            home_won=1,
        )
    )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# (a) Regla de ambos equipos ≥15 partidos previos
# ---------------------------------------------------------------------------

class TestBothTeamsRule:
    """game_num_in_season ≥ 16 (= 15 partidos previos) para AMBOS equipos."""

    def test_excludes_when_away_below_16_games(self):
        """Local en partido 20, visitante en partido 10 → fila EXCLUIDA."""
        games = _games_df(home_prior=19, away_prior=9, test_game_id="g_exc_away")
        base = _season_base("2016-17")
        test_date = base + timedelta(days=19 + 5)
        ff, rt, oa, ct, av = _split_groups(
            [_feature_row("g_exc_away", "2016-17", test_date)]
        )
        result = assemble_from_dfs(ff, rt, oa, ct, av, games)
        assert "g_exc_away" not in result["game_id"].values

    def test_excludes_when_home_below_16_games(self):
        """Local en partido 10, visitante en partido 20 → fila EXCLUIDA."""
        games = _games_df(home_prior=9, away_prior=19, test_game_id="g_exc_home")
        base = _season_base("2016-17")
        test_date = base + timedelta(days=19 + 5)
        ff, rt, oa, ct, av = _split_groups(
            [_feature_row("g_exc_home", "2016-17", test_date)]
        )
        result = assemble_from_dfs(ff, rt, oa, ct, av, games)
        assert "g_exc_home" not in result["game_id"].values

    def test_includes_when_both_at_exactly_16(self):
        """Local y visitante en partido 16 (= 15 previos) → fila INCLUIDA."""
        games = _games_df(home_prior=15, away_prior=15, test_game_id="g_inc_16")
        base = _season_base("2016-17")
        test_date = base + timedelta(days=15 + 5)
        ff, rt, oa, ct, av = _split_groups(
            [_feature_row("g_inc_16", "2016-17", test_date)]
        )
        result = assemble_from_dfs(ff, rt, oa, ct, av, games)
        assert "g_inc_16" in result["game_id"].values
        assert len(result) == 1

    def test_mixed_excluded_and_included_in_same_call(self):
        """Dado un partido que pasa y uno que no, solo el que pasa aparece."""
        # Equipos distintos para cada game: evita colisiones de fecha en game_num
        # g_bad: teams 1 vs 2, away at game 5 → excluido
        games_bad = _games_df(
            home_prior=20, away_prior=4,
            test_game_id="g_bad", home_team=1, away_team=2,
        )
        # g_ok: teams 3 vs 4, both at game 20 → incluido
        games_ok = _games_df(
            home_prior=19, away_prior=19,
            test_game_id="g_ok", home_team=3, away_team=4,
        )

        games = pd.concat([games_bad, games_ok], ignore_index=True)

        base = _season_base("2016-17")
        rows = [
            _feature_row("g_bad", "2016-17", base + timedelta(days=20 + 5)),
            _feature_row("g_ok",  "2016-17", base + timedelta(days=19 + 5)),
        ]
        ff, rt, oa, ct, av = _split_groups(rows)
        result = assemble_from_dfs(ff, rt, oa, ct, av, games)

        assert "g_bad" not in result["game_id"].values
        assert "g_ok" in result["game_id"].values


# ---------------------------------------------------------------------------
# (b) Warmup seasons ausentes del set final
# ---------------------------------------------------------------------------

class TestWarmupExclusion:
    """Las temporadas de warmup (2014-15, 2015-16) nunca aparecen en el output."""

    def test_warmup_season_absent_and_training_present(self):
        """2014-15 (warmup) → excluido. 2016-17 (entrenamiento) → incluido."""
        # Warmup game — usa equipo 3 y 4 para no mezclar historias de equipo
        gw_base = _season_base("2014-15")
        gw_date = gw_base + timedelta(days=15 + 5)
        games_w = _games_df(
            home_prior=15, away_prior=15, test_game_id="g_wm",
            season="2014-15", home_team=3, away_team=4,
        )

        # Training game
        gt_base = _season_base("2016-17")
        gt_date = gt_base + timedelta(days=15 + 5)
        games_t = _games_df(
            home_prior=15, away_prior=15, test_game_id="g_tr",
            season="2016-17", home_team=5, away_team=6,
        )

        games = pd.concat([games_w, games_t], ignore_index=True)
        rows = [
            _feature_row("g_wm", "2014-15", gw_date),
            _feature_row("g_tr", "2016-17", gt_date),
        ]
        ff, rt, oa, ct, av = _split_groups(rows)
        result = assemble_from_dfs(ff, rt, oa, ct, av, games)

        assert "g_wm" not in result["game_id"].values
        assert "g_tr" in result["game_id"].values
        assert "2014-15" not in result["season"].tolist()

    def test_2015_16_warmup_excluded(self):
        """2015-16 también es warmup y no debe aparecer."""
        base = _season_base("2015-16")
        test_date = base + timedelta(days=15 + 5)
        games = _games_df(
            home_prior=15, away_prior=15, test_game_id="g_wm2",
            season="2015-16", home_team=7, away_team=8,
        )
        ff, rt, oa, ct, av = _split_groups(
            [_feature_row("g_wm2", "2015-16", test_date)]
        )
        result = assemble_from_dfs(ff, rt, oa, ct, av, games)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# (c) Invariante cero-NaN
# ---------------------------------------------------------------------------

class TestNanInvariant:
    """NaN que sobrevive a las exclusiones debe levantar ValueError con detalle."""

    def _passing_game_setup(
        self, game_id: str, nan_col: str
    ) -> tuple[pd.DataFrame, ...]:
        """Crea un juego que pasa ambos filtros pero tiene NaN en nan_col."""
        base = _season_base("2016-17")
        test_date = base + timedelta(days=15 + 5)
        games = _games_df(home_prior=15, away_prior=15, test_game_id=game_id)
        row = _feature_row(game_id, "2016-17", test_date)
        row[nan_col] = float("nan")
        ff, rt, oa, ct, av = _split_groups([row])
        return ff, rt, oa, ct, av, games

    def test_raises_on_nan_in_efg_diff(self):
        ff, rt, oa, ct, av, games = self._passing_game_setup("g_nan_efg", "efg_diff")
        with pytest.raises(ValueError, match="Invariante VIOLADO"):
            assemble_from_dfs(ff, rt, oa, ct, av, games)

    def test_raises_on_nan_in_off_rating_diff(self):
        ff, rt, oa, ct, av, games = self._passing_game_setup(
            "g_nan_offr", "off_rating_diff"
        )
        with pytest.raises(ValueError, match="Invariante VIOLADO"):
            assemble_from_dfs(ff, rt, oa, ct, av, games)

    def test_error_message_names_affected_column(self):
        """El mensaje de error debe listar la columna afectada."""
        ff, rt, oa, ct, av, games = self._passing_game_setup(
            "g_nan_msg", "availability_diff"
        )
        with pytest.raises(ValueError, match="availability_diff"):
            assemble_from_dfs(ff, rt, oa, ct, av, games)


# ---------------------------------------------------------------------------
# (d) Orden exacto de columnas features_v1
# ---------------------------------------------------------------------------

class TestColumnOrder:
    """Las columnas del output deben coincidir EXACTAMENTE con FEATURES_V1_COLS."""

    def test_exact_column_order(self):
        base = _season_base("2016-17")
        test_date = base + timedelta(days=15 + 5)
        games = _games_df(home_prior=15, away_prior=15, test_game_id="g_cols")
        ff, rt, oa, ct, av = _split_groups(
            [_feature_row("g_cols", "2016-17", test_date)]
        )
        result = assemble_from_dfs(ff, rt, oa, ct, av, games)
        assert list(result.columns) == FEATURES_V1_COLS

    def test_total_column_count(self):
        assert len(FEATURES_V1_COLS) == 19  # 3 id + 15 features + 1 target

    def test_target_is_last_column(self):
        assert FEATURES_V1_COLS[-1] == "home_won"

    def test_game_identifiers_are_first_three(self):
        assert FEATURES_V1_COLS[:3] == ["game_id", "season", "game_date"]


# ---------------------------------------------------------------------------
# (e) Sanity checks en datos reales
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_features() -> pd.DataFrame:
    """Llama a assemble_features() una vez y comparte el resultado entre tests."""
    return assemble_features()


class TestSanityRealData:
    """Propiedades estadísticas esperadas sobre los 14 429 partidos reales."""

    def test_home_won_rate_within_historical_range(self, real_features):
        """Ventaja de local histórica en la NBA: ~56-59%."""
        mean_hw = real_features["home_won"].mean()
        assert 0.55 <= mean_hw <= 0.60, (
            f"home_won rate {mean_hw:.4f} fuera del rango histórico [0.55, 0.60]"
        )

    def test_row_count_within_expected_range(self, real_features):
        """Esperado ~9 000-11 500 filas tras exclusiones (10 temporadas × ~1 000)."""
        n = len(real_features)
        assert 9_000 <= n <= 11_500, f"Row count {n:,} fuera del rango esperado"

    def test_no_nan_in_feature_cols(self, real_features):
        """Invariante cero-NaN: ninguna feature puede tener NaN en el output final."""
        nan_counts = real_features[_FEATURE_COLS].isna().sum()
        failing = nan_counts[nan_counts > 0]
        assert failing.empty, f"Columnas con NaN: {failing.to_dict()}"

    def test_training_seasons_only(self, real_features):
        """Solo aparecen temporadas de entrenamiento (2016-17 a 2025-26)."""
        from nba_predictor.config import TRAINING_SEASONS

        seasons_present = set(real_features["season"].unique())
        unexpected = seasons_present - set(TRAINING_SEASONS)
        assert not unexpected, f"Temporadas inesperadas en output: {unexpected}"

    def test_exact_column_set(self, real_features):
        assert list(real_features.columns) == FEATURES_V1_COLS
