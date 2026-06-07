"""Download and normalization of NBA game box scores."""
import re

import pandas as pd

from .nba_client import NBAClient

# Regex to convert "MM:SS" to decimal minutes
_MIN_RE = re.compile(r"^(\d+):(\d+)$")

# API column mapping → canonical schema (team)
_TEAM_COL_MAP = {
    "TEAM_ID": "team_id",
    "FGM": "fgm",
    "FGA": "fga",
    "FG3M": "fg3m",
    "FG3A": "fg3a",
    "FTM": "ftm",
    "FTA": "fta",
    "OREB": "oreb",
    "DREB": "dreb",
    "AST": "ast",
    "STL": "stl",
    "BLK": "blk",
    "TO": "tov",
    "PF": "pf",
    "PLUS_MINUS": "plus_minus",
}

# API column mapping → canonical schema (player)
_PLAYER_COL_MAP = {
    "PLAYER_ID": "player_id",
    "TEAM_ID": "team_id",
    "FGM": "fgm",
    "FGA": "fga",
    "FG3M": "fg3m",
    "FG3A": "fg3a",
    "FTM": "ftm",
    "FTA": "fta",
    "OREB": "oreb",
    "DREB": "dreb",
    "AST": "ast",
    "STL": "stl",
    "BLK": "blk",
    "TO": "tov",
    "PF": "pf",
    "PLUS_MINUS": "plus_minus",
}


def _parse_minutes(raw: str | None) -> float | None:
    """
    Converts 'MM:SS' to decimal minutes (e.g., '32:30' → 32.5).

    Necessary because strings cannot be averaged; Phase 2 rolling windows
    operate on numbers.
    """
    if raw is None:
        return None
    if isinstance(raw, float):
        # pandas may return NaN as float when the cell is NULL
        return None if pd.isna(raw) else raw
    s = str(raw).strip()
    m = _MIN_RE.match(s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 60.0
    return None


def _normalize_team_stats(
    raw_team: pd.DataFrame,
    game_id: str,
    home_team_id: int,
) -> pd.DataFrame:
    """
    Converts the team_stats DataFrame from BoxScoreTraditionalV2 to the canonical schema.

    home_team_id is explicitly passed because the API does not expose a reliable
    home/away field in all seasons; we derive it from the data we already have
    in the games table.

    Returns exactly 2 rows (one per team). Raises ValueError otherwise.
    """
    df = raw_team.rename(columns=_TEAM_COL_MAP)[list(_TEAM_COL_MAP.values())].copy()

    if len(df) != 2:
        raise ValueError(
            f"game_id={game_id}: expected 2 team rows, got {len(df)}"
        )

    df["game_id"] = game_id
    df["is_home"] = (df["team_id"] == home_team_id).astype(int)

    cols = [
        "game_id", "team_id", "is_home",
        "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
        "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "plus_minus",
    ]
    return df[cols].reset_index(drop=True)


def _normalize_player_stats(
    raw_player: pd.DataFrame,
    game_id: str,
    home_team_id: int,
) -> pd.DataFrame:
    """
    Converts the player_stats DataFrame from BoxScoreTraditionalV2 to the canonical schema.

    START_POSITION is a string like 'G', 'F', 'C' for starters and '' for bench players.
    minutes are converted from 'MM:SS' to decimal to allow averaging in Phase 2.
    """
    df = raw_player.copy()

    # Derive started before renaming columns
    df["started"] = df["START_POSITION"].apply(
        lambda x: 1 if (x and str(x).strip()) else 0
    )

    # Convert minutes to decimal
    df["minutes"] = df["MIN"].apply(_parse_minutes)

    df = df.rename(columns=_PLAYER_COL_MAP)[
        list(_PLAYER_COL_MAP.values()) + ["minutes", "started"]
    ].copy()

    df["game_id"] = game_id
    df["is_home"] = (df["team_id"] == home_team_id).astype(int)

    cols = [
        "game_id", "player_id", "team_id", "is_home", "minutes", "started",
        "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
        "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "plus_minus",
    ]
    return df[cols].reset_index(drop=True)


def ingest_boxscore(
    client: NBAClient,
    game_id: str,
    home_team_id: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    Downloads and normalizes the box score for a game.

    Args:
        client: NBAClient configured with throttle and retry.
        game_id: official NBA identifier, e.g., '0022300001'.
        home_team_id: Home team ID (obtained from the games table).

    Returns:
        (team_stats, player_stats, raw_payload) ready to be passed to
        save_team_game_stats / save_player_game_stats / save_raw_boxscore.
    """
    raw_team, raw_player, raw_payload = client.fetch_boxscore(game_id)
    team_stats = _normalize_team_stats(raw_team, game_id, home_team_id)
    player_stats = _normalize_player_stats(raw_player, game_id, home_team_id)
    return team_stats, player_stats, raw_payload
