"""
Núcleo del endpoint "predicciones del día" — 13e-2 (Decisión 13e-2.1).

Produce el payload {message, data} del día. Sin servidor web todavía:
el entrypoint FastAPI vive en api/app.py (tarea separada).

CONTRATO B-CON-DATA (Decisión 13e-2.1):
    message: texto Telegram FINAL, construido por format_daily_message() bajo
             unit tests que fijan el formato exacto. n8n transporta el mensaje
             sin tocarlo.
    data:    lista de GamePrediction para observabilidad y predictions_log.

DÍAS DEGRADADOS (Decisión 13e-2.5):
    1. Sin partidos  → games=[], mensaje de descanso (heartbeat).
    2. Feed caído    → FEED_DOWN en todos los partidos + línea de advertencia global.
    3. NYS al invocar → flag NYS por equipo afectado; otros partidos no se tocan.
    4. Fallo duro (modelo no carga / schedule inaccesible) → excepción ruidosa;
       NUNCA payload incompleto publicado en silencio.

FRONTERA token-PDF → equipo del schedule:
    La normalización simétrica de injury_report (_normalize_name) se aplica a
    AMBOS lados de la comparación. El endpoint NUNCA compara strings crudos.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nba_predictor.ingestion.future_schedule import ScheduledGame
    from nba_predictor.storage.base import DataStore

_log = logging.getLogger(__name__)

# Meses abreviados en español para el mensaje de Telegram.
_MESES_ES: dict[int, str] = {
    1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
    7: "jul", 8: "ago", 9: "sep", 10: "oct", 11: "nov", 12: "dic",
}


# ---------------------------------------------------------------------------
# Tipos públicos
# ---------------------------------------------------------------------------


class AvailabilityFlag(str, Enum):
    OK        = "ok"
    NYS       = "nys"       # Not Yet Submitted — disponibilidad sin confirmar
    FEED_DOWN = "feed_down" # Feed de injury report no disponible hoy


@dataclass
class PlayerHighlight:
    """Un jugador destacado de un partido, en forma publicable.

    Contrato adjudicado en D-EXP-4: HECHOS DESCRIPTIVOS. Los números son la
    mediana y el rango observados en los últimos partidos jugados — no una
    predicción con cobertura prometida. Ninguna palabra del mensaje puede
    implicar probabilidad de cobertura: la medida real de [min,max] es 85.6%
    en puntos a nivel global y 83.4% en titulares, que es justo el subgrupo
    del que salen los destacados.
    """

    player_name: str
    team: str                       # "home" | "away"
    pts_median: float
    pts_min: float
    pts_max: float
    reb_median: float
    ast_median: float
    games_in_window: int            # partidos jugados que alimentan la ventana
    status: str | None = None       # None | "Q" (Questionable; Out y Doubtful no entran)
    fg3m_median: float | None = None
    is_shooting_specialist: bool = False
    # Solo los especialistas muestran triples, y solo la mediana: en stats de
    # soporte corto el rango es poco informativo (Desviación 2 de D-EXP-4).
    player_id: int | None = None
    # Identidad para player_predictions_log. No aparece en el mensaje: el
    # nombre es para el lector y el id es para el JOIN de grading, igual que
    # absences_applied guarda ids mientras el mensaje muestra nombres.
    rank: int | None = None
    # D-PRE-9: puesto dentro de su equipo (1|2) por minutos rolling. Va al log;
    # el mensaje no lo imprime (el orden de las líneas ya lo dice). None en
    # objetos construidos a mano = 1 implícito, como las filas viejas del log.


@dataclass
class GamePrediction:
    """Predicción para un partido individual."""

    home_tricode: str
    away_tricode: str
    game_date: str                      # YYYY-MM-DD
    probability_home: float             # P(local gana) ∈ (0, 1), redondeado a 4 decimales
    home_absences: list[str]            # nombres display de ausentes del local (solo Out)
    away_absences: list[str]            # nombres display de ausentes del visitante (solo Out)
    availability_flag: AvailabilityFlag
    model_version: str
    nys_tricodes: list[str] = field(default_factory=list)
    # Tricodes de equipos con NYS para target_date. Solo relevante cuando
    # availability_flag == NYS. Vacío para OK y FEED_DOWN.
    tip_off_cdmx: str | None = None
    # Hora del tip-off en hora del centro de México, formato "H:MM CDMX".
    # None si el schedule no provee hora (offseason, tests sin tip-off).
    game_id: str | None = None
    # game_id del schedule CDN. Clave del JOIN de grading en predictions_log
    # (13e-2.4) — el log se cruza contra `games` por game_id, jamás por nombres.
    home_absence_ids: list[int] = field(default_factory=list)
    away_absence_ids: list[int] = field(default_factory=list)
    # player_ids Out efectivamente aplicados al cálculo de availability_diff.
    # Los campos *_absences de arriba son su versión display (para el mensaje);
    # estos son la EVIDENCIA que va a predictions_log (ids, no nombres).
    players: list["PlayerHighlight"] = field(default_factory=list)
    players_data_available: bool = False
    # Degradación declarada (herencia 13e-2.5): sin datos de jugador la sección
    # NO se emite y el flag lo declara en `data`. Jamás una sección vacía, que
    # sería ambigua entre "no hay datos" y "no hay destacados". El default es
    # False a propósito: mientras el cableado no exista (Tarea C), el mensaje
    # vigente se reproduce byte a byte.
    started_at_publish: bool | None = None
    # D-PRE-2: True si el tip-off REAL (gameDateTimeUTC) ya ocurrió al servir.
    # El mensaje lo DECLARA (degradación declarada, 13e-2.5): la predicción se
    # publica igual, pero no es pre-partido. None = no determinable (sin reloj
    # inyectado, sin hora UTC o con hora TBD): sin marcador. El default None
    # reproduce el mensaje vigente byte a byte.


@dataclass
class DailyResult:
    """Resultado diario completo: predicciones + metadatos de degradación."""

    target_date: str                    # YYYY-MM-DD
    games: list[GamePrediction]
    feed_down: bool
    feed_down_reason: str | None        # descripción del error si feed_down
    model_version: str | None           # None solo si games está vacía (sin partidos)
    tipoff_tbd_count: int = 0
    # D-PRE-2: partidos del día con hora TBD (NBA Cup sin definir). No llevan
    # marcador de "iniciado" porque su hora no se conoce; el contador lo declara.
    unresolved_team_count: int = 0
    unresolved_team_tokens: list[str] = field(default_factory=list)
    # D-PRE-4: equipos del PDF que no resolvieron contra el catálogo. Su
    # partido se publica DECLARADO ("disponibilidad sin confirmar" en ambos
    # equipos, el mismo marcador del caso NYS de 13e-2.5): se sabe que faltan
    # filas de ese partido pero no de cuál de los dos lados.
    players_trimmed: bool = False
    # D-PRE-9: True si el mensaje 2 con dos destacados por equipo excedía el
    # presupuesto y se recortó a uno por equipo. Recorte DECLARADO: el mensaje
    # lo dice en una línea y el log registra solo lo publicado.
    highlights_mode: str | None = None
    highlights_prev_season: str | None = None
    # D-PRE-11: "regimen" (primeros 21 días: centro C3, rango [mín, máx] sin
    # etiqueta) | "r1" (después: centro B0, rango de puntos R1 al 80%) | None
    # (sin destacados calculados, o resultado construido a mano: título previo).
    highlights_truncated: str | None = None
    # D-PRE-9, dos guardas en escalera: None = mensaje completo; "top1" =
    # recortado a uno por equipo (> 3 800); "omitted" = ni con uno por equipo
    # cabe (> 4 000): el mensaje 2 se sustituye por una sola línea que lo
    # declara y player_predictions_log no registra nada, porque no se
    # publicó ningún destacado. Los players calculados siguen en data.


# Interruptor manual del feed (D-PRE-4). La env var se lee en el BORDE
# (server.py); aquí solo viven el nombre y la razón literal que se publica.
FORCE_FEED_DOWN_ENV = "NBA_PREDICTOR_FORCE_FEED_DOWN"
FORCE_FEED_DOWN_REASON = f"feed desactivado manualmente ({FORCE_FEED_DOWN_ENV})"


class _CatalogoVacio(RuntimeError):
    """Centinela interno: el catálogo de nombres llegó vacío.

    No se propaga nunca fuera de build_daily_predictions; existe para que
    la rama de degradación comparta EXACTAMENTE el camino del feed caído
    en vez de duplicar su lógica y arriesgarse a que diverjan.
    """


# ---------------------------------------------------------------------------
# Función de orquestación principal
# ---------------------------------------------------------------------------


def build_daily_predictions(
    target_date: date,
    store: "DataStore",
    *,
    season: str,
    scheduled_games: "list[ScheduledGame] | None" = None,
    version_name: str | None = None,
    player_map: dict[int, str] | None = None,
    max_injury_requests: int = 20,
    save_injury_raw: bool = True,
    now_et: datetime | None = None,
    now_cdmx: datetime | None = None,
    force_feed_down: bool = False,
) -> DailyResult:
    """
    Orquesta el pipeline completo para un día: schedule → ausencias → predicciones.

    Parameters
    ----------
    target_date       : Fecha a predecir.
    store             : DataStore activo (local o cloud).
    season            : Temporada CDN, ej. "2026-27".
    scheduled_games   : Lista de partidos del día (para tests o CDN override).
                        Si None, llama a fetch_future_schedule filtrado a target_date.
    version_name      : Versión del modelo a usar. None → la más reciente local.
    player_map        : dict player_id → nombre display. Alimenta el NameIndex
                        del injury report Y los nombres de los destacados.
                        VACÍO O None = DEGRADACIÓN DECLARADA (D-PROD-1d): se
                        trata como feed caído, porque sin catálogo ninguna
                        ausencia puede resolverse y el resultado parecería un
                        día sin bajas. Nunca es un default inocuo.
    max_injury_requests: Presupuesto HEAD de descubrimiento del PDF.
    save_injury_raw   : Persistir el PDF crudo via store.save_raw_injury_report().
    now_et            : Hora actual (aware) inyectada por el borde (server.py).
                        Decide desde qué corte del PDF se sondea (D-PRE-1).
                        None = lista histórica desde 11_45PM, correcta solo
                        para días ya terminados.
    now_cdmx          : Hora actual (aware) inyectada por el borde (D-PRE-2).
                        Decide started_at_publish de cada partido contra su
                        tip-off REAL. None = no se determina (sin marcador).
    force_feed_down   : Interruptor manual (D-PRE-4), leído por el borde de la
                        env var NBA_PREDICTOR_FORCE_FEED_DOWN. True = no se
                        descubre ni se parsea el PDF y el feed se declara caído
                        con razón literal. Para el caso en que el cotejo humano
                        encuentre filas mal atribuidas: publicar declarando es
                        mejor que publicar mal.

    Returns
    -------
    DailyResult con la lista de GamePrediction y metadatos de degradación.

    Raises
    ------
    RuntimeError / FileNotFoundError si el schedule es inaccesible o el modelo
    no carga (fallo duro — escenario 4 de Decisión 13e-2.5).
    """
    player_map = player_map or {}

    # ── 1. Schedule ── (fallo duro si raises)
    games = _resolve_schedule(target_date, season, scheduled_games)

    if not games:
        _log.info("Sin partidos para %s — día de descanso.", target_date)
        return DailyResult(
            target_date=target_date.isoformat(),
            games=[],
            feed_down=False,
            feed_down_reason=None,
            model_version=None,
        )

    # ── 2. Teams catalog ──
    teams_df = store.load_teams()
    norm_team_map = _build_norm_team_map(teams_df)

    # ── 3. Injury report (best-effort) ──
    absences_by_tid: dict[int, list[int]] = {}
    nys_team_ids: set[int] = set()
    statuses_by_tid: dict[int, dict[int, str]] = {}
    feed_down = False
    feed_down_reason: str | None = None
    unresolved: list[tuple[str, str]] = []

    # ── 3.-1 Interruptor manual (D-PRE-4): manda sobre todo lo demás ──
    if force_feed_down:
        feed_down = True
        feed_down_reason = FORCE_FEED_DOWN_REASON
        _log.warning("Feed de injury report DESACTIVADO MANUALMENTE (%s).", FORCE_FEED_DOWN_ENV)

    # ── 3.0 Catálogo de nombres: sin él, el injury report es papel mojado ──
    # REGLA ADJUDICADA (D-PROD-1d): un player_map vacío se trata como FEED
    # CAÍDO (13e-2.5 caso 2), no como "nadie lesionado". El NameIndex nacería
    # vacío y ninguna ausencia haría match, así que el resultado tendría el
    # aspecto perfectamente sano de un día sin bajas — que es exactamente la
    # mentira silenciosa que el bug 2 de D-PROD-1c mantuvo viva sin un solo
    # error en los logs. Se predice, se declara, y se grita en los logs.
    if not player_map and not feed_down:
        feed_down = True
        feed_down_reason = (
            "Catálogo de nombres de jugador vacío: ninguna ausencia podría "
            "resolverse, así que el reporte de lesiones se declara no disponible."
        )
        _log.error(
            "player_map VACÍO — se declara feed caído. Sin catálogo no hay "
            "matching de ausencias posible (ver tabla players / D-PROD-1d)."
        )

    try:
        if feed_down:
            raise _CatalogoVacio(feed_down_reason)
        absences_by_tid, nys_team_ids, statuses_by_tid, unresolved = _fetch_absences(
            target_date=target_date,
            store=store,
            norm_team_map=norm_team_map,
            player_map=player_map,
            max_requests=max_injury_requests,
            save_raw=save_injury_raw,
            now_et=now_et,
        )
    except _CatalogoVacio:
        pass  # feed_down y su razón ya quedaron fijados arriba
    except Exception as exc:
        feed_down = True
        feed_down_reason = str(exc)
        _log.warning("Feed de injury report no disponible: %s", exc)

    # ── 3.1 Equipos sin resolver (D-PRE-4): degradación DECLARADA del partido ──
    # Antes era un WARNING y las filas se perdían: el partido salía con
    # "Bajas: –" de aspecto sano. Ahora los dos equipos de ese matchup llevan
    # "disponibilidad sin confirmar" (marcador del caso NYS): se sabe que faltan
    # filas del partido, no de qué lado, y declarar de más es mejor que callar.
    matchups_sin_resolver = {mu for _, mu in unresolved if mu}
    for sg in games:
        if f"{sg.away_tricode}@{sg.home_tricode}" in matchups_sin_resolver:
            nys_team_ids = nys_team_ids | {sg.home_team_id, sg.away_team_id}
    unresolved_tokens = sorted({tok for tok, _ in unresolved})

    # ── 4. Modelo (fallo duro si no carga) ──
    resolved_version = version_name or _discover_latest_version()
    pipeline, _ = store.load_model(resolved_version)

    # ── 5. Predicciones por partido ──
    game_preds: list[GamePrediction] = []
    for sg in games:
        gp = _predict_one(
            sg=sg,
            pipeline=pipeline,
            version=resolved_version,
            absences_by_tid=absences_by_tid,
            nys_team_ids=nys_team_ids,
            feed_down=feed_down,
            player_map=player_map,
        )
        gp.started_at_publish = started_at_publish(sg, now_cdmx)
        game_preds.append(gp)

    # ── 6. Destacados del mensaje 2 (D-PROD-1c) ──
    # BEST-EFFORT, y no por comodidad: la misión del endpoint es la predicción
    # (precedente cerrado 2026-08-25/26 para el snapshot y para predictions_log).
    # Un fallo aquí deja players_data_available=False, un WARNING, y el mensaje 1
    # servido completo e intacto.
    destacados = _attach_highlights(
        store=store,
        target_date=target_date,
        scheduled=games,
        game_preds=game_preds,
        statuses_by_tid=statuses_by_tid,
        player_map=player_map,
        season=season,
    )
    # Best-effort también aquí: cualquier retorno no esperado = sin modo (título previo).
    modo_destacados, previa_destacados = destacados if destacados else (None, None)

    result = DailyResult(
        target_date=target_date.isoformat(),
        games=game_preds,
        feed_down=feed_down,
        feed_down_reason=feed_down_reason,
        model_version=resolved_version,
        tipoff_tbd_count=count_tipoff_tbd(games),
        unresolved_team_count=len(unresolved_tokens),
        unresolved_team_tokens=unresolved_tokens,
        highlights_mode=modo_destacados,
        highlights_prev_season=previa_destacados,
    )
    # ── 7. Presupuesto de longitud del mensaje 2 (D-PRE-9) ──
    # Antes de devolver: el recorte cambia gp.players, y el log de destacados
    # se construye de ESTE resultado — registra lo publicado, no lo calculado.
    apply_players_length_budget(result)
    return result


def apply_players_length_budget(
    result: DailyResult, limit: int | None = None
) -> bool:
    """Si el mensaje 2 excede el presupuesto, recorta a UN destacado por equipo.

    Pre-registro D-PRE-9: más de 3 800 caracteres → recorte DECLARADO a top 1
    ese día, con una línea que lo dice. Se conserva rank 1 (o rank None, que
    es 1 implícito). Devuelve True si recortó. Modifica result in situ.

    SEGUNDA GUARDA (adjudicada por Antonio, D-PRE-9): si incluso con uno por
    equipo el mensaje excede PLAYERS_MESSAGE_HARD_LIMIT (4 000), el mensaje 2
    se SUSTITUYE por una línea que declara la omisión y highlights_truncated
    pasa a "omitted". Nunca se envía a Telegram algo que pueda rebotar contra
    su límite duro (4 096): un Send fallido es un silencio, la omisión es
    degradación declarada (13e-2.5).
    """
    limit = PLAYERS_MESSAGE_BUDGET if limit is None else limit
    if len(format_players_message(result)) <= limit:
        return False
    for gp in result.games:
        gp.players = [p for p in gp.players if p.rank in (None, 1)]
    result.players_trimmed = True
    result.highlights_truncated = "top1"
    largo = len(format_players_message(result))
    _log.warning(
        "Mensaje 2 recortado a un destacado por equipo por longitud (D-PRE-9): %d caracteres",
        largo,
    )
    if largo > PLAYERS_MESSAGE_HARD_LIMIT:
        result.highlights_truncated = "omitted"
        _log.error(
            "Mensaje 2 OMITIDO por longitud (D-PRE-9): %d > %d aun con uno por equipo",
            largo, PLAYERS_MESSAGE_HARD_LIMIT,
        )
    return True


def count_tipoff_tbd(games: "list[ScheduledGame]") -> int:
    """Partidos con hora TBD (D-PRE-2): sin marcador posible, se declaran contados."""
    return sum(1 for sg in games if getattr(sg, "tip_off_tbd", False) is True)


def started_at_publish(sg: "ScheduledGame", now: datetime | None) -> bool | None:
    """¿El partido ya empezó en el instante de servir? (D-PRE-2)

    Compara el tip-off REAL (gameDateTimeUTC, aware) contra la hora inyectada
    por el borde; la función jamás lee el reloj. La frontera exacta cuenta como
    iniciado: en el minuto del tip-off la predicción ya no es pre-partido.

    None cuando no se puede determinar — sin reloj, sin hora UTC, u hora TBD —
    y entonces el mensaje no lleva marcador: un "iniciado" inventado sobre una
    hora de relleno sería peor que no decir nada.
    """
    if now is None:
        return None
    if now.tzinfo is None:
        raise ValueError("now_cdmx debe llevar zona horaria (aware).")
    if getattr(sg, "tip_off_tbd", False) is True:
        return None
    tip = getattr(sg, "tip_off_utc", None)
    if not isinstance(tip, datetime) or tip.tzinfo is None:
        return None
    return tip <= now


# ---------------------------------------------------------------------------
# Formato del mensaje Telegram
# ---------------------------------------------------------------------------


_DISCLAIMER = "Predicciones estadísticas — no constituyen consejo de apuestas."

# Límite duro de un mensaje de texto de Telegram, en caracteres UTF-8.
TELEGRAM_MAX_CHARS = 4096

# Presupuesto del mensaje 2 (pre-registro D-PRE-9): por encima, recorte
# declarado a un destacado por equipo. 296 caracteres bajo el límite duro.
PLAYERS_MESSAGE_BUDGET = 3800

# Segunda guarda (D-PRE-9): por encima de esto, ni recortado se envía; el
# mensaje 2 es una sola línea que lo declara. 96 bajo el límite duro.
PLAYERS_MESSAGE_HARD_LIMIT = 4000
_PLAYERS_OMITTED_LINE = "🏀 Destacados · {fecha}: omitidos hoy por longitud del día."

# Línea del recorte declarado (D-PRE-9), bajo el título: el lector sabe antes
# de leer que hoy hay uno por equipo y por qué.
_PLAYERS_TRIMMED_LINE = "(un destacado por equipo hoy por longitud)"

# Título del SEGUNDO mensaje. Declara la ventana UNA vez y de forma factual:
# no dice "esperado", "proyectado" ni nada que prometa cobertura (D-EXP-4).
_PLAYERS_TITLE = "🏀 Destacados · {fecha} (últimos 10 partidos)"

# D-PRE-11: títulos por modo. El "80%" solo aparece fuera del régimen, donde el
# rango de puntos es R1 y su cobertura MEDIDA lo respalda (D-EXP-7: 79.0-79.5%
# por temporada; regla de D-EXP-4). En régimen el rango es [mín, máx], sin
# etiqueta de cobertura.
_PLAYERS_TITLE_REGIMEN = "🏀 Destacados · {fecha} (inicio de temporada: mezcla con {prev})"
_PLAYERS_TITLE_R1 = "🏀 Destacados · {fecha} (últimos 10 partidos · rango de puntos al 80%)"

# Marca editorial de D-EXP-1, una vez por partido (no por jugador): el peso
# medido de Questionable es ~0.48. Es el destino que la puerta de D-EXP-2 le
# dio a los pesos — contenido honesto, sin que el modelo los use.
_Q_LEGEND = "Q = cuestionable: históricamente ~50% de los Q juegan"

# Ventana declarada en el encabezado. Un jugador con menos partidos lleva
# su propio sufijo en vez de heredar una afirmación que no le aplica.
_PLAYERS_WINDOW = 10
_FEED_DOWN_MSG = (
    "⚠️ Reporte de lesiones no disponible — predicciones sin ajuste de bajas de hoy."
)
# D-PRE-2: marcador de partido ya iniciado al publicar. Texto congelado en test.
_STARTED_MSG = "⏱ Iniciado al publicar · predicción sin información del partido en curso"


def format_daily_message(result: DailyResult) -> str:
    """
    Genera el texto FINAL del canal de Telegram a partir de un DailyResult.

    Es una función pura: misma entrada → mismo texto.
    Cada rama tiene su unit test que fija el output exacto (Decisión 13e-2.1).

    Formato (auditado contra los 5 escenarios de Decisión 13e-2.5):

        🏀 Predicciones NBA · 15 oct 2026

        LAL @ BOS · 19:30 CDMX
        BOS 67% — LAL 33%
        Bajas BOS: Jaylen Brown
        Bajas LAL: –

        GSW @ MIA
        MIA 54% — GSW 46%
        Bajas MIA: –
        ⚠️ Disponibilidad GSW sin confirmar

        Predicciones estadísticas — no constituyen consejo de apuestas.
        Modelo: v1_logistic_bclean_2026-08-22

    Descanso:
        🏀 Predicciones NBA · 15 oct 2026
        Sin partidos hoy.
    """
    d = date.fromisoformat(result.target_date)
    date_str = f"{d.day} {_MESES_ES[d.month]} {d.year}"
    header = f"🏀 Predicciones NBA · {date_str}"

    if not result.games:
        return f"{header}\nSin partidos hoy."

    lines: list[str] = [header]

    if result.feed_down:
        lines.append(_FEED_DOWN_MSG)

    for gp in sorted(result.games, key=_tip_sort_key):
        lines.append("")

        # Encabezado del partido: VISITANTE @ LOCAL [· HH:MM CDMX]
        matchup = f"{gp.away_tricode} @ {gp.home_tricode}"
        if gp.tip_off_cdmx:
            matchup += f" · {gp.tip_off_cdmx}"
        lines.append(matchup)
        # D-PRE-2: declarado justo bajo el encabezado, antes del número, para
        # que el lector sepa qué está leyendo antes de leerlo. `is True` y no
        # truthiness: None (no determinable) no enciende nada.
        if gp.started_at_publish is True:
            lines.append(_STARTED_MSG)

        home_pct = round(gp.probability_home * 100)
        away_pct = 100 - home_pct
        lines.append(f"{gp.home_tricode} {home_pct}% — {gp.away_tricode} {away_pct}%")

        if gp.availability_flag == AvailabilityFlag.FEED_DOWN:
            pass  # sin líneas de bajas — el disclaimer global ya cubre el escenario

        else:
            # Local
            if gp.home_tricode in gp.nys_tricodes:
                lines.append(f"⚠️ Disponibilidad {gp.home_tricode} sin confirmar")
            else:
                names = ", ".join(gp.home_absences) if gp.home_absences else "–"
                lines.append(f"Bajas {gp.home_tricode}: {names}")

            # Visitante
            if gp.away_tricode in gp.nys_tricodes:
                lines.append(f"⚠️ Disponibilidad {gp.away_tricode} sin confirmar")
            else:
                names = ", ".join(gp.away_absences) if gp.away_absences else "–"
                lines.append(f"Bajas {gp.away_tricode}: {names}")

    lines.append("")
    lines.append(_DISCLAIMER)
    lines.append(f"Modelo: {result.model_version}")

    return "\n".join(lines)


def format_players_message(result: DailyResult) -> str:
    """Genera el SEGUNDO mensaje de Telegram: los destacados del día.

    Función pura, hermana de format_daily_message, con su formato igualmente
    congelado bajo tests (13e-2.1). Devuelve "" cuando ningún partido trae
    datos de jugador; n8n no dispara el segundo Send en ese caso, y el
    silencio no es ambiguo porque el mensaje 1 ya salió (heartbeat).

    POR QUÉ UN SEGUNDO MENSAJE Y NO UNA SECCIÓN: medido en D-PROD-1, cuatro
    destacados por partido en el mensaje único daban 6 225 caracteres contra
    el límite duro de 4 096 de Telegram. Ningún formato de mensaje único cabe
    con 15 partidos; el mensaje 1 (validado en producción desde agosto) no se
    toca, y los destacados viajan aparte con un destacado por equipo.

    Formato:

        🏀 Destacados · 21 oct 2026 (últimos 10 partidos)

        LAL @ BOS
        • Jayson Tatum (BOS): 27 pts (14-41) · 8 reb · 5 ast
        • Luka Doncic (LAL) (Q): 31 pts (19-49) · 9 reb · 8 ast

        Q = cuestionable: históricamente ~50% de los Q juegan
    """
    bloques = [b for b in (_player_block(gp)
                           for gp in sorted(result.games, key=_tip_sort_key)) if b]
    if not bloques:
        return ""

    d = date.fromisoformat(result.target_date)
    fecha = f"{d.day} {_MESES_ES[d.month]} {d.year}"
    if result.highlights_truncated == "omitted":
        return _PLAYERS_OMITTED_LINE.format(fecha=fecha)
    if result.highlights_mode == "regimen" and result.highlights_prev_season:
        titulo = _PLAYERS_TITLE_REGIMEN.format(fecha=fecha, prev=result.highlights_prev_season)
    elif result.highlights_mode == "r1":
        titulo = _PLAYERS_TITLE_R1.format(fecha=fecha)
    else:
        titulo = _PLAYERS_TITLE.format(fecha=fecha)
    lines: list[str] = [titulo]
    if result.players_trimmed:
        lines.append(_PLAYERS_TRIMMED_LINE)
    for bloque in bloques:
        lines.append("")
        lines.extend(bloque)

    # La leyenda va UNA vez al pie del mensaje, no por partido: con un
    # destacado por equipo, repetirla en cada bloque costaría más que la
    # propia sección.
    if any(p.status == "Q" for gp in result.games if gp.players_data_available
           for p in gp.players):
        lines.append("")
        lines.append(_Q_LEGEND)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _tip_sort_key(g: GamePrediction) -> tuple:
    """Orden por tip-off ascendente; los partidos sin hora van al final.

    Parsea la hora numéricamente para que "7:30 CDMX" < "17:30 CDMX" — el
    orden alfabético los invertiría. Compartida por los dos mensajes: el
    lector ve los partidos en el mismo orden en ambos.
    """
    if not g.tip_off_cdmx:
        return (1, 0, 0)
    try:
        h, m = g.tip_off_cdmx.split(" ")[0].split(":")
        return (0, int(h), int(m))
    except Exception:
        return (1, 0, 0)


def _round_half_up(x: float) -> int:
    """Redondeo a entero con .5 SIEMPRE hacia arriba.

    No se usa round() de Python, que redondea al par: round(10.5) == 10 pero
    round(11.5) == 12. El caso no es exótico aquí — es el caso COMÚN: la
    mediana de 10 partidos es el promedio de los dos centrales, así que los
    .5 exactos abundan, y un lector vería 10.5 -> 10 junto a 11.5 -> 12 sin
    explicación posible.
    """
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def _format_player(p: "PlayerHighlight", tricode: str) -> str:
    """Una línea de jugador del SEGUNDO mensaje. Formato congelado (13e-2.1).

        • Nombre (LAL) (Q): 24 pts (11-35) · 7 reb · 5 ast · 3 3P (últimos 8)

    El TRICODE es obligatorio: con dos jugadores de equipos distintos bajo el
    mismo encabezado de partido, el orden no basta para saber quién es de
    quién — la ambigüedad que D-PROD-1 declaró sin resolver y que aquí muere.

    El sufijo de ventana solo aparece cuando la ventana NO es de 10: afirmar
    "últimos 10" con 8 partidos sería exactamente la exageración que D-EXP-4
    prohibió.
    """
    marca = f"{p.player_name} ({tricode}) (Q)" if p.status else f"{p.player_name} ({tricode})"
    partes = [
        f"• {marca}: {_round_half_up(p.pts_median)} pts "
        f"({_round_half_up(p.pts_min)}-{_round_half_up(p.pts_max)})",
        f"{_round_half_up(p.reb_median)} reb",
        f"{_round_half_up(p.ast_median)} ast",
    ]
    if p.is_shooting_specialist and p.fg3m_median is not None:
        partes.append(f"{_round_half_up(p.fg3m_median)} 3P")
    linea = " · ".join(partes)
    if p.games_in_window < _PLAYERS_WINDOW:
        linea += f" (últimos {p.games_in_window})"
    return linea


def _player_block(gp: GamePrediction) -> list[str]:
    """Bloque de destacados de UN partido, o nada.

    Sin datos disponibles (o sin jugadores) el bloque se omite por completo.
    La degradación NO se declara aquí y no es un olvido: el mensaje 1 ya la
    declaró en su sitio y el flag viaja en `data.players_data_available`;
    repetirla sería ruido, y un bloque vacío sería justo la ambigüedad que
    13e-2.5 prohíbe.

    Orden: local primero y visitante después — el mismo orden que ya siguen
    las líneas de bajas del mensaje 1.
    """
    if not gp.players_data_available or not gp.players:
        return []

    locales = [p for p in gp.players if p.team == "home"]
    visitantes = [p for p in gp.players if p.team != "home"]

    lineas = [f"{gp.away_tricode} @ {gp.home_tricode}"]
    lineas.extend(_format_player(p, gp.home_tricode) for p in locales)
    lineas.extend(_format_player(p, gp.away_tricode) for p in visitantes)
    return lineas


def _et_to_cdmx_str(dt_et: datetime) -> str:
    """Convierte un datetime ET (naive o aware) a 'H:MM CDMX'.

    Mexico City es permanentemente UTC-6 desde que abolió el horario de verano
    en abril 2023. El código usa zoneinfo para que el offset ET (EST/EDT) sea
    calculado correctamente por el sistema operativo.
    """
    from zoneinfo import ZoneInfo

    et_tz = ZoneInfo("America/New_York")
    cdmx_tz = ZoneInfo("America/Mexico_City")
    if dt_et.tzinfo is None:
        dt_et = dt_et.replace(tzinfo=et_tz)
    dt_cdmx = dt_et.astimezone(cdmx_tz)
    return f"{dt_cdmx.hour}:{dt_cdmx.strftime('%M')} CDMX"


def _resolve_schedule(
    target_date: date,
    season: str,
    scheduled_games: "list[ScheduledGame] | None",
) -> "list[ScheduledGame]":
    """Devuelve la lista de partidos para target_date."""
    if scheduled_games is not None:
        return scheduled_games

    from nba_predictor.ingestion.future_schedule import fetch_future_schedule
    all_future = fetch_future_schedule(season, from_date=target_date)
    return [g for g in all_future if g.game_date == target_date]


def resolve_team_id(token: str, norm_team_map: dict[str, int]) -> int | None:
    """team_id de un nombre de equipo tal como lo escribe el PDF, o None.

    D-PRE-4: la normalización es odds_logic.normalize_team, la ÚNICA del repo
    para equipos (alfanumérica + alias). Antes se usaba _normalize_name, que es
    para PERSONAS: convertía 'Philadelphia76ers' en 'philadelphiaers' y
    'LAClippers' en 'laclippers', ninguno coincidía con la tabla teams, y las
    bajas de PHI y LAC se descartaban con un WARNING.
    """
    from nba_predictor.jobs.odds_logic import normalize_team

    return norm_team_map.get(normalize_team(token))


def _build_norm_team_map(teams_df: Any) -> dict[str, int]:
    """Devuelve normalized_team_name → team_id para todas las franquicias.

    Normalización SIMÉTRICA con odds_logic.normalize_team (D-PRE-4): la misma
    función en las claves del catálogo ("Philadelphia 76ers") y en los tokens
    del PDF ("Philadelphia76ers"). Consultar SIEMPRE con resolve_team_id.
    """
    from nba_predictor.jobs.odds_logic import normalize_team

    return {
        normalize_team(str(row["name"])): int(row["team_id"])
        for _, row in teams_df.iterrows()
    }


def _fetch_absences(
    target_date: date,
    store: "DataStore",
    norm_team_map: dict[str, int],
    player_map: dict[int, str],
    max_requests: int,
    save_raw: bool,
    now_et: datetime | None = None,
) -> tuple[
    dict[int, list[int]], set[int], dict[int, dict[int, str]], list[tuple[str, str]]
]:
    """Descarga el PDF, parsea y devuelve ausencias, NYS, estatus y equipos sin
    resolver (token, matchup) para target_date.

    now_et (D-PRE-1): hora actual inyectada por el borde; decide desde qué corte
    se empieza a sondear. Sin ella, un request de las 15:00 ET buscaría desde
    11_45PM y jamás llegaría a los cortes de la tarde.

    A diferencia de get_absences() (que devuelve un AbsenceResult compacto),
    esta función filtra explícitamente por target_date en ambos player_rows y
    nys_entries, de modo que un PDF multi-fecha (hoy + mañana) no marque como
    NYS un partido de hoy cuando el equipo ya entregó para hoy y solo tiene
    NYS para mañana.

    Returns:
        (absences_by_tid, nys_team_ids, statuses_by_tid)
        absences_by_tid:  team_id → [player_ids Out] solo para target_date.
        nys_team_ids:     team_ids con NYS para target_date.
        statuses_by_tid:  team_id → {player_id: estatus} para target_date, con
                          TODOS los estatus (no solo Out). Los destacados del
                          mensaje 2 se filtran con ESTE dict y no con un
                          descubrimiento propio: un segundo snapshot del día
                          podría discrepar del que sustenta el mensaje 1, y el
                          canal publicaría dos verdades distintas del mismo PDF.

    Raises:
        RuntimeError si discover_latest_snapshot agota su presupuesto.
        requests.HTTPError / ConnectionError si download_snapshot falla.
    """
    from nba_predictor.ingestion.injury_report import (
        InjuryStatus,
        NameIndex,
        check_parse_coverage,
        count_name_lines,
        discover_latest_snapshot,
        download_snapshot,
        parse_pdf,
    )

    target_date_str = target_date.strftime("%Y-%m-%d")
    target_date_mdy = target_date.strftime("%m/%d/%Y")

    url, suffix = discover_latest_snapshot(
        target_date_str, max_requests=max_requests, now_et=now_et
    )
    pdf_bytes = download_snapshot(url)

    if save_raw:
        try:
            store.save_raw_injury_report(target_date_str, suffix, pdf_bytes)
        except Exception as exc:
            # Best-effort: la predicción ya tiene el dato; el archivo es secundario.
            # Un fallo de persistencia NO es degradación del dato servido (13e-2).
            _log.warning(
                "No se pudo persistir el snapshot del injury report (%s_%s): %s",
                target_date_str, suffix, exc,
            )

    player_rows, nys_entries = parse_pdf(pdf_bytes)
    # D-PRE-3: si el parser leyó mucho menos de lo que el documento contiene
    # (cambio de formato del PDF), ParseCoverageError sube como cualquier fallo
    # del feed y build_daily_predictions lo DECLARA como feed caído. Va DESPUÉS
    # de persistir el snapshot: el PDF que no se pudo leer queda archivado
    # para diagnosticarlo.
    check_parse_coverage(len(player_rows), count_name_lines(pdf_bytes))
    name_idx = NameIndex.from_player_map(player_map)

    absences_by_tid: dict[int, list[int]] = {}
    nys_team_ids: set[int] = set()
    statuses_by_tid: dict[int, dict[int, str]] = {}
    # D-PRE-4: (token del PDF, matchup) de cada equipo que no resolvió. Ya no
    # es un WARNING que se pierde: build_daily_predictions declara el partido.
    unresolved: list[tuple[str, str]] = []

    # Una sola pasada: Out alimenta las ausencias del modelo y TODOS los
    # estatus alimentan la elegibilidad de los destacados.
    for row in player_rows:
        if row.game_date != target_date_mdy:
            continue
        tid = resolve_team_id(row.team, norm_team_map)
        if tid is None:
            _log.error("Equipo del PDF sin match en catálogo: %r (%s)", row.team, row.matchup)
            unresolved.append((row.team, row.matchup or ""))
            continue
        pid = name_idx.match(row.player_name)
        if pid is None:
            continue
        statuses_by_tid.setdefault(tid, {})[pid] = row.status.value
        if row.status == InjuryStatus.OUT and pid not in absences_by_tid.get(tid, []):
            absences_by_tid.setdefault(tid, []).append(pid)

    # NYS solo para la fecha objetivo
    for entry in nys_entries:
        if entry.game_date != target_date_mdy:
            continue
        tid = resolve_team_id(entry.team, norm_team_map)
        if tid is not None:
            nys_team_ids.add(tid)
        else:
            # Un NYS no trae matchup: no se puede atribuir a un partido. Se
            # cuenta y se grita; con la normalización vigente (30/30) no ocurre.
            _log.error("Equipo NYS sin match en catálogo: %r", entry.team)
            unresolved.append((entry.team, ""))

    return absences_by_tid, nys_team_ids, statuses_by_tid, unresolved


def _attach_highlights(
    *,
    store: "DataStore",
    target_date: date,
    scheduled: "list[ScheduledGame]",
    game_preds: list[GamePrediction],
    statuses_by_tid: dict[int, dict[int, str]],
    player_map: dict[int, str],
    season: str | None = None,
) -> tuple[str | None, str | None]:
    """Rellena players / players_data_available de cada partido, in situ.

    D-PRE-11: devuelve (modo, temporada anterior) — modo "regimen" los primeros
    21 días desde el primer partido 002 de la temporada en `games` (si aún no
    hay ninguno, el inicio es el propio target_date: día 0), "r1" después; o
    (None, None) si no se calcularon destacados.

    D-PRE-8: carga también roster_signals de la temporada efectiva (season,
    la del payload CDN). Solo deciden el EQUIPO actual; las estadísticas
    siguen siendo de temporada regular. Si no cargan, se sigue con la regla de
    D-PRE-6 (equipo = última fila jugada) y un WARNING: la señal mejora el
    mensaje 2, su ausencia no lo invalida.

    El histórico se carga UNA vez para todos los partidos del día: los mismos
    métodos del DataStore que ya usa live_lookup, sin lector nuevo. Cargarlo
    por partido habría multiplicado por once una lectura de ~370k filas.

    Best-effort en DOS niveles, deliberadamente:
      - un fallo global (no carga el histórico) deja el día sin destacados y
        el mensaje 1 intacto;
      - un fallo de UN partido no contamina a los demás.
    El mensaje 2 simplemente omite lo que no pudo construir, y el flag lo
    declara en `data`.
    """
    import pandas as pd

    from nba_predictor.api.player_highlights import (
        build_game_highlights,
        derive_box_stats,
        highlight_mode,
        recent_seasons,
        season_of,
    )

    temporadas = recent_seasons(target_date)
    previa = recent_seasons(target_date, 2)[0]
    try:
        all_games = store.load_games()[["game_id", "game_date"]].copy()
        all_games["game_date"] = pd.to_datetime(all_games["game_date"])
        hist = all_games[all_games["game_date"] < pd.Timestamp(target_date)]

        # Se lee POR TEMPORADA y no la tabla entera: el filtro viaja a BigQuery
        # en vez de traerse 371k filas para descartar el 75% en memoria. Fue
        # justo ese exceso el que hizo que 1 GiB no alcanzara (OOM del
        # 2026-09-24); leer lo necesario es el fix, subir el límite era el
        # parche.
        trozos = [store.load_player_game_stats(season=s) for s in temporadas]
        pgs = pd.concat(trozos, ignore_index=True) if trozos else pd.DataFrame()
        if pgs.empty:
            _log.warning(
                "Sin destacados hoy: no hay player_game_stats en %s", temporadas
            )
            return None, None

        hist_pgs = pgs[pgs["game_id"].isin(set(hist["game_id"]))].merge(
            hist, on="game_id", validate="many_to_one"
        )
        hist_pgs = derive_box_stats(hist_pgs)
        _log.info(
            "Destacados: %d filas de historial en %s", len(hist_pgs), temporadas
        )
    except Exception as exc:
        _log.warning("Sin destacados hoy: no se pudo cargar el histórico: %s", exc)
        return None, None

    # Modo del día: el inicio de temporada se lee del calendario JUGADO (games),
    # la misma definición del experimento (D-EXP-7).
    actual = season_of(target_date)
    de_la_temporada = all_games[
        all_games["game_id"].astype(str).str.startswith("002")
        & all_games["game_date"].map(lambda d: season_of(d.date())).eq(actual)
    ]
    inicio_temp = de_la_temporada["game_date"].min()
    inicio = (min(inicio_temp.date(), target_date)
              if not pd.isna(inicio_temp) else target_date)
    modo = highlight_mode(target_date, inicio)
    _log.info("Destacados: modo %s (inicio de temporada %s)", modo, inicio)

    roster_signals = None
    try:
        from nba_predictor.jobs.roster_signals import season_window

        inicio, fin = season_window(season or season_of(target_date))
        senales = store.load_roster_signals(start_date=inicio, end_date=fin)
        # Anti-leakage del mismo tipo que el historial: solo lo visto ANTES
        # del día a publicar.
        roster_signals = senales[
            pd.to_datetime(senales["game_date"]) < pd.Timestamp(target_date)
        ]
        _log.info("Destacados: %d señales de plantilla en %s", len(roster_signals), season)
    except Exception as exc:
        _log.warning(
            "Señales de plantilla no disponibles (equipo = última fila jugada): %s", exc
        )

    por_game_id = {sg.game_id: sg for sg in scheduled}
    for gp in game_preds:
        sg = por_game_id.get(gp.game_id)
        if sg is None:
            continue
        try:
            gp.players = build_game_highlights(
                hist_pgs,
                home_team_id=sg.home_team_id,
                away_team_id=sg.away_team_id,
                statuses_by_tid=statuses_by_tid,
                player_map=player_map,
                target_date=target_date,
                roster_signals=roster_signals,
                mode=modo,
            )
            gp.players_data_available = bool(gp.players)
        except Exception as exc:
            _log.warning(
                "Sin destacados para %s @ %s: %s", gp.away_tricode, gp.home_tricode, exc
            )
            gp.players = []
            gp.players_data_available = False
    return modo, previa


def _predict_one(
    sg: "ScheduledGame",
    pipeline: Any,
    version: str,
    absences_by_tid: dict[int, list[int]],
    nys_team_ids: set[int],
    feed_down: bool,
    player_map: dict[int, str],
) -> GamePrediction:
    """Calcula features + predicción para un partido y devuelve un GamePrediction."""
    import pandas as pd

    from nba_predictor.features.live_lookup import compute_live_features
    from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS

    absent_home = absences_by_tid.get(sg.home_team_id, [])
    absent_away = absences_by_tid.get(sg.away_team_id, [])

    # Availability flag y tricodes NYS
    if feed_down:
        flag = AvailabilityFlag.FEED_DOWN
        nys_tricodes: list[str] = []
    else:
        nys_tricodes = []
        if sg.home_team_id in nys_team_ids:
            nys_tricodes.append(sg.home_tricode)
        if sg.away_team_id in nys_team_ids:
            nys_tricodes.append(sg.away_tricode)
        flag = AvailabilityFlag.NYS if nys_tricodes else AvailabilityFlag.OK

    # Features y predicción
    features = compute_live_features(
        home_team_id=sg.home_team_id,
        away_team_id=sg.away_team_id,
        game_date=sg.game_date,
        absent_home_ids=absent_home,
        absent_away_ids=absent_away,
    )

    X = pd.DataFrame([features])[OFFICIAL_LOGISTIC_COLS]
    prob = float(pipeline.predict_proba(X)[0, 1])

    home_absence_names = [player_map.get(pid, f"#{pid}") for pid in absent_home]
    away_absence_names = [player_map.get(pid, f"#{pid}") for pid in absent_away]

    tip_off_cdmx: str | None = None
    if sg.tip_off_et is not None:
        try:
            tip_off_cdmx = _et_to_cdmx_str(sg.tip_off_et)
        except Exception:
            pass  # fallo de conversión es no-fatal; el mensaje se publica sin hora

    return GamePrediction(
        home_tricode=sg.home_tricode,
        away_tricode=sg.away_tricode,
        game_date=sg.game_date.isoformat(),
        probability_home=round(prob, 4),
        home_absences=home_absence_names,
        away_absences=away_absence_names,
        availability_flag=flag,
        model_version=version,
        nys_tricodes=nys_tricodes,
        tip_off_cdmx=tip_off_cdmx,
        game_id=sg.game_id,
        home_absence_ids=list(absent_home),
        away_absence_ids=list(absent_away),
    )


def _discover_latest_version() -> str:
    """Devuelve el nombre de la versión más reciente del registry local."""
    from nba_predictor.config import settings
    from nba_predictor.models.registry import VERSION_PREFIX

    models_dir = settings.processed_dir.parent / "models"
    if not models_dir.exists():
        raise FileNotFoundError(f"Directorio de modelos no encontrado: {models_dir}")

    versions = sorted(
        p.name for p in models_dir.glob(f"{VERSION_PREFIX}_*") if p.is_dir()
    )
    if not versions:
        raise FileNotFoundError(f"Sin versiones de modelo en {models_dir}")
    return versions[-1]
