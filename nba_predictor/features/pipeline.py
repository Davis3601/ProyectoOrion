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

    def calculate_rolling_set(
        self, df: pd.DataFrame, columns: list[str], windows: list[int], group_col: str
    ) -> pd.DataFrame:
        """
        Calcula múltiples medias móviles para varias columnas y ventanas.
        
        Args:
            df: DataFrame con los datos.
            columns: Lista de columnas para aplicar rolling.
            windows: Lista de tamaños de ventana.
            group_col: Columna para agrupar.
            
        Returns:
            DataFrame con todas las nuevas columnas calculadas.
        """
        results = {}
        for col in columns:
            for window in windows:
                res = self.calculate_rolling(df, col, window, group_col)
                results[res.name] = res
        
        return pd.DataFrame(results, index=df.index)

    def calculate_game_diffs(
        self, games_df: pd.DataFrame, rolling_df: pd.DataFrame, stats_cols: list[str]
    ) -> pd.DataFrame:
        """
        Calcula la diferencia entre las estadísticas rolling del equipo local y el visitante.
        
        Args:
            games_df: DataFrame de partidos con home_team_id y away_team_id.
            rolling_df: DataFrame con estadísticas rolling (debe tener game_id y team_id).
            stats_cols: Lista de columnas de estadísticas para calcular la diferencia.
            
        Returns:
            DataFrame con las columnas de diferencia (stat_diff).
        """
        # Unir stats del equipo local
        home_stats = pd.merge(
            games_df[['game_id', 'home_team_id']],
            rolling_df,
            left_on=['game_id', 'home_team_id'],
            right_on=['game_id', 'team_id'],
            how='left'
        )
        
        # Unir stats del equipo visitante
        away_stats = pd.merge(
            games_df[['game_id', 'away_team_id']],
            rolling_df,
            left_on=['game_id', 'away_team_id'],
            right_on=['game_id', 'team_id'],
            how='left'
        )
        
        diffs = pd.DataFrame(index=games_df.index)
        for col in stats_cols:
            diffs[f"{col}_diff"] = home_stats[col] - away_stats[col]
            
        return diffs
