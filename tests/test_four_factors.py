import pandas as pd
import pytest
from nba_predictor.features.four_factors import calculate_efg, calculate_tov_rate

def test_calculate_efg():
    data = pd.DataFrame({
        'fgm': [10, 20],
        'fg3m': [2, 4],
        'fga': [20, 40]
    })
    # Game 1: (10 + 0.5 * 2) / 20 = 11 / 20 = 0.55
    # Game 2: (20 + 0.5 * 4) / 40 = 22 / 40 = 0.55
    res = calculate_efg(data)
    assert res.iloc[0] == 0.55
    assert res.iloc[1] == 0.55

def test_calculate_efg_zero_fga():
    data = pd.DataFrame({
        'fgm': [0],
        'fg3m': [0],
        'fga': [0]
    })
    res = calculate_efg(data)
    assert res.iloc[0] == 0.0

def test_calculate_tov_rate():
    data = pd.DataFrame({
        'tov': [10],
        'fga': [40],
        'fta': [10]
    })
    # TOV Rate = 10 / (40 + 0.44 * 10 + 10) = 10 / 54.4
    expected = 10 / (40 + 0.44 * 10 + 10)
    res = calculate_tov_rate(data)
    assert res.iloc[0] == pytest.approx(expected)

def test_calculate_tov_rate_zero_possessions():
    data = pd.DataFrame({
        'tov': [0],
        'fga': [0],
        'fta': [0]
    })
    res = calculate_tov_rate(data)
    assert res.iloc[0] == 0.0

def test_generate_four_factors_features():
    from nba_predictor.features.pipeline import FeaturePipeline
    from nba_predictor.features.four_factors import generate_four_factors_features
    
    # Games: Team 1 vs Team 2
    games = pd.DataFrame({
        'game_id': ['G1'],
        'home_team_id': [1],
        'away_team_id': [2],
        'game_date': pd.to_datetime(['2023-01-10'])
    })
    
    # Historical stats for teams before G1
    stats = pd.DataFrame({
        'game_id': ['G0', 'G0'],
        'team_id': [1, 2],
        'fgm': [20, 10], # Team 1 has better eFG
        'fg3m': [0, 0],
        'fga': [40, 40],
        'tov': [5, 10],   # Team 1 has better TOV%
        'fta': [0, 0]
    })
    
    # We need to include the target game in stats so rolling can calculate "before G1"
    # But wait, generate_four_factors_features takes stats_df and games_df.
    # It calculates rolling on stats_df.
    
    # Historical games to establish rolling averages
    hist_games = pd.DataFrame({
        'game_id': ['G0'],
        'game_date': pd.to_datetime(['2023-01-01'])
    })
    
    # Combined stats and games for the generator
    all_stats = pd.concat([stats, pd.DataFrame({
        'game_id': ['G1', 'G1'],
        'team_id': [1, 2],
        'fgm': [0, 0], 'fg3m': [0, 0], 'fga': [0, 0], 'tov': [0, 0], 'fta': [0, 0]
    })], ignore_index=True)
    
    all_games = pd.concat([hist_games, games], ignore_index=True)
    
    pipeline = FeaturePipeline()
    res = generate_four_factors_features(all_stats, all_games, pipeline, windows=[1])
    
    # G1 should have efg_roll_1_diff and tov_rate_roll_1_diff
    # Team 1 eFG (G0) = 20/40 = 0.5
    # Team 2 eFG (G0) = 10/40 = 0.25
    # efg_diff = 0.5 - 0.25 = 0.25
    
    # Team 1 TOV Rate (G0) = 5 / (40 + 5) = 5/45 = 0.111
    # Team 2 TOV Rate (G0) = 10 / (40 + 10) = 10/50 = 0.2
    # tov_rate_diff = 0.111 - 0.2 = -0.0888
    
    g1_res = res[all_games['game_id'] == 'G1'].iloc[0]
    assert g1_res['efg_roll_1_diff'] == 0.25
    assert g1_res['tov_rate_roll_1_diff'] == pytest.approx(-0.0888, abs=1e-3)
