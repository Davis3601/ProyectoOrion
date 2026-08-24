"""
Maquinaria vectorizada de medias móviles anti-leakage.

Todos los grupos de features (Four Factors, ratings, etc.) llaman a
compute_rolling_means(). La garantía anti-leakage vive aquí y no se repite
en ningún otro módulo.

Patrón canónico:
    ordenar por (team_id, game_date)
    → groupby(team_id)
    → shift(1)          ← garantía anti-leakage
    → rolling(N).mean()

Por qué shift(1) va ANTES del rolling
--------------------------------------
pandas' rolling(N).mean() en la posición i incluye la fila i misma. Sin
shift(1), la media del partido i contiene la estadística del propio partido i —
eso es la fuga temporal silenciosa clásica que infla artificialmente las
métricas de backtesting. El shift(1) desplaza la serie una posición hacia
adelante dentro de cada grupo de equipo, de modo que la ventana en i solo
cubre las filas [i-N, i-1], nunca la fila i.
"""
from __future__ import annotations

import pandas as pd

from nba_predictor.config import ROLLING_WINDOW_GAMES
from nba_predictor.storage import get_datastore


def load_team_stats_with_dates() -> pd.DataFrame:
    """
    Carga team_game_stats unido con game_date y season del DataStore.

    Devuelve un DataFrame con una fila por (equipo, partido) ordenado por
    game_date. Columnas: game_id, team_id, season, game_date, is_home y todas
    las stats crudas (fgm, fga, fg3m, fg3a, ftm, fta, oreb, dreb, ast, stl,
    blk, tov, pf, plus_minus).

    El JOIN many_to_one valida que cada game_id tenga exactamente un partido
    en la tabla games; si falla, hay inconsistencia entre capas (fallar ruidoso).
    """
    ds = get_datastore()
    games = ds.load_games()[["game_id", "season", "game_date"]]
    stats = ds.load_team_game_stats()
    return (
        stats.merge(games, on="game_id", validate="many_to_one")
        .sort_values(["team_id", "game_date"], kind="stable")
        .reset_index(drop=True)
    )


def compute_rolling_means(
    df: pd.DataFrame,
    stat_cols: list[str],
    window: int = ROLLING_WINDOW_GAMES,
) -> pd.DataFrame:
    """
    Añade columnas de media móvil para cada columna en stat_cols.

    Para cada fila (equipo, partido), la media móvil cubre los `window`
    partidos inmediatamente anteriores de ESE equipo. La ventana CRUZA límites
    de temporada — el groupby es solo por team_id, nunca por season (ver CLAUDE.md
    § "La ventana móvil CRUZA temporadas").

    Garantía anti-leakage
    ---------------------
    Se aplica shift(1) DENTRO del groupby ANTES de rolling(N).mean(). Sin
    shift(1), rolling(N).mean() en la posición i incluye la fila i misma —
    esa es la fuga silenciosa clásica. Ver módulo docstring para más detalle.

    Parameters
    ----------
    df : DataFrame con columnas {team_id, game_date} ∪ stat_cols.
         Típicamente el resultado de load_team_stats_with_dates().
    stat_cols : Columnas sobre las que calcular la media móvil.
    window : Número de partidos previos requeridos para una media válida.
             Con menos partidos previos → NaN (nunca imputado). Por defecto
             ROLLING_WINDOW_GAMES de config.

    Returns
    -------
    DataFrame en el mismo orden de filas que la entrada, con columnas nuevas
    ``{col}_rolling`` para cada col en stat_cols. Las columnas originales no
    se modifican.

    Raises
    ------
    ValueError si faltan columnas requeridas o window < 1.
    """
    missing = ({"team_id", "game_date"} | set(stat_cols)) - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {sorted(missing)}")
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")

    # Copia ordenada para que el shift+rolling opere en orden cronológico
    # por equipo. sort_index() al final restaura el orden original de filas.
    df_sorted = df.sort_values(["team_id", "game_date"], kind="stable").copy()

    for col in stat_cols:
        # shift(1) luego rolling: el núcleo del patrón anti-leakage.
        # min_periods=window exige ventana completa; con menos historia → NaN.
        df_sorted[f"{col}_rolling"] = (
            df_sorted.groupby("team_id")[col]
            .transform(lambda s: s.shift(1).rolling(window, min_periods=window).mean())
        )

    return df_sorted.sort_index()
