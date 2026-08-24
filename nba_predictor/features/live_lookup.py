"""
Lookup puntual de features para un partido en vivo — Fase 5a (Decisión 3).

Para un partido (home, away, fecha, ausencias), calcula las 11 features de
OFFICIAL_LOGISTIC_COLS con EXACTAMENTE la misma lógica que la pipeline
vectorizada. Esta equivalencia es el criterio de cierre de toda la Fase 5a
(test en tests/test_live_equivalence.py).

Estrategia: dummy-row
---------------------
La pipeline vectorizada usa shift(1).rolling(N) sobre la serie completa del
equipo. El rolling en la posición i cubre las filas [i-N, i-1] — es decir,
para el partido ACTUAL en posición i los valores de ENTRADA son los N partidos
ANTERIORES (nunca el propio). Para el partido EN VIVO (que no existe en la BD):

    1. Cargar histórico: game_date < target_date (nunca el día del partido).
    2. Para cada equipo, añadir una fila ficticia ("dummy") en target_date con
       stats NaN.
    3. Aplicar compute_rolling_means: el dummy recibe el rolling correcto
       (cubre los N partidos reales anteriores) porque shift(1) mueve las stats
       del dummy a la posición i+1 (que no nos importa) y el rolling de la
       posición del dummy cubre [i-N, i-1] de los datos reales.
    4. Extraer el rolling del dummy → features de entrada para el modelo.

Anti-leakage garantizado: el filtro game_date < target_date es estricto.

Disponibilidad (v0 manual, Decisión 2)
---------------------------------------
La disponibilidad viva acepta dos modos:

    absent_ids : Lista de player_id ausentes declarados (v0: usuario los lee del
                 injury report y los pasa). Numerador = rotación − ausentes.

    activated_ids : Lista de player_id que SÍ jugarán (modo exacto para tests).
                    Numerador = sum(minutes_rolling de activados).

    Si ninguno, ausencias vacías = rotación completa activada.

El modo `activated_ids` reproduce EXACTAMENTE el numerador de la pipeline
vectorizada (jugadores con fila en player_game_stats) y permite el test de
equivalencia de Fase 5a.

Deuda: players_ids nuevos (no en rotación de los últimos N partidos) contribuyen
al numerador histórico pero son invisibles en el modo absent (v0). Documentado
como PARCIALMENTE ABIERTO en CLAUDE.md.
"""
from __future__ import annotations

import logging
from collections.abc import Collection
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from nba_predictor.config import REST_DAYS_CAP, ROLLING_WINDOW_GAMES
from nba_predictor.features.availability import _add_player_minutes_rolling
from nba_predictor.features.rolling import compute_rolling_means
from nba_predictor.features._shared import add_opponent_stats
from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS

_log = logging.getLogger(__name__)

# Columnas crudas de four factors (mismo orden que four_factors.py)
_FF_RAW_COLS: list[str] = ["fgm", "fg3m", "fga", "fta", "oreb", "tov", "opp_dreb"]

# Columnas de stats del equipo para ratings (mismo orden que ratings.py)
_RAT_STAT_COLS: list[str] = ["fgm", "fg3m", "fga", "fta", "oreb", "dreb", "tov", "ftm"]
_RAT_ROLLING_COLS: list[str] = ["pts", "opp_pts", "poss"]

