"""
Funciones PURAS de la captura de odds de mercado (D-ODDS-2).

Sin red, sin BigQuery, sin reloj: todo lo que aqui se decide es testeable con
un dict y una lista. El CLI delgado (scripts/odds_capture_job.py) hace la red y
la escritura; este modulo hace las decisiones. Mismo patron que ingest_logic.py.

ALCANCE (D-ODDS-2): las odds son DIAGNOSTICO. Jamas entran como feature de
ningun modelo, jamas tocan el pipeline de predicciones ni a B-limpia. Este
modulo no importa nada de features/ ni de models/ y no debe empezar a hacerlo.

LO QUE DELIBERADAMENTE NO HACE: no deriva probabilidades implicitas y no quita
vig. Eso es ANALISIS y vive en otra tarea; la captura archiva el precio crudo
tal como lo publico el bookmaker. La unica transformacion admitida es
american -> decimal, que es un cambio de FORMATO exacto y reversible, no una
inferencia (y evita gastar un segundo credito de API por el mismo dato).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any

_log = logging.getLogger(__name__)

SOURCE = "the-odds-api"
MARKET_H2H = "h2h"

SNAPSHOT_LABELS: tuple[str, ...] = ("publish", "evening", "late")

# Clave del deporte en The Odds API (verificada en el paso 0 de la tarea).
SPORT_KEY = "basketball_nba"


class UnknownSnapshotLabel(ValueError):
    """El label no es uno de los tres pre-registrados."""


def validate_snapshot_label(label: str) -> str:
    """Los tres labels estan CERRADOS (D-ODDS-2): publish, evening, late.

    Se valida en la frontera y no mas adentro: un label libre acabaria como
    basura en una columna que luego el analisis agrupa, y nadie lo notaria
    hasta el analisis.
    """
    if label not in SNAPSHOT_LABELS:
        raise UnknownSnapshotLabel(
            f"snapshot_label invalido: {label!r}. Validos: {list(SNAPSHOT_LABELS)}"
        )
    return label


# ---------------------------------------------------------------------------
# Conversion de formato de precio
# ---------------------------------------------------------------------------


def american_to_decimal(american: int) -> float:
    """Precio americano -> decimal. Conversion EXACTA, no una estimacion.

        +150 -> 2.50      (1 + 150/100)
        -200 -> 1.50      (1 + 100/200)

    Se pide la API en formato americano (entero, encaja en INT64) y el decimal
    se deriva aqui: pedir los dos formatos costaria una segunda llamada y por
    tanto otro credito, para un dato que es el mismo numero escrito distinto.
    """
    if american == 0:
        raise ValueError("precio americano 0 no existe en ningun mercado")
    if american > 0:
        return round(1.0 + american / 100.0, 6)
    return round(1.0 + 100.0 / abs(american), 6)


# ---------------------------------------------------------------------------
# Normalizacion de nombres de equipo
# ---------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]")

# The Odds API publica nombres completos ("Los Angeles Lakers"). El catalogo
# del sistema (tabla teams) usa la misma forma, asi que la normalizacion
# simetrica basta y NO hace falta una tabla de alias... salvo los casos en que
# ambos lados escriben la franquicia distinto. Estos son los unicos observados;
# cualquier otro desajuste sale como WARNING y se adjudica, no se adivina.
_ALIAS: dict[str, str] = {
    "laclippers": "losangelesclippers",
    "laclipper": "losangelesclippers",
}


def normalize_team(name: str) -> str:
    """Nombre de equipo -> clave canonica comparable.

    Minusculas y sin separadores, con la MISMA transformacion aplicada a ambos
    lados de la comparacion (regla de 13e-1: jamas comparar strings crudos).
    """
    key = _NON_ALNUM.sub("", (name or "").lower())
    return _ALIAS.get(key, key)


def build_schedule_index(
    scheduled: list[tuple[str, date, str, str]]
) -> dict[tuple[date, str, str], str]:
    """[(game_id, game_date, home, away)] -> {(fecha, home_norm, away_norm): game_id}.

    El indice se construye desde el schedule CDN YA INGESTADO: el game_id
    canonico del sistema es el de la NBA, no el id propio de The Odds API (que
    es un hash suyo y no cruza con predictions_log).
    """
    return {
        (gdate, normalize_team(home), normalize_team(away)): gid
        for gid, gdate, home, away in scheduled
    }


# ---------------------------------------------------------------------------
# Parseo del payload de la API
# ---------------------------------------------------------------------------


def _commence_date(commence_time: str) -> date | None:
    """commence_time viene en UTC ISO con Z. Devuelve la fecha UTC.

    NO se convierte a ET ni a CDMX aqui: el emparejamiento contra el schedule
    se intenta tambien con el dia anterior (ver match_events), porque un
    partido de las 19:00 ET cae en el dia UTC siguiente.
    """
    try:
        return datetime.fromisoformat(commence_time.replace("Z", "+00:00")).date()
    except (ValueError, TypeError, AttributeError):
        return None


def _et_game_date(commence_time: str) -> date | None:
    """Dia al que PERTENECE el partido segun la aritmetica ET del matching.

    Un evento de 02:00 UTC es un partido de la tarde-noche ET del dia ANTERIOR.
    Se aplica la misma regla que usa match_events para buscar en el indice, de
    modo que una fila sin match no quede archivada en un dia distinto al que
    tendria si SI hubiera emparejado.
    """
    utc = _commence_date(commence_time)
    if utc is None:
        return None
    hora = commence_time[11:13]
    if hora.isdigit() and int(hora) < 12:
        return date.fromordinal(utc.toordinal() - 1)
    return utc


def extract_h2h_rows(
    event: dict[str, Any],
    game_id: str | None,
    game_date: date,
    snapshot_label: str,
    capture_ts: datetime,
    matched: bool,
) -> list[dict[str, Any]]:
    """Una fila por bookmaker del evento. Solo mercado h2h.

    game_id puede ser None: una fila sin match SE ARCHIVA igual (matched=false).
    Filosofia RAW — la odd capturada es evidencia IRRECUPERABLE (la API cobra
    x10 por historicas), mientras que la interpretacion se puede arreglar
    despues. Descartar la fila perderia el dato para siempre por no saber a que
    partido pertenece hoy.

    Un bookmaker sin h2h, sin los dos outcomes o con precios no enteros se
    OMITE con WARNING: media fila de odds no es dato parcial util, es ruido que
    el analisis tendria que limpiar despues sin saber que falta.
    """
    home = event.get("home_team") or ""
    away = event.get("away_team") or ""
    home_key, away_key = normalize_team(home), normalize_team(away)
    filas: list[dict[str, Any]] = []

    for bm in event.get("bookmakers", []) or []:
        mercado = next(
            (m for m in bm.get("markets", []) or [] if m.get("key") == MARKET_H2H), None
        )
        if mercado is None:
            continue

        precios: dict[str, int] = {}
        for out in mercado.get("outcomes", []) or []:
            clave = normalize_team(out.get("name", ""))
            precio = out.get("price")
            if isinstance(precio, bool) or not isinstance(precio, int):
                continue
            precios[clave] = precio

        if home_key not in precios or away_key not in precios:
            _log.warning(
                "Bookmaker %s sin precio h2h completo para %s @ %s — omitido",
                bm.get("key"), away, home,
            )
            continue

        filas.append({
            "game_date": game_date,
            "api_event_id": str(event.get("id") or ""),
            "matched": matched,
            "game_id": game_id,
            "home_team": home,
            "away_team": away,
            "bookmaker": bm.get("key") or "",
            "market": MARKET_H2H,
            "home_price_american": precios[home_key],
            "away_price_american": precios[away_key],
            "home_price_decimal": american_to_decimal(precios[home_key]),
            "away_price_decimal": american_to_decimal(precios[away_key]),
            "snapshot_label": snapshot_label,
            "capture_ts": capture_ts,
            "source": SOURCE,
            "ingested_at": capture_ts,
        })
    return filas


def match_events(
    events: list[dict[str, Any]],
    schedule_index: dict[tuple[date, str, str], str],
    snapshot_label: str,
    capture_ts: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """(filas para BigQuery, avisos de partidos sin match).

    Un evento de la API sin match en el schedule NO tumba el job: se registra
    como aviso y se sigue. Razones legitimas de no-match: pretemporada o
    exhibicion que el sistema no ingesta, partido aplazado, o una franquicia
    escrita distinto. Fallar el job por eso perderia TODAS las odds del dia por
    un solo partido raro.
    """
    validate_snapshot_label(snapshot_label)
    ts = capture_ts or datetime.now(timezone.utc)

    filas: list[dict[str, Any]] = []
    sin_match: list[str] = []

    for ev in events:
        home_key = normalize_team(ev.get("home_team", ""))
        away_key = normalize_team(ev.get("away_team", ""))
        fecha_utc = _commence_date(ev.get("commence_time", ""))
        if fecha_utc is None:
            sin_match.append(
                f"{ev.get('away_team')} @ {ev.get('home_team')} (commence_time ilegible)"
            )
            continue

        # Un partido de la tarde-noche ET cae en el dia UTC SIGUIENTE, asi que
        # se prueba la fecha UTC y la anterior. Sin esto, casi todo partido
        # nocturno quedaria sin match.
        gid = None
        fecha_match = None
        for candidata in (fecha_utc, date.fromordinal(fecha_utc.toordinal() - 1)):
            gid = schedule_index.get((candidata, home_key, away_key))
            if gid:
                fecha_match = candidata
                break

        if not gid:
            # SE ARCHIVA IGUAL (decision adjudicada 2026-09-20, opcion a).
            # El WARNING se conserva como observabilidad, pero ya NO implica
            # descarte. game_date sale de commence_time con la MISMA aritmetica
            # ET del matching — jamas de la fecha de captura, que pondria el
            # partido en el dia equivocado justo en los nocturnos.
            sin_match.append(
                f"{ev.get('away_team')} @ {ev.get('home_team')} ({fecha_utc.isoformat()})"
            )
            filas.extend(
                extract_h2h_rows(
                    ev, None, _et_game_date(ev.get("commence_time", "")) or fecha_utc,
                    snapshot_label, ts, matched=False,
                )
            )
            continue

        filas.extend(
            extract_h2h_rows(ev, gid, fecha_match, snapshot_label, ts, matched=True)
        )

    return filas, sin_match


# CLAVE DE IDEMPOTENCIA ENMENDADA (2026-09-20): game_id SALE de la clave.
# Con filas sin match, game_id es NULL y dos eventos distintos del mismo dia
# colisionarian en ese NULL, pisandose el uno al otro. api_event_id es la
# identidad NATIVA del dato y no puede faltar.
MERGE_KEYS: tuple[str, ...] = (
    "game_date", "api_event_id", "bookmaker", "snapshot_label",
)
