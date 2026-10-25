"""
Selección y estadística de los jugadores destacados del mensaje 2 (D-PROD-1c).

CONTRATO ADJUDICADO (D-EXP-4 + D-PROD-1b): HECHOS DESCRIPTIVOS. Lo que se
publica es la mediana y el rango OBSERVADOS en los últimos partidos jugados —
jamás una predicción con cobertura prometida. D-EXP-3 cerró que el rolling
directo le gana a cualquier descomposición, así que aquí no hay modelo: hay
lectura honesta del historial.

CERO LECTORES NUEVOS: este módulo no consulta nada. Recibe el histórico de
player_game_stats ya cargado por el DataStore — el mismo camino que usa
live_lookup para las features — y trabaja en memoria. Un lector propio de
stats habría sido una segunda fuente de verdad, que es como nacen los gemelos
(capa 4 y su gemelo, D-ODDS).

ANTI-LEAKAGE: el llamador entrega solo filas con game_date ESTRICTAMENTE
anterior al partido. Aquí no hay shift() que aplicar porque el partido a
publicar todavía no existe en los datos.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

    from nba_predictor.api.daily_predictions import PlayerHighlight

_log = logging.getLogger(__name__)

# Ventana declarada en el título del mensaje 2.
STAT_WINDOW = 10

# Recencia y profundidad de historial: definidas en config porque son
# parámetros del producto, no detalles de esta función.
from nba_predictor.config import (  # noqa: E402
    HIGHLIGHT_HISTORY_SEASONS,
    HIGHLIGHT_RECENCY_DAYS,
)

# D-PRE-9: destacados por equipo. Mismos filtros para el 2 que para el 1.
HIGHLIGHTS_PER_TEAM = 2

# Mínimo de partidos jugados para que un jugador sea publicable. Con menos de
# tres, "mediana y rango" describen ruido, no una costumbre; el puesto pasa al
# siguiente por minutos. El sufijo "(últimos n)" cubre el tramo 3..9.
MIN_GAMES_IN_WINDOW = 3

# Umbral del especialista de triples. NO venía fijado por D-PROD-1b (allí solo
# se congeló que el especialista muestra 3P y solo la mediana); se fija aquí:
# una mediana de 2+ triples por partido es un tirador cuyo volumen de triples
# informa al lector. Decisión declarada, no heredada.
SPECIALIST_MIN_FG3M = 2.0

# Estatus del injury report que EXCLUYEN a un jugador de los destacados.
# Doubtful entra aquí por medición, no por intuición: D-EXP-1 midió
# P(juega | Doubtful) ~ 0.02 sobre 917 instancias — opera como eufemismo de
# Out, y publicar a alguien que no va a jugar es exactamente la exageración
# que el producto no puede permitirse.
EXCLUDED_STATUSES: frozenset[str] = frozenset({"Out", "Doubtful"})

# Único estatus que se marca en el mensaje. Probable (~0.91) y Available van
# sin marca: marcar lo que casi siempre ocurre es ruido.
MARKED_STATUS = "Questionable"
MARK = "Q"


def derive_box_stats(pgs: "pd.DataFrame") -> "pd.DataFrame":
    """Añade pts y reb, que player_game_stats NO trae como columnas.

    pts = 2*fgm + fg3m + ftm — porque fgm YA incluye los triples:
    2*(fgm - fg3m) + 3*fg3m + ftm se simplifica a esto. reb = oreb + dreb.
    """
    out = pgs.copy()
    out["pts"] = 2 * out["fgm"] + out["fg3m"] + out["ftm"]
    out["reb"] = out["oreb"] + out["dreb"]
    return out


def player_window_stats(
    played: "pd.DataFrame",
    window: int = STAT_WINDOW,
) -> dict[str, Any]:
    """Mediana, mínimo y máximo de los ÚLTIMOS `window` partidos jugados.

    `played` son las filas de UN jugador, ya filtradas a minutes > 0. Se ordena
    por fecha y se toman las últimas: el "últimos 10" del título tiene que ser
    literal.

    Devuelve también `minutes_rolling` (el criterio de selección) y
    `games_in_window` (el n real, que el formato declara cuando es < 10).
    """
    ventana = played.sort_values("game_date", kind="stable").tail(window)
    n = len(ventana)
    if n == 0:
        return {"games_in_window": 0}
    return {
        "games_in_window": n,
        "minutes_rolling": float(ventana["minutes"].mean()),
        "pts_median": float(ventana["pts"].median()),
        "pts_min": float(ventana["pts"].min()),
        "pts_max": float(ventana["pts"].max()),
        "reb_median": float(ventana["reb"].median()),
        "ast_median": float(ventana["ast"].median()),
        "fg3m_median": float(ventana["fg3m"].median()),
    }


def status_mark(status: str | None) -> str | None:
    """Marca publicable de un estatus del injury report, o None.

    Solo Questionable se marca. Un estatus desconocido NO se marca y tampoco
    excluye: inventar una marca para un vocabulario que no reconocemos sería
    afirmar algo que no medimos.
    """
    return MARK if status == MARKED_STATUS else None


def is_eligible(status: str | None) -> bool:
    """¿Este jugador puede aparecer en el mensaje?

    Sin estatus (no listado en el reporte) = elegible: la inmensa mayoría de
    los jugadores no aparecen en el injury report y están perfectamente sanos.
    """
    return status not in EXCLUDED_STATUSES


def latest_team_rows(
    hist_pgs: "pd.DataFrame",
    roster_signals: "pd.DataFrame | None" = None,
) -> "pd.DataFrame":
    """Fila MÁS RECIENTE de cada jugador: player_id, team_id, game_date.

    D-PRE-8: lo "visto" son las filas de player_game_stats (temporada regular)
    MÁS las señales de plantilla (pretemporada e injury report). Gana la más
    reciente por game_date; empate, por game_id / ref_id — el orden de cadena
    de los dos identificadores, que es estable y no depende del orden de carga.

    Las señales SOLO deciden el equipo: no entran a las estadísticas, que
    siguen saliendo exclusivamente de hist_pgs (temporada regular).
    """
    import pandas as pd

    ref = "game_id" if "game_id" in hist_pgs.columns else None
    partes = [pd.DataFrame({
        "player_id": hist_pgs["player_id"].astype("int64"),
        "team_id": hist_pgs["team_id"].astype("int64"),
        "game_date": pd.to_datetime(hist_pgs["game_date"]),
        "ref": hist_pgs[ref].astype(str) if ref else "",
    })]
    if roster_signals is not None and not roster_signals.empty:
        partes.append(pd.DataFrame({
            "player_id": roster_signals["player_id"].astype("int64"),
            "team_id": roster_signals["team_id"].astype("int64"),
            "game_date": pd.to_datetime(roster_signals["game_date"]),
            "ref": roster_signals["ref_id"].astype(str),
        }))
    vistos = pd.concat(partes, ignore_index=True)
    return (
        vistos.sort_values(["game_date", "ref"], kind="stable")
        .groupby("player_id", sort=False)
        .tail(1)[["player_id", "team_id", "game_date"]]
    )


def rank_team_candidates(
    hist_pgs: "pd.DataFrame",
    team_id: int,
    statuses: dict[int, str],
    window: int = STAT_WINDOW,
    min_games: int = MIN_GAMES_IN_WINDOW,
    *,
    target_date: date | None = None,
    recency_days: int = HIGHLIGHT_RECENCY_DAYS,
    roster_signals: "pd.DataFrame | None" = None,
) -> list[dict[str, Any]]:
    """Candidatos de UN equipo, ordenados por minutos rolling descendente.

    Cuatro filtros, en este orden: EQUIPO ACTUAL (¿es suyo hoy?), RECENCIA
    (¿sigue activo?), estatus (¿puede jugar hoy?) y mínimo de partidos (¿hay
    costumbre que describir?).

    EQUIPO ACTUAL (D-PRE-6): el equipo de un jugador es el de su fila MÁS
    RECIENTE en toda la liga — cualquier fila, también de 0 minutos, porque
    desde la Decisión 9 una fila significa "activado con ese equipo". Antes el
    criterio era "su último partido CON ESTE equipo dentro de la recencia", y
    un traspasado seguía siendo candidato de su equipo VIEJO hasta que
    caducaban los 250 días: Jaylen Brown habría salido como destacado de BOS
    hasta el 2026-12-16, porque sus 10 partidos con Boston nunca se renuevan.
    LÍMITE CONOCIDO: antes de su primera fila con el equipo nuevo, el sistema
    no puede saber que cambió de equipo (no hay fuente de rosters).
    D-PRE-8 lo acota: roster_signals (pretemporada + injury report) cuenta
    como fila vista para decidir el EQUIPO, nunca para los números.

    LA RECENCIA NO ES COSMÉTICA. Sin ella el universo es toda la historia y
    gana quien más minutos promedió en CUALQUIER tramo de 10 partidos de la
    última década: el dry-run del 2026-10-21 proponía a Luol Deng por Miami
    con datos de 2016. Se aplica sobre la fila más reciente del jugador.

    ESTADÍSTICAS: los ÚLTIMOS 10 partidos jugados del jugador, con CUALQUIER
    equipo. El mensaje dice "últimos 10 partidos", y eso es literal: un
    traspasado aparece con su equipo nuevo y sus números reales recientes
    aunque los haya hecho con el anterior (adjudicado por Antonio, D-PRE-6).

    target_date None desactiva SOLO la recencia — para tests que fijan otras
    propiedades; la ruta de producción SIEMPRE lo pasa. El equipo actual se
    aplica siempre: es un hecho de los datos, no un filtro temporal.
    """
    if hist_pgs.empty:
        return []
    # Equipo actual: la fila más reciente de cada jugador entre lo jugado y las
    # señales de plantilla (desempate estable por game_id / ref_id).
    ultima = latest_team_rows(hist_pgs, roster_signals)
    actuales = ultima[ultima["team_id"] == team_id]
    if target_date is not None:
        import pandas as pd

        corte = pd.Timestamp(target_date - timedelta(days=recency_days))
        actuales = actuales[actuales["game_date"] >= corte]
    del_equipo_hoy = {int(p) for p in actuales["player_id"]}
    if not del_equipo_hoy:
        return []

    jugados = hist_pgs[
        hist_pgs["player_id"].isin(del_equipo_hoy)
        & hist_pgs["minutes"].notna()
        & (hist_pgs["minutes"] > 0)
    ]

    candidatos: list[dict[str, Any]] = []
    for pid, filas in jugados.groupby("player_id"):
        estatus = statuses.get(int(pid))
        if not is_eligible(estatus):
            continue
        stats = player_window_stats(filas, window)
        if stats["games_in_window"] < min_games:
            continue
        candidatos.append({"player_id": int(pid), "status": estatus, **stats})

    # Desempate por player_id: el orden tiene que ser determinista o dos
    # servidas del mismo día podrían publicar jugadores distintos.
    return sorted(candidatos, key=lambda c: (-c["minutes_rolling"], c["player_id"]))


def build_game_highlights(
    hist_pgs: "pd.DataFrame",
    *,
    home_team_id: int,
    away_team_id: int,
    statuses_by_tid: dict[int, dict[int, str]],
    player_map: dict[int, str],
    window: int = STAT_WINDOW,
    min_games: int = MIN_GAMES_IN_WINDOW,
    target_date: date | None = None,
    recency_days: int = HIGHLIGHT_RECENCY_DAYS,
    roster_signals: "pd.DataFrame | None" = None,
    per_team: int = HIGHLIGHTS_PER_TEAM,
) -> list["PlayerHighlight"]:
    """Top `per_team` por equipo (D-PRE-9: 2) como PlayerHighlight listos para formato.

    Orden de salida: local rank 1, local rank 2, visitante rank 1, visitante
    rank 2 — el formato agrupa por lado conservando este orden.

    Un equipo sin candidato publicable simplemente no aporta jugador; el otro
    sí. No se rellena el hueco con alguien que no cumple: el contrato dice top
    N POR EQUIPO, y un equipo con un solo candidato publica uno.
    """
    salida: list["PlayerHighlight"] = []
    for team_id, lado in ((home_team_id, "home"), (away_team_id, "away")):
        candidatos = rank_team_candidates(
            hist_pgs, team_id, statuses_by_tid.get(team_id, {}), window, min_games,
            target_date=target_date, recency_days=recency_days,
            roster_signals=roster_signals,
        )
        if not candidatos:
            # Plantel renovado por completo, expansión, o todos excluidos por
            # estatus. El equipo se OMITE: publicar a un jugador rancio con
            # aspecto sano es peor que no publicar a nadie.
            _log.warning(
                "Sin destacado VIGENTE para el equipo %s (recencia %d días): se omite",
                team_id, recency_days,
            )
            continue
        for rank, c in enumerate(candidatos[:per_team], start=1):
            salida.append(_highlight(c, lado, rank, player_map))
    return salida


def _highlight(
    c: dict[str, Any], lado: str, rank: int, player_map: dict[int, str]
) -> "PlayerHighlight":
    """Un candidato rankeado → PlayerHighlight publicable."""
    from nba_predictor.api.daily_predictions import PlayerHighlight

    fg3m = c["fg3m_median"]
    return PlayerHighlight(
        player_name=player_map.get(c["player_id"], f"#{c['player_id']}"),
        team=lado,
        pts_median=c["pts_median"],
        pts_min=c["pts_min"],
        pts_max=c["pts_max"],
        reb_median=c["reb_median"],
        ast_median=c["ast_median"],
        games_in_window=c["games_in_window"],
        status=status_mark(c["status"]),
        fg3m_median=fg3m,
        is_shooting_specialist=fg3m >= SPECIALIST_MIN_FG3M,
        player_id=c["player_id"],
        rank=rank,
    )


def season_of(d: date) -> str:
    """Temporada NBA a la que pertenece una fecha: '2026-27' para oct-2026.

    La temporada arranca en octubre, así que de enero a septiembre la fecha
    pertenece a la temporada que empezó el año anterior. Misma regla que el
    resto del camino en vivo — derivada de la FECHA, jamás del reloj.
    """
    inicio = d.year if d.month >= 10 else d.year - 1
    return f"{inicio}-{(inicio + 1) % 100:02d}"


def recent_seasons(target_date: date, n: int = HIGHLIGHT_HISTORY_SEASONS) -> list[str]:
    """Las n temporadas hasta target_date, de la más antigua a la más reciente.

    Acota lo que se LEE de BigQuery. No confundir con la recencia: esta decide
    cuánto pasado hace falta para completar "los últimos 10 partidos jugados"
    de un candidato vigente; aquella decide quién es candidato.
    """
    inicio = target_date.year if target_date.month >= 10 else target_date.year - 1
    return [f"{a}-{(a + 1) % 100:02d}" for a in range(inicio - n + 1, inicio + 1)]
