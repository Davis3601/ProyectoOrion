"""
Ensamblado de la capa FEATURES — Layer 3 del pipeline NBA Predictor.

Decisiones CERRADAS (ver "Fase 2 — Decisión del ensamblado" en CLAUDE.md):

Unión 1:1 entre los 5 grupos
-------------------------------
Los 5 módulos de features producen cada uno una fila por partido. Se unen
por game_id con inner joins validados validate="one_to_one". Los juegos que
aparecen en un grupo pero no en otro (ventanas dobles del Grupo 3, etc.)
se pierden en el inner join y se reportan en el log — nunca se hacen joins
permisivos para "recuperarlos".

Regla de exclusión: AMBOS equipos ≥15 partidos previos en la temporada
------------------------------------------------------------------------
La clave es que AMBOS lados de la comparación deben tener historia estacional
suficiente. Si uno de los dos equipos está en sus primeros 15 partidos de la
temporada, sus features de medias móviles estarán dominadas por la temporada
anterior (roster potencialmente distinto), contaminando toda la fila.
Formalmente: se incluye si game_num_in_season ≥ 16 para AMBOS equipos.

game_num_in_season se computa fresco dentro de cada temporada (groupby de
team_id × season, orden cronológico, 1-indexado). Los warmup no interfieren
porque el agrupado reinicia por temporada.

Invariante: CERO NaN tras exclusiones
--------------------------------------
Tras aplicar las exclusiones (warmup + primeros 15 de cada equipo), la tabla
no debe tener NaN en ninguna feature. Las exclusiones garantizan historia
suficiente por construcción; un NaN sobreviviente es un bug, nunca un estado
aceptable. Se levanta ValueError con detalle, nunca fillna silencioso.

Columnas de features_v1 (orden exacto)
----------------------------------------
game_id, season, game_date,
efg_diff, tov_rate_diff, oreb_rate_diff, ft_rate_diff,       (Grupo 1)
off_rating_diff, def_rating_diff, net_rating_diff,            (Grupo 2)
off_rating_adj_diff, def_rating_adj_diff, net_rating_adj_diff,(Grupo 3)
rest_diff, home_b2b, away_b2b, neutral_site,                  (Grupo 4)
availability_diff,                                            (Grupo 5)
home_won                                                      (target)
"""
from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from nba_predictor.config import TRAINING_SEASONS
from nba_predictor.features.availability import build_availability
from nba_predictor.features.context import build_context
from nba_predictor.features.four_factors import build_four_factors
from nba_predictor.features.opponent_adjust import build_adjusted_ratings
from nba_predictor.features.ratings import build_ratings
from nba_predictor.storage import get_datastore

# Orden exacto de columnas en features_v1 — no modificar sin actualizar CLAUDE.md
FEATURES_V1_COLS: list[str] = [
    "game_id", "season", "game_date",
    # Grupo 1 — Four Factors
    "efg_diff", "tov_rate_diff", "oreb_rate_diff", "ft_rate_diff",
    # Grupo 2 — Ratings crudos
    "off_rating_diff", "def_rating_diff", "net_rating_diff",
    # Grupo 3 — Ratings ajustados por oponente
    "off_rating_adj_diff", "def_rating_adj_diff", "net_rating_adj_diff",
    # Grupo 4 — Contexto
    "rest_diff", "home_b2b", "away_b2b", "neutral_site",
    # Grupo 5 — Disponibilidad
    "availability_diff",
    # Target
    "home_won",
]

# Columnas de features (sin identificadores ni target)
_FEATURE_COLS: list[str] = [
    c for c in FEATURES_V1_COLS if c not in ("game_id", "season", "game_date", "home_won")
]

