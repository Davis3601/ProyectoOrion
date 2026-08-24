"""
Grupo 2 — Ratings ofensivo/defensivo rolling (Fase 2).

Tres diferencias LOCAL - VISITANTE calculadas sobre medias móviles:

    off_rating_diff : off_rating local - off_rating visitante
                      (positivo = local ataca mejor por 100 posesiones)
    def_rating_diff : def_rating local - def_rating visitante
                      (negativo = local PERMITE MENOS puntos = ventaja; ver nota)
    net_rating_diff : net_rating local - net_rating visitante
                      (algebraicamente = off_rating_diff - def_rating_diff)

Nota sobre def_rating_diff
--------------------------
Un def_rating_diff NEGATIVO significa que el local permite MENOS puntos por
100 posesiones = ventaja local. La convención LOCAL - VISITANTE se mantiene
mecánica (sin invertir el signo), igual que tov_rate_diff en el Grupo 1.
El modelo aprende el coeficiente negativo correcto automáticamente.

Orden de cálculo (CRÍTICO — no alterar):
    1. Self-join sobre game_id para añadir opp_{fgm,fg3m,fga,fta,oreb,dreb,tov,ftm}
       vía _shared.add_opponent_stats.
    2. Derivar POR PARTIDO: pts, opp_pts (cantidades) y poss (estimación Oliver).
    3. compute_rolling_means sobre pts, opp_pts, poss (cantidades, no ratios).
    4. Calcular ratings SOBRE los promedios rolling (ratio de medias, no media de
       ratios — mismo principio que Four Factors; ver docstring de compute_ratings).
    5. Pivotar a nivel partido y calcular diffs LOCAL - VISITANTE.

Fórmulas:
    pts      = (FGM - FG3M) x 2 + FG3M x 3 + FTM
    opp_pts  = ídem con stats del oponente
    poss ≈ 0.5 x (team_half + opp_half)
        team_half = FGA + 0.44xFTA - 1.07x(OREB/(OREB+OPP_DREB))x(FGA-FGM) + TOV
        opp_half  = OPP_FGA + 0.44xOPP_FTA
                    - 1.07x(OPP_OREB/(OPP_OREB+DREB))x(OPP_FGA-OPP_FGM) + OPP_TOV

    off_rating = 100 x pts_rolling / poss_rolling
    def_rating = 100 x opp_pts_rolling / poss_rolling
    net_rating = off_rating - def_rating
"""
from __future__ import annotations

import pandas as pd

from nba_predictor.config import ROLLING_WINDOW_GAMES
from nba_predictor.features._shared import load_stats_with_opponent
from nba_predictor.features.rolling import compute_rolling_means

# Columnas crudas del equipo necesarias para pts y poss
_STAT_COLS: list[str] = ["fgm", "fg3m", "fga", "fta", "oreb", "dreb", "tov", "ftm"]
# Columnas del oponente (añadidas vía self-join con prefijo opp_)
_OPP_STAT_COLS: list[str] = [f"opp_{c}" for c in _STAT_COLS]
# Cantidades por partido que pasan por rolling (no los ratios directamente)
_ROLLING_COLS: list[str] = ["pts", "opp_pts", "poss"]

OUTPUT_COLS: list[str] = [
    "game_id", "season", "game_date",
    "off_rating_diff", "def_rating_diff", "net_rating_diff",
]


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def build_ratings(window: int = ROLLING_WINDOW_GAMES) -> pd.DataFrame:
    """
    Carga datos del DataStore y calcula ratings rolling para todos los partidos.

    Punto de entrada para scripts/notebooks. Para tests usar compute_ratings
    con DataFrames sintéticos (sin acceso a BD).

    Returns
    -------
    DataFrame con columnas OUTPUT_COLS, ordenado por game_date.
    NaN en partidos sin historia suficiente.
    """
    stats = load_stats_with_opponent(_STAT_COLS)
    return compute_ratings(stats, window=window)


def build_team_ratings(window: int = ROLLING_WINDOW_GAMES) -> pd.DataFrame:
    """
    Ratings rolling a nivel equipo-partido (no diffs de partido).

    Útil para inspección del rango de valores y para el test de sanidad (e).
    Columnas clave: team_id, game_id, game_date, off_rating, def_rating, net_rating.
    """
    stats = load_stats_with_opponent(_STAT_COLS)
    return compute_team_rolling_ratings(stats, window=window)


def compute_ratings(
    stats: pd.DataFrame,
    window: int = ROLLING_WINDOW_GAMES,
) -> pd.DataFrame:
    """
    Calcula off_rating_diff, def_rating_diff, net_rating_diff por partido.

    Función de pura computación — testeable sin BD. El DataFrame stats debe
    tener columnas de equipo Y del oponente (ya enriquecido con self-join,
    o construido sintéticamente en tests).

    Parameters
    ----------
    stats : DataFrame con UNA FILA por (equipo, partido). Columnas obligatorias:
            team_id, game_id, game_date, season, is_home,
            fgm, fg3m, fga, fta, oreb, dreb, tov, ftm,
            opp_fgm, opp_fg3m, opp_fga, opp_fta, opp_oreb, opp_dreb, opp_tov, opp_ftm.
    window : Partidos previos requeridos para una media válida.

    Returns
    -------
    DataFrame con columnas OUTPUT_COLS. Una fila por partido.

    Por qué pts/opp_pts/poss se pasan por rolling ANTES de dividir
    ---------------------------------------------------------------
    poss varía con el ritmo del partido. Si promediáramos off_rating por
    partido, un partido rápido (100 posesiones) y uno lento (85) pesarían
    igual. Al promediar primero las cantidades (pts, poss) y dividir después,
    la ponderación es proporcional al volumen de posesiones: partidos con más
    muestra estadística pesan más.

    Ejemplo para intuición:
        Partido 1 (rápido): pts=60, poss=44  → off_rating = 136.4
        Partido 2 (lento) : pts=110, poss=66  → off_rating = 166.7
        Media de ratios    : (136.4 + 166.7) / 2         = 151.5  ← incorrecto
        Ratio de medias    : 100 x (60+110) / (44+66)    = 154.5  ← correcto
    """
    _validate_input(stats)
    if window < 1:
        raise ValueError(f"window debe ser >= 1, recibido {window}")

    rolled = compute_team_rolling_ratings(stats, window=window)
    return _to_game_differences(rolled)


