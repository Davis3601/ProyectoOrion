import pandas as pd
import numpy as np
import pytest
from nba_predictor.features.pipeline import FeaturePipeline

def test_pipeline_initialization():
    pipeline = FeaturePipeline()
    assert pipeline is not None

def test_rolling_average_no_lookahead():
    # Setup mock data: 5 games for a team
    data = pd.DataFrame({
        'game_id': ['G1', 'G2', 'G3', 'G4', 'G5'],
        'team_id': [1, 1, 1, 1, 1],
        'val': [10.0, 20.0, 30.0, 40.0, 50.0],
        'game_date': pd.to_datetime(['2023-01-01', '2023-01-02', '2023-01-03', '2023-01-04', '2023-01-05'])
    })
    
    pipeline = FeaturePipeline()
    # We want rolling mean of 'val' with window 2, shifted so it doesn't include current game
    # G1: NaN
    # G2: val(G1) / 1 = 10 (Wait, if window is 2, min_periods should probably be 1)
    # G3: (val(G1) + val(G2)) / 2 = 15
    # G4: (val(G2) + val(G3)) / 2 = 25
    result = pipeline.calculate_rolling(data, 'val', window=2, group_col='team_id')
    
    # Expected: [NaN, 10.0, 15.0, 25.0, 35.0]
    expected = [np.nan, 10.0, 15.0, 25.0, 35.0]
    pd.testing.assert_series_equal(result, pd.Series(expected, name='val_roll_2'))

def test_rolling_average_multiple_teams():
    data = pd.DataFrame({
        'game_id': ['G1', 'G2', 'G3', 'G4'],
        'team_id': [1, 2, 1, 2],
        'val': [10.0, 100.0, 20.0, 200.0],
        'game_date': pd.to_datetime(['2023-01-01', '2023-01-01', '2023-01-02', '2023-01-02'])
    }).sort_values(['team_id', 'game_date'])
    
    pipeline = FeaturePipeline()
    result = pipeline.calculate_rolling(data, 'val', window=1, group_col='team_id')
    
    # Team 1: G1(10) -> G3(10)
    # Team 2: G2(100) -> G4(100)
    # Result should be [NaN, NaN, 10.0, 100.0] if sorted by index
    # But calculate_rolling should return in same order as input if possible, or we check by team
    
    t1_results = result[data['team_id'] == 1]
    t2_results = result[data['team_id'] == 2]
    
    assert pd.isna(t1_results.iloc[0])
    assert t1_results.iloc[1] == 10.0
    assert pd.isna(t2_results.iloc[0])
    assert t2_results.iloc[1] == 100.0
