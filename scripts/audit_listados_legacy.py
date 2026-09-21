"""
LISTADOS DEL PARSER LEGACY + INVARIANTE DE ATRIBUCION DE EQUIPO (D-RES-3c r2).

*** LEE DE LA NUBE (solo lectura) *** con CloudDataStore EXPLICITO.

Genera data/audit_itextsharp/{fecha}_listado_legacy.txt con todas las filas, el
bloque NYS y, al pie, un invariante automatico nuevo: cruza (jugador, equipo
atribuido) contra los pares (player_id, team_id) reales de player_game_stats.

EL INVARIANTE ES GUARDA, NO PRUEBA. Una fila "JAMAS JUGO" es error SEGURO (el
jugador no estuvo en ese equipo en ninguna temporada del corpus, asi que la
atribucion no puede ser un traspaso). Cero discrepancias NO demuestra que el
parser este bien: la misatribucion de 13e-1 (Trae Young -> Portland) habria
caido aqui, pero un error que respete la plantilla no. La correccion la
establece la auditoria humana del listado contra el PDF.

Categorias:
  CONFIRMADO   (P,T) aparece en player_game_stats de ESA temporada.
  POSIBLE MOV. (P,T) no en esa temporada, pero SI en alguna otra del corpus:
               traspaso, reincorporacion o reporte emitido fuera de su etapa.
  NO VERIFICABLE el documento declara que el jugador no esta en la plantilla
               ("Not With Team", G League, two-way): su ausencia de
               player_game_stats con ese equipo es lo ESPERADO.
  JAMAS JUGO   (P,T) no aparece en NINGUNA temporada, el jugador SI tiene
               partidos esa temporada y el documento NO lo declara fuera de
               plantilla -> error seguro de atribucion.
  SIN DATOS    el jugador no registra partidos esa temporada (G League,
               two-way, lesionado toda la campaña): no hay con que juzgar.
  SIN MATCH    el nombre o el equipo no resolvieron a id.

Uso:
    python scripts/audit_listados_legacy.py
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from nba_predictor.config import settings
from nba_predictor.ingestion.injury_report import (
    NameIndex,
    _normalize_name,
    load_player_names_from_raw_json,
)
from nba_predictor.ingestion.injury_report_legacy import parse_pdf_any
from nba_predictor.storage.cloud import CloudDataStore

OUT_DIR = Path("data/audit_itextsharp")
GCS_PREFIX = "raw/injury_reports/"

# PDFs de auditoria (NO son fixtures; sus conteos NO son oficiales).
AUDITORIA: list[tuple[str, str, str]] = [
    ("2019-01-15", "01PM", "2018-19"),
    ("2021-02-10", "01PM", "2020-21"),
]


def make_cloud_store() -> CloudDataStore:
    """CloudDataStore EXPLICITO. Nunca get_datastore(): el .env no manda aqui."""
    return CloudDataStore(
        project_id=settings.gcp_project_id,
        dataset=settings.bq_dataset,
        bucket_name=settings.gcs_bucket,
    )


def _season_prefix(season: str) -> str:
    """'2018-19' -> '00218', prefijo de game_id de temporada regular."""
    return f"002{season[2:4]}"


def pares_por_temporada(conn: sqlite3.Connection) -> dict[str, set[tuple[int, int]]]:
    """{temporada: {(player_id, team_id)}} desde player_game_stats."""
    filas = conn.execute(
        "SELECT g.season, p.player_id, p.team_id "
        "FROM player_game_stats p JOIN games g ON g.game_id = p.game_id"
    ).fetchall()
    out: dict[str, set[tuple[int, int]]] = {}
    for season, pid, tid in filas:
        out.setdefault(season, set()).add((int(pid), int(tid)))
    return out


def mapa_equipos(conn: sqlite3.Connection) -> dict[str, int]:
    """nombre normalizado -> team_id, con la MISMA normalizacion del parser."""
    return {
        _normalize_name(str(name)): int(tid)
        for tid, name in conn.execute("SELECT team_id, name FROM teams")
    }


# Marcas con las que el PROPIO documento declara que el jugador no esta en la
# plantilla activa de ese equipo. Ante ellas, la ausencia en player_game_stats
# es lo esperado y el invariante no puede pronunciarse.
_MARCAS_NO_PLANTILLA = ("not with team", "g league", "two-way", "two way")


def _no_plantilla(row) -> bool:
    texto = f"{row.category or ''} {row.reason or ''}".lower()
    return any(m in texto for m in _MARCAS_NO_PLANTILLA)


def clasificar(rows, name_idx: NameIndex, equipos: dict[str, int],
               pares_temp: set[tuple[int, int]],
               pares_todos: set[tuple[int, int]],
               jugadores_temp: set[int]) -> tuple[Counter, list[str]]:
    conteo: Counter = Counter()
    detalle: list[str] = []
    for i, r in enumerate(rows, 1):
        pid = name_idx.match(r.player_name)
        tid = equipos.get(_normalize_name(r.team))
        if pid is None or tid is None:
            conteo["SIN MATCH"] += 1
            detalle.append(f"    fila {i:>3}  SIN MATCH    {r.player_name} / {r.team}"
                           f"  (pid={pid}, tid={tid})")
        elif (pid, tid) in pares_temp:
            conteo["CONFIRMADO"] += 1
        elif (pid, tid) in pares_todos:
            conteo["POSIBLE MOV."] += 1
            detalle.append(f"    fila {i:>3}  POSIBLE MOV. {r.player_name} / {r.team}")
        elif _no_plantilla(r):
            # El propio documento declara que el jugador NO esta jugando para
            # ese equipo ("Not With Team", G League, two-way). Que no aparezca
            # en player_game_stats con ese equipo es lo ESPERADO, no un error.
            # Cobrado con Ariza, Trevor / Oklahoma City Thunder el 2026-09-14:
            # traspasado a OKC en nov-2020, nunca debuto ahi, acabo en Miami.
            # La regla "jamas jugo => error seguro" era falsa por construccion.
            conteo["NO VERIFICABLE"] += 1
            detalle.append(f"    fila {i:>3}  NO VERIFICABLE {r.player_name} / {r.team}"
                           f"  (doc: {(r.category or r.reason or '')[:28]!r})")
        elif pid in jugadores_temp:
            conteo["JAMAS JUGO"] += 1
            detalle.append(f"    fila {i:>3}  JAMAS JUGO   {r.player_name} / {r.team}"
                           f"  <-- ERROR SEGURO DE ATRIBUCION")
        else:
            conteo["SIN DATOS"] += 1
            detalle.append(f"    fila {i:>3}  SIN DATOS    {r.player_name} / {r.team}")
    return conteo, detalle


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.disable(logging.WARNING)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    store = make_cloud_store()

    conn = sqlite3.connect(settings.db_path)
    pares_temp_todas = pares_por_temporada(conn)
    equipos = mapa_equipos(conn)
    pares_todos = {p for s in pares_temp_todas.values() for p in s}

    for fecha, sufijo, season in AUDITORIA:
        raw = store._gcs.bucket(store.bucket_name).blob(
            f"{GCS_PREFIX}{fecha}_{sufijo}.pdf").download_as_bytes()
        rep = parse_pdf_any(raw)

        nombres = load_player_names_from_raw_json(
            settings.raw_dir, glob_pattern=f"{_season_prefix(season)}*.json"
        )
        name_idx = NameIndex.from_player_map(nombres)
        pares_temp = pares_temp_todas.get(season, set())
        jugadores_temp = {pid for pid, _ in pares_temp}

        conteo, detalle = clasificar(
            rep.rows, name_idx, equipos, pares_temp, pares_todos, jugadores_temp
        )

        L = [
            f"LISTADO LEGACY — {fecha}_{sufijo}.pdf   layout={rep.layout}",
            "Generado por parse_pdf_any(). PENDIENTE DE AUDITORIA HUMANA contra el",
            "PDF: el listado no prueba nada por si mismo.",
            "=" * 150,
            f"{'#':>4}  {'equipo':<24}{'jugador':<26}{'category':<16}"
            f"{'estatus':<14}{'prev':<7}{'game_date':<12}razon",
            "-" * 150,
        ]
        for i, r in enumerate(rep.rows, 1):
            L.append(f"{i:>4}  {r.team:<24}{r.player_name:<26}{str(r.category):<16}"
                     f"{r.status.value:<14}{str(r.previous_status):<7}"
                     f"{r.game_date:<12}{r.reason}")
        L += [
            "-" * 150,
            f"FILAS DEVUELTAS: {len(rep.rows)}",
            f"LINEAS CANDIDATAS (extract_text, '^[A-Z][^,]+, [A-Z]'): {rep.n_candidates}",
            "NOTA: las candidatas SUBESTIMAN — la primera fila de cada bloque de",
            "      partido empieza con la fecha, no con el apellido.",
            "",
            "BLOQUE NYS (NOT YET SUBMITTED):",
        ]
        for e in rep.nys:
            L.append(f"    {e.team:<26} game_date={e.game_date}")
        L += [
            f"TOTAL NYS: {len(rep.nys)}",
            "",
            "=" * 150,
            f"INVARIANTE DE ATRIBUCION DE EQUIPO (temporada {season}) — GUARDA, NO PRUEBA",
            "=" * 150,
            f"  CONFIRMADO   : {conteo['CONFIRMADO']:>3}   (jugador jugo en ese equipo esa temporada)",
            f"  POSIBLE MOV. : {conteo['POSIBLE MOV.']:>3}   (no esa temporada, si en otra: traspaso)",
            f"  NO VERIFICABLE:{conteo['NO VERIFICABLE']:>3}   (el doc declara no-plantilla: Not With Team, G League, two-way)",
            f"  JAMAS JUGO   : {conteo['JAMAS JUGO']:>3}   <-- error seguro de atribucion",
            f"  SIN DATOS    : {conteo['SIN DATOS']:>3}   (jugador sin partidos esa temporada)",
            f"  SIN MATCH    : {conteo['SIN MATCH']:>3}   (nombre o equipo sin resolver)",
        ]
        if detalle:
            L += ["", "  DETALLE de todo lo que no es CONFIRMADO:"] + detalle
        L += ["", "RESULTADO: pendiente de auditoria humana."]

        path = OUT_DIR / f"{fecha}_listado_legacy.txt"
        path.write_text("\n".join(L) + "\n", encoding="utf-8")
        print(f"{path}  filas={len(rep.rows)}  {dict(conteo)}")


if __name__ == "__main__":
    main()