def compute_team_rolling_ratings(
    stats: pd.DataFrame,
    window: int = ROLLING_WINDOW_GAMES,
) -> pd.DataFrame:
    """
    Ratings rolling a nivel equipo-partido. Una fila por (equipo, partido).

    Columnas añadidas: off_rating, def_rating, net_rating.
    NaN cuando no hay historia suficiente (mismos criterios que compute_rolling_means).

    Expuesta públicamente para test (e): verificar que off_rating ∈ [95, 125]
    sobre datos reales, donde los diffs intermedios no son directamente accesibles.
    """
    # Paso 2: cantidades por partido (pts, opp_pts, poss)
    with_quantities = _compute_game_quantities(stats)

    # Paso 3: medias móviles de las cantidades (NO de los ratios)
    rolled = compute_rolling_means(with_quantities, stat_cols=_ROLLING_COLS, window=window)

    # Paso 4: ratings sobre los promedios rolling
    rolled["off_rating"] = 100.0 * rolled["pts_rolling"] / rolled["poss_rolling"]
    rolled["def_rating"] = 100.0 * rolled["opp_pts_rolling"] / rolled["poss_rolling"]
    rolled["net_rating"] = rolled["off_rating"] - rolled["def_rating"]

    return rolled


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _compute_game_quantities(stats: pd.DataFrame) -> pd.DataFrame:
    """
    Paso 2: deriva pts, opp_pts y poss (estimación Oliver) por partido.

    Estas son CANTIDADES (no ratios) y se promedian correctamente con rolling.
    La fórmula de posesiones promedia las estimaciones del equipo y del oponente:
    cada lado estima sus propias posesiones; el promedio reduce el error.

    OREB / (OREB + OPP_DREB): fracción de rebotes ofensivos propios. Mide
    cuántos rebotes ofensivos se convierten en segundas oportunidades vs. los
    que recupera la defensa rival.
    """
    df = stats.copy()

    # Puntos desde stats crudas (validado en CHECK 2 de Fase 1)
    df["pts"] = (df["fgm"] - df["fg3m"]) * 2 + df["fg3m"] * 3 + df["ftm"]
    df["opp_pts"] = (
        (df["opp_fgm"] - df["opp_fg3m"]) * 2 + df["opp_fg3m"] * 3 + df["opp_ftm"]
    )

    # Posesiones — fórmula Dean Oliver (aproximación estándar de la liga)
    # team_half: estimación desde la perspectiva del propio equipo
    # opp_half : estimación desde la perspectiva del rival (usa dreb PROPIO)
    team_half = (
        df["fga"]
        + 0.44 * df["fta"]
        - 1.07 * (df["oreb"] / (df["oreb"] + df["opp_dreb"])) * (df["fga"] - df["fgm"])
        + df["tov"]
    )
    opp_half = (
        df["opp_fga"]
        + 0.44 * df["opp_fta"]
        - 1.07
        * (df["opp_oreb"] / (df["opp_oreb"] + df["dreb"]))
        * (df["opp_fga"] - df["opp_fgm"])
        + df["opp_tov"]
    )
    df["poss"] = 0.5 * (team_half + opp_half)

    return df


def _to_game_differences(stats_with_ratings: pd.DataFrame) -> pd.DataFrame:
    """
    Paso 5: pivota de (equipo, partido) a partido y calcula diffs LOCAL - VISITANTE.

    validate="one_to_one" garantiza que cada partido tenga exactamente un local
    y un visitante en los datos con ratings — falla ruidosamente si no es así.
    """
    keep_home = ["game_id", "season", "game_date", "off_rating", "def_rating", "net_rating"]
    keep_away = ["game_id", "off_rating", "def_rating", "net_rating"]

    home = stats_with_ratings[stats_with_ratings["is_home"] == 1][keep_home].copy()
    away = stats_with_ratings[stats_with_ratings["is_home"] == 0][keep_away].copy()

    games = home.merge(
        away, on="game_id", suffixes=("_home", "_away"), validate="one_to_one"
    )

    result = games[["game_id", "season", "game_date"]].copy()
    for rating, label in [
        ("off_rating", "off_rating_diff"),
        ("def_rating", "def_rating_diff"),
        ("net_rating", "net_rating_diff"),
    ]:
        result[label] = games[f"{rating}_home"] - games[f"{rating}_away"]

    return result.sort_values("game_date").reset_index(drop=True)


def _validate_input(stats: pd.DataFrame) -> None:
    required = (
        {"team_id", "game_id", "game_date", "season", "is_home"}
        | set(_STAT_COLS)
        | set(_OPP_STAT_COLS)
    )
    missing = required - set(stats.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {sorted(missing)}")
