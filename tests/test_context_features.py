import pandas as pd
from nba_predictor.features.context import calculate_rest_days

def test_calculate_rest_days():
    data = pd.DataFrame({
        'team_id': [1, 1, 1],
        'game_date': pd.to_datetime(['2023-01-01', '2023-01-02', '2023-01-05'])
    })
    # G1: None -> NaN
    # G2: 2023-01-02 - 2023-01-01 = 1 day
    # G3: 2023-01-05 - 2023-01-02 = 3 days
    res = calculate_rest_days(data)
    assert pd.isna(res.iloc[0])
    assert res.iloc[1] == 1
    assert res.iloc[2] == 3

def test_calculate_rest_days_multiple_teams():
    data = pd.DataFrame({
        'team_id': [1, 2, 1, 2],
        'game_date': pd.to_datetime(['2023-01-01', '2023-01-01', '2023-01-03', '2023-01-04'])
    })
    res = calculate_rest_days(data)
    # Original order: [NaN, NaN, 2, 3]
    assert pd.isna(res.iloc[0])
    assert pd.isna(res.iloc[1])
    assert res.iloc[2] == 2
    assert res.iloc[3] == 3

def test_generate_context_features():
    from nba_predictor.features.context import generate_context_features
    
    games = pd.DataFrame({
        'game_id': ['G1', 'G2'],
        'home_team_id': [1, 2],
        'away_team_id': [2, 1],
        'game_date': pd.to_datetime(['2023-01-01', '2023-01-02'])
    })
    
    stats = pd.DataFrame({
        'game_id': ['G1', 'G1', 'G2', 'G2'],
        'team_id': [1, 2, 2, 1]
    })
    
    features = generate_context_features(games, stats)
    
    # G1: Both first games -> rest_diff = 7 - 7 = 0, b2b = 0
    assert features.iloc[0]['rest_diff'] == 0
    assert features.iloc[0]['home_b2b'] == 0
    assert features.iloc[0]['away_b2b'] == 0
    
    # G2: 
    # Home Team (2): played G1 on 01-01. G2 is 01-02. Rest = 1. home_b2b = 1.
    # Away Team (1): played G1 on 01-01. G2 is 01-02. Rest = 1. away_b2b = 1.
    # rest_diff = 1 - 1 = 0
    assert features.iloc[1]['rest_diff'] == 0
    assert features.iloc[1]['home_b2b'] == 1
    assert features.iloc[1]['away_b2b'] == 1
