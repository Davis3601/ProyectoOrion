"""
Injury Report PDF de la NBA — descubrimiento, parseo, matching y ausencias.

FUENTE CANÓNICA
==============
PDFs oficiales en:
  https://ak-static.cms.nba.com/referee/injury/
  Injury-Report_{YYYY-MM-DD}_{sufijo}.pdf

Dos formatos de sufijo observados (spike 2026-08-21):
  - Nuevo (2026+): {HH}_{MM}{AM|PM}  ej. 01_15PM, 10_00AM
  - Viejo (pre-2025): {HH}{AM|PM}    ej. 06AM, 12PM, 11PM

Servidor AmazonS3, sin WAF. URLs inexistentes → 403 XML (no 404):
condición de existencia = HEAD status==200.
Accesible desde datacenter Cloud Run (verificado 2026-08-22).

TRES FIXES DEL SPIKE (obligatorios)
====================================
(a) Sufijos romanos/Jr. comprimidos sin espacio en el PDF:
    "ButlerIII, Jimmy" → "Butler III, Jimmy" (regex antes de PLAYER_PAT).
(b) Continuación de razón: toda línea de continuación (incluyendo líneas
    que empiezan con una nueva categoría de razón) se acumula en el campo
    reason del jugador actual — nunca crea filas adicionales.
    Problema de geometría: extract_text() lineariza por Y; la razón del
    jugador N puede interleavearse con la fila del jugador N+1 en el texto
    plano. Acumular en lugar de crear filas extra es más robusto.
    Detección de jugador embebido: si una línea "continuation" contiene un
    PLAYER_PAT precedido de texto de razón (ej. "Injury/Illness - …;
    McConnell, T.J.  Probable  Soreness"), el fragmento anterior se añade
    al jugador actual y se recupera el nuevo jugador — nunca se pierde.
(c) NOT YET SUBMITTED → registro de equipo (lista separada), no fila de
    jugador fantasma. El nombre del equipo se extrae sin la cabecera del
    partido (fecha, hora, matchup) que a veces aparece en la misma línea.

MÓDULO AUTÓNOMO (13e-1)
========================
No integrado a ingest_job todavía — la integración es 13e-2.
Proporciona las primitivas; el endpoint "predicciones del día" las invoca.
"""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from nba_predictor.storage.base import DataStore

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes de configuración
# ---------------------------------------------------------------------------

# URL base del servidor S3 de injury reports de la NBA.
# No documentado oficialmente — monitorear si deja de responder.
INJURY_REPORT_URL_TEMPLATE: str = (
    "https://ak-static.cms.nba.com/referee/injury/"
    "Injury-Report_{date}_{suffix}.pdf"
)

INJURY_REPORT_MAX_REQUESTS_DEFAULT: int = 20

# ---------------------------------------------------------------------------
# Tipos de datos
# ---------------------------------------------------------------------------


class InjuryStatus(str, Enum):
    OUT = "Out"
    DOUBTFUL = "Doubtful"
    QUESTIONABLE = "Questionable"
    PROBABLE = "Probable"
    AVAILABLE = "Available"


@dataclass
class InjuryRow:
    """Una fila de jugador en el injury report."""

    game_date: str         # MM/DD/YYYY del PDF
    game_time: str         # HH:MM(ET), best-effort (puede estar vacío)
    matchup: str           # ABR@ABR del matchup column
    team: str              # Nombre del equipo tal como aparece en el PDF
    player_name: str       # Formato PDF: "Apellido [Sufijo], Nombre"
    status: InjuryStatus
    reason: str


@dataclass
class AbsenceResult:
    """Resultado de conversión de un injury report a ausencias tipadas.

    El consumer de get_absences() recibe esto y decide cómo tratarlo.
    Equipos NOT YET SUBMITTED: absences[team] = [], not_submitted_teams incluye el equipo.
    El consumer no debe asumir disponibilidad completa para esos equipos.
    """

    target_date: str                         # YYYY-MM-DD
    snapshot_url: str                        # URL del PDF descargado
    snapshot_suffix: str                     # sufijo del PDF, ej. "01_15PM"
    fetched_at: str                          # ISO 8601 UTC del momento de descarga
    absences: dict[str, list[int]]           # team_name → [player_id, …] (solo Out)
    not_submitted_teams: list[str]           # equipos con NOT YET SUBMITTED
    status_counts: dict[str, int]            # distribución completa de status
    unmatched_names: list[str]               # nombres sin player_id encontrado


@dataclass
class NysEntry:
    """Equipo con NOT YET SUBMITTED en el injury report.

    Incluye game_date para distinguir el partido al que corresponde el NYS:
    un equipo puede tener filas de jugadores en 03/13 y NYS en 03/14
    (partidos distintos en la misma publicación del reporte).
    """
    team: str        # nombre del equipo tal como aparece en el PDF
    game_date: str   # MM/DD/YYYY heredado del contexto al momento del NYS


