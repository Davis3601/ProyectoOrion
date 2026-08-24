"""
Grupo 3 — Ajuste por calidad de oponente (Fase 2).

Tres diferencias LOCAL - VISITANTE con sufijo _adj:

    off_rating_adj_diff : off_rating ajustado local - visitante
    def_rating_adj_diff : def_rating ajustado local - visitante
    net_rating_adj_diff : net_rating ajustado local - visitante

Enfoque: ajuste de PRIMER ORDEN, variante (a). Decisión cerrada — no SRS,
no iterativo, no ELO. Ver "Fase 2 — Decisión del Grupo 3" en CLAUDE.md.

Fórmulas:
    off_rating_adj = off_rating_rolling
                     - mean(opp_def_rating EN LA FECHA de cada enfrentamiento)
                     + league_avg

    def_rating_adj = def_rating_rolling
                     - mean(opp_off_rating EN LA FECHA de cada enfrentamiento)
                     + league_avg

    net_rating_adj = off_rating_adj - def_rating_adj

Donde:
- off_rating_rolling / def_rating_rolling son los ratings del Grupo 2 (con shift).
- opp_def_rating EN LA FECHA = el rolling de Grupo 2 del rival en ese partido
  (ya existe con shift, es un merge no un cálculo nuevo — variante a).
- league_avg = media expanding de off_rating de TODOS los equipos,
  usando SOLO fechas anteriores (shift de un día en serie diaria).

Orden de cálculo:
    1. Cargar team_ratings del Grupo 2 (compute_team_rolling_ratings).
    2. Self-join en game_id para añadir opp_off_rating y opp_def_rating
       (los ratings Grupo 2 del rival en la fecha de cada enfrentamiento).
    3. compute_rolling_means sobre opp_off_rating y opp_def_rating (con shift):
       da la media de los N rivales previos.
    4. league_avg: media expanding de off_rating diaria, shifteada un día.
    5. Aplicar fórmulas y pivotar a diffs LOCAL - VISITANTE.

Anti-leakage (doble garantía):
    - opp_def_rating al partido K = rating rolling DEL RIVAL antes de K
      (Grupo 2 ya tiene shift interno).
    - La media de esos ratings sobre la ventana usa shift(1) ADICIONAL
      en compute_rolling_means, por lo que el partido G no incluye
      su propio enfrentamiento.
    - league_avg: shift en serie diaria, excluye la fecha actual.
"""
from __future__ import annotations

import pandas as pd

from nba_predictor.config import ROLLING_WINDOW_GAMES
from nba_predictor.features._shared import add_opponent_stats
from nba_predictor.features.ratings import build_team_ratings, compute_team_rolling_ratings
from nba_predictor.features.rolling import compute_rolling_means

OUTPUT_COLS: list[str] = [
    "game_id", "season", "game_date",
    "off_rating_adj_diff", "def_rating_adj_diff", "net_rating_adj_diff",
]


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def build_adjusted_ratings(window: int = ROLLING_WINDOW_GAMES) -> pd.DataFrame:
    """
    Carga datos del DataStore, calcula ratings Grupo 2 y aplica ajuste por oponente.

    Punto de entrada para scripts/notebooks.

    Returns
    -------
    DataFrame con columnas OUTPUT_COLS, ordenado por game_date.
    NaN cuando no hay historia suficiente (propio o del oponente).
    """
    team_ratings = build_team_ratings(window=window)
    return compute_adjusted_ratings(team_ratings, window=window)


def compute_adjusted_ratings(
    team_ratings: pd.DataFrame,
    window: int = ROLLING_WINDOW_GAMES,
) -> pd.DataFrame:
    """
    Ajusta ratings por calidad de oponente y retorna diffs LOCAL - VISITANTE.

    Función de pura computación — testeable sin BD. Acepta el output de
    compute_team_rolling_ratings (o un DataFrame sintético con el mismo esquema).

    Parameters
    ----------
    team_ratings : DataFrame con UNA FILA por (equipo, partido). Columnas mínimas:
                   team_id, game_id, game_date, season, is_home,
                   off_rating, def_rating, net_rating.
    window       : Ventana de enfrentamientos previos (mismo hiperparámetro que Grupo 2).

    Returns
    -------
    DataFrame con columnas OUTPUT_COLS. Una fila por partido.
    """
    _validate_input(team_ratings)
    if window < 1:
        raise ValueError(f"window debe ser >= 1, recibido {window}")

    team_adj = compute_team_adjusted_ratings(team_ratings, window=window)
    return _to_game_differences(team_adj)


