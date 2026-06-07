import pandas as pd
import numpy as np
from .utils import add_opponent_stats

def calculate_possessions(df: pd.DataFrame) -> pd.Series:
    """
    Estimates possessions for a team in a game.

    Formula: FGA + 0.44 * FTA + TOV - OREB

    Args:
        df: DataFrame with 'fga', 'fta', 'tov', and 'oreb' columns.

    Returns:
        pd.Series with estimated possessions.
    """
    return df['fga'] + 0.44 * df['fta'] + df['tov'] - df['oreb']

def calculate_ratings(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculates Offensive, Defensive, and Net Ratings.

    Args:
        df: DataFrame with team and opponent ('opp_pts') statistics.

    Returns:
        pd.DataFrame with 'ortg', 'drtg', and 'netrtg' columns.
    """
    # If opponent stats are missing, add them (need pts for DRtg)
    if 'opp_pts' not in df.columns:
        # Calculate own points if missing
        if 'pts' not in df.columns:
            df = df.copy()
            df['pts'] = 2 * df['fgm'] + df['fg3m'] + df['ftm']
        
        df = add_opponent_stats(df, cols_to_add=['pts'])
    
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
    pipeline,
    windows: list[int] = [10]
) -> pd.DataFrame:
    """
    Generates Ratings features (Offensive, Defensive, Net) and their differences.

    Args:
        stats_df: DataFrame with stats per team and game.
        games_df: Games DataFrame.
        pipeline: FeaturePipeline instance for rolling calculations.
        windows: List of window sizes for rolling means.

    Returns:
        DataFrame with home-away differences for each rating and window.
    """
    df = stats_df.copy()
    if 'pts' not in df.columns:
        df['pts'] = 2 * df['fgm'] + df['fg3m'] + df['ftm']
    
    # 1. Prepare data with opponent stats
    df = add_opponent_stats(df, cols_to_add=['pts'])
    
    # 2. Calculate raw metrics
    ratings = calculate_ratings(df)
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
    
    rolling_df['game_id'] = df['game_id']
    rolling_df['team_id'] = df['team_id']
    
    # 4. Calculate Home - Away differences
    stats_to_diff = []
    for w in windows:
        for f in feature_cols:
            stats_to_diff.append(f'{f}_roll_{w}')
        
    diffs = pipeline.calculate_game_diffs(games_df, rolling_df, stats_to_diff)
    
    return diffs
