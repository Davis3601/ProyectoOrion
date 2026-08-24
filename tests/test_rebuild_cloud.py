"""Tests unitarios de scripts/rebuild_cloud.py — solo funciones puras, sin GCP."""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.rebuild_cloud import (
    _build_report,
    _compare_features_content,
    _normalize_for_compare,
    _resultset_to_df,
    _season_from_game_id,
    _sha256_file,
)


# ---------------------------------------------------------------------------
# _season_from_game_id
# ---------------------------------------------------------------------------


class TestSeasonFromGameId:
    def test_earliest_warmup_season(self) -> None:
        # Temporada de calentamiento más antigua descargada
        assert _season_from_game_id("0021400001") == "2014-15"

    def test_second_warmup_season(self) -> None:
        assert _season_from_game_id("0021500001") == "2015-16"

    def test_mid_training_season(self) -> None:
        assert _season_from_game_id("0022300001") == "2023-24"

    def test_latest_training_season(self) -> None:
        # Temporada más reciente ingestada
        assert _season_from_game_id("0022500001") == "2025-26"

    def test_different_sequence_numbers(self) -> None:
        # El número de secuencia no afecta la temporada
        assert _season_from_game_id("0021699999") == "2016-17"
        assert _season_from_game_id("0021600001") == "2016-17"


# ---------------------------------------------------------------------------
# _resultset_to_df
# ---------------------------------------------------------------------------

_SAMPLE_RAW = {
    "resource": "boxscoretraditionalv2",
    "resultSets": [
        {
            "name": "PlayerStats",
            "headers": ["GAME_ID", "PLAYER_ID", "FGM"],
            "rowSet": [["0021400001", 1234, 5], ["0021400001", 5678, 3]],
        },
        {
            "name": "TeamStats",
            "headers": ["GAME_ID", "TEAM_ID", "FGM"],
            "rowSet": [["0021400001", 1610612737, 42]],
        },
    ],
}


class TestResultsetToDf:
    def test_finds_team_stats(self) -> None:
        df = _resultset_to_df(_SAMPLE_RAW, "TeamStats")
        assert list(df.columns) == ["GAME_ID", "TEAM_ID", "FGM"]
        assert len(df) == 1
        assert df["TEAM_ID"].iloc[0] == 1610612737

    def test_finds_player_stats(self) -> None:
        df = _resultset_to_df(_SAMPLE_RAW, "PlayerStats")
        assert list(df.columns) == ["GAME_ID", "PLAYER_ID", "FGM"]
        assert len(df) == 2

    def test_missing_name_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="NonExistent"):
            _resultset_to_df(_SAMPLE_RAW, "NonExistent")

    def test_error_message_lists_available(self) -> None:
        with pytest.raises(KeyError) as exc_info:
            _resultset_to_df(_SAMPLE_RAW, "Missing")
        assert "PlayerStats" in str(exc_info.value)
        assert "TeamStats" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _normalize_for_compare
# ---------------------------------------------------------------------------


class TestNormalizeForCompare:
    def test_int64_nullable_to_float64(self) -> None:
        """BigQuery devuelve Int64 nullable; debe convertirse a float64."""
        df = pd.DataFrame({"home_won": pd.array([1, 0, None], dtype="Int64")})
        result = _normalize_for_compare(df)
        assert result["home_won"].dtype == "float64"
        assert result["home_won"].iloc[2] != result["home_won"].iloc[2]  # NaN check

    def test_regular_int64_to_float64(self) -> None:
        """SQLite devuelve int64 regular; también se normaliza a float64."""
        df = pd.DataFrame({"team_id": [1610612737, 1610612738]})
        result = _normalize_for_compare(df)
        assert result["team_id"].dtype == "float64"

    def test_date_object_in_object_column(self) -> None:
        """datetime.date en columna object → string ISO YYYY-MM-DD."""
        df = pd.DataFrame({
            "game_date": [datetime.date(2024, 1, 1), datetime.date(2025, 3, 15)]
        })
        result = _normalize_for_compare(df)
        assert result["game_date"].iloc[0] == "2024-01-01"
        assert result["game_date"].iloc[1] == "2025-03-15"

    def test_float_column_unchanged(self) -> None:
        """Columnas float (minutes, plus_minus) no cambian de dtype."""
        df = pd.DataFrame({"minutes": [32.5, 28.0, None]})
        result = _normalize_for_compare(df)
        assert result["minutes"].dtype == "float64"
        assert result["minutes"].iloc[0] == 32.5

    def test_string_column_unchanged(self) -> None:
        """Columnas string (game_id, season) no se modifican."""
        df = pd.DataFrame({"game_id": ["0021400001", "0021400002"]})
        result = _normalize_for_compare(df)
        assert result["game_id"].iloc[0] == "0021400001"

    def test_does_not_mutate_input(self) -> None:
        """La función es pura: el DataFrame original no se modifica."""
        df = pd.DataFrame({"x": pd.array([1, 2], dtype="Int64")})
        original_dtype = str(df["x"].dtype)
        _normalize_for_compare(df)
        assert str(df["x"].dtype) == original_dtype


