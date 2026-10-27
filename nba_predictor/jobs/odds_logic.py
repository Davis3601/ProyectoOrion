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
MARKET_SPREADS = "spreads"
MARKET_TOTALS = "totals"

SNAPSHOT_LABELS: tuple[str, ...] = ("publish", "evening", "late")

# D-ODDS-7: label de las ejecuciones MANUALES. No es un cuarto snapshot diario
# (los tres de arriba siguen cerrados): existe para que una captura de
# verificacion jamas pise por MERGE la fila de un snapshot programado — la
# clave incluye snapshot_label, asi que 'manual' vive en su propia fila y el
# analisis de CLV, que agrupa por los tres labels, no la ve.
MANUAL_LABEL = "manual"

# Clave del deporte en The Odds API (verificada en el paso 0 de la tarea).
SPORT_KEY = "basketball_nba"

# ---------------------------------------------------------------------------
# Mercados y presupuesto de creditos (D-ODDS-7)
# ---------------------------------------------------------------------------
# Coste de una llamada = mercados x regiones. Con los tres mercados y una
# region: 3 creditos (verificado 2026-10-27: x-requests-last = 3). Tres
# snapshots diarios x ~31 dias = ~279 de 500 al mes.
MARKETS_FULL: tuple[str, ...] = (MARKET_H2H, MARKET_SPREADS, MARKET_TOTALS)
MARKETS_H2H_ONLY: tuple[str, ...] = (MARKET_H2H,)

# Umbrales PRE-REGISTRADOS sobre x-requests-used (creditos consumidos en el
# periodo de la API). 400 = WARNING; 450 = se captura solo h2h, porque h2h es
# la evidencia del CLV y tiene prioridad sobre spreads/totals.
CREDITS_WARN_USED = 400
CREDITS_DEGRADE_USED = 450


class UnknownSnapshotLabel(ValueError):
    """El label no es uno de los pre-registrados."""


def validate_snapshot_label(label: str) -> str:
    """Los tres labels programados estan CERRADOS (D-ODDS-2): publish, evening,
    late. Se admite ademas MANUAL_LABEL (D-ODDS-7) para ejecuciones manuales.

    Se valida en la frontera y no mas adentro: un label libre acabaria como
    basura en una columna que luego el analisis agrupa, y nadie lo notaria
    hasta el analisis.
    """
    if label not in SNAPSHOT_LABELS and label != MANUAL_LABEL:
        raise UnknownSnapshotLabel(
            f"snapshot_label invalido: {label!r}. Validos: "
            f"{list(SNAPSHOT_LABELS) + [MANUAL_LABEL]}"
        )
    return label


def parse_quota(headers: dict[str, str] | None) -> tuple[int | None, int | None]:
    """Cabeceras de la API -> (x-requests-used, x-requests-remaining), o None.

    Las cabeceras HTTP no distinguen mayusculas; un valor ilegible es None, no
    cero: cero creditos usados es un dato, "no se sabe" es otro.
    """
    norm = {k.lower(): v for k, v in (headers or {}).items()}

    def _int(nombre: str) -> int | None:
        try:
            return int(float(norm[nombre]))
        except (KeyError, TypeError, ValueError):
            return None

    return _int("x-requests-used"), _int("x-requests-remaining")


def choose_markets(used: int | None) -> tuple[tuple[str, ...], str]:
    """Consumo del mes -> (mercados a pedir, nivel: 'ok' | 'warning' | 'degradado').

    Consumo DESCONOCIDO -> solo h2h (nivel 'degradado'): sin saber cuanto queda,
    se protege la evidencia del CLV antes que spreads/totals. Gastar 3 creditos
    a ciegas podria dejar sin cuota a los h2h de los dias siguientes.
    """
    if used is None or used >= CREDITS_DEGRADE_USED:
        return MARKETS_H2H_ONLY, "degradado"
    if used >= CREDITS_WARN_USED:
        return MARKETS_FULL, "warning"
    return MARKETS_FULL, "ok"


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
    # D-PRE-4: forma abreviada simétrica. El PDF vigente escribe
    # "LosAngelesLakers", pero D-EXP-1 registró "LALakers" en el corpus; un
    # alias de más no puede romper un match existente.
    "lalakers": "losangeleslakers",
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


