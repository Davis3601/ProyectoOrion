"""
ENCUESTA DE LAYOUTS DEL CORPUS iTextSharp (D-RES-3b).

*** ESTE SCRIPT LEE DE LA NUBE. *** Descarga PDFs desde
gs://{bucket}/raw/injury_reports/ con un CloudDataStore construido
EXPLICITAMENTE aqui. SOLO LECTURA: no sube, no borra, no toca BigQuery.

Que hace: describe la estructura de la capa de texto de 19 PDFs iTextSharp
(15 de la muestra congelada + los 4 ya auditados en D-RES-3) para saber
CUANTOS layouts distintos hay antes de diseñar el parser legacy.

NO parsea filas de jugador. NO diseña ni implementa parser. NO diagnostica:
describe y agrupa. El vocabulario de Category / Current Status se recoge
leyendo TOKENS por banda de X del encabezado — no es reconstruccion de filas.

Salida: consola (tabla de firmas y vocabularios) + data/audit_itextsharp/
layout_survey.json.

Uso:
    python scripts/survey_layouts_itextsharp.py
"""
from __future__ import annotations

import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pdfplumber

from nba_predictor.config import settings
from nba_predictor.storage.cloud import CloudDataStore

OUT_DIR = Path("data/audit_itextsharp")
GCS_PREFIX = "raw/injury_reports/"
MUESTRA_PATH = OUT_DIR / "layout_survey_muestra.json"

# Tolerancia de agrupacion de firmas: X0 redondeado a 5 pt (pre-registrado).
X_BUCKET = 5

# Tokens que delimitan las dos columnas cuyo vocabulario se encuesta.
COL_CATEGORY = "Category"
COL_CURRENT = "Current"
COL_REASON = "Reason"
COL_PREVIOUS = "Previous"


def make_cloud_store() -> CloudDataStore:
    """CloudDataStore EXPLICITO. Nunca get_datastore(): el .env no manda aqui."""
    return CloudDataStore(
        project_id=settings.gcp_project_id,
        dataset=settings.bq_dataset,
        bucket_name=settings.gcs_bucket,
    )


def cargar_muestra() -> list[tuple[str, str]]:
    """Muestra CONGELADA antes de mirar contenido. No se sustituye ninguna."""
    d = json.loads(MUESTRA_PATH.read_text(encoding="utf-8"))
    pares = [tuple(p) for trio in d["seleccion_previa_a_mirar_contenido"].values() for p in trio]
    pares += [tuple(p) for p in d["ya_auditados_D_RES_3"]]
    return sorted(set(pares))


def describir(raw: bytes) -> dict:
    """Descripcion estructural de la pagina 1. Solo observacion."""
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        pg = pdf.pages[0]
        words = pg.extract_words()
        lineas = (pg.extract_text() or "").split("\n")

        # El encabezado es la banda horizontal que contiene el token "Matchup".
        anchor = next((w for w in words if w["text"] == "Matchup"), None)
        if anchor is None:
            return {"error": "sin token 'Matchup' en la pagina 1"}
        top = anchor["top"]
        cab = sorted(
            (w for w in words if abs(w["top"] - top) < 3), key=lambda w: w["x0"]
        )
        tokens = [w["text"] for w in cab]
        x0s = [round(w["x0"], 2) for w in cab]

        # Primera linea de datos: la siguiente a la del encabezado.
        idx_cab = next((i for i, ln in enumerate(lineas) if "Matchup" in ln), 0)
        primera_fila = lineas[idx_cab + 1] if len(lineas) > idx_cab + 1 else ""

        firma = (
            tuple(tokens),
            tuple(int(round(x / X_BUCKET) * X_BUCKET) for x in x0s),
        )

        return {
            "paginas": len(pdf.pages),
            "ancho": round(pg.width), "alto": round(pg.height),
            "orientacion": "apaisada" if pg.width > pg.height else "vertical",
            "words_pag1": len(words),
            "cabecera_tokens": tokens,
            "cabecera_x0": x0s,
            "n_columnas_tokens": len(tokens),
            "cabecera_top": round(top, 2),
            "primera_fila_datos": primera_fila[:160],
            "valores_con_espacios": " (ET)" in primera_fila or ", " in primera_fila,
            "firma": firma,
            "_words": words,
            "_top": top,
        }