# game_id especial para la fila dummy del partido en vivo
_LIVE_GAME_PREFIX = "LIVE_"


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def compute_live_features(
    home_team_id: int,
    away_team_id: int,
    game_date: date,
    absent_home_ids: Collection[int] = (),
    absent_away_ids: Collection[int] = (),
    activated_home_ids: Collection[int] | None = None,
    activated_away_ids: Collection[int] | None = None,
    played_home_ids: Collection[int] | None = None,
    played_away_ids: Collection[int] | None = None,
    neutral_site: int = 0,
    window: int = ROLLING_WINDOW_GAMES,
) -> dict[str, Any]:
    """
    Calcula las 11 features de OFFICIAL_LOGISTIC_COLS para UN partido.

    Carga todo el histórico con game_date < game_date del DataStore, añade
    filas dummy y reusa las mismas funciones que la pipeline vectorizada.
    Equivalencia exacta garantizada por tests/test_live_equivalence.py.

    Parameters
    ----------
    home_team_id      : team_id del equipo local.
    away_team_id      : team_id del equipo visitante.
    game_date         : Fecha del partido. Solo se usan datos ANTERIORES.
    absent_home_ids   : player_ids ausentes del equipo local (v0: manual).
    absent_away_ids   : player_ids ausentes del equipo visitante (v0: manual).
    activated_home_ids: player_ids activos del local (modo exacto para tests).
                        Si se pasan, sobreescribe absent_home_ids.
    activated_away_ids: player_ids activos del visitante (modo exacto para tests).
    played_home_ids   : player_ids que jugaron minutos reales del local (subconjunto
                        de activated_home_ids). Si None, trata todos los activados
                        como jugadores (mejor aproximación para predicción en vivo).
    played_away_ids   : ídem para el visitante.
    neutral_site      : 0/1; 1 para burbuja COVID u otras sedes neutrales.
    window            : Ventana rolling (default ROLLING_WINDOW_GAMES = 10).

    Returns
    -------
    dict con claves == OFFICIAL_LOGISTIC_COLS (11 features).

    Raises
    ------
    ValueError si no hay historia suficiente para algún equipo.
    """
    from nba_predictor.storage import get_datastore

    ds = get_datastore()
    target = pd.Timestamp(game_date)

    # ── 1. Cargar histórico estrictamente anterior al partido ──
    all_games = ds.load_games()
    # game_date puede ser date o str; normalizar a Timestamp para comparar
    all_games["game_date"] = pd.to_datetime(all_games["game_date"])
    hist_games = all_games[all_games["game_date"] < target].copy()

    hist_game_ids = set(hist_games["game_id"])

    raw_tgs = ds.load_team_game_stats()
    hist_tgs = raw_tgs[raw_tgs["game_id"].isin(hist_game_ids)].copy()
    # Enriquecer con game_date y season para compute_rolling_means
    hist_tgs = hist_tgs.merge(
        hist_games[["game_id", "game_date", "season"]], on="game_id", validate="many_to_one"
    )

    # ── 2. Four Factors ──
    ff = _compute_four_factors_live(hist_tgs, home_team_id, away_team_id, target, window)

    # ── 3. Ratings ──
    rat = _compute_ratings_live(hist_tgs, home_team_id, away_team_id, target, window)

    # ── 4. Context (rest, b2b, neutral_site) ──
    ctx = _compute_context_live(hist_games, home_team_id, away_team_id, game_date, neutral_site)

    # ── 5. Availability ──
    raw_pgs = ds.load_player_game_stats()
    hist_pgs = raw_pgs[raw_pgs["game_id"].isin(hist_game_ids)].copy()
    hist_pgs = hist_pgs.merge(
        hist_games[["game_id", "game_date"]], on="game_id", validate="many_to_one"
    )
    avail_diff = _compute_availability_live(
        hist_pgs=hist_pgs,
        hist_games=hist_games,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        absent_home_ids=set(absent_home_ids),
        absent_away_ids=set(absent_away_ids),
        activated_home_ids=set(activated_home_ids) if activated_home_ids is not None else None,
        activated_away_ids=set(activated_away_ids) if activated_away_ids is not None else None,
        played_home_ids=set(played_home_ids) if played_home_ids is not None else None,
        played_away_ids=set(played_away_ids) if played_away_ids is not None else None,
        window=window,
    )

    result = {**ff, **rat, **ctx, "availability_diff": avail_diff}

    # Validar que todas las 11 features están presentes
    missing = [c for c in OFFICIAL_LOGISTIC_COLS if c not in result]
    if missing:
        raise RuntimeError(f"Features faltantes: {missing}")

    return {col: result[col] for col in OFFICIAL_LOGISTIC_COLS}


