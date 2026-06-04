"""Lógica de descarga y normalización del calendario."""
import logging
from datetime import date

import pandas as pd

from .nba_client import NBAClient

_log = logging.getLogger(__name__)


# Burbuja COVID: temporada regular en sede neutral
_BUBBLE_START = date(2020, 7, 30)
_BUBBLE_END = date(2020, 8, 14)


def _is_neutral_site(game_date: date) -> bool:
    return _BUBBLE_START <= game_date <= _BUBBLE_END


def _normalize_season_schedule(raw: pd.DataFrame, season: str) -> pd.DataFrame:
    """
    Convierte el DataFrame crudo de LeagueGameFinder a nuestro esquema canónico.

    El raw trae cada partido 2 veces (una por equipo). Hacemos self-join sobre GAME_ID
    para producir una fila por partido con home/away identificados claramente.
    """
    raw = raw.copy()
    raw["GAME_DATE"] = pd.to_datetime(raw["GAME_DATE"]).dt.date

    # Defensa 1: eliminar duplicados exactos de (GAME_ID, TEAM_ID).
    # Si un equipo aparece dos veces para el mismo partido, nos quedamos con una fila.
    raw = raw.drop_duplicates(subset=["GAME_ID", "TEAM_ID"], keep="first")

    # Defensa 2: quedarnos solo con partidos que tengan EXACTAMENTE 2 equipos.
    # Cualquier game_id con un número distinto de filas es anómalo y lo descartamos.
    rows_per_game = raw.groupby("GAME_ID")["TEAM_ID"].transform("size")
    raw = raw[rows_per_game == 2]

    # Identificamos local vs visitante por la columna MATCHUP
    # 'vs.' = local, '@' = visitante
    raw["is_home"] = raw["MATCHUP"].str.contains("vs.", regex=False)

    home = raw[raw["is_home"]].copy()
    away = raw[~raw["is_home"]].copy()

    # Defensa 3: descartar partidos que no tienen exactamente 1 fila local y 1 visitante.
    # Ocurre en juegos de sede neutral (Paris Games, etc.) donde ambas filas usan "@"
    # porque ningún equipo es realmente local. Preferimos descartar a asignar home/away
    # de forma arbitraria, lo que introduciría ruido en la feature de ventaja local.
    valid_ids = (
        set(home.groupby("GAME_ID").filter(lambda g: len(g) == 1)["GAME_ID"])
        & set(away.groupby("GAME_ID").filter(lambda g: len(g) == 1)["GAME_ID"])
    )
    n_dropped = len(set(raw["GAME_ID"])) - len(valid_ids)
    if n_dropped:
        _log.warning(
            f"{season}: {n_dropped} partido(s) descartados por no tener exactamente "
            "1 equipo local y 1 visitante en MATCHUP (probable sede neutral no contemplada)."
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
    """Descarga y normaliza el calendario de una temporada."""
    raw = client.fetch_season_schedule(season)
    return _normalize_season_schedule(raw, season)