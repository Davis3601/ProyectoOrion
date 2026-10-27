"""
CAPTURA DE ODDS DE MERCADO — Cloud Run Job nba-odds-capture (D-ODDS-2).

*** ESCRIBE EN BIGQUERY DE FORMA DELIBERADA. *** Destino:
predictorsnonprod.nba_predictor.market_odds, con CloudDataStore construido
EXPLICITAMENTE aqui — nunca get_datastore(), nunca heredando el modo del .env
(convencion de "Convenciones de codigo", pagada con el incidente GCS del
2026-09-13).

USO DIAGNOSTICO, NO PREDICTIVO (D-ODDS-2): las odds jamas entran como feature
de ningun modelo ni tocan el pipeline de predicciones. Este script no importa
nada de features/ ni de models/.

CLI DELGADO: toda la logica decidible vive en nba_predictor/jobs/odds_logic.py
(funciones puras, con unit tests). Aqui solo hay red, BigQuery y exit codes.

CONTRATO DE FALLO — deliberadamente DISTINTO al best-effort del endpoint: este
job no tiene otra mision que capturar, asi que un fallo de API o de escritura
sale con exit != 0 y traza VISIBLE en los logs del job. Un dia sin partidos NBA
(offseason, jornada vacia) NO es fallo: exit 0, cero filas y log informativo.

La API key se lee de NBA_PREDICTOR_ODDS_API_KEY via Settings (SecretStr) y
JAMAS se imprime: ni en logs, ni en errores, ni en la URL loggeada.

Uso:
    python scripts/odds_capture_job.py --snapshot-label publish
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone

import requests

from nba_predictor.config import settings
from nba_predictor.jobs.odds_logic import (
    CREDITS_DEGRADE_USED,
    CREDITS_WARN_USED,
    MANUAL_LABEL,
    SNAPSHOT_LABELS,
    SPORT_KEY,
    MERGE_KEYS,
    SPREAD_TOTAL_COLUMNS,
    build_schedule_index,
    choose_markets,
    exclude_started,
    match_events,
    parse_quota,
    validate_snapshot_label,
)

log = logging.getLogger("odds_capture")

ODDS_API_URL = f"https://api.the-odds-api.com/v4/sports/{SPORT_KEY}/odds"
# Lista de deportes: NO consume creditos (verificado 2026-10-27:
# x-requests-last = 0 y x-requests-used sin moverse) y devuelve las mismas
# cabeceras de cuota. Es la forma de saber el consumo ANTES de decidir cuantos
# mercados pedir.
SPORTS_URL = "https://api.the-odds-api.com/v4/sports"
TIMEOUT = 20

TABLE = "market_odds"
DATASET = "nba_predictor"

# Coste de la llamada = markets x regions. D-ODDS-7: h2h,spreads,totals con una
# region = 3 creditos; 3 capturas/dia x 31 dias = ~279/mes contra 500. Pedir
# bookmakers concretos en vez de la region entera NO encarece: cada grupo de 10
# bookmakers equivale a 1 region. Umbrales de consumo en odds_logic.
BOOKMAKERS: tuple[str, ...] = ("draftkings", "fanduel", "betmgm")

# Margen HACIA ATRAS del indice de emparejamiento. Cubre el desfase UTC/ET y
# algun aplazamiento. No hay margen hacia adelante: ver load_schedule_window.
SCHEDULE_WINDOW_DAYS = 2


def _api_key() -> str:
    """La clave, o muerte ruidosa. Nunca se imprime su valor."""
    if settings.odds_api_key is None:
        raise RuntimeError(
            "NBA_PREDICTOR_ODDS_API_KEY no esta definida. OJO con el nombre: "
            "Settings usa env_prefix NBA_PREDICTOR_, asi que una variable "
            "llamada solo ODDS_API_KEY NO puebla el campo."
        )
    return settings.odds_api_key.get_secret_value()


def _quota_headers(resp) -> dict[str, str]:
    return {k: v for k, v in resp.headers.items() if k.lower().startswith("x-requests-")}


def _log_consumo(momento: str, cuota: dict[str, str]) -> None:
    """Loggea el consumo leido de las cabeceras y avisa si pasa los umbrales."""
    used, remaining = parse_quota(cuota)
    log.info("Creditos (%s): x-requests-used=%s x-requests-remaining=%s",
             momento, used, remaining)
    if used is not None and used >= CREDITS_WARN_USED:
        log.warning(
            "Consumo de creditos %d >= %d (umbral pre-registrado D-ODDS-7). "
            "A partir de %d se captura solo h2h.", used, CREDITS_WARN_USED,
            CREDITS_DEGRADE_USED,
        )


def choose_markets_from_api() -> tuple[str, ...]:
    """Lee el consumo del mes con la llamada GRATUITA a /sports y elige mercados.

    Si la consulta falla, el consumo es desconocido y choose_markets degrada a
    solo h2h: un fallo de red en la pre-consulta no debe gastar 3 creditos a
    ciegas. La degradacion se DECLARA en el log, nunca en silencio.
    """
    cuota: dict[str, str] = {}
    try:
        resp = requests.get(SPORTS_URL, params={"apiKey": _api_key()}, timeout=TIMEOUT)
        resp.raise_for_status()
        cuota = _quota_headers(resp)
    except requests.RequestException as exc:
        # El mensaje de requests puede incluir la URL con la clave: solo el tipo.
        log.warning("Consulta de cuota fallida (%s): consumo desconocido.",
                    type(exc).__name__)
    _log_consumo("antes de la captura", cuota)
    used, _ = parse_quota(cuota)
    mercados, nivel = choose_markets(used)
    if nivel == "degradado":
        log.warning(
            "DEGRADACION DECLARADA (D-ODDS-7): se captura solo h2h — consumo %s "
            "(umbral %d; desconocido tambien degrada). h2h es la evidencia del CLV "
            "y tiene prioridad; spreads y totals quedan NULL en esta captura.",
            used if used is not None else "desconocido", CREDITS_DEGRADE_USED,
        )
    return mercados


def fetch_odds(markets: tuple[str, ...]) -> tuple[list[dict], dict[str, str]]:
    """Llama a la API. Devuelve (eventos, cabeceras de cuota).

    La URL se loggea SIN la clave: la API la pasa por query string, que es
    justo el sitio donde un secreto acaba copiado en un ticket.
    """
    params = {
        "apiKey": _api_key(),
        "regions": "us",
        "markets": ",".join(markets),
        "oddsFormat": "american",
        "bookmakers": ",".join(BOOKMAKERS),
    }
    log.info("GET %s (markets=%s, bookmakers=%s)", ODDS_API_URL,
             params["markets"], ",".join(BOOKMAKERS))
    resp = requests.get(ODDS_API_URL, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    cuota = _quota_headers(resp)
    _log_consumo("tras la captura", cuota)
    return resp.json(), cuota


def _season_for(today: date) -> str:
    """Temporada NBA de una fecha. El payload CDN manda sobre esto.

    fetch_future_schedule corrige el parametro contra leagueSchedule.seasonYear
    (patron e-0), asi que basta con una derivacion razonable: la temporada
    arranca en octubre.
    """
    if today.month >= 10:
        return f"{today.year}-{(today.year + 1) % 100:02d}"
    return f"{today.year - 1}-{today.year % 100:02d}"


def load_schedule_window(
    store, today: date
) -> tuple[list[tuple[str, date, str, str]], int]:
    """Partidos PROGRAMADOS de la ventana, desde el schedule CDN.

    NO se lee la tabla `games`: esa es la capa STRUCTURED de partidos JUGADOS
    (Decision 1 de Fase 5a) y no contiene un solo partido futuro — verificado
    el 2026-09-20: cero filas con game_date >= hoy. Las odds SIEMPRE son de
    partidos futuros, asi que emparejar contra `games` daba indice vacio, cero
    matches y una tabla que se quedaba vacia para siempre con exit 0: el fallo
    silencioso de manual, introducido por leer la fuente equivocada.

    El game_id sigue siendo el CANONICO de la NBA (el mismo de predictions_log):
    el schedule CDN es justo de donde sale ese id.

    Devuelve (partidos de la ventana, total de partidos futuros del calendario).
    El segundo numero existe para que la guarda anti-silencio pueda separar
    "no hay partidos estos dias" (ventana vacia, calendario lleno: legitimo en
    pretemporada o parones) de "la fuente no respondio" (calendario vacio).
    """
    desde = date.fromordinal(today.toordinal() - SCHEDULE_WINDOW_DAYS)

    from nba_predictor.ingestion.future_schedule import fetch_future_schedule

    # SIN tope superior: The Odds API lista partidos con SEMANAS de antelacion
    # (medido 2026-09-21: 41 eventos entre el 20-oct y el 25-dic, estando a
    # 21-sep). Una ventana de +-2 dias alrededor de hoy los dejaba a todos
    # fuera del indice y los archivaba como matched=false pese a estar en el
    # calendario — corrompiendo justo la columna de la que depende el analisis
    # de CLV. El indice lleva TODOS los futuros; 1206 entradas en un dict no
    # cuestan nada.
    futuros = fetch_future_schedule(_season_for(today), from_date=desde)
    programados = futuros

    # team_id -> nombre completo: The Odds API publica "Los Angeles Lakers",
    # el schedule trae tricodes. El catalogo `teams` es el puente.
    #
    # Se ITERAN las filas en vez de usar .to_dataframe(): con bigquery-storage
    # instalado, to_dataframe() baja por la Storage Read API y exige
    # bigquery.readsessions.create A NIVEL PROYECTO (capa 5 de la cebolla,
    # 2026-08-25 — y cobrada otra vez aqui el 2026-09-21). Para 30 equipos ese
    # permiso de proyecto no se justifica: la iteracion simple usa la API de
    # consulta normal, que jobUser ya cubre.
    filas_teams = store._bq.query(
        f"SELECT team_id, name FROM `{settings.gcp_project_id}.{DATASET}.teams`"
    ).result()
    nombre = {int(r.team_id): str(r.name) for r in filas_teams}

    salida: list[tuple[str, date, str, str]] = []
    for g in programados:
        casa, fuera = nombre.get(g.home_team_id), nombre.get(g.away_team_id)
        if not casa or not fuera:
            log.warning(
                "Partido %s con team_id fuera del catalogo (%s/%s) — omitido",
                g.game_id, g.home_team_id, g.away_team_id,
            )
            continue
        salida.append((g.game_id, g.game_date, casa, fuera))
    return salida, len(futuros)


def merge_rows(store, rows: list[dict]) -> int:
    """MERGE idempotente sobre market_odds. Clave: MERGE_KEYS.

    Idempotente a proposito: re-ejecutar el mismo snapshot del mismo dia
    ACTUALIZA en vez de duplicar. A diferencia de predictions_log (append-only,
    cada servida es un hecho distinto), aqui un snapshot re-capturado es el
    MISMO hecho medido otra vez.

    DESVIACION DECLARADA del encargo, que pedia "MERGE con staging (patron
    Decision 3 de Fase 5b)": crear una tabla de staging exige
    bigquery.tables.create SOBRE EL DATASET, y el mismo encargo manda dar a
    esta SA dataEditor solo A NIVEL TABLA sobre market_odds. Las dos exigencias
    no pueden cumplirse a la vez. Se conserva la que protege (minimo
    privilegio) y el MERGE toma su fuente de un ARRAY<STRUCT> pasado como
    PARAMETRO de consulta: misma semantica de idempotencia, sin tabla temporal
    y sin interpolar valores en el SQL. El volumen lo permite con holgura
    (~10 partidos x 3 bookmakers = ~30 filas por captura).
    """
    from google.cloud import bigquery

    target_id = f"{settings.gcp_project_id}.{DATASET}.{TABLE}"
    columnas = [
        "game_date", "api_event_id", "matched", "game_id",
        "home_team", "away_team", "bookmaker", "market",
        "home_price_american", "away_price_american",
        "home_price_decimal", "away_price_decimal",
        "snapshot_label", "capture_ts", "source", "ingested_at",
        "commence_time",
        *SPREAD_TOTAL_COLUMNS,
    ]
    tipos = {
        "game_date": "DATE", "matched": "BOOL", "home_price_american": "INT64",
        "away_price_american": "INT64", "home_price_decimal": "FLOAT64",
        "away_price_decimal": "FLOAT64", "capture_ts": "TIMESTAMP",
        "ingested_at": "TIMESTAMP",
        "commence_time": "TIMESTAMP",
        # D-ODDS-7: lineas y precios de spreads/totals, FLOAT64 nullable.
        **{c: "FLOAT64" for c in SPREAD_TOTAL_COLUMNS},
    }
    struct_type = bigquery.StructQueryParameterType(
        *[bigquery.ScalarQueryParameterType(tipos.get(c, "STRING"), name=c)
          for c in columnas]
    )
    valores = [
        bigquery.StructQueryParameter(
            None, *[bigquery.ScalarQueryParameter(c, tipos.get(c, "STRING"), r[c])
                    for c in columnas]
        )
        for r in rows
    ]

    on = " AND ".join(f"T.{k} = S.{k}" for k in MERGE_KEYS)
    update = ", ".join(f"T.{c} = S.{c}" for c in columnas if c not in MERGE_KEYS)
    insert_cols = ", ".join(columnas)
    insert_vals = ", ".join(f"S.{c}" for c in columnas)
    sql = (
        f"MERGE `{target_id}` AS T\n"
        f"USING UNNEST(@filas) AS S\n"
        f"ON {on}\n"
        f"WHEN MATCHED THEN UPDATE SET {update}\n"
        f"WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})"
    )
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("filas", struct_type, valores)
        ]
    )
    store._bq.query(sql, job_config=job_config).result()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-label", required=True, choices=[*SNAPSHOT_LABELS, MANUAL_LABEL]
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    label = validate_snapshot_label(args.snapshot_label)
    log.info("Captura de odds — snapshot_label=%s", label)

    from nba_predictor.storage.cloud import CloudDataStore

    store = CloudDataStore(
        project_id=settings.gcp_project_id,
        dataset=settings.bq_dataset,
        bucket_name=settings.gcs_bucket,
    )

    eventos, _ = fetch_odds(choose_markets_from_api())

    if not eventos:
        log.info(
            "La API no devolvio partidos NBA (offseason o jornada vacia). "
            "Cero filas escritas. Esto NO es un fallo."
        )
        sys.exit(0)

    log.info("%d eventos devueltos por la API", len(eventos))
    schedule, total_futuros = load_schedule_window(
        store, datetime.now(timezone.utc).date()
    )
    log.info(
        "%d partidos del schedule en el indice (%d futuros en el calendario)",
        len(schedule), total_futuros,
    )

    # GUARDA ANTI-SILENCIO: la API trajo partidos pero el schedule vino vacio.
    # Eso no es "no hay nada que capturar": es que la fuente del indice esta
    # rota (fuente equivocada, temporada mal derivada, CDN caido). Cobrado el
    # 2026-09-20 leyendo `games`, que solo tiene partidos JUGADOS: el indice
    # salia vacio y el job archivaba cero filas con exit 0, indefinidamente.
    if total_futuros == 0:
        raise RuntimeError(
            f"La API devolvio {len(eventos)} partidos pero el calendario CDN no "
            f"trajo NINGUN partido futuro. El indice de emparejamiento no se "
            f"pudo construir: revisar el CDN y la temporada derivada. Se falla "
            f"ruidosamente en vez de archivar cero en silencio."
        )
    if not schedule:
        log.info(
            "Sin partidos de temporada regular en la ventana (%d futuros en el "
            "calendario). Los %d eventos de la API quedaran sin match: "
            "plausible en pretemporada.", total_futuros, len(eventos),
        )

    # PRE-PARTIDO POR DEFINICIÓN (D-PRE-1 / bug 4): los eventos ya iniciados
    # NO se escriben — una odd en vivo pisaría por MERGE la previa de otro día.
    # UN solo reloj para el filtro y para las filas: el capture_ts escrito es
    # exactamente el instante contra el que se decidió "no iniciado".
    capture_ts = datetime.now(timezone.utc)
    pendientes, iniciados = exclude_started(eventos, capture_ts)
    for desc in iniciados:
        log.info("Evento ya iniciado, omitido (market_odds es pre-partido): %s", desc)
    log.info("Eventos iniciados omitidos: %d de %d", len(iniciados), len(eventos))

    filas, sin_match = match_events(
        pendientes, build_schedule_index(schedule), label, capture_ts
    )
    for aviso in sin_match:
        log.warning("Partido de la API sin match en el schedule: %s", aviso)

    if not filas:
        log.info(
            "Ningun evento emparejo con el schedule (%d eventos, %d partidos "
            "programados, %d avisos). Cero filas. Plausible si la API solo "
            "lista pretemporada o exhibiciones.", len(eventos), len(schedule),
            len(sin_match),
        )
        sys.exit(0)

    escritas = merge_rows(store, filas)
    log.info(
        "MERGE completado: %d filas, %d eventos emparejados, %d sin match, "
        "%d iniciados omitidos.",
        escritas, len(pendientes) - len(sin_match), len(sin_match), len(iniciados),
    )
    # D-ODDS-7: cobertura de spreads/totals sobre filas emparejadas, para que la
    # expectativa pre-registrada (>= 90%) se lea en el log de cada captura.
    emparejadas = [f for f in filas if f["matched"]]
    log.info(
        "Cobertura D-ODDS-7 (filas emparejadas): %d con spread, %d con total, de %d.",
        sum(f["spread_home"] is not None for f in emparejadas),
        sum(f["total_line"] is not None for f in emparejadas),
        len(emparejadas),
    )


if __name__ == "__main__":
    main()
