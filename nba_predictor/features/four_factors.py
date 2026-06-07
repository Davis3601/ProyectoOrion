import pandas as pd

def calculate_efg(df: pd.DataFrame) -> pd.Series:
    """
    Calcula el Effective Field Goal Percentage.
    Fórmula: (FGM + 0.5 * FG3M) / FGA
    """
    numerator = df['fgm'] + 0.5 * df['fg3m']
    denominator = df['fga']
    
    # Manejar división por cero
    return (numerator / denominator).fillna(0.0)

def calculate_tov_rate(df: pd.DataFrame) -> pd.Series:
    """
    Calcula el Turnover Rate.
    Fórmula: TOV / (FGA + 0.44 * FTA + TOV)
    """
    possessions = df['fga'] + 0.44 * df['fta'] + df['tov']
    
    # Manejar división por cero
    return (df['tov'] / possessions).fillna(0.0)

def generate_four_factors_features(
    stats_df: pd.DataFrame, 
    games_df: pd.DataFrame, 
    pipeline,
    windows: list[int] = [10]
) -> pd.DataFrame:
    """
    Genera las features de los Four Factors (eFG% y TOV Rate) y sus diferencias.
    """
    df = stats_df.copy()
    
    # 1. Calcular métricas raw
    df['efg'] = calculate_efg(df)
    df['tov_rate'] = calculate_tov_rate(df)
    
    # 2. Calcular rolling stats
    # Necesitamos game_date para el pipeline
    df = pd.merge(df, games_df[['game_id', 'game_date']], on='game_id')
    
    rolling_df = pipeline.calculate_rolling_set(
        df, 
        columns=['efg', 'tov_rate'], 
        windows=windows, 
        group_col='team_id'
    )
    
    # Unimos con game_id y team_id para que calculate_game_diffs pueda usarlos
    rolling_df['game_id'] = df['game_id']
    rolling_df['team_id'] = df['team_id']
    
    # 3. Calcular diferencias Home - Away
    stats_to_diff = []
    for w in windows:
        stats_to_diff.extend([f'efg_roll_{w}', f'tov_rate_roll_{w}'])
        
    diffs = pipeline.calculate_game_diffs(games_df, rolling_df, stats_to_diff)
    
    return diffs
