"""
Grupo 1 — Four Factors de Dean Oliver (Fase 2).

Cuatro diferencias LOCAL − VISITANTE expresadas como ratios calculados sobre
medias móviles de stats crudas:

    efg_diff       : eFG% local − eFG% visitante   (positivo = local mejor)
    tov_rate_diff  : TOV% local − TOV% visitante   (negativo = local pierde MENOS
                     balones = ventaja local; convención LOCAL − VISITANTE uniforme,
                     el modelo aprende el coeficiente negativo correcto)
    oreb_rate_diff : OREB% local − OREB% visitante (positivo = local mejor)
    ft_rate_diff   : FTrate local − FTrate visitante (positivo = local mejor)

Fórmulas (Dean Oliver):
    eFG%    = (FGM + 0.5 × FG3M) / FGA
    TOV%    = TOV / (FGA + 0.44 × FTA + TOV)
    OREB%   = OREB / (OREB + OPP_DREB)   ← requiere rebotes del oponente
    FT rate = FTA / FGA

Orden de cálculo (CRÍTICO — no alterar):
    1. Enriquecer stats con opp_dreb vía self-join sobre game_id.
    2. compute_rolling_means sobre stats CRUDAS (fgm, fg3m, fga, fta, oreb, tov, opp_dreb).
    3. Calcular ratios SOBRE los promedios rolling (nunca promediar ratios por partido).
    4. Pivotar a nivel partido y calcular diffs LOCAL − VISITANTE.
"""
from __future__ import annotations

import pandas as pd

from nba_predictor.config import ROLLING_WINDOW_GAMES
from nba_predictor.features._shared import load_stats_with_opponent
from nba_predictor.features.rolling import compute_rolling_means

_RAW_COLS: list[str] = ["fgm", "fg3m", "fga", "fta", "oreb", "tov", "opp_dreb"]

OUTPUT_COLS: list[str] = [
    "game_id", "season", "game_date",
    "efg_diff", "tov_rate_diff", "oreb_rate_diff", "ft_rate_diff",
]


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def build_four_factors(window: int = ROLLING_WINDOW_GAMES) -> pd.DataFrame:
    """
    Carga datos del DataStore y calcula los Four Factors para todos los partidos.

    Punto de entrada para scripts/notebooks. Para tests usar compute_four_factors
    con DataFrames sintéticos (sin acceso a BD).

    Returns
    -------
    DataFrame con columnas OUTPUT_COLS, ordenado por game_date.
    Partidos sin historia suficiente (inicio de dataset o ventana incompleta)
    tienen NaN en los diffs — el filtrado de primeros 15 y warmup es responsabilidad
    del ensamblado final de FEATURES.
    """
    stats = _load_stats_with_opp_dreb()
    return compute_four_factors(stats, window=window)


def compute_four_factors(
    stats: pd.DataFrame,
    window: int = ROLLING_WINDOW_GAMES,
) -> pd.DataFrame:
    """
    Calcula los Four Factors a partir de un DataFrame de stats por (equipo, partido).

    Función de pura computación — testeable sin BD.

    Parameters
    ----------
    stats : DataFrame con UNA FILA por (equipo, partido). Columnas obligatorias:
            team_id, game_id, game_date, season, is_home,
            fgm, fg3m, fga, fta, oreb, tov, opp_dreb.
            opp_dreb debe estar ya presente (aplicar _add_opp_dreb previamente).
    window : Partidos previos requeridos para una media válida.

    Returns
    -------
    DataFrame con columnas OUTPUT_COLS. Una fila por partido.

    Por qué ratios SOBRE promedios (paso 3), no promedios DE ratios
    -------------------------------------------------------------
    Un partido con 100 FGA aporta el doble de información que uno con 50 FGA.
    Si promediamos el ratio partido a partido, tratamos ambos como igualmente
    informativos — incorrecto. Al promediar primero las cantidades crudas y
    luego calcular el ratio, la ponderación es proporcional al volumen de tiro:

        Juego 1: 20 FGM / 40 FGA  → eFG = 50 %
        Juego 2: 60 FGM / 100 FGA → eFG = 60 %
        Promedio de ratios : (50+60)/2       = 55.0 %  ← trata juegos igual
        Ratio del promedio : (20+60)/(40+100) ≈ 57.1 % ← pondera por volumen ✓
    """
    _validate_input(stats)
    if window < 1:
        raise ValueError(f"window debe ser >= 1, recibido {window}")

    # Paso 2: medias móviles de stats crudas
    rolled = compute_rolling_means(stats, stat_cols=_RAW_COLS, window=window)

    # Paso 3: ratios sobre promedios rolling
    rolled = _compute_ratios(rolled)

    # Paso 4: diferencias LOCAL − VISITANTE por partido
    return _to_game_differences(rolled)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _load_stats_with_opp_dreb() -> pd.DataFrame:
    """Carga team_game_stats con game_date y añade opp_dreb vía _shared."""
    return load_stats_with_opponent(["dreb"])


def _compute_ratios(rolled: pd.DataFrame) -> pd.DataFrame:
    """
    Paso 3: calcula los ratios de los Four Factors sobre los promedios rolling.

    Opera sobre las columnas *_rolling generadas por compute_rolling_means.
    Si alguna columna rolling es NaN (sin historia suficiente), el ratio
    correspondiente también será NaN — esto se propaga correctamente.
    """
    df = rolled.copy()
    df["efg"] = (df["fgm_rolling"] + 0.5 * df["fg3m_rolling"]) / df["fga_rolling"]
    df["tov_rate"] = df["tov_rolling"] / (
        df["fga_rolling"] + 0.44 * df["fta_rolling"] + df["tov_rolling"]
    )
    df["oreb_rate"] = df["oreb_rolling"] / (df["oreb_rolling"] + df["opp_dreb_rolling"])
    df["ft_rate"] = df["fta_rolling"] / df["fga_rolling"]
    return df


def _to_game_differences(stats_with_ratios: pd.DataFrame) -> pd.DataFrame:
    """
    Paso 4: pivota de (equipo, partido) a partido y calcula diffs LOCAL − VISITANTE.

    validate="one_to_one" garantiza que cada partido tenga exactamente una fila
    de equipo local y una de visitante en los datos enriched.
    """
    keep_home = ["game_id", "season", "game_date", "efg", "tov_rate", "oreb_rate", "ft_rate"]
    keep_away = ["game_id", "efg", "tov_rate", "oreb_rate", "ft_rate"]

    home = stats_with_ratios[stats_with_ratios["is_home"] == 1][keep_home].copy()
    away = stats_with_ratios[stats_with_ratios["is_home"] == 0][keep_away].copy()

    games = home.merge(
        away, on="game_id", suffixes=("_home", "_away"), validate="one_to_one"
    )

    result = games[["game_id", "season", "game_date"]].copy()
    for factor, label in [
        ("efg", "efg_diff"),
        ("tov_rate", "tov_rate_diff"),
        ("oreb_rate", "oreb_rate_diff"),
        ("ft_rate", "ft_rate_diff"),
    ]:
        result[label] = games[f"{factor}_home"] - games[f"{factor}_away"]

    return result.sort_values("game_date").reset_index(drop=True)


def _validate_input(stats: pd.DataFrame) -> None:
    required = {"team_id", "game_id", "game_date", "season", "is_home"} | set(_RAW_COLS)
    missing = required - set(stats.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {sorted(missing)}")
