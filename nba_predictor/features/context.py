"""
Grupo 4 — Features de contexto del partido (Fase 2).

Salida: una fila por partido con features de contexto (NO de fuerza estadística).
Columnas: game_id, season, game_date, rest_diff, home_b2b, away_b2b, neutral_site.

Features
--------
rest_diff
    home_rest_days − away_rest_days (con rest_days ya capeados en REST_DAYS_CAP).
    Diferencia: un equipo con más descanso tiene ventaja relativa.
    Positivo → local más descansado; negativo → visitante más descansado.

home_b2b / away_b2b
    Flag absoluto 0/1: el equipo local/visitante juega en back-to-back
    (rest_days == 0, i.e. jugó ayer). ABSOLUTO, no diferencia: un b2b tiene
    efectos de fatiga acumulada que rest_diff no captura simétricamente — un
    equipo con 0 días de descanso no es "lo opuesto" a uno con 3.

neutral_site
    Flag 0/1 copiado directo de games.neutral_site. Anula conceptualmente la
    ventaja de local (burbuja COVID 2020 = 88 partidos marcados).

Definición de rest_days (por equipo)
-------------------------------------
    rest_days = (game_date − prev_game_date).days − 1

Ejemplos:
    lunes → martes   : gap = 1d → rest_days = 0 → is_b2b = 1
    lunes → miércoles: gap = 2d → rest_days = 1 → is_b2b = 0
    lunes → viernes  : gap = 4d → rest_days = 3 → is_b2b = 0

Cap en REST_DAYS_CAP (config.py, valor 7): valores mayores (All-Star break,
inicio de temporada por cruce) se recortan a 7. Más de una semana no añade
frescura marginal y los ~100 días de offseason distorsionarían la feature.

Primer partido histórico de cada equipo: sin partido anterior → rest_days = NaN.
Estos partidos son siempre los primeros de una temporada y caen entre los
primeros 15 de cada equipo → se excluyen del set de entrenamiento por la
regla estándar.

Anti-leakage
------------
Trivial: rest_days usa solo el partido ANTERIOR (pasado), nunca el actual
ni futuros. shift(1) dentro de groupby(team_id), ordenado por game_date,
garantiza esto: la fila i del equipo T solo ve la fecha del partido i-1 de T.
"""
from __future__ import annotations

import pandas as pd

from nba_predictor.config import REST_DAYS_CAP
from nba_predictor.storage import get_datastore

OUTPUT_COLS: list[str] = [
    "game_id", "season", "game_date",
    "rest_diff", "home_b2b", "away_b2b", "neutral_site",
]


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def build_context() -> pd.DataFrame:
    """
    Carga partidos del DataStore y calcula features de contexto.

    Punto de entrada para scripts/notebooks.

    Returns
    -------
    DataFrame con columnas OUTPUT_COLS, ordenado por game_date.
    """
    games = get_datastore().load_games()
    return compute_context(games)


def compute_context(games: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula features de contexto a partir del DataFrame de partidos.

    Función de pura computación — testeable sin BD.

    Parameters
    ----------
    games : DataFrame con columnas game_id, season, game_date, home_team_id,
            away_team_id, neutral_site. game_date puede ser datetime.date o
            datetime64 — se convierte internamente para el aritmético.

    Returns
    -------
    DataFrame con OUTPUT_COLS, ordenado por game_date.
    rest_diff / home_b2b / away_b2b son NaN para los partidos del primer
    partido histórico de cada equipo (sin partido previo disponible).
    """
    _validate_input(games)
    team_rest = compute_team_rest(games)
    return _to_game_context(games, team_rest)


def compute_team_rest(games: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula días de descanso y flag b2b por (equipo, partido).

    Expuesta para tests: permite verificar rest_days individuales sin el
    pivot home/away que produce compute_context.

    Orden de cálculo
    ----------------
    1. Ampliar a una fila por (equipo, partido): concat de home_team_id y
       away_team_id con is_home=1/0.
    2. Ordenar por (team_id, game_date).
    3. shift(1) dentro de groupby(team_id) → fecha del partido anterior.
    4. gap = (game_date − prev_date).days; rest_days = (gap − 1).clip(upper=CAP).
    5. is_b2b = (rest_days == 0), NaN donde rest_days es NaN.

    Returns
    -------
    DataFrame con columnas: game_id, team_id, is_home, rest_days, is_b2b.
    Una fila por (equipo, partido); 2 filas por game_id.
    """
    home = (
        games[["game_id", "game_date", "home_team_id"]]
        .rename(columns={"home_team_id": "team_id"})
        .assign(is_home=1)
    )
    away = (
        games[["game_id", "game_date", "away_team_id"]]
        .rename(columns={"away_team_id": "team_id"})
        .assign(is_home=0)
    )
    tg = pd.concat([home, away], ignore_index=True)

    # Convertir a datetime64 para habilitar .dt.days en la diferencia.
    # Idempotente si ya es datetime64; maneja datetime.date (object dtype).
    tg["game_date"] = pd.to_datetime(tg["game_date"])
    tg = tg.sort_values(["team_id", "game_date"], kind="stable")

    # Fecha del partido anterior del mismo equipo.
    # NaT para el primer partido del equipo en el dataset (sin historia).
    tg["prev_date"] = tg.groupby("team_id")["game_date"].shift(1)

    # Gap en días calendario; rest_days = gap − 1.
    # NaT − NaT = NaT; .dt.days de NaT = NaN; NaN − 1 = NaN.
    gap = (tg["game_date"] - tg["prev_date"]).dt.days
    tg["rest_days"] = (gap - 1).clip(upper=REST_DAYS_CAP)
    # clip preserva NaN cuando gap es NaN.

    # is_b2b: 1.0 si rest_days==0; 0.0 si rest_days>0; NaN si rest_days es NaN.
    # .where(cond) sustituye con NaN donde ~cond, preservando el valor original donde cond.
    tg["is_b2b"] = (tg["rest_days"] == 0).where(tg["rest_days"].notna())

    return tg[["game_id", "team_id", "is_home", "rest_days", "is_b2b"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _to_game_context(games: pd.DataFrame, team_rest: pd.DataFrame) -> pd.DataFrame:
    """
    Pivota de (equipo, partido) a partido y ensambla la tabla de contexto.

    validate="one_to_one" garantiza que cada game_id tenga exactamente un equipo
    local y uno visitante en team_rest — falla ruidosamente si no es así.
    """
    home_side = (
        team_rest[team_rest["is_home"] == 1][["game_id", "rest_days", "is_b2b"]]
        .rename(columns={"rest_days": "home_rest_days", "is_b2b": "home_b2b"})
    )
    away_side = (
        team_rest[team_rest["is_home"] == 0][["game_id", "rest_days", "is_b2b"]]
        .rename(columns={"rest_days": "away_rest_days", "is_b2b": "away_b2b"})
    )
    result = (
        games[["game_id", "season", "game_date", "neutral_site"]]
        .merge(home_side, on="game_id", validate="one_to_one")
        .merge(away_side, on="game_id", validate="one_to_one")
    )
    # rest_diff: NaN si cualquiera de los dos lados es NaN (propagación automática).
    result["rest_diff"] = result["home_rest_days"] - result["away_rest_days"]

    return result[OUTPUT_COLS].sort_values("game_date").reset_index(drop=True)


def _validate_input(games: pd.DataFrame) -> None:
    required = {"game_id", "season", "game_date", "home_team_id", "away_team_id", "neutral_site"}
    missing = required - set(games.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {sorted(missing)}")