# Columnas de feature por grupo para el join (no duplicar season/game_date)
_G1 = ["efg_diff", "tov_rate_diff", "oreb_rate_diff", "ft_rate_diff"]
_G2 = ["off_rating_diff", "def_rating_diff", "net_rating_diff"]
_G3 = ["off_rating_adj_diff", "def_rating_adj_diff", "net_rating_adj_diff"]
_G4 = ["rest_diff", "home_b2b", "away_b2b", "neutral_site"]
_G5 = ["availability_diff"]


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def assemble_features(log: Callable[[str], None] | None = None) -> pd.DataFrame:
    """
    Punto de entrada completo: calcula todos los grupos y ensambla.

    Llama a cada build_* (puede tardar ~30-60 s con 14 429 partidos),
    luego delega a assemble_from_dfs para la lógica de unión y exclusión.

    Parameters
    ----------
    log : Callable opcional para recibir mensajes de progreso/conteos.

    Returns
    -------
    DataFrame con columnas FEATURES_V1_COLS, una fila por partido de
    entrenamiento, ordenado por game_date.
    """
    _log = log or (lambda _: None)

    _log("Grupo 1 — four_factors...")
    ff = build_four_factors()
    _log(f"  {len(ff)} filas")

    _log("Grupo 2 — ratings...")
    rt = build_ratings()
    _log(f"  {len(rt)} filas")

    _log("Grupo 3 — opponent_adjust...")
    oa = build_adjusted_ratings()
    _log(f"  {len(oa)} filas")

    _log("Grupo 4 — context...")
    ct = build_context()
    _log(f"  {len(ct)} filas")

    _log("Grupo 5 — availability...")
    av = build_availability()
    _log(f"  {len(av)} filas")

    games = get_datastore().load_games()
    return assemble_from_dfs(ff, rt, oa, ct, av, games, log=_log)