# ---------------------------------------------------------------------------
# _build_report
# ---------------------------------------------------------------------------


class TestBuildReport:
    def _all_ok_results(self) -> dict:
        return {
            "teams": {"local_count": 30, "cloud_count": 30, "ok": True, "error": None},
            "games": {"local_count": 14429, "cloud_count": 14429, "ok": True, "error": None},
        }

    def test_all_ok_shows_success_verdict(self) -> None:
        report = _build_report(self._all_ok_results())
        assert "EQUIVALENCIA EXACTA" in report
        assert "DISCREPANCIAS" not in report

    def test_all_ok_includes_checkmarks(self) -> None:
        report = _build_report(self._all_ok_results())
        assert "✅" in report

    def test_failure_shows_failure_verdict(self) -> None:
        results = {
            "teams": {"local_count": 30, "cloud_count": 30, "ok": True, "error": None},
            "games": {
                "local_count": 14429,
                "cloud_count": 14000,
                "ok": False,
                "error": "conteos diferentes (14,429 vs 14,000)",
            },
        }
        report = _build_report(results)
        assert "DISCREPANCIAS" in report
        assert "NO MARCAR COMO ÉXITO" in report
        assert "❌" in report

    def test_failure_includes_error_message(self) -> None:
        results = {
            "games": {
                "local_count": 100,
                "cloud_count": 99,
                "ok": False,
                "error": "conteos diferentes",
            }
        }
        report = _build_report(results)
        assert "conteos diferentes" in report

    def test_counts_appear_in_report(self) -> None:
        results = {
            "teams": {"local_count": 30, "cloud_count": 30, "ok": True, "error": None}
        }
        report = _build_report(results)
        assert "30" in report


# ---------------------------------------------------------------------------
# _sha256_file
# ---------------------------------------------------------------------------


class TestSha256File:
    def test_consistent_hash_same_file(self, tmp_path: Path) -> None:
        """El mismo archivo produce el mismo hash en dos llamadas."""
        f = tmp_path / "test.parquet"
        f.write_bytes(b"hello world")
        assert _sha256_file(f) == _sha256_file(f)

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        """Archivos con contenido diferente producen hashes distintos."""
        f1 = tmp_path / "a.parquet"
        f2 = tmp_path / "b.parquet"
        f1.write_bytes(b"content_a")
        f2.write_bytes(b"content_b")
        assert _sha256_file(f1) != _sha256_file(f2)

    def test_known_hash(self, tmp_path: Path) -> None:
        """Verifica contra un SHA-256 conocido para detectar regresiones."""
        import hashlib
        content = b"nba_predictor_test"
        f = tmp_path / "known.bin"
        f.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert _sha256_file(f) == expected


# ---------------------------------------------------------------------------
# _compare_features_content
# ---------------------------------------------------------------------------

_FEATURES_COLS = ["game_id", "season", "home_won", "efg_diff"]


def _make_features_df(n: int = 3) -> pd.DataFrame:
    return pd.DataFrame({
        "game_id": [f"002140{i:04d}" for i in range(n)],
        "season": ["2014-15"] * n,
        "home_won": [1, 0, 1][:n],
        "efg_diff": [0.05, -0.03, 0.01][:n],
    })


class TestCompareFeaturesContent:
    def test_identical_dfs_returns_true(self) -> None:
        df = _make_features_df()
        ok, err = _compare_features_content(df, df.copy())
        assert ok is True
        assert err is None

    def test_count_mismatch_returns_false(self) -> None:
        local_df = _make_features_df(3)
        cloud_df = _make_features_df(2)
        ok, err = _compare_features_content(local_df, cloud_df)
        assert ok is False
        assert err is not None
        assert "conteo" in err

    def test_value_difference_returns_false(self) -> None:
        local_df = _make_features_df(3)
        cloud_df = _make_features_df(3)
        cloud_df.loc[0, "efg_diff"] = 999.0  # valor diferente
        ok, err = _compare_features_content(local_df, cloud_df)
        assert ok is False
        assert err is not None

    def test_dtype_difference_only_returns_true(self) -> None:
        """Int64 nullable vs int64 regular no es discrepancia de valor."""
        local_df = pd.DataFrame({
            "game_id": ["0021400001"],
            "home_won": pd.array([1], dtype="int64"),
        })
        cloud_df = pd.DataFrame({
            "game_id": ["0021400001"],
            "home_won": pd.array([1], dtype="Int64"),  # nullable BigQuery
        })
        ok, err = _compare_features_content(local_df, cloud_df)
        assert ok is True

    def test_error_message_includes_game_ids(self) -> None:
        local_df = _make_features_df(3)
        cloud_df = _make_features_df(3)
        cloud_df["efg_diff"] = [100.0, 200.0, 300.0]  # todos diferentes
        ok, err = _compare_features_content(local_df, cloud_df)
        assert ok is False
        assert "002140" in err  # algún game_id aparece en el mensaje
