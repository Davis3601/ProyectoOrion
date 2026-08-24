"""
Grupo 5 — Disponibilidad de jugadores (Fase 2).

Salida: availability_diff = disponibilidad_local − disponibilidad_visitante.

Decisiones CERRADAS (ver "Fase 2 — Decisión del Grupo 5" en CLAUDE.md):

"Disponible esta noche" = Interpretación B (tiene fila en player_game_stats).
    La interpretación A (minutes > 0 del partido actual) es LEAKAGE: si un
    suplente profundo entra o no depende del marcador del propio partido que se
    predice. La fila en el box score refleja la lista de activos pre-tip-off:
    información publicada por la NBA antes del tip-off. Legal.

Valor del jugador = minutos rolling.
    Media móvil de sus minutos en los últimos N partidos JUGADOS (minutes > 0),
    con shift(1) — el partido actual nunca entra en su propio valor. Los
    DNP-banca (minutes=NULL) no cuentan como partidos jugados para el rolling.

Fórmula:
    disponibilidad = Σ minutes_rolling de jugadores activados esta noche
                     ────────────────────────────────────────────────────
                     Σ minutes_rolling de la ROTACIÓN RECIENTE del equipo

    Rotación reciente = jugadores que aparecieron (tienen fila) para ese equipo
    en los últimos N partidos del equipo, anteriores al actual.

    Si denominador = 0 (sin historia previa) → disponibilidad = NaN.

Convención para NaN en minutes_rolling
---------------------------------------
Un jugador sin ningún partido jugado previo (debutante o sin historia en el
dataset) tiene minutes_rolling = NaN. Contribuye 0 al numerador y al
denominador. Justificación: un jugador sin historial no aporta información
de fuerza; 0 es la elección conservadora y evita dividir por cantidades
inventadas.

Traspasos
---------
El rolling de minutos es por player_id, no por equipo. La historia del jugador
viaja con él al ser traspasado. Un recién llegado activado esta noche entra al
NUMERADOR con sus minutos rolling de su equipo anterior; no entra al DENOMINADOR
hasta que su aparición para este equipo quede dentro de la ventana del equipo.

Forward-fill del rolling
------------------------
Después de calcular minutes_rolling sobre los partidos jugados, se propaga
hacia adelante (ffill) dentro de cada jugador para que las filas DNP también
tengan el valor correcto. Sin ffill, un jugador DNP no podría contribuir al
denominador de partidos futuros al recuperarse de una lesión. El ffill no
viola el anti-leakage porque solo propaga valores de partidos PASADOS.

Denominador vectorizado
-----------------------
Para cada equipo, se construye una matriz (rank × player_id) con los valores
de minutes_rolling por aparición. La secuencia shift(1) → ffill(limit=N-1)
da, para cada rango R, el valor más reciente del jugador en [R-N, R-1]:
- shift(1): el partido G no contribuye a su propio denominador.
- ffill(limit=N-1): el valor del jugador se propaga hasta N rangos hacia
  adelante. Un jugador que no apareció en los N rangos anteriores queda en 0.

Anti-leakage
------------
shift(1) dentro de groupby(player_id) garantiza que el partido actual nunca
entra en su propio rolling. El denominador también usa shift(1) a nivel de
rank del equipo (via ffill). Mismo patrón que todos los grupos anteriores.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from nba_predictor.config import ROLLING_WINDOW_GAMES
from nba_predictor.storage import get_datastore

OUTPUT_COLS: list[str] = ["game_id", "season", "game_date", "availability_diff"]
_REQUIRED_COLS: frozenset[str] = frozenset(
    {"game_id", "player_id", "team_id", "is_home", "game_date", "minutes"}
)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def build_availability(window: int = ROLLING_WINDOW_GAMES) -> pd.DataFrame:
    """
    Carga datos del DataStore y calcula availability_diff para todos los partidos.

    Punto de entrada para scripts/notebooks.
    """
    ds = get_datastore()
    games = ds.load_games()[["game_id", "season", "game_date"]]
    player_stats = ds.load_player_game_stats()
    stats = player_stats.merge(games, on="game_id", validate="many_to_one")
    return compute_availability(stats, window=window, games_meta=games)


def compute_availability(
    player_stats: pd.DataFrame,
    window: int = ROLLING_WINDOW_GAMES,
    games_meta: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Calcula availability_diff a partir de player_game_stats con game_date.

    Función de pura computación — testeable sin BD. Acepta DataFrames
    sintéticos para tests unitarios.

    Parameters
    ----------
    player_stats : Una fila por (player_id, game_id). Columnas mínimas:
                   game_id, player_id, team_id, is_home, game_date, minutes.
                   minutes = NaN/None → DNP (activado pero no jugó).
    window       : Ventana de partidos jugados para el rolling de minutos.
    games_meta   : DataFrame opcional con (game_id, season, game_date) para
                   enriquecer la salida. Si None se infiere de player_stats.

    Returns
    -------
    DataFrame con OUTPUT_COLS, ordenado por game_date. availability_diff = NaN
    cuando no hay historia suficiente (primeros partidos de cada equipo).
    """
    _validate_input(player_stats, window)

    # Pasos 1-3: rolling de minutos por jugador (shift anti-leakage + ffill DNP)
    stats = _add_player_minutes_rolling(player_stats, window)

    # Paso 4: numerador y denominador por (game_id, team_id)
    numerator = _compute_numerator(stats)
    denominator = _compute_denominator(stats, window)

    # Paso 5: disponibilidad = num / denom (NaN si denom == 0)
    team_avail = numerator.merge(denominator, on=["game_id", "team_id"])
    team_avail["availability"] = np.where(
        team_avail["denominator"] > 0,
        team_avail["numerator"] / team_avail["denominator"],
        np.nan,
    )

    # Paso 6: diff LOCAL − VISITANTE
    return _to_game_differences(team_avail, player_stats, games_meta)


