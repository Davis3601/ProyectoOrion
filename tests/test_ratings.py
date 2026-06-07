import pandas as pd
import pytest
from nba_predictor.features.ratings import calculate_possessions, calculate_ratings


def test_calculate_ratings():
    data = pd.DataFrame({
        'pts': [110, 100],
        'opp_pts': [100, 110],
        'fga': [90, 90],
        'fta': [20, 20],
        'tov': [10, 10],
        'oreb': [10, 10]
    })

    # Poss = 90 + 0.44 * 20 + 10 - 10 = 90 + 8.8 = 98.8
    # ORtg = 100 * (110 / 98.8) = 111.336...
    # DRtg = 100 * (100 / 98.8) = 101.214...
    # NetRtg = 111.336 - 101.214 = 10.121...

    res = calculate_ratings(data)

    expected_poss = 90 + 0.44 * 20 + 10 - 10
    expected_ortg = 100 * (110 / expected_poss)
    expected_drtg = 100 * (100 / expected_poss)
    expected_netrtg = expected_ortg - expected_drtg

    assert res['ortg'].iloc[0] == pytest.approx(expected_ortg)
    assert res['drtg'].iloc[0] == pytest.approx(expected_drtg)
    assert res['netrtg'].iloc[0] == pytest.approx(expected_netrtg)


def test_calculate_ratings_zero_poss():
    data = pd.DataFrame({
        'pts': [0],
        'opp_pts': [0],
        'fga': [0],
        'fta': [0],
        'tov': [0],
        'oreb': [0]
    })
    res = calculate_ratings(data)
    assert res['ortg'].iloc[0] == 0.0
    assert res['drtg'].iloc[0] == 0.0
    assert res['netrtg'].iloc[0] == 0.0


def test_calculate_ratings_requires_opp_pts():
    # opp_pts is a hard precondition: the old silent fallback used to change
    # the input's row count, breaking index alignment for callers.
    data = pd.DataFrame({
        'pts': [110],
        'fga': [90],
        'fta': [20],
        'tov': [10],
        'oreb': [10]
    })
    with pytest.raises(ValueError, match="opp_pts"):
        calculate_ratings(data)


def test_calculate_possessions_clipped_at_zero():
    # Anomalous data where OREB > FGA + 0.44*FTA + TOV must not produce
    # negative possessions (which would yield huge negative ratings).
    data = pd.DataFrame({
        'fga': [5],
        'fta': [0],
        'tov': [1],
        'oreb': [10]
    })
    poss = calculate_possessions(data)
    assert poss.iloc[0] == 0.0
