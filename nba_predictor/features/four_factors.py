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

def calculate_oreb_rate(df: pd.DataFrame) -> pd.Series:
    """
    Calcula el Offensive Rebound Rate.
    Fórmula: OREB / (OREB + Opponent DREB)
    """
    denominator = df['oreb'] + df['opp_dreb']
    return (df['oreb'] / denominator).fillna(0.0)

def calculate_ft_rate(df: pd.DataFrame) -> pd.Series:
    """
    Calcula el Free Throw Rate.
    Fórmula: FTA / FGA
    """
    return (df['fta'] / df['fga']).fillna(0.0)

def add_opponent_stats(df: pd.DataFrame) -> pd.DataFrame:
    """
    Añade columnas del oponente (prefijo 'opp_') uniendo el DF consigo mismo.
    """
    # Seleccionamos las columnas que queremos del oponente
    opp_stats = df[['game_id', 'team_id', 'dreb']].copy()
    opp_stats.columns = ['game_id', 'opp_team_id', 'opp_dreb']
    
    # Unimos para que cada equipo vea las stats de su oponente en ese game_id
    # En NBA cada game_id tiene exactamente 2 equipos
    joined = pd.merge(
        df,
        opp_stats,
        on='game_id',
        suffixes=('', '_extra')
    )
    
    # Filtramos donde team_id != opp_team_id
    joined = joined[joined['team_id'] != joined['opp_team_id']].copy()
    
    return joined

def generate_four_factors_features(
    stats_df: pd.DataFrame, 
    games_df: pd.DataFrame, 
    pipeline,
    windows: list[int] = [10]
) -> pd.DataFrame:
    """
    Genera las features de los Four Factors (eFG%, TOV%, OREB%, FT Rate) y sus diferencias.
    """
    # 1. Preparar datos con stats del oponente para OREB%
    df = add_opponent_stats(stats_df)
    
    # 2. Calcular métricas raw
    df['efg'] = calculate_efg(df)
    df['tov_rate'] = calculate_tov_rate(df)
    df['oreb_rate'] = calculate_oreb_rate(df)
    df['ft_rate'] = calculate_ft_rate(df)
    
    # 3. Calcular rolling stats
    # Necesitamos game_date para el pipeline
    df = pd.merge(df, games_df[['game_id', 'game_date']], on='game_id')
    
    feature_cols = ['efg', 'tov_rate', 'oreb_rate', 'ft_rate']
    rolling_df = pipeline.calculate_rolling_set(
        df, 
        columns=feature_cols, 
        windows=windows, 
        group_col='team_id'
    )
    
    # Unimos con game_id y team_id para que calculate_game_diffs pueda usarlos
    rolling_df['game_id'] = df['game_id']
    rolling_df['team_id'] = df['team_id']
    
    # 4. Calcular diferencias Home - Away
    stats_to_diff = []
    for w in windows:
        for f in feature_cols:
            stats_to_diff.append(f'{f}_roll_{w}')
        
    diffs = pipeline.calculate_game_diffs(games_df, rolling_df, stats_to_diff)
    
    return diffs