def compute_team_availability(
    player_stats: pd.DataFrame,
    window: int = ROLLING_WINDOW_GAMES,
) -> pd.DataFrame:
    """
    Disponibilidad a nivel (game_id, team_id). Expuesta para tests.

    Columnas: game_id, team_id, numerator, denominator, availability.
    """
    _validate_input(player_stats, window)
    stats = _add_player_minutes_rolling(player_stats, window)
    numerator = _compute_numerator(stats)
    denominator = _compute_denominator(stats, window)
    result = numerator.merge(denominator, on=["game_id", "team_id"])
    result["availability"] = np.where(
        result["denominator"] > 0,
        result["numerator"] / result["denominator"],
        np.nan,
    )
    return result


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _add_player_minutes_rolling(
    player_stats: pd.DataFrame,
    window: int,
) -> pd.DataFrame:
    """
    Añade la columna 'minutes_rolling' a todas las filas de player_stats.

    El rolling se calcula SOLO sobre partidos jugados (minutes > 0), con
    shift(1) anti-leakage. Luego se propaga (ffill) a las filas DNP para
    que las ausencias no interrumpan la historia del jugador.

    Jugador sin partidos jugados previos → minutes_rolling = NaN.
    """
    played = (
        player_stats[player_stats["minutes"].notna() & (player_stats["minutes"] > 0)]
        .sort_values(["player_id", "game_date"], kind="stable")
        .copy()
    )
    played["minutes_rolling"] = played.groupby("player_id")["minutes"].transform(
        lambda s: s.shift(1).rolling(window, min_periods=1).mean()
    )

    # Merge de vuelta a TODAS las filas: DNP reciben NaN (no aparecen en played)
    stats = player_stats.merge(
        played[["game_id", "player_id", "minutes_rolling"]],
        on=["game_id", "player_id"],
        how="left",
    )

    # ffill per jugador: DNP conservan el valor rolling de su último partido jugado
    stats = stats.sort_values(["player_id", "game_date"], kind="stable")
    stats["minutes_rolling"] = stats.groupby("player_id")["minutes_rolling"].transform(
        "ffill"
    )
    return stats


def _compute_numerator(stats: pd.DataFrame) -> pd.DataFrame:
    """
    Numerador por (game_id, team_id): suma de minutes_rolling de activados.
    NaN → 0 (debutante sin historia no aporta minutos esperados).
    """
    return (
        stats.assign(contrib=stats["minutes_rolling"].fillna(0))
        .groupby(["game_id", "team_id"])["contrib"]
        .sum()
        .reset_index(name="numerator")
    )


