import pandas as pd

class FeaturePipeline:
    """
    Framework base para la generación de features y manejo de ventanas temporales.
    """
    
    def calculate_rolling(self, df: pd.DataFrame, col: str, window: int, group_col: str) -> pd.Series:
        """
        Calcula la media móvil de una columna, agrupada por una columna (ej. team_id),
        asegurando que no hay look-ahead bias mediante un desplazamiento (shift).
        
        Args:
            df: DataFrame con los datos (debe incluir game_date).
            col: Columna sobre la cual calcular la media.
            window: Tamaño de la ventana.
            group_col: Columna para agrupar (normalmente team_id).
            
        Returns:
            pd.Series con los valores calculados, alineados con el índice original.
        """
        # Asegurar que los datos estén ordenados cronológicamente para cada grupo
        # Aunque el input suela venir ordenado, lo garantizamos aquí para la lógica de rolling
        original_index = df.index
        df_work = df.sort_values([group_col, 'game_date'])
        
        rolling = (
            df_work.groupby(group_col)[col]
            .transform(lambda x: x.shift(1).rolling(window=window, min_periods=1).mean())
        )
        
        # Re-alinear con el índice original para que el output coincida con el input
        result = rolling.loc[original_index]
        result.name = f"{col}_roll_{window}"
        
        return result