# ---------------------------------------------------------------------------
# Four Factors
# ---------------------------------------------------------------------------

def _compute_four_factors_live(
    hist_tgs: pd.DataFrame,
    home_team_id: int,
    away_team_id: int,
    target: pd.Timestamp,
    window: int,
) -> dict[str, float]:
    """Replica el paso 1-4 de four_factors.py con filas dummy en target."""
    # 1. Añadir opp_dreb al histórico completo (self-join sobre game_id)
    stats_with_opp = add_opponent_stats(hist_tgs, ["dreb"])

    # 2. Filtrar a los dos equipos del partido
    both = stats_with_opp[stats_with_opp["team_id"].isin([home_team_id, away_team_id])].copy()

    # 3. Añadir filas dummy para recibir el rolling del live game
    dummy_cols = _FF_RAW_COLS + ["team_id", "game_id", "game_date", "season", "is_home"]
    dummy_rows = []
    for tid, is_home in [(home_team_id, 1), (away_team_id, 0)]:
        row: dict[str, Any] = {c: np.nan for c in _FF_RAW_COLS}
        row.update({
            "team_id": tid,
            "game_id": f"{_LIVE_GAME_PREFIX}{tid}",
            "game_date": target,
            "season": "LIVE",
            "is_home": is_home,
        })
        dummy_rows.append(row)
    extended = pd.concat(
        [both[dummy_cols], pd.DataFrame(dummy_rows)], ignore_index=True
    )

    # 4. Rolling sobre stats crudas (el dummy recibe el rolling correcto)
    rolled = compute_rolling_means(extended, stat_cols=_FF_RAW_COLS, window=window)

    # 5. Extraer filas dummy y calcular ratios
    home_row = rolled[rolled["game_id"] == f"{_LIVE_GAME_PREFIX}{home_team_id}"].iloc[0]
    away_row = rolled[rolled["game_id"] == f"{_LIVE_GAME_PREFIX}{away_team_id}"].iloc[0]

    def _ratios(row: pd.Series) -> tuple[float, float, float, float]:
        fgm = row["fgm_rolling"]
        fg3m = row["fg3m_rolling"]
        fga = row["fga_rolling"]
        fta = row["fta_rolling"]
        oreb = row["oreb_rolling"]
        tov = row["tov_rolling"]
        opp_dreb = row["opp_dreb_rolling"]
        efg = (fgm + 0.5 * fg3m) / fga
        tov_rate = tov / (fga + 0.44 * fta + tov)
        oreb_rate = oreb / (oreb + opp_dreb)
        ft_rate = fta / fga
        return float(efg), float(tov_rate), float(oreb_rate), float(ft_rate)

    h_efg, h_tov, h_oreb, h_ft = _ratios(home_row)
    a_efg, a_tov, a_oreb, a_ft = _ratios(away_row)

    return {
        "efg_diff": h_efg - a_efg,
        "tov_rate_diff": h_tov - a_tov,
        "oreb_rate_diff": h_oreb - a_oreb,
        "ft_rate_diff": h_ft - a_ft,
    }


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------

