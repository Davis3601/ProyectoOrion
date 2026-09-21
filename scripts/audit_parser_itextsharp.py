"""
AUDITORIA DEL PARSER DE INJURY REPORT CONTRA EL CORPUS iTextSharp (D-RES-3).

*** ESTE SCRIPT LEE DE LA NUBE. *** Descarga 4 PDFs desde
gs://{bucket}/raw/injury_reports/ con un CloudDataStore construido
EXPLICITAMENTE aqui — nunca get_datastore(), nunca heredando el modo del .env
(convencion nacida del incidente del 2026-09-13). SOLO LECTURA: no sube, no
borra, no toca BigQuery ni otros prefijos.

Que hace: corre parse_pdf() tal cual sobre PDFs generados por iTextSharp
(temporadas 2018-19 a 2022-23) y vuelca el listado COMPLETO de cada uno para
que un humano lo audite contra el documento. NO corrige el parser. NO adjudica
correccion: los invariantes que calcula son GUARDAS, no prueba — el protocolo
de 13e-1 establece que solo la lectura humana del listado contra el PDF
establece correccion.

Salida por PDF, en data/audit_itextsharp/:
    {fecha}_listado.txt      todas las filas + bloque NYS
    {fecha}_invariantes.txt  los cuatro invariantes del pre-registro

Uso:
    python scripts/audit_parser_itextsharp.py
"""
from __future__ import annotations

import re
import sys
import traceback
from dataclasses import asdict
from pathlib import Path

from nba_predictor.config import settings
from nba_predictor.ingestion.injury_report import parse_pdf
from nba_predictor.storage.cloud import CloudDataStore

OUT_DIR = Path("data/audit_itextsharp")
GCS_PREFIX = "raw/injury_reports/"

# Muestra PRE-REGISTRADA (CLAUDE.md, D-RES-3). Fijada antes de mirar contenido;
# no se sustituye ninguna, pase lo que pase al parsearlas.
MUESTRA: list[tuple[str, str]] = [
    ("2019-01-15", "01PM"),
    ("2020-08-05", "11AM"),   # burbuja de Orlando
    ("2021-02-10", "01PM"),
    ("2023-01-20", "01PM"),
]

# PDF de referencia del corpus GemBox, ya auditado a mano en 13e-1.
REFERENCIA_GEMBOX = ("2024-03-13", "11PM")

ESTATUS_VALIDOS = {"Out", "Doubtful", "Questionable", "Probable", "Available"}

# Linea con patron "Apellido, Nombre" para el conteo independiente del
# invariante 1. Deliberadamente NO reutiliza la maquinaria del parser: un
# conteo que dependiera del parser seria circular (leccion del 2026-08-22).
PAT_JUGADOR = re.compile(r"[A-Z][A-Za-z'`.\-]+(?:\s(?:Jr\.|Sr\.|II|III|IV|V))?,\s?[A-Z]")


def make_cloud_store() -> CloudDataStore:
    """CloudDataStore EXPLICITO. Nunca get_datastore(): el .env no manda aqui."""
    return CloudDataStore(
        project_id=settings.gcp_project_id,
        dataset=settings.bq_dataset,
        bucket_name=settings.gcs_bucket,
    )


def fetch_pdf(store: CloudDataStore, fecha: str, sufijo: str) -> bytes:
    blob = store._gcs.bucket(store.bucket_name).blob(f"{GCS_PREFIX}{fecha}_{sufijo}.pdf")
    return blob.download_as_bytes()


def contar_lineas_jugador(pdf_bytes: bytes) -> int:
    """Conteo independiente de lineas con patron 'Apellido, Nombre'."""
    import io

    import pdfplumber

    n = 0
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for linea in (page.extract_text() or "").split("\n"):
                if PAT_JUGADOR.search(linea):
                    n += 1
    return n


def escribir_listado(fecha: str, sufijo: str, rows, nys) -> Path:
    path = OUT_DIR / f"{fecha}_listado.txt"
    lineas = [
        f"LISTADO COMPLETO — {fecha}_{sufijo}.pdf (corpus iTextSharp)",
        "Generado por parse_pdf() SIN modificar. Pendiente de auditoria humana",
        "contra el PDF: el listado no prueba nada por si mismo.",
        "=" * 110,
        f"{'#':>4}  {'equipo':<22}{'jugador':<30}{'estatus':<14}{'game_date':<12}razon",
        "-" * 110,
    ]
    for i, r in enumerate(rows, 1):
        d = asdict(r)
        estatus = d["status"].value if hasattr(d["status"], "value") else str(d["status"])
        lineas.append(
            f"{i:>4}  {d['team']:<22}{d['player_name']:<30}{estatus:<14}"
            f"{d['game_date']:<12}{d['reason']}"
        )
    lineas += ["-" * 110, f"TOTAL FILAS: {len(rows)}", "", "BLOQUE NYS (NOT YET SUBMITTED):"]
    if nys:
        for e in nys:
            d = asdict(e)
            lineas.append(f"    {d['team']:<24} game_date={d['game_date']}")
    else:
        lineas.append("    (ninguno)")
    lineas.append(f"TOTAL NYS: {len(nys)}")
    path.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return path


