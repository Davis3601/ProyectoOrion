"""
Parser de injury reports para el corpus LEGACY (generador iTextSharp) — D-RES-3c.

POR QUE UN MODULO APARTE: parse_pdf() de injury_report.py esta auditado a mano
en 6 rondas contra el corpus GemBox y es la ruta que sirve produccion hoy. El
corpus 2018-19..2022-23 lo genero iTextSharp con OTRO layout, y la auditoria
D-RES-3 midio que parse_pdf() devuelve CERO filas sobre el (sin lanzar: falla en
silencio). Tocar aquel parser para acomodar dos formatos arriesgaria la ruta
viva por un corpus historico; este modulo vive al lado y nadie mas lo llama
todavia.

DESPACHO POR LAYOUT, NO POR FECHA: detect_layout() lee el /Producer y los
tokens del encabezado. Una firma que no este en la lista blanca levanta
UnknownLayoutError con el productor y los tokens observados — jamas se intenta
parsear "a ver si sale". Un formato desconocido tratado como conocido es
exactamente como nacieron el gemelo de la capa 4 y el fallo silencioso del
cron.

ALCANCE MEDIDO (D-RES-3b + biseccion de D-RES-3c): el corpus iTextSharp tiene
MAS de dos layouts. La lista blanca de este modulo cubre ITEXT_V1 (hasta
2019-11-14) e ITEXT_V2 (desde 2020-01-07); entre 2019-11-15 y 2019-12-31 hay al
menos dos variantes mas, que aqui caen deliberadamente en UnknownLayoutError
hasta que se adjudiquen.

NO parsea nada por su cuenta en produccion: get_absences, el endpoint y el job
siguen usando parse_pdf(). Los conteos que produce NO son oficiales hasta la
auditoria humana de los listados (protocolo 13e-1).
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

import pdfplumber

from nba_predictor.config import INJURY_INCOHERENT_WARN_RATE
from nba_predictor.ingestion.injury_report import (
    InjuryRow,
    InjuryStatus,
    NysEntry,
    parse_pdf,
)

_log = logging.getLogger(__name__)

# Banda Y del encabezado: los 19 PDFs encuestados lo traen en top=58.0 y el
# fixture GemBox en top=107.7. El ancla es el token "Matchup", presente en
# ambas familias; la tolerancia absorbe el subpixel del generador.
_HEADER_ANCHOR = "Matchup"
_HEADER_Y_TOL = 3.0

# Margen inferior reservado al pie de pagina ("Page 1 of 3"). Se excluye por
# BANDA Y y no por texto: filtrar por el string dejaria pasar cualquier pie que
# cambie de redaccion, que es como se cuelan las filas fantasma.
_FOOTER_MARGIN = 28.0

# Holgura izquierda de las bandas X. Los datos se alinean ~1 pt a la IZQUIERDA
# del encabezado de su columna (medido: header "Game Time" en x=74.0, dato
# "07:00" en x=72.9), asi que una banda que empiece exactamente en el X0 del
# encabezado deja el dato en la columna anterior. Con 2 pt todavia se colaba;
# 4 pt separa limpio y sigue muy por debajo del hueco minimo entre columnas
# (53 pt en el layout mas apretado).
_BAND_EPSILON = 4.0

# Tolerancia vertical para considerar que dos tokens pertenecen a la MISMA
# linea fisica. El generador escribe la continuacion de una razon 4 pt por
# debajo del ancla, asi que debe ser menor que ese salto.
_ROW_Y_TOL = 2.0

# Cobertura minima exigida antes de devolver filas (guarda anti-silencio).
_MIN_COVERAGE = 0.8

# Linea candidata a fila de jugador en la capa de texto: "Apellido, Nombre".
# Deliberadamente independiente de la maquinaria de columnas — un conteo que
# dependiera del propio parser seria circular (leccion del 2026-08-22).
_CANDIDATE_LINE = re.compile(r"^[A-Z][^,]+, [A-Z]")

_STATUS_BY_TEXT = {s.value: s for s in InjuryStatus}

# Tricodes por nombre de equipo. SOLO alimentan el invariante interno (el equipo
# de una fila debe ser uno de los dos de su matchup); no participan en el
# parseo. Un nombre que no este aqui deja la fila como NO EVALUABLE, jamas la
# marca incoherente: el invariante avisa, nunca inventa. Los dos alias de Los
# Angeles existen porque el corpus escribe "LA Clippers" y "Los Angeles Lakers".
_TRICODE_BY_TEAM: dict[str, str] = {
    "atlantahawks": "ATL", "bostonceltics": "BOS", "brooklynnets": "BKN",
    "charlottehornets": "CHA", "chicagobulls": "CHI", "clevelandcavaliers": "CLE",
    "dallasmavericks": "DAL", "denvernuggets": "DEN", "detroitpistons": "DET",
    "goldenstatewarriors": "GSW", "houstonrockets": "HOU", "indianapacers": "IND",
    "laclippers": "LAC", "losangelesclippers": "LAC",
    "lalakers": "LAL", "losangeleslakers": "LAL",
    "memphisgrizzlies": "MEM", "miamiheat": "MIA", "milwaukeebucks": "MIL",
    "minnesotatimberwolves": "MIN", "neworleanspelicans": "NOP",
    "newyorkknicks": "NYK", "oklahomacitythunder": "OKC", "orlandomagic": "ORL",
    "philadelphia76ers": "PHI", "phoenixsuns": "PHX", "portlandtrailblazers": "POR",
    "sacramentokings": "SAC", "sanantoniospurs": "SAS", "torontoraptors": "TOR",
    "utahjazz": "UTA", "washingtonwizards": "WAS",
}

_MATCHUP_RE = re.compile(r"^([A-Z]{3})@([A-Z]{3})$")


@dataclass
class LegacyInjuryRow(InjuryRow):
    """InjuryRow del corpus legacy con el veredicto del invariante interno.

    Es una SUBCLASE y no un campo nuevo en InjuryRow para no tocar
    injury_report.py, la ruta auditada que sirve produccion. `coherent` solo
    significa algo donde el invariante corrio: las filas GemBox no lo llevan, y
    su ausencia no debe leerse como "coherente verificado".
    """

    coherent: bool = True


def _tricode(team: str) -> str | None:
    """Tricode de un nombre de equipo, o None si no esta en la referencia."""
    return _TRICODE_BY_TEAM.get(re.sub(r"[^a-z0-9]", "", team.lower()))


class UnknownLayoutError(RuntimeError):
    """El PDF no coincide con ninguna firma de la lista blanca."""


class ParserCoverageError(RuntimeError):
    """El parser devolvio muchas menos filas de las que el documento insinua."""


@dataclass(frozen=True)
class Column:
    """Una columna logica del reporte y el X0 donde empieza su encabezado."""

    name: str
    x0: float


@dataclass(frozen=True)
class Layout:
    """Firma de un layout: columnas ordenadas por X0.

    Las bandas se derivan [X0_i, X0_{i+1}) y la ULTIMA cierra en el ancho de
    pagina. Ninguna banda queda sin tope: una banda abierta por la derecha se
    traga la columna siguiente y produce datos verosimiles y falsos (medido el
    2026-09-13 en la encuesta de layouts, cuando Current Status sin tope
    devolvio 188 "estatus" que en realidad eran lesiones).
    """

    name: str
    columns: tuple[Column, ...]
    header_tokens: tuple[str, ...]

    def bands(self, page_width: float) -> list[tuple[str, float, float]]:
        xs = [c.x0 for c in self.columns]
        topes = xs[1:] + [page_width]
        return [(c.name, c.x0, tope) for c, tope in zip(self.columns, topes)]


# --- Lista blanca -----------------------------------------------------------
# X0 tomados de la encuesta D-RES-3b (redondeados a 5 pt). El emparejamiento se
# hace por SECUENCIA DE TOKENS; los X0 se usan para las bandas, no para decidir
# la firma, porque el generador mueve subpixeles entre corridas.

LAYOUT_ITEXT_V1 = Layout(
    name="ITEXT_V1",
    columns=(
        Column("game_date", 20.0),
        Column("game_time", 75.0),
        Column("matchup", 125.0),
        Column("team", 180.0),
        Column("player_name", 270.0),
        Column("category", 380.0),
        Column("reason", 495.0),
        Column("current_status", 605.0),
        Column("previous_status", 720.0),
    ),
    header_tokens=(
        "Game", "Date", "Game", "Time", "Matchup", "Team", "Player", "Name",
        "Category", "Reason", "Current", "Status", "Previous", "Status",
    ),
)

LAYOUT_ITEXT_V2 = Layout(
    name="ITEXT_V2",
    columns=(
        Column("game_date", 20.0),
        Column("game_time", 95.0),
        Column("matchup", 170.0),
        Column("team", 245.0),
        Column("player_name", 370.0),
        Column("current_status", 500.0),
        Column("reason", 605.0),
    ),
    header_tokens=(
        "Game", "Date", "Game", "Time", "Matchup", "Team", "Player", "Name",
        "Current", "Status", "Reason",
    ),
)

LAYOUT_GEMBOX = Layout(
    name="GEMBOX",
    columns=(),   # la ruta GemBox delega en parse_pdf(), que trae sus anclas
    header_tokens=(
        "GameDate", "GameTime", "Matchup", "Team", "PlayerName",
        "CurrentStatus", "Reason",
    ),
)

_WHITELIST: tuple[Layout, ...] = (LAYOUT_GEMBOX, LAYOUT_ITEXT_V1, LAYOUT_ITEXT_V2)


@dataclass
class ParsedReport:
    """Resultado del despacho: filas, NYS y de que layout salieron."""

    layout: str
    rows: list[InjuryRow]
    nys: list[NysEntry]
    n_candidates: int


# ---------------------------------------------------------------------------
# Deteccion de layout
# ---------------------------------------------------------------------------


def _producer(pdf_bytes: bytes) -> str:
    """/Producer del PDF. Se escanea el archivo COMPLETO, no la cabecera.

    iTextSharp escribe los metadatos al FINAL; un escaneo de los primeros KB
    devuelve "ausente" y llevo a un falso fallo de verificacion el 2026-09-13.
    """
    m = re.search(rb"/Producer\((.{0,40}?)\)", pdf_bytes, re.DOTALL)
    if not m:
        return "desconocido"
    return m.group(1).decode("latin-1", errors="replace").split("\x92")[0].strip()


def _header_words(page) -> list[dict]:
    """Palabras de la banda Y del encabezado, ordenadas por X."""
    words = page.extract_words()
    anchor = next((w for w in words if w["text"] == _HEADER_ANCHOR), None)
    if anchor is None:
        return []
    top = anchor["top"]
    return sorted(
        (w for w in words if abs(w["top"] - top) < _HEADER_Y_TOL),
        key=lambda w: w["x0"],
    )


def detect_layout(pdf_bytes: bytes) -> Layout:
    """Devuelve el Layout de la lista blanca que corresponde al PDF.

    Raises:
        UnknownLayoutError: firma fuera de la lista blanca. El mensaje lleva el
            productor y los tokens observados para que el siguiente humano sepa
            exactamente que variante encontro, sin volver a abrir el PDF.
    """
    productor = _producer(pdf_bytes)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        cab = _header_words(pdf.pages[0])
    tokens = tuple(w["text"] for w in cab)

    for layout in _WHITELIST:
        if tokens == layout.header_tokens:
            return layout

    raise UnknownLayoutError(
        f"Layout no reconocido. /Producer={productor!r}; "
        f"tokens del encabezado={list(tokens)}. "
        f"Firmas conocidas: {[lay.name for lay in _WHITELIST]}. "
        f"El corpus iTextSharp tiene variantes sin adjudicar entre 2019-11-15 "
        f"y 2019-12-31 (ver CLAUDE.md, D-RES-3c)."
    )


# ---------------------------------------------------------------------------
# Reconstruccion de filas por bandas Y ancladas a Player Name
# ---------------------------------------------------------------------------


def _cell_text(words: list[dict], x_ini: float, x_fin: float) -> str:
    """Texto de las palabras que caen en una banda X, en orden de lectura."""
    sel = sorted(
        (w for w in words if x_ini - _BAND_EPSILON <= w["x0"] < x_fin - _BAND_EPSILON),
        key=lambda w: (round(w["top"], 1), w["x0"]),
    )
    return " ".join(w["text"] for w in sel).strip()


@dataclass(frozen=True)
class _Event:
    """Una BANDA Y del cuerpo del documento, con lo que contiene.

    kind "player" -> fila de jugador (ancla: "Apellido, Nombre").
    kind "nys"    -> bloque NOT YET SUBMITTED (ancla: el token "SUBMITTED").
    """

    kind: str
    top: float
    cells: dict[str, str]


def _anchors(words: list[dict], bands: dict[str, tuple[float, float]],
             cuerpo: list[dict]) -> list[tuple[float, str]]:
    """Anclas del cuerpo, de los DOS tipos, ordenadas por Y.

    REGLA UNICA DEL PARSER: una banda va de punto medio a punto medio entre
    anclas consecutivas, y un bloque NOT YET SUBMITTED es un ancla como
    cualquier otra. Antes las filas NYS se recortaban del cuerpo por una banda
    de +-2 pt alrededor de "SUBMITTED", y eso producia los dos defectos que
    D-EXP-5 destapo:
      (a) el valor NUEVO de una columna de bloque (fecha, hora, matchup, equipo)
          que aterriza SOLO en una fila NYS se perdia, y las filas siguientes
          heredaban el valor viejo. Medido en 2018-12-18: "12/19/2018" aparece
          UNA vez en todo el PDF, en la fila NYS de CLE@CHA (pagina 2, top=409),
          asi que las 52 filas salieron fechadas 12/18 aunque 12 de los 16
          partidos del documento eran del 19.
      (b) un nombre de equipo de DOS lineas dentro de un bloque NYS escapaba al
          recorte, porque "SUBMITTED" se centra entre sus dos lineas y queda a
          4 pt de cada una. Medido en la misma pagina 3: "Minnesota" (top=275) y
          "Timberwolves" (top=283) con SUBMITTED en 279 — los dos fragmentos
          caian en la banda de VanVleet, la ultima ancla de la pagina, que
          llegaba hasta el pie. De ahi que un jugador de Toronto saliera
          atribuido a Minnesota (clase Trae Young -> Portland).
    Con las anclas unificadas la fila NYS tiene banda propia: se lleva sus
    fragmentos y entrega su valor de columna al estado de propagacion.
    """
    px0, px1 = bands["player_name"]
    jugadores = [
        (w["top"], "player") for w in cuerpo
        if px0 - _BAND_EPSILON <= w["x0"] < px1 - _BAND_EPSILON and "," in w["text"]
    ]
    nys = [(w["top"], "nys") for w in words if w["text"] == "SUBMITTED"]
    return sorted(jugadores + nys, key=lambda a: a[0])


def _events_from_page(page, layout: Layout) -> list[_Event]:
    """Eventos (filas de jugador y bloques NYS) de una pagina, en orden de lectura.

    BANDAS POR PUNTO MEDIO, no por top del ancla. Cuando la razon ocupa dos
    lineas, el generador CENTRA verticalmente las celdas de una linea respecto
    al bloque: medido en 2019-01-15 pagina 2, la fila de Antetokounmpo tiene el
    equipo y la primera linea de razon en top=131, el jugador y el estatus en
    135, y la continuacion de la razon en 139. Anclar la banda en el top del
    jugador dejaba el 131 dentro de la fila ANTERIOR — asi Robinson, Duncan
    heredo "Milwaukee Bucks" (jugaba en Miami) y la razon de Giannis. El punto
    medio entre anclas consecutivas parte el espacio donde no hay texto.
    """
    words = page.extract_words()
    bands = {n: (a, b) for n, a, b in layout.bands(page.width)}
    cab = _header_words(page)
    if not cab:
        return []
    header_top = cab[0]["top"]
    limite_pie = page.height - _FOOTER_MARGIN

    cuerpo = [w for w in words if header_top + _HEADER_Y_TOL < w["top"] < limite_pie]
    anclas = _anchors(words, bands, cuerpo)
    if not anclas:
        return []

    tops = [t for t, _ in anclas]
    bordes = [header_top + _HEADER_Y_TOL]
    bordes += [(t1 + t2) / 2 for t1, t2 in zip(tops, tops[1:])]
    bordes.append(limite_pie)

    salida: list[_Event] = []
    for i, (top, kind) in enumerate(anclas):
        franja = [w for w in cuerpo if bordes[i] <= w["top"] < bordes[i + 1]]
        celdas = {n: _cell_text(franja, a, b) for n, (a, b) in bands.items()}
        salida.append(_Event(kind=kind, top=top, cells=celdas))
    return salida


def _rows_from_page(page, layout: Layout) -> list[dict]:
    """Celdas de las filas de JUGADOR de una pagina, sin los bloques NYS.

    Vista estrecha de _events_from_page, que es donde vive la geometria. Existe
    porque la propagacion necesita TODOS los eventos (fix D-RES-3e) pero el
    cuerpo del reporte son solo las filas de jugador, y separar las dos vistas
    evita que un consumidor de filas tenga que saber que un NYS tambien es un
    evento.
    """
    return [{**ev.cells, "_top": ev.top}
            for ev in _events_from_page(page, layout) if ev.kind == "player"]


def _apply_matchup_invariant(rows: list[LegacyInjuryRow], layout_name: str) -> None:
    """Marca y AVISA las filas cuyo equipo no es uno de los dos de su matchup.

    Es GUARDA, no prueba: un error que respete el matchup pasa sin ruido, y la
    correccion sigue estableciendola la auditoria humana del listado contra el
    PDF (protocolo 13e-1). Lo que si hace es cerrar la puerta al modo de fallo
    caro de este proyecto — la misatribucion silenciosa. La fila NUNCA se
    corrige ni se descarta: se marca coherent=False y se avisa, porque un parser
    que arregla en silencio miente igual que uno que calla.
    """
    evaluables = malas = 0
    for row in rows:
        m = _MATCHUP_RE.match((row.matchup or "").strip())
        tri = _tricode(row.team or "")
        if not m or tri is None:
            continue
        evaluables += 1
        if tri not in m.groups():
            malas += 1
            row.coherent = False
            _log.warning(
                "Invariante de matchup violado (%s): %r atribuido a %r (%s) "
                "con matchup %r, fecha %r",
                layout_name, row.player_name, row.team, tri, row.matchup, row.game_date,
            )
    if evaluables and malas / evaluables > INJURY_INCOHERENT_WARN_RATE:
        _log.warning(
            "Invariante de matchup (%s): %d de %d filas incoherentes (%.2f%%), "
            "por encima del umbral de %.2f%%",
            layout_name, malas, evaluables, 100 * malas / evaluables,
            100 * INJURY_INCOHERENT_WARN_RATE,
        )


def parse_pdf_legacy(
    pdf_bytes: bytes, layout: Layout
) -> tuple[list[LegacyInjuryRow], list[NysEntry]]:
    """Filas + NYS de un PDF iTextSharp con el layout ya detectado.

    Los encabezados de partido (fecha, hora, matchup) y el equipo se PROPAGAN
    hacia abajo: el generador los escribe una vez por bloque y los deja en
    blanco en las filas siguientes, igual que en la ruta GemBox.

    LA EXCLUSION DEL CUERPO NO ES EXCLUSION DEL ESTADO (fix D-RES-3e): el estado
    se actualiza con TODO evento del documento, incluidos los bloques NOT YET
    SUBMITTED, que son los que a veces traen el valor nuevo de una columna de
    bloque. Solo los eventos de jugador emiten fila. El estado cruza de pagina
    sin reiniciarse — un bloque de equipo puede partirse entre paginas — y lo
    sobreescribe el primer evento de la pagina nueva que traiga valor propio.
    """
    rows: list[LegacyInjuryRow] = []
    # NYS: la maquinaria existente ya funciona sobre iTextSharp (verificado en
    # D-RES-3: 13/14/10/15 entradas con fecha en los cuatro PDFs auditados).
    _, nys = parse_pdf(pdf_bytes)

    estado = {"game_date": "", "game_time": "", "matchup": "", "team": ""}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for ev in _events_from_page(page, layout):
                for col in estado:
                    estado[col] = ev.cells.get(col) or estado[col]
                if ev.kind != "player":
                    continue

                estatus_txt = (ev.cells.get("current_status") or "").strip()
                estatus = _STATUS_BY_TEXT.get(estatus_txt)
                if estatus is None:
                    # Sin estatus reconocible no hay fila de jugador que valga:
                    # se ignora y la guarda de cobertura lo delatara si pasa a
                    # menudo. No se inventa un estatus por defecto.
                    _log.debug("Fila sin estatus reconocible: %r", ev.cells)
                    continue

                rows.append(
                    LegacyInjuryRow(
                        game_date=estado["game_date"],
                        game_time=estado["game_time"],
                        matchup=estado["matchup"],
                        team=estado["team"],
                        player_name=ev.cells.get("player_name", ""),
                        status=estatus,
                        reason=ev.cells.get("reason", ""),
                        category=ev.cells.get("category") or None,
                        previous_status=ev.cells.get("previous_status") or None,
                    )
                )
    _apply_matchup_invariant(rows, layout.name)
    return rows, nys


def count_candidate_lines(pdf_bytes: bytes) -> int:
    """Lineas con patron 'Apellido, Nombre' en la capa de texto."""
    n = 0
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for linea in (page.extract_text() or "").split("\n"):
                if _CANDIDATE_LINE.match(linea.strip()):
                    n += 1
    return n


def parse_pdf_any(pdf_bytes: bytes) -> ParsedReport:
    """Punto de entrada: detecta el layout y despacha.

    GEMBOX delega en parse_pdf() sin tocarlo. Los layouts iTextSharp pasan por
    parse_pdf_legacy y por la guarda de cobertura.

    Raises:
        UnknownLayoutError: firma fuera de la lista blanca.
        ParserCoverageError: el parser devolvio menos del 80% de las filas que
            la capa de texto insinua. Existe porque el modo de fallo REAL
            medido en D-RES-3 fue devolver cero filas SIN lanzar: un parser que
            calla es peor que uno que rompe.
    """
    layout = detect_layout(pdf_bytes)

    if layout.name == "GEMBOX":
        rows, nys = parse_pdf(pdf_bytes)
        return ParsedReport(layout.name, rows, nys, count_candidate_lines(pdf_bytes))

    rows, nys = parse_pdf_legacy(pdf_bytes, layout)
    n_cand = count_candidate_lines(pdf_bytes)
    if n_cand and len(rows) < _MIN_COVERAGE * n_cand:
        raise ParserCoverageError(
            f"Cobertura insuficiente con layout {layout.name}: "
            f"filas devueltas={len(rows)}, lineas candidatas={n_cand} "
            f"(minimo exigido {_MIN_COVERAGE:.0%})."
        )
    return ParsedReport(layout.name, rows, nys, n_cand)
