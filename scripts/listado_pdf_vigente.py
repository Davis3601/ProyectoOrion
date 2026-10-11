"""Listado legible de un injury report archivado, para cotejo humano (D-PRE-3).

SOLO LECTURA: descarga un PDF de gs://{bucket}/raw/injury_reports/ con un
CloudDataStore construido EXPLÍCITAMENTE (convención de scripts: el modo se
fija, jamás se hereda del .env) y no escribe nada en GCS ni en BigQuery. La
salida va a stdout y, si se pide, a un .txt local.

PARA QUÉ: el día cero (2026-10-20) el primer PDF de 2026-27 se coteja a mano
contra el documento ANTES de la publicación de las 13:00 CDMX (protocolo
13e-1). La guarda de cobertura de v14 detecta un parser que no lee filas;
NO detecta filas leídas con equipo o estatus equivocados. Eso solo lo caza
un humano leyendo el listado junto al PDF.

MISMAS PIEZAS QUE EL ENDPOINT, no copias: parse_pdf, NameIndex sobre la tabla
players, el mapa de equipos de daily_predictions y el contador/veredicto de
la guarda. Un listado construido con otra lógica cotejaría otra cosa.

Muestra toda columna de la que depende la atribución (regla de D-RES-3e):
fecha, hora, matchup, equipo del PDF, equipo resuelto, coherencia equipo-en-
matchup, jugador del PDF, nombre resuelto o SIN MATCH, estatus y razón.
Nombres, no ids (lección de D-PROD-1f).

Uso:
    python scripts/listado_pdf_vigente.py latest
    python scripts/listado_pdf_vigente.py 2026-10-19_08_15PM.pdf --out listado.txt

Borrable sin residuo: nada del pipeline lo importa.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PREFIX = "raw/injury_reports/"
_SUFFIX_NEW = re.compile(r"^(\d{2})_(\d{2})(AM|PM)$")
_SUFFIX_OLD = re.compile(r"^(\d{2})(AM|PM)$")


# ---------------------------------------------------------------------------
# Funciones puras
# ---------------------------------------------------------------------------


def suffix_minutes(suffix: str) -> int:
    """Minuto del día (ET) del corte. Viejo HH -> HH:30 (D-RES-2); nuevo HH_MM."""
    m = _SUFFIX_NEW.match(suffix)
    if m:
        h, mm, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
    else:
        m = _SUFFIX_OLD.match(suffix)
        if not m:
            return -1
        h, mm, ampm = int(m.group(1)), 30, m.group(2)
    h = h % 12 + (12 if ampm == "PM" else 0)
    return h * 60 + mm


def object_sort_key(name: str) -> tuple[str, int]:
    """'YYYY-MM-DD_SUFIJO.pdf' -> (fecha, minuto). El orden por nombre NO sirve:
    '01_15PM' < '11_45AM' alfabéticamente aunque sea posterior."""
    base = name.rsplit("/", 1)[-1].removesuffix(".pdf")
    fecha, _, sufijo = base.partition("_")
    return fecha, suffix_minutes(sufijo)


def pick_latest(names: list[str]) -> str:
    pdfs = [n for n in names if n.endswith(".pdf")]
    if not pdfs:
        raise FileNotFoundError(f"No hay PDFs bajo {PREFIX}")
    return max(pdfs, key=object_sort_key)


def render_listing(
    objeto: str,
    rows: list,
    nys: list,
    resolve_player: Callable[[str], str | None],
    resolve_team: Callable[[str], str | None],
    tricode: Callable[[str], str | None],
    n_lines: int | None,
    guard_msg: str | None,
) -> str:
    """Listado completo, en el orden del documento. Sin filtrar por fecha: el
    PDF es multi-fecha y el cotejo debe ver TODO lo que el parser leyó."""
    out: list[str] = []
    out.append(f"INJURY REPORT {objeto}")
    out.append(
        f"filas del parser={len(rows)} | líneas con nombre (regex de la guarda)="
        f"{n_lines if n_lines is not None else 'ILEGIBLE'} | NYS={len(nys)}"
    )
    out.append(
        "GUARDA DE COBERTURA: "
        + (f"DISPARARÍA -> {guard_msg}" if guard_msg else "no dispara (cobertura sana)")
    )
    sin_match = sum(1 for r in rows if resolve_player(r.player_name) is None)
    sin_equipo = sum(1 for r in rows if resolve_team(r.team) is None)
    incoh = sum(
        1 for r in rows
        if (t := tricode(r.team)) and r.matchup and t not in r.matchup.split("@")
    )
    out.append(
        f"jugadores SIN MATCH={sin_match} | equipos sin resolver={sin_equipo} | "
        f"equipo fuera de su matchup={incoh}"
    )
    conteo: dict[str, int] = {}
    for r in rows:
        conteo[r.status.value] = conteo.get(r.status.value, 0) + 1
    out.append("estatus: " + ", ".join(f"{k}={v}" for k, v in sorted(conteo.items())))
    out.append("")

    bloque = None
    for i, r in enumerate(rows, 1):
        clave = (r.game_date, r.matchup, r.team)
        if clave != bloque:
            bloque = clave
            equipo = resolve_team(r.team) or "EQUIPO SIN RESOLVER"
            t = tricode(r.team)
            coh = "?" if not (t and r.matchup) else ("OK" if t in r.matchup.split("@") else "FUERA")
            out.append(
                f"--- {r.game_date} {r.game_time} {r.matchup} | {r.team} -> {equipo} "
                f"[{t or '?'} en matchup: {coh}]"
            )
        nombre = resolve_player(r.player_name) or "SIN MATCH"
        out.append(
            f"  {i:3d}. {r.player_name:<28} -> {nombre:<26} {r.status.value:<12} {r.reason}"
        )

    out.append("")
    out.append(f"NOT YET SUBMITTED ({len(nys)}):")
    for e in nys:
        out.append(f"  {e.game_date}  {e.team} -> {resolve_team(e.team) or 'EQUIPO SIN RESOLVER'}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("objeto", help="nombre bajo raw/injury_reports/ o 'latest'")
    ap.add_argument("--out", help="ruta local opcional para guardar el listado")
    args = ap.parse_args()

    from nba_predictor.api.daily_predictions import _build_norm_team_map, resolve_team_id
    from nba_predictor.config import settings
    from nba_predictor.ingestion.injury_report import (
        NameIndex,
        ParseCoverageError,
        check_parse_coverage,
        count_name_lines,
        parse_pdf,
    )
    from nba_predictor.ingestion.injury_report_legacy import _tricode
    from nba_predictor.storage.cloud import CloudDataStore

    # EXPLÍCITO: lectura de la nube, jamás get_datastore() (convención 2026-09-13).
    store = CloudDataStore(
        project_id=settings.gcp_project_id,
        dataset=settings.bq_dataset,
        bucket_name=settings.gcs_bucket,
    )
    bucket = store._gcs.bucket(store.bucket_name)

    if args.objeto == "latest":
        nombres = [b.name for b in store._gcs.list_blobs(store.bucket_name, prefix=PREFIX)]
        objeto = pick_latest(nombres)
    else:
        objeto = args.objeto if args.objeto.startswith(PREFIX) else PREFIX + args.objeto

    pdf_bytes = bucket.blob(objeto).download_as_bytes()
    rows, nys = parse_pdf(pdf_bytes)
    n_lines = count_name_lines(pdf_bytes)
    try:
        check_parse_coverage(len(rows), n_lines)
        guard_msg = None
    except ParseCoverageError as exc:
        guard_msg = str(exc)

    player_map = store.load_player_names()
    idx = NameIndex.from_player_map(player_map)
    team_map = _build_norm_team_map(store.load_teams())
    id_to_team = {
        int(r["team_id"]): str(r["name"]) for _, r in store.load_teams().iterrows()
    }

    def resolve_player(pdf_name: str) -> str | None:
        pid = idx.match(pdf_name)
        return player_map.get(pid) if pid is not None else None

    def resolve_team(token: str) -> str | None:
        tid = resolve_team_id(token, team_map)  # la MISMA resolución del endpoint
        return id_to_team.get(tid) if tid is not None else None

    texto = render_listing(
        objeto.removeprefix(PREFIX), rows, nys, resolve_player, resolve_team,
        _tricode, n_lines, guard_msg,
    )
    sys.stdout.reconfigure(encoding="utf-8")
    print(texto)
    if args.out:
        Path(args.out).write_text(texto + "\n", encoding="utf-8")
        print(f"\n[listado guardado en {args.out}]")


if __name__ == "__main__":
    main()
