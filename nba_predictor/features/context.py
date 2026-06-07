import pandas as pd

def calculate_rest_days(df: pd.DataFrame) -> pd.Series:
    """
    Calcula los días de descanso desde el último partido para cada equipo.
    """
    original_index = df.index
    # Asegurar que game_date es datetime
    df_work = df.copy()
    df_work['game_date'] = pd.to_datetime(df_work['game_date'])
    df_work = df_work.sort_values(['team_id', 'game_date'])
    
    # Diferencia entre fechas consecutivas para el mismo equipo
    rest = df_work.groupby('team_id')['game_date'].diff().dt.days
    
    return rest.loc[original_index]

def generate_context_features(
    games_df: pd.DataFrame, 
    stats_df: pd.DataFrame
) -> pd.DataFrame:
    """
    Genera features contextuales: rest_diff, home_b2b, away_b2b.
    """
    # 1. Calcular días de descanso por cada participación de equipo
    df_rest = stats_df[['game_id', 'team_id']].copy()
    # Necesitamos game_date
    df_rest = pd.merge(df_rest, games_df[['game_id', 'game_date']], on='game_id')
    df_rest['rest_days'] = calculate_rest_days(df_rest)
    
    # 2. Unir con games_df para local y visitante
    home_rest = pd.merge(
        games_df[['game_id', 'home_team_id']],
        df_rest,
        left_on=['game_id', 'home_team_id'],
        right_on=['game_id', 'team_id'],
        how='left'
    )['rest_days']
    
    away_rest = pd.merge(
        games_df[['game_id', 'away_team_id']],
        df_rest,
        left_on=['game_id', 'away_team_id'],
        right_on=['game_id', 'team_id'],
        how='left'
    )['rest_days']
    
    # 3. Calcular features finales
    features = pd.DataFrame(index=games_df.index)
    
    # rest_diff: diferencia de días de descanso (capamos a un máximo razonable para evitar outliers)
    # Si es el primer partido (NaN), usamos un valor por defecto (ej. 7 días)
    h_rest = home_rest.fillna(7)
    a_rest = away_rest.fillna(7)
    features['rest_diff'] = h_rest - a_rest
    
    # b2b: Jugó el día anterior
    features['home_b2b'] = (home_rest == 1).astype(int)
    features['away_b2b'] = (away_rest == 1).astype(int)
    
    return features
