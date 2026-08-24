"""Utility functions for feature engineering, such as opponent stat aggregation."""
import pandas as pd

def add_opponent_stats(df: pd.DataFrame, cols_to_add: list[str]) -> pd.DataFrame:
    """
    Adds opponent columns (prefix 'opp_') by joining the DataFrame with itself.

    Args:
        df: DataFrame with game_id, team_id and stats. Each game_id must have
            exactly 2 team rows.
        cols_to_add: List of column names to add from the opponent.

    Returns:
        DataFrame with added opponent columns, same row count as the input
        (raises ValueError otherwise).
    """
    # Select opponent columns
    opp_stats = df[['game_id', 'team_id'] + cols_to_add].copy()

    # Rename columns for the opponent
    new_cols = {col: f'opp_{col}' for col in cols_to_add}
    new_cols['team_id'] = 'opp_team_id'
    opp_stats = opp_stats.rename(columns=new_cols)

    # Join so each team sees its opponent's stats for that game_id
    joined = pd.merge(
        df,
        opp_stats,
        on='game_id',
        suffixes=('', '_extra')
    )

    # Filter where team_id != opp_team_id
    joined = joined[joined['team_id'] != joined['opp_team_id']].copy()

    # Guard: with exactly 2 team rows per game_id, the self-join preserves the
    # row count. Any deviation (1 or >2 rows per game) would silently produce
    # missing or duplicated feature rows downstream.
    if len(joined) != len(df):
        raise ValueError(
            f"add_opponent_stats: expected {len(df)} rows after opponent join, "
            f"got {len(joined)} — check that each game_id has exactly 2 team rows"
        )

    return joined
