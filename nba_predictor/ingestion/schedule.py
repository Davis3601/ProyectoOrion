"""Logic for schedule download and normalization."""
import logging
from datetime import date

import pandas as pd

from .nba_client import NBAClient

_log = logging.getLogger(__name__)


# COVID Bubble: regular season in neutral site
_BUBBLE_START = date(2020, 7, 30)
_BUBBLE_END = date(2020, 8, 14)


def _is_neutral_site(game_date: date) -> bool:
    return _BUBBLE_START <= game_date <= _BUBBLE_END


def _normalize_season_schedule(raw: pd.DataFrame, season: str) -> pd.DataFrame:
    """
    Converts the raw LeagueGameFinder DataFrame to our canonical schema.

    The raw data includes each game twice (one per team). We perform a self-join
    on GAME_ID to produce one row per game with home/away teams clearly identified.
    """
    raw = raw.copy()
    raw["GAME_DATE"] = pd.to_datetime(raw["GAME_DATE"]).dt.date

    # Defense 1: remove exact duplicates of (GAME_ID, TEAM_ID).
    # If a team appears twice for the same game, we keep one row.
    raw = raw.drop_duplicates(subset=["GAME_ID", "TEAM_ID"], keep="first")

    # Defense 2: keep only games that have EXACTLY 2 teams.
    # Any game_id with a different number of rows is anomalous and discarded.
    rows_per_game = raw.groupby("GAME_ID")["TEAM_ID"].transform("size")
    raw = raw[rows_per_game == 2]

    # Identify home vs away by the MATCHUP column
    # 'vs.' = home, '@' = away
    raw["is_home"] = raw["MATCHUP"].str.contains("vs.", regex=False)

    home = raw[raw["is_home"]].copy()
    away = raw[~raw["is_home"]].copy()

    # Defense 3: discard games that don't have exactly 1 home row and 1 away row.
    # Occurs in neutral site games (Paris Games, etc.) where both rows use "@"
    # because no team is truly at home. We prefer to discard rather than assign
    # home/away arbitrarily, which would introduce noise in the home advantage feature.
    valid_ids = (
        set(home.groupby("GAME_ID").filter(lambda g: len(g) == 1)["GAME_ID"])
        & set(away.groupby("GAME_ID").filter(lambda g: len(g) == 1)["GAME_ID"])
    )
    n_dropped = len(set(raw["GAME_ID"])) - len(valid_ids)
    if n_dropped:
        _log.warning(
            f"{season}: {n_dropped} game(s) discarded for not having exactly "
            "1 home team and 1 away team in MATCHUP (likely unhandled neutral site)."
        )
    home = home[home["GAME_ID"].isin(valid_ids)]
    away = away[away["GAME_ID"].isin(valid_ids)]

    home = home.rename(columns={
        "TEAM_ID": "home_team_id",
        "PTS": "home_pts",
        "WL": "home_wl",
    })
    away = away.rename(columns={
        "TEAM_ID": "away_team_id",
        "PTS": "away_pts",
    })

    games = home[["GAME_ID", "GAME_DATE", "home_team_id", "home_pts", "home_wl"]].merge(
        away[["GAME_ID", "away_team_id", "away_pts"]],
        on="GAME_ID",
        how="inner",
        validate="one_to_one",
    )

    games["home_won"] = (games["home_wl"] == "W").astype("Int64")
    games["neutral_site"] = games["GAME_DATE"].apply(_is_neutral_site).astype(int)
    games["season"] = season
    games["season_type"] = "Regular Season"

    games = games.rename(columns={
        "GAME_ID": "game_id",
        "GAME_DATE": "game_date",
    })

    columns = [
        "game_id", "season", "season_type", "game_date",
        "home_team_id", "away_team_id",
        "home_pts", "away_pts", "home_won",
        "neutral_site",
    ]
    return games[columns].sort_values("game_date").reset_index(drop=True)


def ingest_season_schedule(client: NBAClient, season: str) -> pd.DataFrame:
    """Downloads and normalizes the schedule for a season."""
    raw = client.fetch_season_schedule(season)
    return _normalize_season_schedule(raw, season)