def _compute_ratings_live(
    hist_tgs: pd.DataFrame,
    home_team_id: int,
    away_team_id: int,
    target: pd.Timestamp,
    window: int,
) -> dict[str, float]:
    """Replica el paso 1-5 de ratings.py con filas dummy en target."""
    # 1. Self-join para añadir stats del oponente (mismo set que ratings.py)
    stats_with_opp = add_opponent_stats(hist_tgs, _RAT_STAT_COLS)

    # 2. Derivar pts, opp_pts, poss por partido (solo en datos históricos reales)
    with_q = _compute_game_quantities(stats_with_opp)

    # 3. Filtrar a los dos equipos
    both = with_q[with_q["team_id"].isin([home_team_id, away_team_id])].copy()

    # 4. Añadir filas dummy (NaN para las cantidades derivadas)
    qty_cols = _RAT_ROLLING_COLS + ["team_id", "game_id", "game_date", "season", "is_home"]
    dummy_rows = []
    for tid, is_home in [(home_team_id, 1), (away_team_id, 0)]:
        row: dict[str, Any] = {c: np.nan for c in _RAT_ROLLING_COLS}
        row.update({
            "team_id": tid,
            "game_id": f"{_LIVE_GAME_PREFIX}{tid}",
            "game_date": target,
            "season": "LIVE",
            "is_home": is_home,
        })
        dummy_rows.append(row)

    extended = pd.concat(
        [both[qty_cols], pd.DataFrame(dummy_rows)], ignore_index=True
    )

    # 5. Rolling sobre cantidades
    rolled = compute_rolling_means(extended, stat_cols=_RAT_ROLLING_COLS, window=window)

    # 6. Extraer dummy y calcular ratings
    home_row = rolled[rolled["game_id"] == f"{_LIVE_GAME_PREFIX}{home_team_id}"].iloc[0]
    away_row = rolled[rolled["game_id"] == f"{_LIVE_GAME_PREFIX}{away_team_id}"].iloc[0]

    def _ratings(row: pd.Series) -> tuple[float, float]:
        off = 100.0 * row["pts_rolling"] / row["poss_rolling"]
        def_ = 100.0 * row["opp_pts_rolling"] / row["poss_rolling"]
        return float(off), float(def_)

    h_off, h_def = _ratings(home_row)
    a_off, a_def = _ratings(away_row)

    return {
        "off_rating_diff": h_off - a_off,
        "def_rating_diff": h_def - a_def,
    }


def _compute_game_quantities(stats: pd.DataFrame) -> pd.DataFrame:
    """
    Deriva pts, opp_pts, poss por partido — misma fórmula que ratings.py.
    Solo se aplica a filas con stats reales (no a dummies).
    """
    df = stats.copy()
    df["pts"] = (df["fgm"] - df["fg3m"]) * 2 + df["fg3m"] * 3 + df["ftm"]
    df["opp_pts"] = (
        (df["opp_fgm"] - df["opp_fg3m"]) * 2 + df["opp_fg3m"] * 3 + df["opp_ftm"]
    )
    oreb_frac = df["oreb"] / (df["oreb"] + df["opp_dreb"])
    opp_oreb_frac = df["opp_oreb"] / (df["opp_oreb"] + df["dreb"])
    team_half = df["fga"] + 0.44 * df["fta"] - 1.07 * oreb_frac * (df["fga"] - df["fgm"]) + df["tov"]
    opp_half = df["opp_fga"] + 0.44 * df["opp_fta"] - 1.07 * opp_oreb_frac * (df["opp_fga"] - df["opp_fgm"]) + df["opp_tov"]
    df["poss"] = 0.5 * (team_half + opp_half)
    return df


# ---------------------------------------------------------------------------
# Context (rest, b2b, neutral_site)
# ---------------------------------------------------------------------------

