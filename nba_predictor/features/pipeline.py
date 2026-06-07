"""Base framework for feature generation and temporal window management."""
import pandas as pd

class FeaturePipeline:
    """
    Base framework for feature generation and temporal window management.
    """
    
    def calculate_rolling(self, df: pd.DataFrame, col: str, window: int, group_col: str) -> pd.Series:
        """
        Calculates the rolling mean of a column, grouped by a column (e.g., team_id),
        ensuring no look-ahead bias by applying a shift.
        
        Args:
            df: DataFrame with the data (must include game_date).
            col: Column on which to calculate the mean.
            window: Window size.
            group_col: Column to group by (usually team_id).
            
        Returns:
            pd.Series with the calculated values, aligned with the original index.
        """
        # Ensure data is sorted chronologically for each group
        # Although input is usually sorted, we guarantee it here for rolling logic
        original_index = df.index
        df_work = df.sort_values([group_col, 'game_date'])
        
        rolling = (
            df_work.groupby(group_col)[col]
            .transform(lambda x: x.shift(1).rolling(window=window, min_periods=1).mean())
        )
        
        # Re-align with original index so output matches input
        result = rolling.loc[original_index]
        result.name = f"{col}_roll_{window}"
        
        return result

    def calculate_rolling_set(
        self, df: pd.DataFrame, columns: list[str], windows: list[int], group_col: str
    ) -> pd.DataFrame:
        """
        Calculates multiple rolling means for various columns and windows.
        
        Args:
            df: DataFrame with the data.
            columns: List of columns to apply rolling to.
            windows: List of window sizes.
            group_col: Column to group by.
            
        Returns:
            DataFrame with all newly calculated columns.
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
        Calculates the difference between home and away team rolling statistics.
        
        Args:
            games_df: Games DataFrame with home_team_id and away_team_id.
            rolling_df: DataFrame with rolling statistics (must have game_id and team_id).
            stats_cols: List of statistics columns to calculate the difference for.
            
        Returns:
            DataFrame with difference columns (stat_diff).
        """
        # Join home team stats
        home_stats = pd.merge(
            games_df[['game_id', 'home_team_id']],
            rolling_df,
            left_on=['game_id', 'home_team_id'],
            right_on=['game_id', 'team_id'],
            how='left'
        )
        
        # Join away team stats
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