def parse_commence(commence_time: Any) -> datetime | None:
    """commence_time de la API (ISO UTC con Z) -> datetime aware en UTC, o None."""
    try:
        dt = datetime.fromisoformat(str(commence_time).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else None


def exclude_started(
    events: list[dict[str, Any]],
    capture_ts: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    """(eventos aún no iniciados, descripción de los ya iniciados).

    market_odds es PRE-PARTIDO por definición (D-PRE-1 / bug 4). En octubre
    los snapshots evening (19:45 ET) y late (23:15 ET) caen después del tipoff
    de casi todos los partidos; si la API lista eventos en curso, el MERGE por
    clave sobrescribiría la odd previa de otro día con una odd EN VIVO y el
    análisis de CLV compararía contra un precio que no es de cierre. No se
    sabe todavía qué devuelve la API para eventos iniciados: en vez de esperar
    a verlo, es invariante — commence_time <= capture_ts NO se escribe jamás.
    La frontera exacta se excluye: en el instante del tipoff ya no es previa.

    Un commence_time ilegible NO se decide aquí (no se sabe si empezó): sigue
    al matching, que ya lo avisa y no lo escribe.
    """
    if capture_ts.tzinfo is None:
        raise ValueError("capture_ts debe llevar zona horaria (aware, UTC).")
    pendientes: list[dict[str, Any]] = []
    iniciados: list[str] = []
    for ev in events:
        inicio = parse_commence(ev.get("commence_time"))
        if inicio is not None and inicio <= capture_ts:
            iniciados.append(
                f"{ev.get('away_team')} @ {ev.get('home_team')} "
                f"(commence {inicio:%Y-%m-%d %H:%M}Z <= captura {capture_ts:%H:%M}Z)"
            )
            continue
        pendientes.append(ev)
    return pendientes, iniciados


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


SPREAD_TOTAL_COLUMNS: tuple[str, ...] = (
    "spread_home", "spread_home_price", "spread_away", "spread_away_price",
    "total_line", "over_price", "under_price",
)


def _num(valor: Any) -> float | None:
    """int/float de la API -> float. bool no es numero (True == 1 en Python)."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor)


def _market(bm: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((m for m in bm.get("markets", []) or [] if m.get("key") == key), None)


def extract_spreads(
    bm: dict[str, Any], home_key: str, away_key: str, contexto: str
) -> dict[str, float | None]:
    """Mercado spreads de UN bookmaker -> las cuatro columnas de spread.

    Mercado ausente = las cuatro en None, sin aviso (un bookmaker menor puede
    no publicarlo; el pre-registro lo admite). Mercado presente pero sin los
    dos lados completos (linea y precio numericos) = las cuatro en None CON
    WARNING: medio spread no es dato parcial util, es ruido.

    La linea se archiva tal cual la publica el bookmaker, con su signo. Que
    spread_home == -spread_away es lo esperado y lo fija un test sobre el
    payload real; si algun dia no se cumple se AVISA y se archiva igual — las
    dos columnas existen justo para no tener que elegir.
    """
    vacio: dict[str, float | None] = {
        "spread_home": None, "spread_home_price": None,
        "spread_away": None, "spread_away_price": None,
    }
    mercado = _market(bm, MARKET_SPREADS)
    if mercado is None:
        return vacio
    lados: dict[str, tuple[float | None, float | None]] = {}
    for out in mercado.get("outcomes", []) or []:
        lados[normalize_team(out.get("name", ""))] = (
            _num(out.get("point")), _num(out.get("price"))
        )
    casa, fuera = lados.get(home_key), lados.get(away_key)
    if casa is None or fuera is None or None in casa or None in fuera:
        _log.warning("Bookmaker %s con spreads incompleto para %s — spreads en NULL",
                     bm.get("key"), contexto)
        return vacio
    if casa[0] != -fuera[0]:
        _log.warning("Bookmaker %s con spreads asimetrico para %s (%s / %s) — archivado tal cual",
                     bm.get("key"), contexto, casa[0], fuera[0])
    return {
        "spread_home": casa[0], "spread_home_price": casa[1],
        "spread_away": fuera[0], "spread_away_price": fuera[1],
    }


def extract_totals(bm: dict[str, Any], contexto: str) -> dict[str, float | None]:
    """Mercado totals de UN bookmaker -> total_line, over_price, under_price.

    Misma regla que spreads: ausente = None sin aviso; incompleto = None con
    WARNING. Over y Under con lineas DISTINTAS tampoco caben en una sola
    columna total_line: None con WARNING en vez de elegir una en silencio.
    """
    vacio: dict[str, float | None] = {"total_line": None, "over_price": None, "under_price": None}
    mercado = _market(bm, MARKET_TOTALS)
    if mercado is None:
        return vacio
    lados = {
        str(out.get("name", "")).strip().lower(): (_num(out.get("point")), _num(out.get("price")))
        for out in mercado.get("outcomes", []) or []
    }
    over, under = lados.get("over"), lados.get("under")
    if over is None or under is None or None in over or None in under:
        _log.warning("Bookmaker %s con totals incompleto para %s — totals en NULL",
                     bm.get("key"), contexto)
        return vacio
    if over[0] != under[0]:
        _log.warning("Bookmaker %s con lineas Over/Under distintas para %s (%s / %s) — "
                     "totals en NULL", bm.get("key"), contexto, over[0], under[0])
        return vacio
    return {"total_line": over[0], "over_price": over[1], "under_price": under[1]}


def extract_h2h_rows(
    event: dict[str, Any],
    game_id: str | None,
    game_date: date,
    snapshot_label: str,
    capture_ts: datetime,
    matched: bool,
) -> list[dict[str, Any]]:
    """Una fila por bookmaker del evento: h2h OBLIGATORIO, spreads y totals
    en la misma fila cuando existen (D-ODDS-7).

    La fila sigue anclada al h2h: es la evidencia del CLV y la que define la
    clave. Un bookmaker con spreads pero SIN h2h se omite igual que antes, y
    `market` sigue valiendo 'h2h' porque nombra el mercado de las columnas de
    precio home/away_price_*; las columnas de spread y total se describen solas
    y quedan en NULL cuando el mercado no vino (o no se pidio).

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
            # D-PRE-1 / bug 4: el tipoff de la propia API, persistido para que
            # el análisis pueda verificar capture_ts < commence_time fila a fila.
            "commence_time": parse_commence(event.get("commence_time")),
            # D-ODDS-7: spreads y totals del MISMO bookmaker, NULL si no vinieron.
            **extract_spreads(bm, home_key, away_key, f"{away} @ {home}"),
            **extract_totals(bm, f"{away} @ {home}"),
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
