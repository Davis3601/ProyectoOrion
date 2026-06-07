import pandas as pd

def add_opponent_stats(df: pd.DataFrame, cols_to_add: list[str]) -> pd.DataFrame:
    """
    Añade columnas del oponente (prefijo 'opp_') uniendo el DF consigo mismo.
    """
    # Seleccionamos las columnas que queremos del oponente
    opp_stats = df[['game_id', 'team_id'] + cols_to_add].copy()
    
    # Renombrar columnas para el oponente
    new_cols = {col: f'opp_{col}' for col in cols_to_add}
    new_cols['team_id'] = 'opp_team_id'
    opp_stats = opp_stats.rename(columns=new_cols)
    
    # Unimos para que cada equipo vea las stats de su oponente en ese game_id
    joined = pd.merge(
        df,
        opp_stats,
        on='game_id',
        suffixes=('', '_extra')
    )
    
    # Filtramos donde team_id != opp_team_id
    joined = joined[joined['team_id'] != joined['opp_team_id']].copy()
    
    return joined
