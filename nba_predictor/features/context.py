"""Implementation of contextual features like rest days and back-to-back indicators."""
import pandas as pd

DEFAULT_REST_DAYS = 7

def calculate_rest_days(df: pd.DataFrame) -> pd.Series:
    """
    Calculates rest days since the last game for each team.

    Args:
        df: DataFrame with 'team_id' and 'game_date' columns. Must have a
            unique index (raises ValueError otherwise).

    Returns:
        pd.Series with rest days since the previous game.
    """
    if not df.index.is_unique:
        raise ValueError("calculate_rest_days: df must have a unique index")

    original_index = df.index
    # Ensure game_date is datetime
    df_work = df.copy()
    df_work['game_date'] = pd.to_datetime(df_work['game_date'])
    df_work = df_work.sort_values(['team_id', 'game_date'])

    # Difference between consecutive dates for the same team
    rest = df_work.groupby('team_id')['game_date'].diff().dt.days

    return rest.loc[original_index]

def generate_context_features(
    games_df: pd.DataFrame,
    stats_df: pd.DataFrame
) -> pd.DataFrame:
    """
    Generates contextual features: rest_diff, home_b2b, away_b2b.

    Args:
        games_df: Games DataFrame.
        stats_df: DataFrame with stats per team and game.

    Returns:
        DataFrame with contextual features for each game.
    """
    # 1. Calculate rest days for each team appearance
    df_rest = stats_df[['game_id', 'team_id']].copy()
    # Need game_date
    df_rest = pd.merge(df_rest, games_df[['game_id', 'game_date']], on='game_id', validate="many_to_one")
    df_rest['rest_days'] = calculate_rest_days(df_rest)

    # 2. Join with games_df for home and away
    home_rest = pd.merge(
        games_df[['game_id', 'home_team_id']],
        df_rest,
        left_on=['game_id', 'home_team_id'],
        right_on=['game_id', 'team_id'],
        how='left',
        validate="one_to_one"
    )['rest_days']

    away_rest = pd.merge(
        games_df[['game_id', 'away_team_id']],
        df_rest,
        left_on=['game_id', 'away_team_id'],
        right_on=['game_id', 'team_id'],
        how='left',
        validate="one_to_one"
    )['rest_days']

    # 3. Calculate final features
    features = pd.DataFrame(index=games_df.index)

    # NOTE: home_rest/away_rest come from pd.merge, which resets the index to a
    # RangeIndex, while `features` uses games_df.index. All assignments below
    # use .values (positional) to avoid silent NaN from index misalignment.

    # rest_diff: difference in rest days.
    # If it's the first game of the season (NaN), use DEFAULT_REST_DAYS.
    features['rest_diff'] = home_rest.fillna(DEFAULT_REST_DAYS).values - away_rest.fillna(DEFAULT_REST_DAYS).values

    # b2b: played the day before. A team's first game of the season (NaN rest)
    # is intentionally NOT a back-to-back — fill NaN with 0 explicitly before
    # comparing instead of relying on the implicit NaN == 1 -> False behavior.
    features['home_b2b'] = (home_rest.fillna(0) == 1).astype(int).values
    features['away_b2b'] = (away_rest.fillna(0) == 1).astype(int).values

    return features
