"""Implementation of NBA team ratings (Offensive, Defensive, and Net Ratings)."""
import pandas as pd
import numpy as np
from .pipeline import FeaturePipeline
from .utils import add_opponent_stats

def calculate_possessions(df: pd.DataFrame) -> pd.Series:
    """
    Estimates possessions for a team in a game.

    Formula: FGA + 0.44 * FTA + TOV - OREB

    Args:
        df: DataFrame with 'fga', 'fta', 'tov', and 'oreb' columns.

    Returns:
        pd.Series with estimated possessions, clipped at 0. The estimate can
        go negative on anomalous data (OREB > FGA + 0.44*FTA + TOV), which
        would otherwise produce huge negative ratings that bypass the
        inf-replacement guard downstream.
    """
    poss = df['fga'] + 0.44 * df['fta'] + df['tov'] - df['oreb']
    return poss.clip(lower=0)

def calculate_ratings(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculates Offensive, Defensive, and Net Ratings.

    Args:
        df: DataFrame with team stats. Must include 'pts' and 'opp_pts'
            (use add_opponent_stats to attach opponent points beforehand).

    Returns:
        pd.DataFrame with 'ortg', 'drtg', and 'netrtg' columns, aligned with
        df's index and row count.

    Raises:
        ValueError: if 'pts' or 'opp_pts' is missing. This is a hard
            precondition — silently deriving them here used to change the
            row count of the input (via the opponent self-join), breaking
            index alignment for callers.
    """
    missing = [col for col in ('pts', 'opp_pts') if col not in df.columns]
    if missing:
        raise ValueError(
            f"calculate_ratings: missing required columns {missing}. "
            "Call add_opponent_stats(df, cols_to_add=['pts']) first."
        )

    poss = calculate_possessions(df)

    # ORtg = 100 * (PTS / Poss)
    ortg = (100 * df['pts'] / poss).replace([np.inf, -np.inf], 0.0).fillna(0.0)

    # DRtg = 100 * (Opp PTS / Poss)
    drtg = (100 * df['opp_pts'] / poss).replace([np.inf, -np.inf], 0.0).fillna(0.0)

    # NetRtg = ORtg - DRtg
    netrtg = ortg - drtg

    return pd.DataFrame({
        'ortg': ortg,
        'drtg': drtg,
        'netrtg': netrtg
    })

def generate_ratings_features(
    stats_df: pd.DataFrame,
    games_df: pd.DataFrame,
    pipeline: FeaturePipeline,
    windows: list[int] | None = None
) -> pd.DataFrame:
    """
    Generates Ratings features (Offensive, Defensive, Net) and their differences.

    Args:
        stats_df: DataFrame with stats per team and game.
        games_df: Games DataFrame.
        pipeline: FeaturePipeline instance for rolling calculations.
        windows: List of window sizes for rolling means. Defaults to [10].

    Returns:
        DataFrame with home-away differences for each rating and window.
    """
    if windows is None:
        windows = [10]

    df = stats_df.copy()
    if 'pts' not in df.columns:
        df['pts'] = 2 * df['fgm'] + df['fg3m'] + df['ftm']

    # 1. Prepare data with opponent stats
    df = add_opponent_stats(df, cols_to_add=['pts'])

    # 2. Calculate raw metrics
    ratings = calculate_ratings(df)
    # Hard guard (not assert: assert is stripped under `python -O`)
    if len(ratings) != len(df):
        raise ValueError(
            f"calculate_ratings row count mismatch: {len(df)} in, {len(ratings)} out"
        )
    df = pd.concat([df, ratings], axis=1)

    # 3. Calculate rolling stats
    df = pd.merge(df, games_df[['game_id', 'game_date']], on='game_id', validate="many_to_one")

    feature_cols = ['ortg', 'drtg', 'netrtg']
    rolling_df = pipeline.calculate_rolling_set(
        df,
        columns=feature_cols,
        windows=windows,
        group_col='team_id'
    )

    # .values: positional assignment, independent of rolling_df's index
    rolling_df['game_id'] = df['game_id'].values
    rolling_df['team_id'] = df['team_id'].values

    # 4. Calculate Home - Away differences
    stats_to_diff = []
    for w in windows:
        for f in feature_cols:
            stats_to_diff.append(f'{f}_roll_{w}')

    diffs = pipeline.calculate_game_diffs(games_df, rolling_df, stats_to_diff)

    return diffs