# ---------------------------------------------------------------------------
# Generación de candidatos de sufijo — funciones puras
# ---------------------------------------------------------------------------


def _suffix_candidates_new() -> list[str]:
    """Candidatos formato 2026+: {HH}_{MM}{AM|PM}, de más tarde a más temprano.

    El injury report se publica típicamente en PM, por eso PM va primero.
    Minutos: 45, 30, 15, 00 (únicos publicados; la NBA no usa granularidad menor).
    """
    cands: list[str] = []
    # PM: 11:45PM → 12:00PM (horas en orden 11→1→12 para ir de más tarde a más temprano)
    for h in [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 12]:
        for m in [45, 30, 15, 0]:
            cands.append(f"{h:02d}_{m:02d}PM")
    # AM: 11:45AM → 12:00AM
    for h in [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 12]:
        for m in [45, 30, 15, 0]:
            cands.append(f"{h:02d}_{m:02d}AM")
    return cands


def _suffix_candidates_old() -> list[str]:
    """Candidatos formato pre-2025: {HH}{AM|PM}, de más tarde a más temprano."""
    cands: list[str] = []
    for h in [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 12]:
        cands.append(f"{h:02d}PM")
    for h in [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 12]:
        cands.append(f"{h:02d}AM")
    return cands


def _all_suffix_candidates(last_suffix_hint: str | None = None) -> list[str]:
    """Lista combinada de candidatos: nuevo formato primero (más probable para 2026).

    Si se pasa last_suffix_hint (sufijo del último PDF exitoso del mismo job),
    se coloca al inicio — primer intento con el sufijo más probable del día.
    El resto de la lista mantiene el orden "más tarde a más temprano".
    """
    new_cands = _suffix_candidates_new()
    old_cands = _suffix_candidates_old()
    all_cands = list(dict.fromkeys(new_cands + old_cands))  # dedup, orden preservado

    if last_suffix_hint and last_suffix_hint in all_cands:
        all_cands = [last_suffix_hint] + [c for c in all_cands if c != last_suffix_hint]

    return all_cands


# ---------------------------------------------------------------------------
# Descubrimiento del snapshot más reciente
# ---------------------------------------------------------------------------


def discover_latest_snapshot(
    target_date: str,
    *,
    session: requests.Session | None = None,
    max_requests: int = INJURY_REPORT_MAX_REQUESTS_DEFAULT,
    last_suffix_hint: str | None = None,
    timeout: int = 10,
) -> tuple[str, str]:
    """Encuentra la URL del PDF más reciente para target_date via HEAD probing.

    Itera los candidatos de más tarde a más temprano. Condición de éxito:
    HEAD status==200 (los inexistentes devuelven 403 XML, nunca 404).

    Args:
        target_date: fecha en formato YYYY-MM-DD (ej. "2026-03-13").
        session: requests.Session a reutilizar (se crea uno si None).
        max_requests: presupuesto máximo de HEAD requests (default 20).
        last_suffix_hint: sufijo del último éxito para intentar primero.
        timeout: timeout en segundos por request HEAD.

    Returns:
        (url, suffix) del snapshot más tardío encontrado en target_date.

    Raises:
        RuntimeError: si no se encuentra ningún PDF en el presupuesto.
    """
    sess = session or requests.Session()
    candidates = _all_suffix_candidates(last_suffix_hint)

    tried: list[str] = []
    for suffix in candidates[:max_requests]:
        url = INJURY_REPORT_URL_TEMPLATE.format(date=target_date, suffix=suffix)
        tried.append(suffix)
        try:
            resp = sess.head(url, timeout=timeout)
            if resp.status_code == 200:
                _log.info(
                    "Injury report encontrado: %s_%s (%d bytes HEAD Content-Length)",
                    target_date, suffix, resp.headers.get("Content-Length", 0),
                )
                return url, suffix
            _log.debug("HEAD %s → %d", suffix, resp.status_code)
        except requests.exceptions.Timeout:
            _log.debug("HEAD %s → timeout", suffix)
        except requests.exceptions.RequestException as exc:
            _log.debug("HEAD %s → error: %r", suffix, exc)

    raise RuntimeError(
        f"No se encontró el injury report para {target_date} tras "
        f"{len(tried)} intentos (budget={max_requests}). "
        f"Sufijos probados: {tried[:10]}{'...' if len(tried) > 10 else ''}. "
        f"Puede ser festivo, día sin partidos o cambio en el esquema de nombrado del servidor."
    )