def vocabulario_por_banda(words: list[dict], tokens: list[str], x0s: list[float],
                          top: float) -> dict[str, Counter]:
    """Tokens que caen en la banda X de Category y de Current Status.

    La banda de una columna va desde su X0 hasta el X0 de la siguiente columna
    del encabezado. Se leen TOKENS sueltos, no filas reconstruidas.
    """
    pos = {t: x for t, x in zip(tokens, x0s)}

    def siguiente_columna(desde: float) -> float:
        """X0 de la siguiente columna real a la derecha de `desde`.

        "Current Status" y "Player Name" son DOS tokens de UNA columna, asi que
        el token contiguo no sirve de tope: hay que saltar al siguiente token
        que empiece columna. Sin este salto, la banda de Current Status se
        extendia hasta el infinito en el layout sin columna Previous y se
        tragaba la columna Reason entera (medido el 2026-09-13: 188 "estatus"
        distintos, casi todos lesiones).
        """
        candidatos = [x for t, x in pos.items() if x > desde + 40 and t != "Status"]
        return min(candidatos, default=10_000.0)

    bandas: dict[str, tuple[float, float]] = {}
    if COL_CATEGORY in pos and COL_REASON in pos:
        bandas["Category"] = (pos[COL_CATEGORY], pos[COL_REASON])
    if COL_CURRENT in pos:
        fin = pos.get(COL_PREVIOUS) or siguiente_columna(pos[COL_CURRENT])
        bandas["CurrentStatus"] = (pos[COL_CURRENT], fin)

    out: dict[str, Counter] = {k: Counter() for k in bandas}
    for w in words:
        if w["top"] <= top + 3:
            continue
        for col, (a, b) in bandas.items():
            if a - 2 <= w["x0"] < b - 2:
                out[col][w["text"]] += 1
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    store = make_cloud_store()
    muestra = cargar_muestra()

    resultados: list[dict] = []
    vocab_global: dict[str, Counter] = defaultdict(Counter)
    por_firma: dict[tuple, list[str]] = defaultdict(list)

    print(f"ENCUESTA DE LAYOUTS — {len(muestra)} PDFs del corpus iTextSharp")
    print("=" * 118)
    print(f"{'fecha':<12}{'suf':<7}{'pag':>4}{'orient':>10}{'words':>7}{'cols':>6}"
          f"{'top':>8}  {'encabezado (tokens)'}")
    print("-" * 118)

    for fecha, sufijo in muestra:
        blob = store._gcs.bucket(store.bucket_name).blob(f"{GCS_PREFIX}{fecha}_{sufijo}.pdf")
        info = describir(blob.download_as_bytes())
        if "error" in info:
            print(f"{fecha:<12}{sufijo:<7}  {info['error']}")
            resultados.append({"fecha": fecha, "sufijo": sufijo, **info})
            continue

        words = info.pop("_words")
        top = info.pop("_top")
        voc = vocabulario_por_banda(words, info["cabecera_tokens"], info["cabecera_x0"], top)
        for col, c in voc.items():
            vocab_global[col].update(c)

        por_firma[info["firma"]].append(fecha)
        print(f"{fecha:<12}{sufijo:<7}{info['paginas']:>4}{info['orientacion']:>10}"
              f"{info['words_pag1']:>7}{info['n_columnas_tokens']:>6}{info['cabecera_top']:>8.1f}  "
              f"{' '.join(info['cabecera_tokens'])[:52]}")
        resultados.append({
            "fecha": fecha, "sufijo": sufijo,
            **{k: v for k, v in info.items() if k != "firma"},
            "firma_tokens": list(info["firma"][0]),
            "firma_x0_bucket": list(info["firma"][1]),
            "vocabulario": {c: dict(v) for c, v in voc.items()},
        })

    print("\n" + "=" * 118)
    print(f"FIRMAS DISTINTAS: {len(por_firma)}")
    print("=" * 118)
    for i, (firma, fechas) in enumerate(sorted(por_firma.items(), key=lambda kv: min(kv[1])), 1):
        tokens, xs = firma
        print(f"\nFIRMA {i}  —  {len(fechas)} PDFs   rango {min(fechas)} -> {max(fechas)}")
        print(f"  tokens : {' '.join(tokens)}")
        print(f"  X0 (5pt): {list(xs)}")
        print(f"  fechas : {', '.join(sorted(fechas))}")

    print("\n" + "=" * 118)
    print("VOCABULARIO OBSERVADO POR BANDA DE COLUMNA (tokens, no filas)")
    print("=" * 118)
    for col, c in vocab_global.items():
        print(f"\n  {col}  ({len(c)} tokens distintos, {sum(c.values())} ocurrencias)")
        for tok, n in c.most_common():
            print(f"      {n:>5}  {tok}")

    (OUT_DIR / "layout_survey.json").write_text(
        json.dumps(
            {
                "pdfs": resultados,
                "firmas": [
                    {"tokens": list(f[0]), "x0_bucket": list(f[1]),
                     "n_pdfs": len(ds), "fechas": sorted(ds),
                     "rango": [min(ds), max(ds)]}
                    for f, ds in por_firma.items()
                ],
                "vocabulario_global": {c: dict(v) for c, v in vocab_global.items()},
            },
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nEscrito: {OUT_DIR / 'layout_survey.json'}")


if __name__ == "__main__":
    main()