def assemble_from_dfs(
    four_factors: pd.DataFrame,
    ratings: pd.DataFrame,
    opponent_adjust: pd.DataFrame,
    context: pd.DataFrame,
    availability: pd.DataFrame,
    games: pd.DataFrame,
    log: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    """
    Lógica de ensamblado — testeable con DataFrames sintéticos.

    Parameters
    ----------
    four_factors, ratings, opponent_adjust, context, availability :
        Salidas de los módulos de features. Columnas según OUTPUT_COLS de
        cada módulo (game_id, season, game_date + features propias).
    games : DataFrame de la tabla `games` con game_id, season, game_date,
            home_team_id, away_team_id, home_won.
    log   : Callable para mensajes de progreso (None = silencio).

    Returns
    -------
    DataFrame con FEATURES_V1_COLS, filtrado a TRAINING_SEASONS y con la
    regla de ambos equipos ≥15 partidos previos aplicada.
    """
    _log = log or (lambda _: None)

    # -----------------------------------------------------------------
    # 1. Uniones secuenciales con inner join validado 1:1
    # -----------------------------------------------------------------
    df = four_factors[["game_id", "season", "game_date"] + _G1].copy()
    n = len(df)
    _log(f"Inicio (Grupo 1): {n} filas")

    df = df.merge(
        ratings[["game_id"] + _G2], on="game_id", how="inner", validate="one_to_one"
    )
    _log(f"Tras join Grupo 2: {len(df)} filas (perdidas en join: {n - len(df)})")
    n = len(df)

    df = df.merge(
        opponent_adjust[["game_id"] + _G3], on="game_id", how="inner", validate="one_to_one"
    )
    _log(f"Tras join Grupo 3: {len(df)} filas (perdidas en join: {n - len(df)})")
    n = len(df)

    df = df.merge(
        context[["game_id"] + _G4], on="game_id", how="inner", validate="one_to_one"
    )
    _log(f"Tras join Grupo 4: {len(df)} filas (perdidas en join: {n - len(df)})")
    n = len(df)

    df = df.merge(
        availability[["game_id"] + _G5], on="game_id", how="inner", validate="one_to_one"
    )
    _log(f"Tras join Grupo 5: {len(df)} filas (perdidas en join: {n - len(df)})")
    _log(f"Unión completa: {len(df)} filas")

    # -----------------------------------------------------------------
    # 2. Añadir home_won y team IDs desde la tabla games
    # -----------------------------------------------------------------
    games_meta = games[["game_id", "home_team_id", "away_team_id", "home_won"]].copy()
    df = df.merge(games_meta, on="game_id", how="inner", validate="one_to_one")

    # -----------------------------------------------------------------
    # 3. Calcular game_num_in_season para cada equipo
    # -----------------------------------------------------------------
    team_game_nums = _compute_team_game_nums(games)

    # Número de partido del equipo LOCAL en su temporada
    df = df.merge(
        team_game_nums.rename(
            columns={"team_id": "home_team_id", "game_num_in_season": "home_game_num"}
        ),
        on=["game_id", "home_team_id"],
        how="inner",
        validate="one_to_one",
    )
    # Número de partido del equipo VISITANTE en su temporada
    df = df.merge(
        team_game_nums.rename(
            columns={"team_id": "away_team_id", "game_num_in_season": "away_game_num"}
        ),
        on=["game_id", "away_team_id"],
        how="inner",
        validate="one_to_one",
    )

    # -----------------------------------------------------------------
    # 4. Filtrar a TRAINING_SEASONS (excluir warmup 2014-15, 2015-16)
    # -----------------------------------------------------------------
    n_before = len(df)
    df = df[df["season"].isin(TRAINING_SEASONS)].copy()
    _log(
        f"Tras excluir warmup ({n_before - len(df)} filas): {len(df)} filas"
    )

    # Desglose por temporada antes de aplicar la regla de primeros 15
    for season in sorted(df["season"].unique()):
        _log(f"  {season}: {(df['season'] == season).sum()} filas (pre-exclusión)")

    # -----------------------------------------------------------------
    # 5. Regla de exclusión: AMBOS equipos ≥15 partidos previos en la temporada
    #    game_num_in_season ≥ 16 ↔ al menos 15 partidos anteriores en esa temporada
    # -----------------------------------------------------------------
    n_before = len(df)
    both_ready = (df["home_game_num"] >= 16) & (df["away_game_num"] >= 16)
    df = df[both_ready].copy()
    _log(
        f"Tras excluir primeros 15 partidos (regla de ambos equipos): "
        f"{len(df)} filas (perdidas: {n_before - len(df)})"
    )

    # -----------------------------------------------------------------
    # 6. Invariante: CERO NaN en las features tras exclusiones
    # -----------------------------------------------------------------
    _assert_no_nan(df, _FEATURE_COLS)

    # -----------------------------------------------------------------
    # 7. Selección y orden exacto de columnas (features_v1)
    # -----------------------------------------------------------------
    return (
        df[FEATURES_V1_COLS]
        .sort_values("game_date")
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _compute_team_game_nums(games: pd.DataFrame) -> pd.DataFrame:
    """
    Número de partido de cada equipo dentro de su temporada (1-indexado).

    game_num_in_season = 1 → primer partido del equipo en esa temporada.
    game_num_in_season ≥ 16 → al menos 15 partidos previos (regla de inclusión).

    Computado desde la tabla games completa (todas las temporadas), con
    groupby(team_id, season) para reset fresco por temporada.
    """
    home = (
        games[["game_id", "season", "game_date", "home_team_id"]]
        .rename(columns={"home_team_id": "team_id"})
    )
    away = (
        games[["game_id", "season", "game_date", "away_team_id"]]
        .rename(columns={"away_team_id": "team_id"})
    )
    tg = (
        pd.concat([home, away], ignore_index=True)
        .sort_values(["team_id", "season", "game_date"], kind="stable")
    )
    tg["game_num_in_season"] = tg.groupby(["team_id", "season"]).cumcount() + 1
    return tg[["game_id", "team_id", "game_num_in_season"]]


def _assert_no_nan(df: pd.DataFrame, feature_cols: list[str]) -> None:
    """
    Verifica el invariante de cero NaN en las features.

    Si sobrevive algún NaN tras las exclusiones, levanta ValueError con
    detalle diagnóstico: columnas afectadas, número de filas y ejemplos.
    Nunca llama a fillna — un NaN es un bug, no un estado a tolerar.
    """
    nan_mask = df[feature_cols].isna().any(axis=1)
    if not nan_mask.any():
        return

    nan_by_col = df[feature_cols].isna().sum()
    nan_cols = nan_by_col[nan_by_col > 0].to_dict()
    example_ids = df.loc[nan_mask, "game_id"].head(5).tolist()

    raise ValueError(
        f"Invariante VIOLADO: {nan_mask.sum()} filas con NaN tras las exclusiones.\n"
        f"Columnas afectadas (NaN count): {nan_cols}\n"
        f"game_ids de ejemplo: {example_ids}\n"
        "Un NaN es un bug — investigar antes de continuar. NUNCA usar fillna."
    )