def _compute_denominator(stats: pd.DataFrame, window: int) -> pd.DataFrame:
    """
    Denominador por (game_id, team_id): minutos rolling de la rotación reciente.

    Para cada equipo, construye una matriz (rank × player_id) y aplica:
        shift(1) → ffill(limit=window-1) → fillna(0) → sum(axis=1)

    shift(1) garantiza anti-leakage (el partido G no se cuenta a sí mismo).
    ffill(limit=N-1) propaga el último valor del jugador hasta N-1 rangos
    más, de modo que para el rango R el denominador incluye a todo jugador
    con aparición en [R-N, R-1] y usa su minutes_rolling más reciente ahí.
    Un jugador fuera de la ventana queda en 0 (fillna).
    """
    # Asignar team_game_rank: índice cronológico del partido dentro del equipo
    team_games = (
        stats[["team_id", "game_id", "game_date"]]
        .drop_duplicates()
        .sort_values(["team_id", "game_date"], kind="stable")
    )
    team_games["team_game_rank"] = team_games.groupby("team_id").cumcount()
    stats_r = stats.merge(team_games[["game_id", "team_id", "team_game_rank"]], on=["game_id", "team_id"])

    denom_rows: list[dict] = []
    for team_id, tg in stats_r.groupby("team_id"):
        pivot = tg.pivot_table(
            index="team_game_rank",
            columns="player_id",
            values="minutes_rolling",
            aggfunc="last",
        )
        # pivot_table silently drops rows where ALL values are NaN (rank 0 of
        # a team's history: first game, every player has minutes_rolling=NaN).
        # Reindex to restore them — they contribute 0 to the denominator.
        expected_ranks = sorted(tg["team_game_rank"].unique())
        pivot = pivot.reindex(expected_ranks)

        shifted = pivot.shift(1)
        # ffill(limit=0) is invalid in pandas; handle window=1 separately.
        if window > 1:
            shifted = shifted.ffill(limit=window - 1)
        denom_matrix = shifted.fillna(0)
        denom_series = denom_matrix.sum(axis=1)

        rank_to_game = (
            tg[["team_game_rank", "game_id"]]
            .drop_duplicates()
            .set_index("team_game_rank")["game_id"]
        )
        for rank, denom_val in denom_series.items():
            denom_rows.append(
                {"game_id": rank_to_game[rank], "team_id": team_id, "denominator": denom_val}
            )

    return pd.DataFrame(denom_rows)


def _to_game_differences(
    team_avail: pd.DataFrame,
    player_stats: pd.DataFrame,
    games_meta: pd.DataFrame | None,
) -> pd.DataFrame:
    """Pivota de (team, game) a game y calcula diff LOCAL − VISITANTE."""
    home_games = (
        player_stats[player_stats["is_home"] == 1][["game_id", "team_id"]].drop_duplicates()
    )
    away_games = (
        player_stats[player_stats["is_home"] == 0][["game_id", "team_id"]].drop_duplicates()
    )
    home_avail = (
        team_avail.merge(home_games, on=["game_id", "team_id"])[["game_id", "availability"]]
        .rename(columns={"availability": "home_availability"})
    )
    away_avail = (
        team_avail.merge(away_games, on=["game_id", "team_id"])[["game_id", "availability"]]
        .rename(columns={"availability": "away_availability"})
    )
    result = home_avail.merge(away_avail, on="game_id", validate="one_to_one")
    result["availability_diff"] = result["home_availability"] - result["away_availability"]

    # Metadatos de partido (season, game_date)
    if games_meta is not None:
        meta = games_meta[["game_id", "season", "game_date"]].drop_duplicates("game_id")
    elif "season" in player_stats.columns:
        meta = player_stats[["game_id", "season", "game_date"]].drop_duplicates("game_id")
    else:
        meta = player_stats[["game_id", "game_date"]].drop_duplicates("game_id").assign(season="unknown")

    result = result.merge(meta, on="game_id", validate="many_to_one")
    return result[OUTPUT_COLS].sort_values("game_date").reset_index(drop=True)


def _validate_input(df: pd.DataFrame, window: int) -> None:
    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {sorted(missing)}")
    if window < 1:
        raise ValueError(f"window debe ser >= 1, recibido {window}")
