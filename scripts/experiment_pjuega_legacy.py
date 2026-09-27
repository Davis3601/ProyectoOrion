"""
D-EXP-5 — P(juega | estatus) SOBRE EL CORPUS iTextSharp (era legacy).

*** SOLO LECTURA DE LA NUBE. *** Los PDFs salen de
gs://{bucket}/raw/injury_reports/ (archivados por el backfill D-RES-2) y la
verdad de juego de BigQuery, con CloudDataStore construido EXPLICITAMENTE
(convencion de scripts, pagada con el incidente GCS del D-RES-2). NO toca la
red de la NBA. NO escribe en GCS ni en BigQuery. NO edita el paquete.

QUE MIDE: lo mismo que D-EXP-1, sobre el otro corpus. Es la extension de la
medicion de P(juega | estatus) a las cinco temporadas iTextSharp
(2018-19..2022-23), habilitada por el cierre de D-RES-3 (parser legacy
adjudicado VERDE por auditoria humana plena el 2026-09-24). Es MEDICION pura:
ningun modelo, ninguna feature, ningun cambio de pipeline.

POR QUE IMPORTA: D-EXP-2 murio de falta de RESOLUCION, no de falta de efecto
(MDE 0.0096/0.0121 contra un rango esperado de 0.000-0.008). Su unica via de
rescate es ampliar el corpus con estas cinco temporadas. Antes de re-medir hay
que saber si los pesos de D-EXP-1 SON LOS MISMOS en la era legacy: usar pesos
de un corpus sobre otro sin comprobarlo seria exactamente el vocabulario
equivocado que este proyecto ya pago cinco veces.

DEFINICIONES: IDENTICAS a D-EXP-1, y no por copia — el modulo de D-EXP-1 se
importa y sus funciones puras (filtrado por fecha objetivo, clasificacion de
instancia, normalizacion de equipo, agregacion) se reutilizan tal cual. Una
segunda implementacion de la misma definicion seria una segunda verdad, y la
comparabilidad de las dos tablas es justo el producto de esta tarea.

DIFERENCIAS DECLARADAS respecto a D-EXP-1:
  parser    parse_pdf_any (despacho por layout). Las fechas cuyo layout no esta
            en la lista blanca levantan UnknownLayoutError y se CUENTAN como
            excluidas — jamas se fuerza un layout sobre ellas (tramo
            2019-11-15..2019-12-31, pendiente de D-RES-3d).
  corte     SOLO publish. El corte late quedo INVALIDADO como medicion
            predictiva en D-EXP-1 (adjudicacion 3: cae en 09PM-11:15PM ET, con
            los partidos en curso o terminados; es retrospectiva, no pronostico).
  nombres   tabla players de BigQuery (catalogo canonico desde D-PROD-1d), un
            solo indice para las cinco temporadas. D-EXP-1 usaba un indice por
            temporada desde los JSON crudos locales; el catalogo no depende del
            disco de esta laptop y es la fuente que usa produccion.
  extra     P(juega | Available, categoria) con la columna Category, que SOLO
            existe en el layout ITEXT_V1. Permite CONTRASTAR la inferencia por
            razon de D-EXP-1 ("G League"/"Two-Way" en el texto) contra la
            etiqueta explicita del documento.

LOS AGREGADOS NO SON OFICIALES hasta que la muestra de auditoria humana se
adjudique contra el PDF y el boxscore (protocolo 13e-1).

Uso:
    python scripts/experiment_pjuega_legacy.py --freeze-audit   # primero
    python scripts/experiment_pjuega_legacy.py
    python scripts/experiment_pjuega_legacy.py --limit 3        # humo
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import logging
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from nba_predictor.ingestion import injury_report_legacy as legacy
from nba_predictor.ingestion.injury_classify import available_bucket
from nba_predictor.ingestion.injury_report import NameIndex
from nba_predictor.ingestion.injury_report_legacy import (
    ParserCoverageError,
    UnknownLayoutError,
    parse_pdf_any,
)
from nba_predictor.storage.cloud import CloudDataStore, _gcs_injury_report_path

# Las definiciones de D-EXP-1 se IMPORTAN, no se copian: la comparabilidad de
# las dos tablas es el producto de esta tarea, y una reimplementacion podria
# divergir en silencio.
_D1_PATH = Path(__file__).resolve().parent / "experiment_pjuega.py"
_SPEC = importlib.util.spec_from_file_location("_dexp1", _D1_PATH)
dexp1 = importlib.util.module_from_spec(_SPEC)
sys.modules["_dexp1"] = dexp1
_SPEC.loader.exec_module(dexp1)

# El clasificador de Available por razon tambien se importa, del script que lo
# congelo con los pesos oficiales (D-EXP-2).
_D2_PATH = Path(__file__).resolve().parent / "experiment_disponibilidad_v2.py"
_SPEC2 = importlib.util.spec_from_file_location("_dexp2", _D2_PATH)
dexp2 = importlib.util.module_from_spec(_SPEC2)
sys.modules["_dexp2"] = dexp2
_SPEC2.loader.exec_module(dexp2)

log = logging.getLogger("pjuega_legacy")

OUT_DIR = Path("data/experiment_pjuega_legacy")
CACHE_DIR = OUT_DIR / "cache"
GEMBOX_RESULTS = Path("data/experiment_pjuega/results.json")

# Las cinco temporadas del corpus iTextSharp. El cambio de productor quedo
# fijado al offseason de 2023 (D-RES-2, hallazgo 1): 2023-04-09 iTextSharp,
# 2023-10-24 GemBox. 2018-19 entra PARCIAL: el borde de retencion cae dentro
# (primer PDF vivo 2018-12-17, archivo regular desde 2018-12-18).
SEASONS: tuple[str, ...] = ("2018-19", "2019-20", "2020-21", "2021-22", "2022-23")

# SOLO publish: el corte late es retrospectiva, no pronostico (D-EXP-1,
# adjudicacion 3).
CUT = "publish"

STATUSES: tuple[str, ...] = dexp1.STATUSES

# Frontera de layouts medida por biseccion en D-RES-3c. El tramo intermedio
# (2019-11-15..2019-12-31) tiene AL MENOS dos variantes mas, fuera de la lista
# blanca hasta D-RES-3d. Estas constantes NO deciden como se parsea — eso lo
# hace detect_layout leyendo el documento; solo sirven para elegir la muestra
# de auditoria por era sin abrir un solo PDF.
V1_LAST = "2019-11-14"
V2_FIRST = "2020-01-07"

AUDIT_SEED = 42

# Muestra dirigida de D-RES-3e: fechas NUEVAS, listado con la columna Matchup
# y las fronteras marcadas. El audit_dates.json de la primera corrida se
# conserva intacto como evidencia de aquella.
AUDIT_FILE_NAME = "audit_dates_d3e.json"

# Fechas ya listadas en la primera corrida de D-EXP-5. La muestra dirigida debe
# ejercitar fechas NUEVAS: repetirlas gastaria el recurso caro (lectura humana)
# sobre documentos ya vistos.
YA_AUDITADAS: tuple[str, ...] = ("2019-01-16", "2020-02-01")


# ---------------------------------------------------------------------------
# Funciones PURAS — sin red, sin BigQuery, unit-testeables
# ---------------------------------------------------------------------------


def era_expected(fecha_iso: str) -> str:
    """Era ESPERADA de una fecha segun la frontera de D-RES-3c.

    Es una expectativa para estratificar la muestra de auditoria, NO la
    decision de parseo: el layout real lo determina detect_layout leyendo el
    /Producer y los tokens del encabezado del propio documento. Si alguna vez
    discrepan, la discrepancia es un hallazgo y el documento manda.
    """
    if fecha_iso <= V1_LAST:
        return "ITEXT_V1"
    if fecha_iso >= V2_FIRST:
        return "ITEXT_V2"
    return "tramo_desconocido"


def freeze_audit_sample(fechas: list[str], seed: int = AUDIT_SEED,
                        excluir: tuple[str, ...] = ()) -> dict:
    """Elige una fecha por era, con seed fijo, ANTES de computar nada.

    Se congela en disco para que el listado de auditoria no pueda elegirse
    despues de ver los numeros. Dos fechas: una de cada layout auditado en
    D-RES-3, para que la muestra ejerza ambos caminos del parser.
    """
    por_era: dict[str, list[str]] = defaultdict(list)
    for f in fechas:
        if f in excluir:
            continue
        por_era[era_expected(f)].append(f)

    rng = random.Random(seed)
    elegidas: dict[str, str] = {}
    for era in ("ITEXT_V1", "ITEXT_V2"):
        candidatas = sorted(por_era.get(era, []))
        if candidatas:
            elegidas[era] = rng.choice(candidatas)
    return {
        "seed": seed,
        "congelado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candidatas_por_era": {k: len(v) for k, v in sorted(por_era.items())},
        "elegidas_antes_de_computar": elegidas,
    }


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """IC95 de Wilson para una proporcion.

    Wilson y no Wald porque varias celdas tienen n chico y p cerca de 0
    (Doubtful, y Out por construccion): el intervalo de Wald se sale de [0,1] y
    da anchura cero cuando k=0, que es una mentira aritmetica.
    """
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / d
    medio = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centro - medio), min(1.0, centro + medio))


def diff_ci(k1: int, n1: int, k2: int, n2: int, z: float = 1.96) -> dict | None:
    """Diferencia de proporciones (1 menos 2) con IC95 de Wald.

    Aqui Wald si: la diferencia no esta acotada a [0,1] y las n de la
    comparacion legacy-vs-GemBox son grandes en las celdas que deciden.
    """
    if n1 <= 0 or n2 <= 0:
        return None
    p1, p2 = k1 / n1, k2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    return {
        "p_legacy": round(p1, 4),
        "p_gembox": round(p2, 4),
        "diff": round(p1 - p2, 4),
        "ic95": [round(p1 - p2 - z * se, 4), round(p1 - p2 + z * se, 4)],
        "diff_pp": round((p1 - p2) * 100, 2),
    }


def pool_por_estatus(resumen_por_temporada: dict, cut: str = CUT) -> dict[str, dict]:
    """Agrega las temporadas de un resumen en (n, jugo) por estatus.

    Sirve para las dos tablas: la del corpus legacy y la de GemBox leida de su
    results.json. Poolear y no promediar: las temporadas tienen n distintos y
    el promedio simple le daria el mismo voto a una celda de 244 y a una de 390.
    """
    out: dict[str, dict] = {st: {"n": 0, "jugo": 0} for st in STATUSES}
    for _season, cortes in resumen_por_temporada.items():
        filas = cortes.get(cut, {})
        for st, v in filas.items():
            if st not in out:
                continue
            out[st]["n"] += v["instancias"]
            out[st]["jugo"] += v["jugo_primaria"]
    return {st: v for st, v in out.items() if v["n"]}


def available_breakdown(instancias: list[dict]) -> dict:
    """P(juega | Available) descompuesto de dos maneras independientes.

    por_categoria   etiqueta EXPLICITA del documento (columna Category, que
                    solo trae el layout ITEXT_V1).
    por_razon       inferencia de D-EXP-1/D-EXP-2 sobre el texto de la razon
                    (_GLEAGUE_RE). Es la unica disponible en ITEXT_V2 y en
                    GemBox.
    concordancia    donde EXISTEN las dos, cuantas veces coinciden. Es la
                    validacion que el corpus GemBox no podia dar: alli no hay
                    columna Category contra la que contrastar la inferencia.
    """
    disponibles = [i for i in instancias
                   if i["veredicto"] == "incluida" and i["status"] == "Available"]

    por_cat: dict[str, dict] = defaultdict(lambda: {"n": 0, "jugo": 0})
    por_razon: dict[str, dict] = defaultdict(lambda: {"n": 0, "jugo": 0})
    por_portable: dict[str, dict] = defaultdict(lambda: {"n": 0, "jugo": 0})
    conc = Counter()

    for i in disponibles:
        es_gleague_razon = bool(dexp2._GLEAGUE_RE.search(i["reason"] or ""))
        clave_razon = "gleague_o_twoway" if es_gleague_razon else "resto"
        por_razon[clave_razon]["n"] += 1
        por_razon[clave_razon]["jugo"] += int(i["jugo_primaria"])

        # Particion PORTABLE (D-RES-3e): category cuando el layout la trae,
        # razon cuando no. Se reporta AL LADO de la inferencia por razon, no en
        # su lugar: la diferencia entre ambas ES el hallazgo, y sustituir una
        # por otra borraria la evidencia de que el instrumento estaba sesgado.
        clave_port = available_bucket(i.get("category"), i["reason"])
        por_portable[clave_port]["n"] += 1
        por_portable[clave_port]["jugo"] += int(i["jugo_primaria"])

        cat = i.get("category")
        if cat:
            por_cat[cat]["n"] += 1
            por_cat[cat]["jugo"] += int(i["jugo_primaria"])
            es_gleague_cat = bool(dexp2._GLEAGUE_RE.search(cat))
            conc["coinciden" if es_gleague_cat == es_gleague_razon else "discrepan"] += 1

    def _cerrar(d: dict) -> dict:
        return {k: {**v, "p_juega": round(v["jugo"] / v["n"], 4)}
                for k, v in sorted(d.items(), key=lambda kv: -kv[1]["n"])}

    return {
        "por_categoria_explicita": _cerrar(por_cat),
        "por_razon_inferida": _cerrar(por_razon),
        "por_particion_portable": _cerrar(por_portable),
        "concordancia_categoria_vs_razon": dict(conc),
    }


def render_comparativa(legacy: dict[str, dict], gembox: dict[str, dict]) -> str:
    """Tabla legacy vs GemBox por estatus, con IC95 de la diferencia."""
    out = [
        "COMPARATIVA legacy (iTextSharp, 2018-19..2022-23) vs GemBox (2023-24..2025-26)",
        "corte publish, P(juega) primaria (minutes > 0), temporadas pooleadas",
        "",
        f"  {'estatus':<13}{'n_leg':>7}{'P_leg':>8}{'IC95 legacy':>18}"
        f"{'n_gem':>7}{'P_gem':>8}{'dif(pp)':>9}{'IC95 dif (pp)':>20}",
    ]
    for st in STATUSES:
        if st not in legacy or st not in gembox:
            continue
        L, G = legacy[st], gembox[st]
        ci = wilson_ci(L["jugo"], L["n"])
        d = diff_ci(L["jugo"], L["n"], G["jugo"], G["n"])
        ci_txt = f"[{ci[0]:.3f},{ci[1]:.3f}]"
        lo, hi = d["ic95"][0] * 100, d["ic95"][1] * 100
        dif_txt = f"[{lo:+.2f},{hi:+.2f}]"
        out.append(
            f"  {st:<13}{L['n']:>7}{L['jugo'] / L['n']:>8.3f}{ci_txt:>18}"
            f"{G['n']:>7}{G['jugo'] / G['n']:>8.3f}{d['diff_pp']:>9.2f}{dif_txt:>20}"
        )
    return "\n".join(out)


def divergencias(legacy: dict[str, dict], gembox: dict[str, dict],
                 umbral_pp: float = 5.0) -> list[str]:
    """Estatus cuya diferencia con GemBox supera el umbral pre-registrado.

    El pre-registro exige DIAGNOSTICO DE INSTRUMENTO antes de atribuir una
    divergencia a la NBA: la explicacion barata ("cambio la politica de la
    liga") es tambien la que no se puede falsar, y este proyecto ya pago cinco
    veces por creerle a un numero verosimil.
    """
    fuera = []
    for st in STATUSES:
        if st not in legacy or st not in gembox:
            continue
        d = diff_ci(legacy[st]["jugo"], legacy[st]["n"],
                    gembox[st]["jugo"], gembox[st]["n"])
        if d and abs(d["diff_pp"]) > umbral_pp:
            fuera.append(st)
    return fuera


def render_audit(instancias: list[dict], fecha: str) -> str:
    """Listado instancia por instancia de una fecha, para cotejo humano.

    Gemelo del de D-EXP-1, con dos columnas mas que solo existen en esta era
    (LAYOUT y CATEGORIA): si el listado no muestra de que layout salio cada
    fila, la auditoria no puede distinguir un fallo de ITEXT_V1 de uno de V2.
    """
    filas = [i for i in instancias if i["date"] == fecha]
    out = [f"AUDITORIA D-EXP-5 — fecha {fecha} (corte {CUT})", "=" * 78]
    if not filas:
        out.append("sin instancias: PDF ausente, layout desconocido o sin filas de esa fecha")
        return "\n".join(out)
    out.append(f"PDF {fecha}_{filas[0]['suffix']}.pdf | layout {filas[0]['layout']} "
               f"| {len(filas)} instancias")
    out.append(f"{'#':>3} {'EQUIPO PDF':<24}{'JUGADOR PDF':<26}{'ESTATUS':<13}"
               f"{'CATEGORIA':<18}{'MIN':>6}  {'JUGO':<7}{'ACTIV':<7}"
               f"{'VEREDICTO':<17}RAZON PDF")
    for n, i in enumerate(sorted(filas, key=lambda x: (x["team"], x["player_name"])), 1):
        minutos = "-" if i["minutes"] is None else f"{i['minutes']:.1f}"
        out.append(
            f"{n:>3} {i['team']:<24}{i['player_name']:<26}{i['status']:<13}"
            f"{(i['category'] or '-'):<18}{minutos:>6}  "
            f"{str(i['jugo_primaria']):<7}{str(i['jugo_secundaria']):<7}"
            f"{i['veredicto']:<17}{(i['reason'] or '')[:40]}"
        )
    out.append(f"    game_ids del dia: {sorted({i['game_id'] for i in filas if i['game_id']})}")
    return "\n".join(out)


def render_diagnostico(instancias: list[dict], status: str, n: int = 20,
                       seed: int = AUDIT_SEED) -> str:
    """Muestra de N instancias de un estatus divergente, para cotejo humano.

    Lo exige el pre-registro: ante desviacion > 5pp respecto a GemBox se
    diagnostica el INSTRUMENTO antes de contar una historia sobre la liga.
    """
    pool = [i for i in instancias
            if i["veredicto"] == "incluida" and i["status"] == status]
    rng = random.Random(seed)
    muestra = rng.sample(pool, min(n, len(pool)))
    out = [f"DIAGNOSTICO DE INSTRUMENTO — estatus {status} ({len(muestra)} de {len(pool)})",
           f"seed {seed}. Cotejar cada linea contra el PDF y el boxscore.", "=" * 78,
           f"{'FECHA':<12}{'LAYOUT':<11}{'EQUIPO PDF':<24}{'JUGADOR PDF':<26}"
           f"{'MIN':>6}  {'JUGO':<7}{'GAME_ID':<12}RAZON"]
    for i in sorted(muestra, key=lambda x: (x["date"], x["player_name"])):
        minutos = "-" if i["minutes"] is None else f"{i['minutes']:.1f}"
        out.append(
            f"{i['date']:<12}{i['layout']:<11}{i['team']:<24}{i['player_name']:<26}"
            f"{minutos:>6}  {str(i['jugo_primaria']):<7}{str(i['game_id'] or '-'):<12}"
            f"{(i['reason'] or '')[:36]}"
        )
    return "\n".join(out)


def frontier_flags(pdf_bytes: bytes, layout) -> dict[str, str]:
    """player_name -> marcas de frontera de su fila, para el listado dirigido.

    El defecto de propagacion vive EN LAS FRONTERAS, asi que una muestra al azar
    puede no ejercitar ninguna: 2021-02-10 tenia cero incoherencias y paso la
    auditoria de D-RES-3 en verde mientras el defecto seguia vivo. Estas marcas
    le dicen al lector humano donde mirar primero:
      PAG-INI / PAG-FIN  primera y ultima fila de una pagina
      POST-NYS           primera fila tras un bloque NOT YET SUBMITTED
      FIN-BLOQUE         ultima fila de un bloque de equipo
    """
    import pdfplumber

    marcas: dict[str, list[str]] = defaultdict(list)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            eventos = legacy._events_from_page(page, layout)
            jugadores = [e for e in eventos if e.kind == "player"]
            if not jugadores:
                continue
            marcas[jugadores[0].cells.get("player_name", "")].append("PAG-INI")
            marcas[jugadores[-1].cells.get("player_name", "")].append("PAG-FIN")
            prev_nys = False
            for ev in eventos:
                if ev.kind == "nys":
                    prev_nys = True
                    continue
                nombre = ev.cells.get("player_name", "")
                if prev_nys:
                    marcas[nombre].append("POST-NYS")
                prev_nys = False
            # fin de bloque: la siguiente fila de jugador trae equipo propio
            for a, b in zip(jugadores, jugadores[1:]):
                if b.cells.get("team"):
                    marcas[a.cells.get("player_name", "")].append("FIN-BLOQUE")
            marcas[jugadores[-1].cells.get("player_name", "")].append("FIN-BLOQUE")
    return {k: ",".join(dict.fromkeys(v)) for k, v in marcas.items()}


def render_audit_dirigido(instancias: list[dict], fecha: str,
                          marcas: dict[str, str]) -> str:
    """Listado con MATCHUP y fronteras marcadas, para la auditoria dirigida.

    La columna Matchup NUNCA estuvo en el listado que se auditó en D-RES-3, y el
    defecto de propagacion vivia justo ahi. Un listado de auditoria debe mostrar
    toda columna de la que dependa la atribucion.
    """
    filas = [i for i in instancias if i["date"] == fecha]
    out = [f"AUDITORIA DIRIGIDA D-RES-3e — fecha {fecha} (corte {CUT})", "=" * 78]
    if not filas:
        out.append("sin instancias para esa fecha")
        return "\n".join(out)
    out.append(f"PDF {fecha}_{filas[0]['suffix']}.pdf | layout {filas[0]['layout']} "
               f"| {len(filas)} instancias")
    out.append("FRONTERA: PAG-INI/PAG-FIN = borde de pagina | POST-NYS = tras bloque "
               "NOT YET SUBMITTED | FIN-BLOQUE = ultima del equipo")
    out.append("COH = invariante interno (equipo del matchup); False = marcada y avisada")
    out.append(f"{'#':>3} {'FRONTERA':<26}{'EQUIPO PDF':<24}{'JUGADOR PDF':<25}"
               f"{'MATCHUP':<9}{'COH':<6}{'ESTATUS':<13}{'FECHA PDF':<12}"
               f"{'MIN':>6}  {'JUGO':<7}{'VEREDICTO':<17}RAZON")
    for n, i in enumerate(sorted(filas, key=lambda x: (x["team"], x["player_name"])), 1):
        minutos = "-" if i["minutes"] is None else f"{i['minutes']:.1f}"
        out.append(
            f"{n:>3} {marcas.get(i['player_name'], '-'):<26}{i['team']:<24}"
            f"{i['player_name']:<25}{(i.get('matchup') or '-'):<9}"
            f"{str(i.get('coherent', True)):<6}{i['status']:<13}"
            f"{i.get('game_date_pdf', fecha):<12}{minutos:>6}  "
            f"{str(i['jugo_primaria']):<7}{i['veredicto']:<17}{(i['reason'] or '')[:30]}"
        )
    return "\n".join(out)


def render_regresion(pdf_bytes: bytes, fecha: str) -> str:
    """Listado de TODAS las filas de un PDF, con su fecha de partido y frontera.

    Existe para el artefacto de regresion de D-RES-3e. El listado dirigido
    normal filtra a la fecha objetivo, asi que las filas que el defecto fechaba
    mal DESAPARECEN de el una vez corregidas — la ausencia es mala evidencia
    para un lector humano. Aqui se ven las dos fechas del documento juntas: el
    bloque de Toronto con fecha 12/19 y VanVleet atribuido a su equipo es
    exactamente lo que antes salia como 12/18 y "Minnesota Timberwolves".
    """
    layout = legacy.detect_layout(pdf_bytes)
    rows, _nys = legacy.parse_pdf_legacy(pdf_bytes, layout)
    marcas = frontier_flags(pdf_bytes, layout)
    out = [f"REGRESION D-RES-3e — documento {fecha} (layout {layout.name})", "=" * 78,
           f"{len(rows)} filas, TODAS las del documento (sin filtrar por fecha objetivo)",
           "FRONTERA: PAG-INI/PAG-FIN | POST-NYS | FIN-BLOQUE. COH = invariante interno.",
           f"{'#':>3} {'FRONTERA':<26}{'FECHA PDF':<12}{'MATCHUP':<9}{'EQUIPO PDF':<24}"
           f"{'JUGADOR PDF':<25}{'COH':<6}{'ESTATUS':<13}RAZON"]
    for n, row in enumerate(rows, 1):
        out.append(
            f"{n:>3} {marcas.get(row.player_name, '-'):<26}{row.game_date:<12}"
            f"{row.matchup:<9}{row.team:<24}{row.player_name:<25}"
            f"{str(getattr(row, 'coherent', True)):<6}"
            f"{row.status.value:<13}{(row.reason or '')[:28]}"
        )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Lectura (GCS / BigQuery)
# ---------------------------------------------------------------------------


def publish_dates(season: str) -> list[dict]:
    """Registros del backfill con corte publish archivado."""
    return [r for r in dexp1.cuts_of_season(season)
            if r["cuts"].get(CUT, {}).get("suffix")]


def fetch_pdf(store: CloudDataStore, fecha: str, sufijo: str) -> bytes:
    """PDF de GCS con cache propia en disco.

    Cache separada de la de D-EXP-1 a proposito: son corpus distintos y
    conviene poder borrar uno sin tocar el otro.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local = CACHE_DIR / f"{fecha}_{sufijo}.pdf"
    if local.exists():
        return local.read_bytes()
    raw = store._gcs.bucket(store.bucket_name).blob(
        store._gcs_path(_gcs_injury_report_path(fecha, sufijo))
    ).download_as_bytes()
    local.write_bytes(raw)
    return raw


def gembox_pool() -> dict[str, dict] | None:
    """(n, jugo) por estatus del corpus GemBox, del results.json de D-EXP-1."""
    if not GEMBOX_RESULTS.exists():
        return None
    data = json.loads(GEMBOX_RESULTS.read_text(encoding="utf-8"))
    return pool_por_estatus(data["resumen"]["por_temporada"], CUT)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _freeze(args: argparse.Namespace) -> None:
    fechas = [r["date"] for s in args.seasons for r in publish_dates(s)]
    destino = OUT_DIR / AUDIT_FILE_NAME
    if destino.exists():
        print(f"YA CONGELADA (no se toca): {destino}")
        print(destino.read_text(encoding="utf-8"))
        return
    muestra = freeze_audit_sample(fechas, excluir=YA_AUDITADAS)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(muestra, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Muestra congelada en {destino} ({len(fechas)} fechas publish candidatas)")
    print(json.dumps(muestra, indent=2, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser(description="D-EXP-5: P(juega|estatus), corpus iTextSharp")
    ap.add_argument("--regresion", metavar="FECHA", default=None,
                    help="emite el listado de regresion de un documento y sale")
    ap.add_argument("--freeze-audit", action="store_true",
                    help="congela la muestra de auditoria y sale (correr ANTES)")
    ap.add_argument("--limit", type=int, default=None, help="N fechas por temporada (humo)")
    ap.add_argument("--seasons", nargs="+", default=list(SEASONS))
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.freeze_audit:
        _freeze(args)
        return

    if args.regresion:
        st = dexp1.make_store()
        rec = next(r for r in publish_dates(args.seasons[0]) if r["date"] == args.regresion)
        pdf = fetch_pdf(st, args.regresion, rec["cuts"][CUT]["suffix"])
        destino = OUT_DIR / f"regresion_{args.regresion}.txt"
        destino.write_text(render_regresion(pdf, args.regresion), encoding="utf-8")
        print(f"Listado de regresion: {destino}")
        return

    store = dexp1.make_store()
    equipos = dexp1.load_teams(store)
    nombres = store.load_player_names()
    idx = NameIndex.from_player_map(nombres)
    print(f"Catalogo: {len(equipos)} equipos | {len(nombres)} jugadores (tabla players)")

    instancias: list[dict] = []
    sin_match_nombres: dict[str, set[str]] = defaultdict(set)
    sin_match_equipos: set[str] = set()
    fechas_excluidas: dict[str, list[str]] = defaultdict(list)
    layouts_vistos: Counter = Counter()
    fechas_ok: dict[str, int] = defaultdict(int)

    for season in args.seasons:
        print(f"\n=== {season} ===", flush=True)
        juegos, minutos = dexp1.load_truth(store, season)
        registros = publish_dates(season)
        if args.limit:
            registros = registros[: args.limit]
        print(f"  {len(juegos)} fechas con partidos | {len(registros)} fechas con publish",
              flush=True)

        for n, rec in enumerate(registros, 1):
            fecha, sufijo = rec["date"], rec["cuts"][CUT]["suffix"]
            try:
                reporte = parse_pdf_any(fetch_pdf(store, fecha, sufijo))
            except UnknownLayoutError:
                # Deliberado: el tramo sin lista blanca NO se fuerza a un
                # layout. Se cuenta y se sigue (D-RES-3d lo resolvera).
                fechas_excluidas["layout_desconocido"].append(fecha)
                continue
            except ParserCoverageError as exc:
                fechas_excluidas["cobertura_insuficiente"].append(f"{fecha}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 — se registra y se sigue
                fechas_excluidas["ilegible"].append(f"{fecha}: {exc!r}")
                continue

            layouts_vistos[reporte.layout] += 1
            fechas_ok[season] += 1
            era_esp = era_expected(fecha)
            if era_esp != "tramo_desconocido" and era_esp != reporte.layout:
                fechas_excluidas["layout_inesperado"].append(
                    f"{fecha}: esperado {era_esp}, leido {reporte.layout}")

            for row in dexp1.filter_rows_for_date(reporte.rows, fecha):
                st = dexp1.status_name(row)
                if st not in STATUSES:
                    continue
                tid = equipos.get(dexp1.normalize_team(row.team))
                pid = idx.match(row.player_name)
                gid = dexp1.game_id_for_team(juegos.get(fecha, []), tid)
                veredicto, p1, p2 = dexp1.classify_instance(tid, pid, gid, minutos)
                if veredicto == "sin_match_nombre":
                    sin_match_nombres[season].add(row.player_name)
                elif veredicto == "sin_match_equipo":
                    sin_match_equipos.add(row.team)
                instancias.append({
                    "season": season, "cut": CUT, "date": fecha, "suffix": sufijo,
                    "layout": reporte.layout,
                    "team": row.team, "player_name": row.player_name, "status": st,
                    "matchup": row.matchup,
                    "coherent": getattr(row, "coherent", True),
                    "reason": row.reason, "category": row.category,
                    "previous_status": row.previous_status,
                    "team_id": tid, "player_id": pid, "game_id": gid,
                    "minutes": minutos.get((gid, pid)) if gid and pid else None,
                    "veredicto": veredicto, "jugo_primaria": p1, "jugo_secundaria": p2,
                })
            if n % 25 == 0:
                print(f"  {n}/{len(registros)} fechas", flush=True)

    # --- agregacion (funciones de D-EXP-1, sin reimplementar) --------------
    resumen = dexp1.summarize(instancias)
    resumen["sin_match_nombres_distintos"] = {s: len(v) for s, v in sin_match_nombres.items()}
    resumen["sin_match_nombres"] = {s: sorted(v) for s, v in sin_match_nombres.items()}
    resumen["equipos_sin_match"] = sorted(sin_match_equipos)
    resumen["layouts"] = dict(layouts_vistos)
    resumen["fechas_parseadas"] = dict(fechas_ok)
    resumen["fechas_excluidas"] = {k: v for k, v in fechas_excluidas.items()}
    resumen["fechas_excluidas_conteo"] = {k: len(v) for k, v in fechas_excluidas.items()}
    resumen["available"] = available_breakdown(instancias)

    # Invariante interno por layout: la tasa que D-RES-3e mide antes/despues.
    inc: dict[str, dict] = {}
    for i in instancias:
        c = inc.setdefault(i["layout"], {"filas": 0, "incoherentes": 0})
        c["filas"] += 1
        c["incoherentes"] += int(not i.get("coherent", True))
    for lay, v in inc.items():
        v["tasa"] = round(v["incoherentes"] / v["filas"], 5) if v["filas"] else None
    resumen["incoherencia_por_layout"] = inc

    pool_leg = pool_por_estatus(resumen["por_temporada"])
    gem = gembox_pool()
    resumen["pool_legacy"] = pool_leg
    if gem:
        resumen["pool_gembox"] = gem
        resumen["comparativa"] = {
            st: diff_ci(pool_leg[st]["jugo"], pool_leg[st]["n"], gem[st]["jugo"], gem[st]["n"])
            for st in STATUSES if st in pool_leg and st in gem
        }
        resumen["divergencias_mayores_5pp"] = divergencias(pool_leg, gem)

    # tasa de sin_match por temporada, contra el umbral de 5% del pre-registro
    tasas: dict[str, float] = {}
    for s in args.seasons:
        del_s = [i for i in instancias if i["season"] == s]
        if del_s:
            nm = sum(1 for i in del_s if i["veredicto"] == "sin_match_nombre")
            tasas[s] = round(nm / len(del_s), 4)
    resumen["tasa_sin_match_nombre"] = tasas

    (OUT_DIR / "results.json").write_text(
        json.dumps({"n_instancias": len(instancias), "resumen": resumen},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / "instancias.json").write_text(
        json.dumps(instancias, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    # --- listados de auditoria (muestra congelada ANTES) ------------------
    audit_file = OUT_DIR / AUDIT_FILE_NAME
    if audit_file.exists():
        fechas_listar = dict(json.loads(audit_file.read_text(encoding="utf-8"))[
            "elegidas_antes_de_computar"])
        # 2018-12-18 no es muestra: es la REGRESION VISIBLE del defecto (fecha
        # rancia sobre fila NYS y VanVleet -> Minnesota). Se emite siempre.
        fechas_listar["REGRESION"] = "2018-12-18"
        for era, fecha in fechas_listar.items():
            filas = [i for i in instancias if i["date"] == fecha]
            marcas = {}
            if filas:
                pdf = fetch_pdf(store, fecha, filas[0]["suffix"])
                marcas = frontier_flags(pdf, legacy.detect_layout(pdf))
            destino = OUT_DIR / f"auditoria_dirigida_{fecha}.txt"
            destino.write_text(render_audit_dirigido(instancias, fecha, marcas),
                               encoding="utf-8")
            print(f"Listado dirigido ({era}): {destino}")
    else:
        print("ATENCION: no hay audit_dates.json — la muestra no estaba congelada")

    # --- diagnostico de instrumento si hay divergencia > 5pp --------------
    for st in resumen.get("divergencias_mayores_5pp", []):
        destino = OUT_DIR / f"diagnostico_{st}.txt"
        destino.write_text(render_diagnostico(instancias, st), encoding="utf-8")
        print(f"DIVERGENCIA > 5pp en {st} -> diagnostico de instrumento: {destino}")

    tabla = dexp1.render_table(resumen)
    comp = render_comparativa(pool_leg, gem) if gem else "(sin results.json de D-EXP-1)"
    (OUT_DIR / "tabla.txt").write_text(tabla + "\n\n" + comp, encoding="utf-8")
    print(tabla)
    print("\n" + comp)
    print("\nAvailable por categoria explicita / por razon / concordancia:")
    print(json.dumps(resumen["available"], indent=2, ensure_ascii=False))
    print(f"\nInstancias: {len(instancias)} | layouts: {dict(layouts_vistos)}")
    print(f"Fechas excluidas: {resumen['fechas_excluidas_conteo'] or 'ninguna'}")
    print(f"Tasa sin_match_nombre por temporada (umbral 5%): {tasas}")
    print(f"Escrito en {OUT_DIR}/")


if __name__ == "__main__":
    main()
