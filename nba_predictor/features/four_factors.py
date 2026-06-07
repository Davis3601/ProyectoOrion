import pandas as pd
import numpy as np
from .utils import add_opponent_stats

def calculate_efg(df: pd.DataFrame) -> pd.Series:
    """
    Calcula el Effective Field Goal Percentage.

    Fórmula: (FGM + 0.5 * FG3M) / FGA

    Args:
        df: DataFrame con las columnas 'fgm', 'fg3m' y 'fga'.

    Returns:
        pd.Series con los valores de eFG%.
    """
    numerator = df['fgm'] + 0.5 * df['fg3m']
    denominator = df['fga']
    
    # Manejar división por cero (0/0 -> NaN, X/0 -> inf)
    return (numerator / denominator).replace([np.inf, -np.inf], 0.0).fillna(0.0)

def calculate_tov_rate(df: pd.DataFrame) -> pd.Series:
    """
    Calcula el Turnover Rate.

    Fórmula: TOV / (FGA + 0.44 * FTA + TOV)

    Args:
        df: DataFrame con las columnas 'tov', 'fga' y 'fta'.

    Returns:
        pd.Series con los valores de Turnover Rate.
    """
    possessions = df['fga'] + 0.44 * df['fta'] + df['tov']
    
    # Manejar división por cero
    return (df['tov'] / possessions).replace([np.inf, -np.inf], 0.0).fillna(0.0)

def calculate_oreb_rate(df: pd.DataFrame) -> pd.Series:
    """
    Calcula el Offensive Rebound Rate.

    Fórmula: OREB / (OREB + Opponent DREB)

    Args:
        df: DataFrame con las columnas 'oreb' y 'opp_dreb'.

    Returns:
        pd.Series con los valores de OREB Rate.
    """
    denominator = df['oreb'] + df['opp_dreb']
    return (df['oreb'] / denominator).replace([np.inf, -np.inf], 0.0).fillna(0.0)

def calculate_ft_rate(df: pd.DataFrame) -> pd.Series:
    """
    Calcula el Free Throw Rate.

    Fórmula: FTA / FGA

    Args:
        df: DataFrame con las columnas 'fta' y 'fga'.

    Returns:
        pd.Series con los valores de Free Throw Rate.
    """
    return (df['fta'] / df['fga']).replace([np.inf, -np.inf], 0.0).fillna(0.0)

def generate_four_factors_features(
    stats_df: pd.DataFrame, 
    games_df: pd.DataFrame, 
    pipeline,
    windows: list[int] = [10]
) -> pd.DataFrame:
    """
    Genera las features de los Four Factors (eFG%, TOV%, OREB%, FT Rate) y sus diferencias.

    Args:
        stats_df: DataFrame con estadísticas por equipo y partido.
        games_df: DataFrame con información de los partidos (home/away IDs, dates).
        pipeline: Instancia de FeaturePipeline para cálculos de rolling.
        windows: Lista de ventanas temporales para las medias móviles.

    Returns:
        DataFrame con las diferencias home-away para cada factor y ventana.
    """
    # 1. Preparar datos con stats del oponente para OREB%
    df = add_opponent_stats(stats_df, cols_to_add=['dreb'])
    
    # 2. Calcular métricas raw
    df['efg'] = calculate_efg(df)
    df['tov_rate'] = calculate_tov_rate(df)
    df['oreb_rate'] = calculate_oreb_rate(df)
    df['ft_rate'] = calculate_ft_rate(df)
    
    # 3. Calcular rolling stats
    # Necesitamos game_date para el pipeline
    df = pd.merge(df, games_df[['game_id', 'game_date']], on='game_id', validate="many_to_one")
    
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