def _compute_context_live(
    hist_games: pd.DataFrame,
    home_team_id: int,
    away_team_id: int,
    game_date: date,
    neutral_site: int,
) -> dict[str, float]:
    """
    Calcula rest_diff, home_b2b, away_b2b, neutral_site para el partido en vivo.

    rest_days = (game_date − last_game_date).days − 1, cap en REST_DAYS_CAP.
    is_b2b = 1 si rest_days == 0.
    """
    target = pd.Timestamp(game_date)

    def _team_rest(team_id: int) -> tuple[float, float]:
        home_mask = hist_games["home_team_id"] == team_id
        away_mask = hist_games["away_team_id"] == team_id
        team_dates = pd.concat([
            hist_games.loc[home_mask, "game_date"],
            hist_games.loc[away_mask, "game_date"],
        ])
        if team_dates.empty:
            return float("nan"), float("nan")
        last = pd.Timestamp(team_dates.max())
        gap = (target - last).days
        rest = float(min(gap - 1, REST_DAYS_CAP))
        b2b = 1.0 if rest == 0.0 else 0.0
        return rest, b2b

    h_rest, h_b2b = _team_rest(home_team_id)
    a_rest, a_b2b = _team_rest(away_team_id)

    return {
        "rest_diff": h_rest - a_rest,
        "home_b2b": h_b2b,
        "away_b2b": a_b2b,
        "neutral_site": float(neutral_site),
    }


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

def _compute_availability_live(
    hist_pgs: pd.DataFrame,
    hist_games: pd.DataFrame,
    home_team_id: int,
    away_team_id: int,
    absent_home_ids: set[int],
    absent_away_ids: set[int],
    activated_home_ids: set[int] | None,
    activated_away_ids: set[int] | None,
    played_home_ids: set[int] | None,
    played_away_ids: set[int] | None,
    window: int,
) -> float:
    """
    Calcula availability_diff para el partido en vivo.

    Replica la lógica de availability.py: numerador / denominador por equipo,
    diff = local − visitante.

    Por qué dos fórmulas de rolling distintas para el numerador
    -----------------------------------------------------------
    La pipeline de entrenamiento (`_add_player_minutes_rolling`) usa
    `groupby(player_id)["minutes"].transform(lambda s: s.shift(1).rolling(N).mean())`
    SOLO sobre los partidos jugados (minutes > 0), y luego ffill a los DNP.

    Resultado para el partido en vivo G:
    - Jugador que jugó en G (played): su serie jugada termina en g_{k-1}; la
      posición de G en esa serie da rolling que cubre [g_{k-N}, g_{k-1}] →
      equivalente a `s.rolling(N).mean().iloc[-1]` sobre hist_pgs (sin shift).
    - Jugador DNP en G: su serie jugada termina en K (último partido jugado);
      el rolling en K vía shift(1) cubre [K-N-1, K-2] (NO incluye K) →
      equivalente a `s.shift(1).rolling(N).mean().iloc[-1]` sobre hist_pgs.

    Para predicción en vivo (played_ids=None, partido futuro): tratamos todos
    los activados como jugadores (rolling sin shift) — la mejor aproximación.
    """
    if hist_pgs.empty:
        return float("nan")

    # minutes_rolling con shift(1) para denominador (replica _compute_denominator)
    all_stats = _add_player_minutes_rolling(hist_pgs, window)

    # Series de partidos jugados (minutes > 0) para los dos tipos de rolling
    played_hist = (
        hist_pgs[hist_pgs["minutes"].notna() & (hist_pgs["minutes"] > 0)]
        .sort_values(["player_id", "game_date"])
    )

    # Rolling SIN shift: para jugadores que jugaron en G.
    # rolling(N).iloc[-1] sobre hist_pgs = mean de últimos N partidos jugados antes de G,
    # incluyendo el más reciente — idéntico al shift(1).rolling(N) evaluado en la
    # posición virtual G de la serie completa (donde G es la siguiente después del último).
    player_rolling_played = (
        played_hist.groupby("player_id")["minutes"]
        .apply(lambda s: s.rolling(window, min_periods=1).mean().iloc[-1]
               if len(s) > 0 else np.nan)
        .fillna(0.0)
    )

    # Rolling CON shift: para jugadores DNP en G.
    # shift(1).rolling(N).iloc[-1] = rolling en la posición del último partido jugado K,
    # cubriendo [K-N-1, K-2] (sin incluir K) — lo que ffill propaga a G en el training.
    player_rolling_dnp = (
        played_hist.groupby("player_id")["minutes"]
        .apply(lambda s: s.shift(1).rolling(window, min_periods=1).mean().iloc[-1]
               if len(s) > 0 else np.nan)
        .fillna(0.0)
    )

    h_avail = _team_availability(
        all_stats=all_stats,
        hist_games=hist_games,
        team_id=home_team_id,
        absent_ids=absent_home_ids,
        activated_ids=activated_home_ids,
        played_ids=played_home_ids,
        player_rolling_played=player_rolling_played,
        player_rolling_dnp=player_rolling_dnp,
        window=window,
    )
    a_avail = _team_availability(
        all_stats=all_stats,
        hist_games=hist_games,
        team_id=away_team_id,
        absent_ids=absent_away_ids,
        activated_ids=activated_away_ids,
        played_ids=played_away_ids,
        player_rolling_played=player_rolling_played,
        player_rolling_dnp=player_rolling_dnp,
        window=window,
    )

    if np.isnan(h_avail) or np.isnan(a_avail):
        return float("nan")
    return float(h_avail - a_avail)


