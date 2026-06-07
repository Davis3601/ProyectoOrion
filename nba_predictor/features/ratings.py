import pandas as pd
import numpy as np
from .utils import add_opponent_stats

def calculate_possessions(df: pd.DataFrame) -> pd.Series:
    """
    Estima las posesiones de un equipo en un partido.

    Fórmula: FGA + 0.44 * FTA + TOV - OREB

    Args:
        df: DataFrame con las columnas 'fga', 'fta', 'tov' y 'oreb'.

    Returns:
        pd.Series con las posesiones estimadas.
    """
    return df['fga'] + 0.44 * df['fta'] + df['tov'] - df['oreb']

def calculate_ratings(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula Offensive, Defensive y Net Ratings.

    Args:
        df: DataFrame con estadísticas del equipo y del oponente ('opp_pts').

    Returns:
        pd.DataFrame con las columnas 'ortg', 'drtg' y 'netrtg'.
    """
    # Si no tiene stats del oponente, las añadimos (necesitamos pts para DRtg)
    if 'opp_pts' not in df.columns:
        # Primero calculamos nuestros propios pts si no están
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
    Genera las features de Ratings (Offensive, Defensive, Net) y sus diferencias.

    Args:
        stats_df: DataFrame con estadísticas por equipo y partido.
        games_df: DataFrame con información de los partidos.
        pipeline: Instancia de FeaturePipeline para cálculos de rolling.
        windows: Lista de ventanas temporales para las medias móviles.

    Returns:
        DataFrame con las diferencias home-away para cada rating y ventana.
    """
    df = stats_df.copy()
    if 'pts' not in df.columns:
        df['pts'] = 2 * df['fgm'] + df['fg3m'] + df['ftm']
    
    # 1. Preparar datos con stats del oponente
    df = add_opponent_stats(df, cols_to_add=['pts'])
    
    # 2. Calcular métricas raw
    ratings = calculate_ratings(df)
    df = pd.concat([df, ratings], axis=1)
    
    # 3. Calcular rolling stats
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
    
    # 4. Calcular diferencias Home - Away
    stats_to_diff = []
    for w in windows:
        for f in feature_cols:
            stats_to_diff.append(f'{f}_roll_{w}')
        
    diffs = pipeline.calculate_game_diffs(games_df, rolling_df, stats_to_diff)
    
    return diffs