def escribir_invariantes(fecha: str, sufijo: str, rows, nys, n_lineas: int) -> tuple[Path, str]:
    """Los cuatro invariantes del pre-registro. GUARDAS, no prueba."""
    estatus = {r.status.value if hasattr(r.status, "value") else str(r.status) for r in rows}
    fuera = estatus - ESTATUS_VALIDOS

    from datetime import date, timedelta

    y, m, d0 = (int(x) for x in fecha.split("-"))
    hoy = date(y, m, d0)
    validas = {hoy.strftime("%m/%d/%Y"), (hoy + timedelta(days=1)).strftime("%m/%d/%Y")}
    fechas_row = {r.game_date for r in rows}
    fechas_malas = fechas_row - validas
    nys_sin_fecha = [asdict(e) for e in nys if not e.game_date]

    i1 = len(rows) == n_lineas
    i2 = not fuera
    i3 = not fechas_malas
    i4 = not nys_sin_fecha
    veredicto = "TODOS PASAN" if all((i1, i2, i3, i4)) else "ALGUNO FALLA"

    txt = [
        f"INVARIANTES — {fecha}_{sufijo}.pdf (corpus iTextSharp)",
        "GUARDAS, NO PRUEBA DE CORRECCION (protocolo 13e-1: solo la lectura",
        "humana del listado contra el PDF establece correccion).",
        "=" * 78,
        "(1) filas parser == conteo independiente 'Apellido, Nombre'",
        f"    parser={len(rows)}  extract_text={n_lineas}  -> {'PASA' if i1 else 'FALLA'}",
        "(2) estatus dentro del vocabulario conocido",
        f"    observados={sorted(estatus)}",
        f"    fuera de vocabulario={sorted(fuera) if fuera else 'ninguno'}  -> {'PASA' if i2 else 'FALLA'}",
        "(3) game_date == fecha del PDF o dia siguiente",
        f"    esperadas={sorted(validas)}  observadas={sorted(fechas_row)}",
        f"    fuera de rango={sorted(fechas_malas) if fechas_malas else 'ninguna'}  -> {'PASA' if i3 else 'FALLA'}",
        "(4) NYS con fecha",
        f"    total NYS={len(nys)}  sin fecha={len(nys_sin_fecha)}  -> {'PASA' if i4 else 'FALLA'}",
        "=" * 78,
        f"VEREDICTO DE INVARIANTES: {veredicto}",
        "RESULTADO DE LA AUDITORIA: pendiente de auditoria humana.",
    ]
    path = OUT_DIR / f"{fecha}_invariantes.txt"
    path.write_text("\n".join(txt) + "\n", encoding="utf-8")
    return path, "\n".join(txt)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    store = make_cloud_store()

    for fecha, sufijo in MUESTRA:
        pdf = fetch_pdf(store, fecha, sufijo)
        print(f"\n{'#' * 78}")
        try:
            rows, nys = parse_pdf(pdf)
        except Exception:
            # Una excepcion ES un resultado de la auditoria, no un accidente que
            # tapar: se registra con su traceback y se sigue con los demas PDFs.
            # NO se arregla el parser aqui (prohibido en el encargo).
            tb = traceback.format_exc()
            path = OUT_DIR / f"{fecha}_invariantes.txt"
            path.write_text(
                f"INVARIANTES — {fecha}_{sufijo}.pdf (corpus iTextSharp)\n"
                f"{'=' * 78}\n"
                f"parse_pdf() LANZO EXCEPCION. Los cuatro invariantes no se\n"
                f"pueden calcular: no hay filas que medir.\n\n"
                f"{tb}\n"
                f"{'=' * 78}\n"
                f"VEREDICTO DE INVARIANTES: NO APLICABLE (excepcion)\n"
                f"RESULTADO DE LA AUDITORIA: pendiente de auditoria humana.\n",
                encoding="utf-8",
            )
            print(f"{fecha}_{sufijo}: parse_pdf() LANZO EXCEPCION")
            print(tb.rstrip())
            print(f"  invariantes -> {path}")
            continue

        n_lineas = contar_lineas_jugador(pdf)
        p1 = escribir_listado(fecha, sufijo, rows, nys)
        p2, resumen = escribir_invariantes(fecha, sufijo, rows, nys, n_lineas)
        print(resumen)
        print(f"  listado -> {p1}")
        print(f"  invariantes -> {p2}")


if __name__ == "__main__":
    main()
