"""
Marcador semanal del mensaje 2 (D-PROD-2, diseño sellado 2026-10-29T02:30:32Z).

Los lunes, el mensaje 2 lleva una cabecera con el acierto de la semana CERRADA
anterior (lunes a domingo por fecha de partido ET):

    📊 Semana 9-15 nov: líneas 50/50 acertaron 52 de 101 · rango 80% cubrió 79 de 98

Es la pieza OBLIGATORIA que acompaña a la línea 50/50 (adjudicación de D-EXP-12):
el canal publica cuánto acertó lo que publicó. Por eso la escalera de longitud
nunca la quita.

POR QUÉ VIVE EN EL PAQUETE Y NO EN EL SCRIPT DE SEGUIMIENTO: lo que se publica
tiene que calcularse en el mismo código que se despliega y se testea. "Resuelto
en el script" no es resuelto (regla de D-PRE-4). La lógica es la del script de
seguimiento —servida de record, veredicto evaluado/no jugó/equipo distinto—
reescrita aquí como funciones puras.

LECTURA: la única lectura nueva es la del log de la semana
(DataStore.load_player_predictions_log), por filas y no con to_dataframe (capa
5 de la cebolla). Los resultados reales salen del historial que el endpoint ya
cargó para los destacados.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from nba_predictor.config import HIGHLIGHT_REGIME_DAYS, SCOREBOARD_MIN_OBS

_CDMX = ZoneInfo("America/Mexico_City")
_REC_DESDE = time(12, 50)
_REC_HASTA = time(13, 30)
_REC_OBJETIVO = time(13, 0)

_MESES = ["ene", "feb", "mar", "abr", "may", "jun",
          "jul", "ago", "sep", "oct", "nov", "dic"]


def closed_week(target_date: date) -> tuple[date, date]:
    """La semana CERRADA anterior a la semana de target_date: (lunes, domingo)."""
    lunes_actual = target_date - timedelta(days=target_date.weekday())
    return lunes_actual - timedelta(days=7), lunes_actual - timedelta(days=1)


def should_publish(target_date: date, played_dates: set[date]) -> bool:
    """¿Este día lleva el marcador?

    El lunes, siempre. Un lunes sin mensaje 2 (día sin partidos) pasa al
    siguiente día con mensaje 2 de la misma semana: un día lo lleva si ningún
    día anterior de su semana tuvo partidos. `played_dates` son las fechas con
    partidos de temporada regular ya jugados (tabla games).
    """
    lunes = target_date - timedelta(days=target_date.weekday())
    d = lunes
    while d < target_date:
        if d in played_dates:
            return False
        d += timedelta(days=1)
    return True


def _cdmx(ts: Any) -> datetime:
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo("UTC"))
    return ts.astimezone(_CDMX)


def _as_date(d: Any) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d)[:10])


def record_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Servida de record por (game_id, player_id) — regla congelada en D-PRE-1
    addendum: entre las filas cuya predicted_at_utc en CDMX cae en la fecha del
    partido, la más cercana a las 13:00 dentro de [12:50, 13:30]; si ninguna cae
    en esa franja, la más temprana del día. Sin fila del día = sin record.
    """
    mejor: dict[tuple[str, int], tuple[tuple, dict[str, Any]]] = {}
    for r in rows:
        local = _cdmx(r["predicted_at_utc"])
        if local.date() != _as_date(r["game_date"]):
            continue
        t = local.time()
        en_franja = _REC_DESDE <= t <= _REC_HASTA
        dist = abs((datetime.combine(local.date(), t) -
                    datetime.combine(local.date(), _REC_OBJETIVO)).total_seconds())
        clave_orden = (0, dist) if en_franja else (1, local.timestamp())
        k = (str(r["game_id"]), int(r["player_id"]))
        if k not in mejor or clave_orden < mejor[k][0]:
            mejor[k] = (clave_orden, r)
    return [v[1] for v in mejor.values()]


def evaluate(
    record: list[dict[str, Any]],
    played: dict[tuple[str, int], tuple[int, float]],
    tricode_by_tid: dict[int, str],
    season_start: date,
) -> dict[str, int]:
    """Cuenta aciertos de la semana sobre las filas de record EVALUADAS.

    `played`: (game_id, player_id) -> (team_id, puntos reales), solo filas con
    minutos > 0. Evaluado = jugó con el equipo publicado (mismo criterio que el
    script de seguimiento: no jugó y equipo distinto quedan fuera).
    - Línea: filas publicadas con línea .5; acierta si real > línea.
    - Rango: filas con rango R1 publicado (fuera del régimen de 21 días, ventana
      completa de 10, rango presente); acierta si pts_min <= real <= pts_max.
    """
    out = {"line_hits": 0, "line_n": 0, "range_hits": 0, "range_n": 0}
    for r in record:
        jugado = played.get((str(r["game_id"]), int(r["player_id"])))
        if jugado is None:
            continue
        team_id, real = jugado
        if tricode_by_tid.get(int(team_id)) != r.get("team_tricode"):
            continue
        if r.get("pts_line") is not None:
            out["line_n"] += 1
            out["line_hits"] += int(real > float(r["pts_line"]))
        es_r1 = (
            r.get("pts_min") is not None and r.get("pts_max") is not None
            and r.get("games_in_window") == 10
            and (_as_date(r["game_date"]) - season_start).days >= HIGHLIGHT_REGIME_DAYS
        )
        if es_r1:
            out["range_n"] += 1
            out["range_hits"] += int(float(r["pts_min"]) <= real <= float(r["pts_max"]))
    return out


def _week_label(ini: date, fin: date) -> str:
    if ini.month == fin.month:
        return f"{ini.day}-{fin.day} {_MESES[fin.month - 1]}"
    return f"{ini.day} {_MESES[ini.month - 1]}-{fin.day} {_MESES[fin.month - 1]}"


def render(ini: date, fin: date, counts: dict[str, int] | None,
           min_obs: int = SCOREBOARD_MIN_OBS) -> str:
    """Texto de la cabecera. counts None = no se pudo calcular (declarado)."""
    cabeza = f"📊 Semana {_week_label(ini, fin)}"
    if counts is None:
        return f"{cabeza}: marcador no disponible hoy"
    linea_ok = counts["line_n"] >= min_obs
    rango_ok = counts["range_n"] >= min_obs
    if not linea_ok and not rango_ok:
        return f"{cabeza}: muestra insuficiente"
    partes = [
        f"líneas 50/50 acertaron {counts['line_hits']} de {counts['line_n']}"
        if linea_ok else "líneas 50/50: muestra insuficiente",
        f"rango 80% cubrió {counts['range_hits']} de {counts['range_n']}"
        if rango_ok else "rango 80%: muestra insuficiente",
    ]
    return f"{cabeza}: " + " · ".join(partes)