def _team_availability(
    all_stats: pd.DataFrame,
    hist_games: pd.DataFrame,
    team_id: int,
    absent_ids: set[int],
    activated_ids: set[int] | None,
    played_ids: set[int] | None,
    player_rolling_played: pd.Series,
    player_rolling_dnp: pd.Series,
    window: int,
) -> float:
    """
    Disponibilidad de UN equipo en el partido en vivo.

    Denominador: suma de minutes_rolling (shift+ffill) de jugadores de la rotación
                 reciente (últimos `window` partidos del equipo).
    Numerador:
      activated_ids + played_ids: usa rolling_played para jugadores que jugaron en G
                                  y rolling_dnp para jugadores DNP en G.
      activated_ids sin played_ids: usa rolling_played para todos (predicción en vivo).
      absent_ids: denominador − contribución de ausentes.
    """
    # Partidos del equipo en orden cronológico
    home_dates = hist_games[hist_games["home_team_id"] == team_id][["game_id", "game_date"]]
    away_dates = hist_games[hist_games["away_team_id"] == team_id][["game_id", "game_date"]]
    team_game_ids = (
        pd.concat([home_dates, away_dates])
        .drop_duplicates("game_id")
        .sort_values("game_date")["game_id"]
        .tolist()
    )
    if not team_game_ids:
        return float("nan")

    last_n_ids = set(team_game_ids[-window:])

    team_stats = all_stats[all_stats["team_id"] == team_id]
    if team_stats.empty:
        return float("nan")

    # ── Denominador ──
    rotation_stats = team_stats[team_stats["game_id"].isin(last_n_ids)]
    if rotation_stats.empty:
        return float("nan")

    player_denom = (
        rotation_stats.sort_values("game_date")
        .groupby("player_id")["minutes_rolling"]
        .last()
        .fillna(0.0)
    )
    denominator = float(player_denom.sum())
    if denominator == 0.0:
        return float("nan")

    # ── Numerador ──
    if activated_ids is not None:
        if played_ids is None:
            # Predicción en vivo: tratar todos los activados como jugadores.
            numerator = float(sum(
                float(player_rolling_played.get(p, 0.0)) for p in activated_ids
            ))
        else:
            # Test de equivalencia: distinción exacta entre jugadores y DNP.
            actually_played = activated_ids & played_ids
            dnp_activated = activated_ids - played_ids
            numerator = float(
                sum(float(player_rolling_played.get(p, 0.0)) for p in actually_played) +
                sum(float(player_rolling_dnp.get(p, 0.0)) for p in dnp_activated)
            )
    else:
        # Modo v0: denominador − contribución de ausentes declarados.
        absent_contrib = float(sum(float(player_denom.get(p, 0.0)) for p in absent_ids))
        numerator = denominator - absent_contrib

    return numerator / denominator
