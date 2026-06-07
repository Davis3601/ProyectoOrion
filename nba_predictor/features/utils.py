import pandas as pd

def add_opponent_stats(df: pd.DataFrame, cols_to_add: list[str]) -> pd.DataFrame:
    """
    Adds opponent columns (prefix 'opp_') by joining the DataFrame with itself.

    Args:
        df: DataFrame with game_id, team_id and stats.
        cols_to_add: List of column names to add from the opponent.

    Returns:
        DataFrame with added opponent columns and rows filtered for matches.
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
    
    return joined
