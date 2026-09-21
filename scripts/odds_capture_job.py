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
    SNAPSHOT_LABELS,
    SPORT_KEY,
    MERGE_KEYS,
    build_schedule_index,
    match_events,
    validate_snapshot_label,
)

log = logging.getLogger("odds_capture")

ODDS_API_URL = f"https://api.the-odds-api.com/v4/sports/{SPORT_KEY}/odds"
TIMEOUT = 20

TABLE = "market_odds"
DATASET = "nba_predictor"

# Coste de la llamada = markets x regions (verificado 2026-09-20 en la doc de
# la API). Con h2h y una sola region: 1 credito. 3 capturas/dia x 31 dias = 93
# creditos/mes contra 500 del tier gratuito. Pedir bookmakers concretos en vez
# de la region entera NO encarece: cada grupo de 10 bookmakers equivale a 1
# region.
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


def fetch_odds() -> tuple[list[dict], dict[str, str]]:
    """Llama a la API. Devuelve (eventos, cabeceras de cuota).

    La URL se loggea SIN la clave: la API la pasa por query string, que es
    justo el sitio donde un secreto acaba copiado en un ticket.
    """
    params = {
        "apiKey": _api_key(),
        "regions": "us",
        "markets": "h2h",
        "oddsFormat": "american",
        "bookmakers": ",".join(BOOKMAKERS),
    }
    log.info("GET %s (markets=h2h, bookmakers=%s)", ODDS_API_URL, ",".join(BOOKMAKERS))
    resp = requests.get(ODDS_API_URL, params=params, timeout=TIMEOUT)
    resp.raise_for_status()

    cuota = {
        k: v for k, v in resp.headers.items()
        if k.lower().startswith("x-requests-")
    }
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
    ]
    tipos = {
        "game_date": "DATE", "matched": "BOOL", "home_price_american": "INT64",
        "away_price_american": "INT64", "home_price_decimal": "FLOAT64",
        "away_price_decimal": "FLOAT64", "capture_ts": "TIMESTAMP",
        "ingested_at": "TIMESTAMP",
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
    parser.add_argument("--snapshot-label", required=True, choices=list(SNAPSHOT_LABELS))
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

    eventos, cuota = fetch_odds()
    if cuota:
        log.info("Cuota de la API: %s", cuota)

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

    filas, sin_match = match_events(eventos, build_schedule_index(schedule), label)
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
        "MERGE completado: %d filas, %d eventos emparejados, %d sin match.",
        escritas, len(eventos) - len(sin_match), len(sin_match),
    )


if __name__ == "__main__":
    main()