def compute_team_adjusted_ratings(
    team_ratings: pd.DataFrame,
    window: int = ROLLING_WINDOW_GAMES,
) -> pd.DataFrame:
    """
    Ratings ajustados a nivel equipo-partido. Una fila por (equipo, partido).

    Columnas añadidas: off_rating_adj, def_rating_adj, net_rating_adj.
    NaN cuando no hay historia suficiente (propio o del oponente).

    Expuesta para inspección y para tests que comparan directamente dos equipos
    sin necesitar el pivot home/away (ej. test b: equipo con calendario fuerte
    vs. calendario débil).
    """
    # Paso 2: añadir ratings del rival en cada partido (variante a)
    with_opp = add_opponent_stats(team_ratings, ["off_rating", "def_rating"])

    # Paso 3: media rolling de los ratings del rival (con shift anti-leakage)
    rolled = compute_rolling_means(
        with_opp, stat_cols=["opp_off_rating", "opp_def_rating"], window=window
    )

    # Paso 4: league_avg por fecha (expanding, sin futuro)
    daily_avg = _compute_daily_league_avg(team_ratings)
    merged = rolled.merge(daily_avg, on="game_date", how="left")

    # Paso 5: aplicar fórmulas del ajuste de primer orden
    merged["off_rating_adj"] = (
        merged["off_rating"]
        - merged["opp_def_rating_rolling"]
        + merged["league_avg"]
    )
    merged["def_rating_adj"] = (
        merged["def_rating"]
        - merged["opp_off_rating_rolling"]
        + merged["league_avg"]
    )
    merged["net_rating_adj"] = merged["off_rating_adj"] - merged["def_rating_adj"]

    return merged


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _compute_daily_league_avg(team_ratings: pd.DataFrame) -> pd.DataFrame:
    """
    Media expanding diaria de off_rating de toda la liga, usando SOLO fechas previas.

    Patrón anti-leakage:
        1. Promediar off_rating de todos los equipos por fecha (instantánea diaria).
        2. shift(1) en la serie diaria → el valor de la fecha D usa solo D-1 y antes.
        3. expanding().mean() → media acumulada de todas las fechas previas.

    off_rating_avg ≈ def_rating_avg por construcción (todo punto anotado es un
    punto permitido desde el lado contrario), por lo que un único escalar
    representa ambos contextos (off y def) para el ajuste.

    Returns
    -------
    DataFrame con columnas (game_date, league_avg). Una fila por fecha.
    """
    valid = team_ratings[["game_date", "off_rating"]].dropna()

    daily = (
        valid.groupby("game_date")["off_rating"]
        .mean()
        .sort_index()
        .reset_index()
        .rename(columns={"off_rating": "_daily_mean"})
    )

    # shift(1): fecha D_i usa solo datos de D_0..D_{i-1}
    daily["league_avg"] = daily["_daily_mean"].shift(1).expanding().mean()

    return daily[["game_date", "league_avg"]]


def _to_game_differences(team_adj: pd.DataFrame) -> pd.DataFrame:
    """
    Pivota de (equipo, partido) a partido y calcula diffs LOCAL - VISITANTE.
    """
    keep_home = [
        "game_id", "season", "game_date",
        "off_rating_adj", "def_rating_adj", "net_rating_adj",
    ]
    keep_away = ["game_id", "off_rating_adj", "def_rating_adj", "net_rating_adj"]

    home = team_adj[team_adj["is_home"] == 1][keep_home].copy()
    away = team_adj[team_adj["is_home"] == 0][keep_away].copy()

    games = home.merge(
        away, on="game_id", suffixes=("_home", "_away"), validate="one_to_one"
    )

    result = games[["game_id", "season", "game_date"]].copy()
    for r, label in [
        ("off_rating_adj", "off_rating_adj_diff"),
        ("def_rating_adj", "def_rating_adj_diff"),
        ("net_rating_adj", "net_rating_adj_diff"),
    ]:
        result[label] = games[f"{r}_home"] - games[f"{r}_away"]

    return result.sort_values("game_date").reset_index(drop=True)


def _validate_input(df: pd.DataFrame) -> None:
    required = {"team_id", "game_id", "game_date", "season", "is_home",
                "off_rating", "def_rating", "net_rating"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {sorted(missing)}")
