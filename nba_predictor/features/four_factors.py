"""Implementation of NBA Four Factors features (eFG%, TOV%, OREB%, FT Rate)."""
import pandas as pd
import numpy as np
from .pipeline import FeaturePipeline
from .utils import add_opponent_stats

def calculate_efg(df: pd.DataFrame) -> pd.Series:
    """
    Calculates Effective Field Goal Percentage.

    Formula: (FGM + 0.5 * FG3M) / FGA

    Args:
        df: DataFrame with 'fgm', 'fg3m', and 'fga' columns.

    Returns:
        pd.Series with eFG% values.
    """
    numerator = df['fgm'] + 0.5 * df['fg3m']
    denominator = df['fga']

    # Handle division by zero (0/0 -> NaN, X/0 -> inf)
    return (numerator / denominator).replace([np.inf, -np.inf], 0.0).fillna(0.0)

def calculate_tov_rate(df: pd.DataFrame) -> pd.Series:
    """
    Calculates Turnover Rate.

    Formula: TOV / (FGA + 0.44 * FTA + TOV)

    Args:
        df: DataFrame with 'tov', 'fga', and 'fta' columns.

    Returns:
        pd.Series with Turnover Rate values.
    """
    possessions = df['fga'] + 0.44 * df['fta'] + df['tov']

    # Handle division by zero
    return (df['tov'] / possessions).replace([np.inf, -np.inf], 0.0).fillna(0.0)

def calculate_oreb_rate(df: pd.DataFrame) -> pd.Series:
    """
    Calculates Offensive Rebound Rate.

    Formula: OREB / (OREB + Opponent DREB)

    Args:
        df: DataFrame with 'oreb' and 'opp_dreb' columns.

    Returns:
        pd.Series with OREB Rate values.
    """
    denominator = df['oreb'] + df['opp_dreb']
    return (df['oreb'] / denominator).replace([np.inf, -np.inf], 0.0).fillna(0.0)

def calculate_ft_rate(df: pd.DataFrame) -> pd.Series:
    """
    Calculates Free Throw Rate.

    Formula: FTA / FGA

    Args:
        df: DataFrame with 'fta' and 'fga' columns.

    Returns:
        pd.Series with Free Throw Rate values.
    """
    return (df['fta'] / df['fga']).replace([np.inf, -np.inf], 0.0).fillna(0.0)

def generate_four_factors_features(
    stats_df: pd.DataFrame,
    games_df: pd.DataFrame,
    pipeline: FeaturePipeline,
    windows: list[int] | None = None
) -> pd.DataFrame:
    """
    Generates Four Factors features (eFG%, TOV%, OREB%, FT Rate) and their differences.

    Args:
        stats_df: DataFrame with stats per team and game.
        games_df: Games DataFrame (home/away IDs, dates).
        pipeline: FeaturePipeline instance for rolling calculations.
        windows: List of window sizes for rolling means. Defaults to [10].

    Returns:
        DataFrame with home-away differences for each factor and window.
    """
    if windows is None:
        windows = [10]

    # 1. Prepare data with opponent stats for OREB%
    df = add_opponent_stats(stats_df, cols_to_add=['dreb'])

    # 2. Calculate raw metrics
    df['efg'] = calculate_efg(df)
    df['tov_rate'] = calculate_tov_rate(df)
    df['oreb_rate'] = calculate_oreb_rate(df)
    df['ft_rate'] = calculate_ft_rate(df)

    # 3. Calculate rolling stats
    # We need game_date for the pipeline
    df = pd.merge(df, games_df[['game_id', 'game_date']], on='game_id', validate="many_to_one")

    feature_cols = ['efg', 'tov_rate', 'oreb_rate', 'ft_rate']
    rolling_df = pipeline.calculate_rolling_set(
        df,
        columns=feature_cols,
        windows=windows,
        group_col='team_id'
    )

    # Join with game_id and team_id so calculate_game_diffs can use them
    # (.values: positional assignment, independent of rolling_df's index)
    rolling_df['game_id'] = df['game_id'].values
    rolling_df['team_id'] = df['team_id'].values

    # 4. Calculate Home - Away differences
    stats_to_diff = []
    for w in windows:
        for f in feature_cols:
            stats_to_diff.append(f'{f}_roll_{w}')

    diffs = pipeline.calculate_game_diffs(games_df, rolling_df, stats_to_diff)

    return diffs
