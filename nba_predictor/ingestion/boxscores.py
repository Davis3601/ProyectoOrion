"""Descarga y normalización de box scores de partidos NBA."""
import re

import pandas as pd

from .nba_client import NBAClient

# Regex para convertir "MM:SS" a minutos decimales
_MIN_RE = re.compile(r"^(\d+):(\d+)$")

# Mapeo de columnas API → esquema canónico (equipo)
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

# Mapeo de columnas API → esquema canónico (jugador)
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
    Convierte 'MM:SS' a minutos decimales (e.g. '32:30' → 32.5).

    Necesario porque no podemos promediar strings; los rolling windows
    de Fase 2 operan sobre números.
    """
    if raw is None:
        return None
    if isinstance(raw, float):
        # pandas puede devolver NaN como float cuando la celda es NULL
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
    Convierte el DataFrame de team_stats de BoxScoreTraditionalV2 al esquema canónico.

    home_team_id se pasa explícitamente porque la API no expone un campo home/away
    confiable en todas las temporadas; lo derivamos a partir de los datos que
    ya tenemos en la tabla games.

    Returns exactamente 2 filas (una por equipo). Lanza ValueError si no es así.
    """
    df = raw_team.rename(columns=_TEAM_COL_MAP)[list(_TEAM_COL_MAP.values())].copy()

    if len(df) != 2:
        raise ValueError(
            f"game_id={game_id}: se esperaban 2 filas de equipo, se obtuvieron {len(df)}"
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
    Convierte el DataFrame de player_stats de BoxScoreTraditionalV2 al esquema canónico.

    START_POSITION es una cadena como 'G', 'F', 'C' para titulares y '' para suplentes.
    minutes se convierte de 'MM:SS' a decimal para permitir promedios en Fase 2.
    """
    df = raw_player.copy()

    # Derivar started antes de renombrar columnas
    df["started"] = df["START_POSITION"].apply(
        lambda x: 1 if (x and str(x).strip()) else 0
    )

    # Convertir minutos a decimal
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
    Descarga y normaliza el box score de un partido.

    Args:
        client: NBAClient configurado con throttle y retry.
        game_id: identificador oficial NBA, e.g. '0022300001'.
        home_team_id: ID del equipo local (obtenido de la tabla games).

    Returns:
        (team_stats, player_stats, raw_payload) listos para pasar a
        save_team_game_stats / save_player_game_stats / save_raw_boxscore.
    """
    raw_team, raw_player, raw_payload = client.fetch_boxscore(game_id)
    team_stats = _normalize_team_stats(raw_team, game_id, home_team_id)
    player_stats = _normalize_player_stats(raw_player, game_id, home_team_id)
    return team_stats, player_stats, raw_payload
