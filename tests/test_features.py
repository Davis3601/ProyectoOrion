import pandas as pd
import numpy as np
import pytest
from nba_predictor.features.pipeline import FeaturePipeline


def test_pipeline_exposes_expected_api():
    pipeline = FeaturePipeline()
    for method in ('calculate_rolling', 'calculate_rolling_set', 'calculate_game_diffs'):
        assert callable(getattr(pipeline, method))


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


def test_rolling_average_interleaved_unsorted_input():
    # Input arrives interleaved by team and NOT pre-sorted — the common
    # real-world case. calculate_rolling must sort internally and still
    # return values aligned with the original row order.
    data = pd.DataFrame({
        'game_id': ['G3', 'G1', 'G4', 'G2'],
        'team_id': [1, 1, 2, 2],
        'val': [20.0, 10.0, 200.0, 100.0],
        'game_date': pd.to_datetime(['2023-01-02', '2023-01-01', '2023-01-02', '2023-01-01'])
    })

    pipeline = FeaturePipeline()
    result = pipeline.calculate_rolling(data, 'val', window=1, group_col='team_id')

    # Row order matches input: G3 (team 1, 2nd game) -> 10.0 (prev val),
    # G1 (team 1, 1st game) -> NaN, G4 (team 2, 2nd game) -> 100.0,
    # G2 (team 2, 1st game) -> NaN
    assert result.iloc[0] == 10.0
    assert pd.isna(result.iloc[1])
    assert result.iloc[2] == 100.0
    assert pd.isna(result.iloc[3])


def test_calculate_rolling_requires_game_date():
    data = pd.DataFrame({
        'team_id': [1],
        'val': [10.0],
    })
    pipeline = FeaturePipeline()
    with pytest.raises(ValueError, match="game_date"):
        pipeline.calculate_rolling(data, 'val', window=2, group_col='team_id')


def test_calculate_rolling_set():
    data = pd.DataFrame({
        'game_id': ['G1', 'G2', 'G3'],
        'team_id': [1, 1, 1],
        'stat1': [10.0, 20.0, 30.0],
        'stat2': [100.0, 200.0, 300.0],
        'game_date': pd.to_datetime(['2023-01-01', '2023-01-02', '2023-01-03'])
    })

    pipeline = FeaturePipeline()
    result_df = pipeline.calculate_rolling_set(data, columns=['stat1', 'stat2'], windows=[2], group_col='team_id')

    # Expected columns: stat1_roll_2, stat2_roll_2
    assert 'stat1_roll_2' in result_df.columns
    assert 'stat2_roll_2' in result_df.columns

    # Check values for stat1_roll_2: [NaN, 10.0, 15.0]
    assert pd.isna(result_df['stat1_roll_2'].iloc[0])
    assert result_df['stat1_roll_2'].iloc[1] == 10.0
    assert result_df['stat1_roll_2'].iloc[2] == 15.0


def test_calculate_game_diffs():
    games = pd.DataFrame({
        'game_id': ['G1'],
        'home_team_id': [1],
        'away_team_id': [2]
    })

    # Rolling stats available for teams heading into G1
    rolling = pd.DataFrame({
        'game_id': ['G1', 'G1'],
        'team_id': [1, 2],
        'stat_roll': [0.55, 0.50]
    })

    pipeline = FeaturePipeline()
    diffs = pipeline.calculate_game_diffs(games, rolling, ['stat_roll'])

    # Expected: stat_roll_diff = 0.55 - 0.50 = 0.05
    assert diffs.iloc[0]['stat_roll_diff'] == pytest.approx(0.05)


def test_calculate_game_diffs_non_default_index():
    # Regression test: games_df coming out of pd.concat/pd.merge can carry a
    # non-default index. The diff computation must not silently produce NaN
    # from index misalignment (merge results always carry a RangeIndex).
    games = pd.DataFrame({
        'game_id': ['G1', 'G2', 'G3'],
        'home_team_id': [1, 3, 5],
        'away_team_id': [2, 4, 6]
    }, index=[10, 20, 30])

    rolling = pd.DataFrame({
        'game_id': ['G1', 'G1', 'G2', 'G2', 'G3', 'G3'],
        'team_id': [1, 2, 3, 4, 5, 6],
        'stat_roll': [0.55, 0.50, 0.60, 0.45, 0.70, 0.40]
    })

    pipeline = FeaturePipeline()
    diffs = pipeline.calculate_game_diffs(games, rolling, ['stat_roll'])

    assert list(diffs.index) == [10, 20, 30]
    assert diffs['stat_roll_diff'].tolist() == pytest.approx([0.05, 0.15, 0.30])
