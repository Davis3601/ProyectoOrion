"""
Utilidades compartidas para feature engineering (Fase 2).

Contiene el self-join reutilizable por todos los grupos de features
(Four Factors, Ratings, ajuste por oponente, etc.).
"""
from __future__ import annotations

import pandas as pd

from nba_predictor.features.rolling import load_team_stats_with_dates


def load_stats_with_opponent(opp_cols: list[str]) -> pd.DataFrame:
    """
    Carga team_game_stats con game_date y añade stats del oponente.

    Wrapper de conveniencia sobre load_team_stats_with_dates + add_opponent_stats.

    Parameters
    ----------
    opp_cols : Columnas del equipo a extraer del oponente (con prefijo 'opp_').
    """
    stats = load_team_stats_with_dates()
    return add_opponent_stats(stats, opp_cols)


def add_opponent_stats(stats: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """
    Self-join sobre game_id para añadir stats del oponente con prefijo 'opp_'.

    Cada game_id tiene 2 filas (local + visitante). El join produce 4 pares;
    los auto-pares (mismo team_id) se descartan, quedando exactamente 2 pares
    correctos (cada equipo obtiene las stats del rival).

    Parameters
    ----------
    stats : DataFrame con al menos game_id, team_id y las columnas en cols.
    cols  : Columnas de stats del equipo a replicar como 'opp_{col}'.

    Returns
    -------
    DataFrame original con columnas 'opp_{col}' añadidas. 2 filas por game_id.

    Raises
    ------
    ValueError si algún game_id no tiene exactamente 2 filas tras el join.
    """
    opp = stats[["game_id", "team_id"] + cols].rename(
        columns={"team_id": "opp_team_id", **{c: f"opp_{c}" for c in cols}}
    )
    merged = stats.merge(opp, on="game_id", how="inner")
    result = (
        merged[merged["team_id"] != merged["opp_team_id"]]
        .drop(columns=["opp_team_id"])
        .copy()
    )

    counts = result.groupby("game_id").size()
    if (counts != 2).any():
        bad = counts[counts != 2]
        raise ValueError(
            f"Se esperaban exactamente 2 filas por game_id tras el self-join. "
            f"Anomalías:\n{bad.head().to_string()}"
        )

    return result.sort_values(["team_id", "game_date"], kind="stable").reset_index(drop=True)