def download_snapshot(
    url: str,
    session: requests.Session | None = None,
    timeout: int = 20,
) -> bytes:
    """Descarga el PDF de injury report y verifica que sea un PDF válido.

    Raises:
        requests.HTTPError: si el servidor devuelve un status != 2xx.
        RuntimeError: si el contenido no empieza con %PDF (respuesta inesperada).
    """
    sess = session or requests.Session()
    resp = sess.get(url, timeout=timeout)
    resp.raise_for_status()
    if resp.content[:4] != b"%PDF":
        raise RuntimeError(
            f"La URL {url} devolvió {resp.status_code} pero el contenido no es PDF "
            f"(primeros bytes: {resp.content[:8]!r}). "
            f"Verificar si el servidor cambió su formato de respuesta."
        )
    _log.info("PDF descargado: %s (%d KB)", url, len(resp.content) // 1024)
    return resp.content


# ---------------------------------------------------------------------------
# Parser PDF — regexes y state machine
# ---------------------------------------------------------------------------

_DATE_PAT = re.compile(r"(\d{2}/\d{2}/\d{4})")
_TIME_PAT = re.compile(r"(\d{1,2}:\d{2}\(ET\))")
_MATCHUP_PAT = re.compile(r"\b([A-Z]{2,3}@[A-Z]{2,3})\b")
_STATUS_PAT = re.compile(r"\b(Out|Doubtful|Questionable|Probable|Available)\b")
_NOT_YET_PAT = re.compile(r"NOT\s*YET\s*SUBMITTED|NOTYET", re.I)

# Nombre en formato PDF: "LastName [Suffix], FirstName"
# El grupo (?:\s+(?:Jr\.|II|III|IV|V|Sr\.)) captura sufijos después de un espacio,
# para distinguir "Butler III" (correcto) de "ButlerIII" (comprimido, fix a).
# El último grupo incluye '.' para capturar iniciales como "T.J." o "J.R.".
_PLAYER_PAT = re.compile(
    r"([A-Z][a-zA-Z'\-\.]+(?:\s+(?:Jr\.|II|III|IV|V|Sr\.))?,\s*[A-Z][a-zA-Z'\-\.]+)"
)

# Fix (a): sufijo romano comprimido sin espacio — "ButlerIII," → "Butler III,"
# Se aplica ANTES del PLAYER_PAT para garantizar que el matching sea correcto y
# que la inversión PDF→canónico dé "Jimmy Butler III" (matchable en el NameIndex).
_SUFFIX_COMPRESS_PAT = re.compile(r"([a-z])(II|III|IV|V|Jr\.?|Sr\.?)(?=[,\s])")

# Categorías de razón conocidas. Se usa para distinguir texto de razón de nombre de
# equipo cuando aparece como prefijo de una línea con jugador embebido (ver player_m
# branch). Ya NO genera filas adicionales — toda continuación se acumula en reason.
_NEW_REASON_PAT = re.compile(
    r"^(Injury/Illness\s*-|G\s*League\s*-|Rest\b|Personal\s*Reasons?|"
    r"Not\s*Injury\s*Related|Coach's\s*Decision|DNP\s*-|Illness\b|"
    r"Conditioning|Return\s*to\s*Competition|Load\s*Management)",
    re.I,
)

_VALID_STATUSES: set[str] = {s.value for s in InjuryStatus}

# Pie de página que aparece en cada hoja: "Page N of M" o "PageNofM"
# (el PDF codifica el footer como token único sin espacios en extract_words)
_PAGE_PAT = re.compile(r"^Page\s*\d+\s*of\s*\d+$", re.I)


def _clean_team_name(s: str) -> str:
    """Elimina prefijo de fecha/hora/matchup de un string candidato a equipo."""
    s = re.sub(r"^\d{2}/\d{2}/\d{4}\s*", "", s)
    s = re.sub(r"^\d{1,2}:\d{2}\s*\(ET\)\s*", "", s)
    s = re.sub(r"^[A-Z]{2,3}@[A-Z]{2,3}\s*", "", s)
    return s.strip()


# ---------------------------------------------------------------------------
# Parser PDF — coordenadas (extract_words)
# ---------------------------------------------------------------------------

# Tolerancia Y para agrupar palabras en la misma fila visual.
# En el PDF de injury report, las líneas DENTRO de una misma celda de tabla
# están separadas ~7pt (razón multilínea y nombre del jugador en la misma
# fila visual), mientras que celdas DISTINTAS están separadas ~22pt.
# Con y_tol=10 se capturan los fragmentos de razón que aparecen 7pt antes
# del nombre del jugador sin fusionar filas de jugadores distintos.
_Y_GROUP_TOL: float = 10.0

# Tolerancia X (pts) en los límites de columna para absorber variación
# horizontal entre palabras adyacentes a la banda de columna.
_COL_TOL: float = 5.0

# Patrones a nivel de palabra individual (sin búsqueda de substring).
_DATE_WORD_PAT = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_TIME_WORD_PAT = re.compile(r"^\d{1,2}:\d{2}")   # "06:30" o "06:30(ET)"
_MATCHUP_WORD_PAT = re.compile(r"^[A-Z]{2,3}@[A-Z]{2,3}$")


def _group_words_into_visual_rows(
    words: list[dict], y_tol: float = _Y_GROUP_TOL
) -> list[list[dict]]:
    """Agrupa palabras de extract_words en filas visuales por coordenada 'top'.

    Las palabras de cada fila se ordenan de izquierda a derecha (por x0).
    La tolerancia y_tol absorbe variación de baseline dentro de un renglón
    tipográfico sin fusionar renglones adyacentes (separados ~10-14pt).
    """
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    rows: list[list[dict]] = []
    cur: list[dict] = [sorted_words[0]]
    cur_top: float = sorted_words[0]["top"]
    for w in sorted_words[1:]:
        if w["top"] - cur_top <= y_tol:
            cur.append(w)
        else:
            rows.append(sorted(cur, key=lambda ww: ww["x0"]))
            cur = [w]
            cur_top = w["top"]
    rows.append(sorted(cur, key=lambda ww: ww["x0"]))
    return rows


def _detect_column_anchors(
    visual_rows: list[list[dict]],
) -> tuple[float, float, float] | None:
    """Extrae (player_x, status_x, reason_x) desde la fila de encabezado.

    La fila de encabezado se identifica buscando en la misma fila una palabra
    que contenga "player" (p.ej. "PlayerName") y otra que contenga "status"
    (p.ej. "CurrentStatus"). Se usa búsqueda de subcadena (case-insensitive)
    para ser robusto a distintas versiones del PDF (el PDF de injury report usa
    "PlayerName" y "CurrentStatus", no "Player" y "Status" en texto plano).
    El anclaje por X —no por Y absoluta— corrige el fallo del spike anterior.

    Returns:
        (player_x, status_x, reason_x) o None si la página no tiene encabezado.
    """
    for row in visual_rows:
        player_word = next(
            (w for w in row if "player" in w["text"].lower()), None
        )
        status_word = next(
            (w for w in row if "status" in w["text"].lower()), None
        )
        if player_word and status_word:
            player_x = player_word["x0"]
            status_x = status_word["x0"]
            after_status = [w for w in row if w["x0"] > status_x + 2]
            reason_x = after_status[0]["x0"] if after_status else status_x + 60.0
            return player_x, status_x, reason_x
    return None


def parse_pdf(pdf_bytes: bytes) -> tuple[list[InjuryRow], list[NysEntry]]:
    """Parsea el PDF de injury report con pdfplumber.extract_words() + coordenadas.

    Reemplaza la implementación anterior (extract_text + state machine), que
    linearizaba el contenido Y y generaba cuatro capas del mismo bug de
    interleaving: fragmentos de razón en fila equivocada, filas fantasma,
    atribución de equipo al bloque siguiente y fronteras rotas en transiciones
    de fecha.

    Diseño:
    1. extract_words() devuelve una entrada por token con su bounding box exacto.
    2. Los tokens se agrupan en filas visuales por coordenada Y (_Y_GROUP_TOL = 2pt)
       sin ninguna linearización.
    3. La fila de encabezado ('Player' + 'Status' en la misma fila) calibra las
       bandas X. Anclaje por X, no por Y — corrige el fallo del spike.
    4. Cada fila de datos se divide en cuatro zonas por x0 vs. anclas:
         info    (x0 < player_x - COL_TOL)          : fecha/hora/matchup/equipo
         player  [player_x-COL_TOL, status_x-COL_TOL): nombre del jugador
         status  [status_x-COL_TOL, reason_x-COL_TOL): Out/Questionable/…
         reason  (x0 >= reason_x - COL_TOL)          : razón/lesión
    5. Celdas vacías (fecha, equipo) se propagan hacia abajo por herencia.
    6. flush() siempre precede a cualquier actualización de ctx, preservando
       el invariante de que el último jugador de cada bloque se emite con su
       equipo correcto. Sin linearización, el equipo nuevo ya aparece en
       info_words antes de que llegue el jugador nuevo.
    7. Las razones multilínea se adjuntan geométricamente: filas con solo
       columna reason acumulan en el jugador actual. Sin linearización no
       hay interleavings entre jugadores adyacentes.

    Args:
        pdf_bytes: contenido binario del PDF.

    Returns:
        Tupla (player_rows, nys_entries):
        - player_rows: lista de InjuryRow (una por jugador).
        - nys_entries: lista de NysEntry(team, game_date) para equipos NYS.

    Raises:
        ValueError: si aparece un status no reconocido (layout del PDF cambió).
        ImportError: si pdfplumber no está instalado.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise ImportError(
            "pdfplumber es necesario para parsear el injury report PDF. "
            "Instala con: pip install pdfplumber"
        ) from exc

    rows: list[InjuryRow] = []
    nys_entries: list[NysEntry] = []
    col_anchors: tuple[float, float, float] | None = None
    ctx: dict[str, str] = {
        "date": "", "time": "", "matchup": "", "team": "",
        "player": "", "status": "", "reason": "",
    }

    def flush() -> None:
        if not ctx["player"]:
            return
        raw_status = ctx["status"]
        if not raw_status:
            _log.warning("Jugador sin status ignorado: %r", ctx["player"])
            ctx["player"] = ctx["status"] = ctx["reason"] = ""
            return
        if raw_status not in _VALID_STATUSES:
            raise ValueError(
                f"Status no reconocido en el PDF: {raw_status!r} "
                f"(jugador: {ctx['player']!r}). "
                f"El layout del injury report puede haber cambiado."
            )
        reason = ctx["reason"]
        # Guarda: si el campo reason acumulado contiene un patrón de jugador
        # (Apellido, Nombre o iniciales) seguido de un status válido, es probable
        # que una fila de jugador haya sido absorbida por la lógica de continuación.
        _emb_m = _PLAYER_PAT.search(reason)
        if _emb_m and _STATUS_PAT.search(reason[_emb_m.end():]):
            _log.warning(
                "Fila embebida potencial en reason de %r (status=%r): %r — "
                "revisar si falta un jugador en la salida.",
                ctx["player"], raw_status, reason,
            )
        rows.append(InjuryRow(
            game_date=ctx["date"],
            game_time=ctx["time"],
            matchup=ctx["matchup"],
            team=ctx["team"],
            player_name=ctx["player"],
            status=InjuryStatus(raw_status),
            reason=reason,
        ))
        ctx["player"] = ctx["status"] = ctx["reason"] = ""

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(
                x_tolerance=3, y_tolerance=3,
                keep_blank_chars=False, use_text_flow=False,
            )
            visual_rows = _group_words_into_visual_rows(words)

            # Re-calibrar anclas si esta página tiene fila de encabezado.
            page_anchors = _detect_column_anchors(visual_rows)
            if page_anchors is not None:
                col_anchors = page_anchors
            if col_anchors is None:
                _log.warning(
                    "Anclas de columna no detectadas en página %d — omitida.",
                    page.page_number,
                )
                continue

            player_x, status_x, reason_x = col_anchors

            for row_words in visual_rows:
                if not row_words:
                    continue

                row_texts = {w["text"] for w in row_words}
                row_joined = " ".join(w["text"] for w in row_words)

                # Encabezado de columnas (ya calibrado) → omitir como dato.
                # La condición espeja _detect_column_anchors: subcadena "player"
                # y "status" en palabras distintas de la misma fila visual.
                has_player_hdr = any("player" in w["text"].lower() for w in row_words)
                has_status_hdr = any("status" in w["text"].lower() for w in row_words)
                if has_player_hdr and has_status_hdr:
                    continue

                # Pie de página "Page N of M"
                if _PAGE_PAT.match(row_joined):
                    continue

                # Encabezado de publicación ("Injury Report: ...")
                if re.match(r"Injury\s+Report\s*:", row_joined, re.I):
                    flush()
                    continue

                # Título del documento ("NBA Official Injury Report…")
                if re.match(r"NBA\s+(Official\s+)?Injury\s+Report", row_joined, re.I):
                    continue

                # NOT YET SUBMITTED
                if _NOT_YET_PAT.search(row_joined):
                    flush()
                    pre_nys = _NOT_YET_PAT.split(row_joined)[0].strip()
                    dm = _DATE_PAT.search(pre_nys)
                    mm = _MATCHUP_PAT.search(pre_nys)
                    if dm:
                        ctx["date"] = dm.group(1)
                    if mm:
                        ctx["matchup"] = mm.group(1)
                    nys_team = _clean_team_name(pre_nys)
                    nys_entries.append(NysEntry(
                        team=nys_team or ctx["team"],
                        game_date=ctx["date"],
                    ))
                    ctx["player"] = ctx["status"] = ctx["reason"] = ""
                    continue

                # — Dividir fila por columna X —
                info_words = [w for w in row_words if w["x0"] < player_x - _COL_TOL]
                player_words = [
                    w for w in row_words
                    if player_x - _COL_TOL <= w["x0"] < status_x - _COL_TOL
                ]
                status_words = [
                    w for w in row_words
                    if status_x - _COL_TOL <= w["x0"] < reason_x - _COL_TOL
                ]
                reason_words = [w for w in row_words if w["x0"] >= reason_x - _COL_TOL]

                status_text = " ".join(w["text"] for w in status_words).strip()
                reason_text = " ".join(w["text"] for w in reason_words).strip()

                # Fix (a): sufijos romanos comprimidos ("ButlerIII") en player col
                raw_player_text = " ".join(w["text"] for w in player_words).strip()
                player_text = _SUFFIX_COMPRESS_PAT.sub(r"\1 \2", raw_player_text)
                player_m = _PLAYER_PAT.search(player_text)

                has_valid_status = bool(_STATUS_PAT.search(status_text))

                # — Sub-parsear info_words: fecha, hora, matchup, equipo —
                new_date = new_time = new_matchup = new_team = ""
                if info_words:
                    exclude_idx: set[int] = set()
                    for i, w in enumerate(info_words):
                        t = w["text"]
                        if _DATE_WORD_PAT.fullmatch(t):
                            new_date = t
                            exclude_idx.add(i)
                        elif _TIME_WORD_PAT.match(t):
                            new_time = _TIME_WORD_PAT.match(t).group(0)
                            exclude_idx.add(i)
                        elif _MATCHUP_WORD_PAT.fullmatch(t):
                            new_matchup = t
                            exclude_idx.add(i)
                        elif t.startswith("(") and t.endswith(")"):
                            # Anotación separada "(ET)" — no es parte del equipo
                            exclude_idx.add(i)
                        elif _PAGE_PAT.match(t):
                            # Footer de página ("Page1of10") fusionado con fila de jugador
                            # por y_tol; excluir para que no contamine el equipo.
                            exclude_idx.add(i)
                    new_team = " ".join(
                        info_words[i]["text"]
                        for i in range(len(info_words))
                        if i not in exclude_idx
                    ).strip()

                if player_m:
                    # — Nueva fila de jugador —
                    # flush() ANTES de cualquier actualización de ctx:
                    # el jugador en curso pertenece al bloque anterior.
                    flush()
                    if new_date:
                        ctx["date"] = new_date
                    if new_time:
                        ctx["time"] = new_time
                    if new_matchup:
                        ctx["matchup"] = new_matchup
                    if new_team:
                        ctx["team"] = new_team
                    ctx["player"] = player_m.group(1).strip()
                    ctx["status"] = status_text if has_valid_status else ""
                    ctx["reason"] = reason_text

                elif not player_words and reason_words and not info_words:
                    # — Fila de continuación: solo columna reason (y quizás status) —
                    # La geometría garantiza que estos fragmentos pertenecen al
                    # jugador actual; sin linearización no hay interleaving.
                    if ctx["player"]:
                        if status_text and status_text in _VALID_STATUSES:
                            _log.warning(
                                "Status válido %r en fila de continuación — ignorado.",
                                status_text,
                            )
                            extra = reason_text
                        else:
                            extra = " ".join(
                                p for p in (status_text, reason_text) if p
                            ).strip()
                        ctx["reason"] = (ctx["reason"] + " " + extra).strip()

                elif info_words and not player_words:
                    # — Solo info_words sin jugador: cambio de fecha/matchup/equipo —
                    flush()
                    if new_date:
                        ctx["date"] = new_date
                    if new_time:
                        ctx["time"] = new_time
                    if new_matchup:
                        ctx["matchup"] = new_matchup
                    if new_team:
                        ctx["team"] = new_team

                # else: fila con contenido solo en status/reason sin jugador
                # ni info — ruido o artefacto, ignorar.

    flush()
    return rows, nys_entries


# ---------------------------------------------------------------------------
# Normalización de nombres y NameIndex
# ---------------------------------------------------------------------------


def _normalize_name(name: str) -> str:
    """ASCII, minúsculas, sin puntuación, sufijos romanos eliminados para la clave.

    La clave sin sufijo permite matches insensibles a "Jr." y "III" que suelen
    estar ausentes o inconsistentes entre la fuente del PDF y el corpus histórico.
    """
    from unicodedata import normalize as unorm_
    # Separar CamelCase antes de cualquier otra transformación: el PDF concatena
    # nombres de varios tokens ("YanicKonan") en un único token sin espacios.
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    # Quitar sufijos comunes antes de normalizar
    name = re.sub(r"\b(Jr\.?|Sr\.?|II|III|IV|V|VI)\b", "", name, flags=re.I)
    name = unorm_("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-z ]", "", name.lower())
    return " ".join(name.split())


def _normalize_name_with_suffix(name: str) -> str:
    """Como _normalize_name pero conserva los sufijos (desempate)."""
    from unicodedata import normalize as unorm_
    # Separar CamelCase: igual que _normalize_name (PDF concatena tokens).
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    name = unorm_("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-z ]", "", name.lower())
    return " ".join(name.split())


def _invert_pdf_name(pdf_name: str) -> str:
    """Invierte "Apellido [Sufijo], Nombre" → "Nombre Apellido [Sufijo]".

    Aplica fix (a) interno: "ButlerIII, Jimmy" → "Butler III, Jimmy" → "Jimmy Butler III".
    """
    # Fix (a) a nivel de nombre individual (por si el parse_pdf no lo vio)
    fixed = _SUFFIX_COMPRESS_PAT.sub(r"\1 \2", pdf_name)
    parts = [p.strip() for p in fixed.split(",", 1)]
    if len(parts) == 2:
        return f"{parts[1]} {parts[0]}"
    return pdf_name


class NameIndex:
    """Mapa nombre-normalizado → player_id para matching de nombres del PDF.

    Cascada de matching (fail-fast conservador):
    1. Invertir "Apellido, Nombre" → "Nombre Apellido".
    2. Normalizar: ASCII, minúsculas, sin puntuación, sufijos romanos eliminados.
    3. Lookup exacto → si único match → devuelve player_id.
    4. Si no hay match: prueba con sufijo conservado como desempate.
    5. Si aún no hay match o es ambiguo → WARNING + None.

    Un match equivocado es peor que un miss: contaminaría availability_diff
    del jugador equivocado. Por eso nunca se usa matching fuzzy agresivo.
    """

    def __init__(self) -> None:
        # normalized_name (sin sufijo) → [player_id, ...]
        self._by_norm: dict[str, list[int]] = {}
        # normalized_name (con sufijo) → [player_id, ...]
        self._by_norm_with_suffix: dict[str, list[int]] = {}

    @classmethod
    def from_player_map(cls, player_map: dict[int, str]) -> "NameIndex":
        """Construye el índice desde player_id → player_name.

        Indexa por nombre normalizado sin sufijo (clave primaria) y
        con sufijo (desempate para nombres homónimos como "John Smith Jr.").
        """
        idx = cls()
        for player_id, full_name in player_map.items():
            norm = _normalize_name(full_name)
            idx._by_norm.setdefault(norm, []).append(player_id)
            norm_ws = _normalize_name_with_suffix(full_name)
            idx._by_norm_with_suffix.setdefault(norm_ws, []).append(player_id)
        return idx

    def match(self, pdf_name: str) -> int | None:
        """Intenta resolver el nombre PDF a un player_id del corpus.

        Args:
            pdf_name: nombre en formato "Apellido [Sufijo], Nombre" del PDF.

        Returns:
            player_id si el match es único y no ambiguo; None en caso contrario.
            Loggea WARNING si no hay match o si es ambiguo.
        """
        inverted = _invert_pdf_name(pdf_name)
        norm = _normalize_name(inverted)

        matches = self._by_norm.get(norm, [])
        if len(matches) == 1:
            return matches[0]

        if len(matches) == 0:
            # Intento con sufijo conservado (edge case: dos jugadores con mismo nombre base)
            norm_ws = _normalize_name_with_suffix(inverted)
            ws_matches = self._by_norm_with_suffix.get(norm_ws, [])
            if len(ws_matches) == 1:
                return ws_matches[0]
            _log.warning(
                "Nombre sin match en el índice: %r (normalizado: %r)",
                pdf_name, norm,
            )
            return None

        # Múltiples candidatos — intento desempate con sufijo
        norm_ws = _normalize_name_with_suffix(inverted)
        ws_matches = self._by_norm_with_suffix.get(norm_ws, [])
        if len(ws_matches) == 1:
            return ws_matches[0]

        _log.warning(
            "Nombre ambiguo: %r → %d candidatos: %s",
            pdf_name, len(matches), matches[:5],
        )
        return None


# ---------------------------------------------------------------------------
# Carga de nombres del corpus — funciones puras sobre el filesystem
# ---------------------------------------------------------------------------


def load_player_names_from_raw_json(
    raw_dir: Path,
    glob_pattern: str = "*.json",
) -> dict[int, str]:
    """Lee player_id → player_name desde JSON en formato legacy stats.nba.com.

    Itera todos los archivos del directorio — el índice crece con el corpus.
    Útil para LocalDataStore (data/raw/) y para pruebas offline.
    """
    players: dict[int, str] = {}
    for path in sorted(raw_dir.glob(glob_pattern)):
        try:
            data = json.loads(path.read_bytes())
            for rs in data.get("resultSets", []):
                headers = rs.get("headers", [])
                pid_idx = next(
                    (i for i, h in enumerate(headers) if h == "PLAYER_ID"), None
                )
                name_idx = next(
                    (i for i, h in enumerate(headers) if h == "PLAYER_NAME"), None
                )
                if pid_idx is not None and name_idx is not None:
                    for row in rs.get("rowSet", []):
                        pid = row[pid_idx]
                        name = row[name_idx]
                        if pid and name:
                            players[int(pid)] = name
        except Exception:  # JSON malformado — continuar
            continue
    return players


def load_player_names_from_cdn_json(
    raw_live_dir: Path,
    glob_pattern: str = "*.json",
) -> dict[int, str]:
    """Lee player_id → player_name desde JSON en formato CDN (boxscores_live/).

    Complementa load_player_names_from_raw_json para el corpus 2026+.
    Estructura CDN: {"game": {"homeTeam": {"players": [{"personId": X, "name": Y}]}}}
    """
    players: dict[int, str] = {}
    for path in sorted(raw_live_dir.glob(glob_pattern)):
        try:
            data = json.loads(path.read_bytes())
            game = data.get("game", {})
            for team_key in ("homeTeam", "awayTeam"):
                for player in game.get(team_key, {}).get("players", []):
                    pid = player.get("personId")
                    name = player.get("name")
                    if pid and name:
                        players[int(pid)] = name
        except Exception:
            continue
    return players


# ---------------------------------------------------------------------------
# Punto de entrada principal
# ---------------------------------------------------------------------------


def get_absences(
    target_date: str,
    *,
    ds: "DataStore",
    player_map: dict[int, str] | None = None,
    raw_dir: Path | None = None,
    raw_live_dir: Path | None = None,
    session: requests.Session | None = None,
    max_requests: int = INJURY_REPORT_MAX_REQUESTS_DEFAULT,
    last_suffix_hint: str | None = None,
    save_raw: bool = True,
) -> AbsenceResult:
    """Descarga el injury report más reciente y devuelve ausencias por equipo.

    Flujo:
    1. Discover → URL + suffix del snapshot más tardío del día.
    2. Download → PDF bytes.
    3. Save RAW vía ds.save_raw_injury_report() (si save_raw=True).
    4. Parse → (player_rows, nys_teams).
    5. Build NameIndex desde player_map o desde raw_dir/raw_live_dir.
    6. Match player_name → player_id para cada Out row.
    7. Devuelve AbsenceResult.

    Args:
        target_date: "YYYY-MM-DD".
        ds: DataStore para persistir el PDF crudo.
        player_map: dict player_id → player_name ya construido (si None,
            se intenta construir desde raw_dir y/o raw_live_dir).
        raw_dir: directorio de JSON legacy para construir player_map.
        raw_live_dir: directorio de JSON CDN para construir player_map.
        session: requests.Session a reutilizar.
        max_requests: presupuesto HEAD de descubrimiento.
        last_suffix_hint: sufijo del último éxito (caché externa).
        save_raw: si True, persiste el PDF via ds.save_raw_injury_report().

    Returns:
        AbsenceResult con ausencias tipadas.
    """
    url, suffix = discover_latest_snapshot(
        target_date,
        session=session,
        max_requests=max_requests,
        last_suffix_hint=last_suffix_hint,
    )
    pdf_bytes = download_snapshot(url, session=session)

    if save_raw:
        ds.save_raw_injury_report(target_date, suffix, pdf_bytes)

    player_rows, nys_entries = parse_pdf(pdf_bytes)

    # Construir NameIndex
    if player_map is None:
        merged: dict[int, str] = {}
        if raw_dir is not None:
            merged.update(load_player_names_from_raw_json(raw_dir))
        if raw_live_dir is not None:
            merged.update(load_player_names_from_cdn_json(raw_live_dir))
        if not merged:
            _log.warning(
                "NameIndex vacío: player_map=None y raw_dir/raw_live_dir no especificados. "
                "Todos los nombres quedarán sin match."
            )
        player_map = merged

    name_idx = NameIndex.from_player_map(player_map)

    # Conteos y matching
    status_counts: dict[str, int] = {}
    absences: dict[str, list[int]] = {}
    unmatched: list[str] = []

    for row in player_rows:
        status_counts[row.status.value] = status_counts.get(row.status.value, 0) + 1
        if row.status == InjuryStatus.OUT:
            team = row.team
            if team not in absences:
                absences[team] = []
            pid = name_idx.match(row.player_name)
            if pid is not None:
                if pid not in absences[team]:  # dedup por partido (misma lesión multi-fila)
                    absences[team].append(pid)
            else:
                if row.player_name not in unmatched:
                    unmatched.append(row.player_name)

    # Equipos NYS: lista vacía en absences + flag en not_submitted_teams
    for entry in nys_entries:
        if entry.team not in absences:
            absences[entry.team] = []

    return AbsenceResult(
        target_date=target_date,
        snapshot_url=url,
        snapshot_suffix=suffix,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        absences=absences,
        not_submitted_teams=[e.team for e in nys_entries],
        status_counts=status_counts,
        unmatched_names=unmatched,
    )
