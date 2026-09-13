"""
BACKFILL HISTORICO DE INJURY REPORTS (D-RES-2) — ESCRIBE EN GCS.

*** ESTE SCRIPT ESCRIBE EN LA NUBE DE FORMA DELIBERADA. ***
Destino: gs://{bucket}/raw/injury_reports/{fecha}_{sufijo}.pdf via metodo 15
(save_raw_injury_report) sobre un CloudDataStore construido EXPLICITAMENTE
aqui — nunca get_datastore(), nunca heredando el modo del .env. Convencion
nacida del incidente del 2026-09-13 (ver CLAUDE.md, "INCIDENTE GCS fase 1").

Promovido por el veredicto verde de fase 1 del spike (cobertura 96.7-100% en
las tres temporadas muestreadas). Pre-registro completo en CLAUDE.md,
"D-RES-2 — BACKFILL COMPLETO (PRE-REGISTRO 2026-09-13)".

DOS CORTES POR FECHA:
    publish = ultimo PDF con creacion ET <= 13:00 CDMX de esa fecha.
    late    = ultimo PDF con creacion ET <= 21:15 CDMX de esa fecha.
El par sirve al experimento de Camino 5: 'publish' es el corte que produccion
habria usado; 'late' es el estado final del dia, contra el que se medira
P(juega | Questionable).

REGLA ANTI-MAQUILLAJE: jamas se toma un corte POSTERIOR al limite. Si no hay
ninguno anterior, la fecha se marca "sin corte valido" y se anota el corte mas
temprano que exista. El historico debe reflejar la misma limitacion que sufre
produccion (NYS / feed caido), no taparla.

IDEMPOTENCIA: se lista el prefijo de GCS al arrancar; un objeto ya presente no
se re-descarga ni se re-sube. Los 89 PDFs de fase 1 quedan intactos.

NO parsea PDFs. NO toca BigQuery. NO toca otros prefijos. NO borra nada.

Uso:
    python scripts/backfill_injury_reports.py --season 2023-24
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# Los helpers puros de sufijos/eras/zonas viven en el spike, ya auditado y
# commiteado; se importan en vez de reescribirlos (sys.path[0] = scripts/).
from spike_backfill_injury_reports import (
    CDMX_TZ,
    ET_TZ,
    SLEEP_BETWEEN_REQUESTS,
    TIMEOUT,
    Prober,
    StopRule,
    build_url,
    era_slots,
)

from nba_predictor.config import settings
from nba_predictor.ingestion.injury_report import download_snapshot
from nba_predictor.storage.cloud import CloudDataStore

log = logging.getLogger("backfill_injury")

OUT_DIR = Path("data/backfill_injury")
GCS_PREFIX = "raw/injury_reports/"

SEASON_REQUEST_BUDGET = 3000

# Ventanas de corte, en hora de CDMX (la conversion a ET va con zoneinfo).
CUTS: dict[str, tuple[int, int]] = {
    "publish": (13, 0),
    "late": (21, 15),
}

# Frontera de formato hallada por biseccion en fase 1 (CLAUDE.md). Se usa solo
# como referencia informativa: la era se verifica en CADA fecha con 2 probes.
ERA_BOUNDARY_FIRST_NEW = "2025-12-22"

# Pares de sonda para detectar la era: (sufijo viejo, sufijo nuevo equivalente).
# Se prueban EN ORDEN y el par decide cuando exactamente uno de los dos vive.
#
# Por que varios pares y no el (07PM / 07_30PM) del spike: en las temporadas
# 2018-19 y 2019-20 el feed publicaba una ventana MAS ESTRECHA — 07PM devuelve
# 403 en todas las fechas sondeadas de esos años mientras 01PM devuelve 200.
# Con un solo par, esas fechas se clasificarian "era AMBIGUA" y la temporada
# entera quedaria sin archivar, en silencio. Medido el 2026-09-13 al cerrar el
# borde de retencion; misma familia de error que el gemelo de la capa 4.
ERA_PROBE_PAIRS: list[tuple[str, str]] = [
    ("01PM", "01_00PM"),
    ("07PM", "07_30PM"),
    ("11AM", "11_00AM"),
]


# ---------------------------------------------------------------------------
# Funciones puras
# ---------------------------------------------------------------------------


def cut_limit_et(target_date: str, hour: int, minute: int) -> datetime:
    """(hour:minute) CDMX de target_date, expresadas en ET. zoneinfo, no offset."""
    y, m, d = (int(x) for x in target_date.split("-"))
    return datetime(y, m, d, hour, minute, tzinfo=CDMX_TZ).astimezone(ET_TZ)


def slots_before_limit(target_date: str, era: str, limit: datetime) -> list[str]:
    """Sufijos de la era con creacion <= limit, del MAS TARDE al mas temprano.

    El orden importa: el consumidor prueba en secuencia y el primer 200 es, por
    construccion, el ultimo corte anterior al limite.
    """
    y, mo, d = (int(x) for x in target_date.split("-"))
    out = [
        suffix
        for h, mi, suffix in era_slots(era)
        if datetime(y, mo, d, h, mi, tzinfo=ET_TZ) <= limit
    ]
    return list(reversed(out))


def cut_from_live(
    target_date: str, era: str, cut_name: str, live: set[str]
) -> str | None:
    """Ultimo corte anterior al limite, elegido entre los cortes YA conocidos.

    Funcion pura: se usa tras un barrido completo, para no re-sondear lo que
    el barrido ya averiguo.
    """
    hour, minute = CUTS[cut_name]
    limit = cut_limit_et(target_date, hour, minute)
    for suffix in slots_before_limit(target_date, era, limit):
        if suffix in live:
            return suffix
    return None


def gcs_object_name(target_date: str, suffix: str) -> str:
    return f"{GCS_PREFIX}{target_date}_{suffix}.pdf"


def season_dates(season: str) -> list[str]:
    """Fechas de temporada regular desde el calendario real ya ingestado."""
    with sqlite3.connect(settings.db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT game_date FROM games "
            "WHERE season = ? AND season_type = 'Regular Season' "
            "ORDER BY game_date",
            (season,),
        ).fetchall()
    return [str(r[0])[:10] for r in rows]


def summarize(records: list[dict]) -> dict:
    """Resumen de la temporada. Funcion pura sobre los registros."""
    total = len(records)
    found = {cut: sum(1 for r in records if r["cuts"][cut]["suffix"]) for cut in CUTS}
    invalid = {
        cut: [r["date"] for r in records if not r["cuts"][cut]["suffix"]] for cut in CUTS
    }
    return {
        "dates": total,
        "found": found,
        "coverage_pct": {
            cut: round(100.0 * found[cut] / total, 1) if total else 0.0 for cut in CUTS
        },
        "no_valid_cut": invalid,
        "eras": {
            era: sum(1 for r in records if r["era"] == era) for era in ("old", "new")
        },
    }


# ---------------------------------------------------------------------------
# Red + GCS
# ---------------------------------------------------------------------------


class SeasonProber(Prober):
    """Prober del spike con el presupuesto de ESTA tarea.

    El Prober importado corta a TOTAL_REQUEST_BUDGET (700, el del spike); una
    temporada entera necesita mas. Se hereda para conservar intacta la conducta
    de red auditada (sleep, timeout, reglas de parada) y cambiar SOLO el tope.
    """

    def check_budget(self) -> None:
        if self.n_requests >= SEASON_REQUEST_BUDGET:
            raise StopRule(f"presupuesto de {SEASON_REQUEST_BUDGET} requests agotado")


def make_cloud_store() -> CloudDataStore:
    """CloudDataStore EXPLICITO. Nunca get_datastore(): el .env no manda aqui."""
    return CloudDataStore(
        project_id=settings.gcp_project_id,
        dataset=settings.bq_dataset,
        bucket_name=settings.gcs_bucket,
    )


def existing_objects(store: CloudDataStore) -> set[str]:
    """Nombres ya presentes bajo el prefijo — base de la idempotencia."""
    blobs = store._gcs.bucket(store.bucket_name).list_blobs(prefix=GCS_PREFIX)
    return {b.name for b in blobs}


def detect_era(prober: Prober, target_date: str) -> tuple[str | None, dict]:
    """Era de la fecha por sondeo. Devuelve ('old'|'new'|None, codigos).

    Prueba los pares de ERA_PROBE_PAIRS en orden y se queda con el primero
    concluyente (exactamente una de las dos familias viva). Tipico: 2 probes;
    hasta 6 en fechas de publicacion escasa (aperturas de temporada).
    """
    codes: dict[str, int | None] = {}
    for old_suffix, new_suffix in ERA_PROBE_PAIRS:
        codes[old_suffix] = prober.head(build_url(target_date, old_suffix))
        codes[new_suffix] = prober.head(build_url(target_date, new_suffix))
        old_hit = codes[old_suffix] == 200
        new_hit = codes[new_suffix] == 200
        if old_hit and not new_hit:
            return "old", codes
        if new_hit and not old_hit:
            return "new", codes
    return None, codes


def find_cut(
    prober: Prober, target_date: str, era: str, cut_name: str
) -> tuple[str | None, int]:
    """Ultimo corte anterior al limite del cut. Devuelve (sufijo|None, probes)."""
    hour, minute = CUTS[cut_name]
    limit = cut_limit_et(target_date, hour, minute)
    candidates = slots_before_limit(target_date, era, limit)
    probes = 0
    for suffix in candidates:
        probes += 1
        if prober.head(build_url(target_date, suffix)) == 200:
            return suffix, probes
    return None, probes


def sweep_family(prober: Prober, target_date: str, era: str) -> set[str]:
    """Barrido COMPLETO de la familia: devuelve los sufijos vivos de esa fecha.

    Coste: 24 HEAD (era vieja) o 96 (nueva). Se paga solo cuando los pares de
    sonda no concluyen — es decir, en fechas de publicacion escasa, que son
    justo las que un par mal elegido perderia en silencio.
    """
    return {
        suffix
        for _h, _m, suffix in era_slots(era)
        if prober.head(build_url(target_date, suffix)) == 200
    }


def resolve_era_by_sweep(
    prober: Prober, target_date: str
) -> tuple[str | None, set[str]]:
    """Resuelve la era barriendo la familia. Sustituye al estado 'ambigua'.

    Se barre primero la familia vieja (las fechas que los pares no resuelven
    son historicamente de esa era, con ventana estrecha); si no aparece nada y
    la fecha es posterior a la frontera de formato, se barre la nueva.
    """
    live = sweep_family(prober, target_date, "old")
    if live:
        return "old", live
    if target_date >= ERA_BOUNDARY_FIRST_NEW:
        live = sweep_family(prober, target_date, "new")
        if live:
            return "new", live
    return None, set()


def earliest_existing(prober: Prober, target_date: str, era: str) -> str | None:
    """Corte mas temprano que exista ese dia. Solo se llama si no hubo valido."""
    for _h, _m, suffix in era_slots(era):
        if prober.head(build_url(target_date, suffix)) == 200:
            return suffix
    return None


# ---------------------------------------------------------------------------
# Backfill de una temporada
# ---------------------------------------------------------------------------


def run_season(season: str, only: list[str] | None = None) -> dict:
    dates = season_dates(season)
    if only:
        faltantes = [d for d in only if d not in dates]
        if faltantes:
            sys.exit(f"Fechas fuera del calendario de {season}: {faltantes}")
        dates = only
    if not dates:
        sys.exit(f"Sin fechas de temporada regular para '{season}' en el calendario local.")

    store = make_cloud_store()
    present = existing_objects(store)
    prober = SeasonProber()

    print(f"BACKFILL {season} -> gs://{settings.gcs_bucket}/{GCS_PREFIX}")
    print("=" * 78)
    print(f"  fechas de temporada regular: {len(dates)}")
    print(f"  objetos ya presentes bajo el prefijo: {len(present)}")
    print(f"  presupuesto: {SEASON_REQUEST_BUDGET} requests")
    print("=" * 78)

    records: list[dict] = []
    uploaded = 0
    uploaded_bytes = 0
    skipped = 0
    stopped: str | None = None

    try:
        for target_date in dates:
            if prober.n_requests >= SEASON_REQUEST_BUDGET:
                raise StopRule(
                    f"presupuesto de {SEASON_REQUEST_BUDGET} requests agotado"
                )

            era, era_codes = detect_era(prober, target_date)
            live: set[str] | None = None
            resolved_by = "pares"

            if era is None:
                # FIX DE CLASE (2026-09-13): el estado "ambigua" ya no existe.
                # Los pares fallan en fechas de publicacion escasa — 2018-12-17
                # solo tenia 05PM y quedo sin archivar pese a existir. Se barre
                # la familia completa y se decide con la misma regla de corte.
                era, live = resolve_era_by_sweep(prober, target_date)
                resolved_by = "barrido"

            rec: dict = {
                "date": target_date,
                "era": era,
                "era_probe_codes": era_codes,
                "resolved_by": resolved_by,
                "live_slots": len(live) if live is not None else None,
                "cuts": {c: {"suffix": None, "bytes": None, "source": None} for c in CUTS},
                "earliest_existing": None,
            }

            if era is None:
                records.append(rec)
                print(f"  {target_date}  SIN ARCHIVO (barrido completo sin un solo corte)")
                continue

            for cut_name in CUTS:
                if live is not None:
                    suffix = cut_from_live(target_date, era, cut_name, live)
                else:
                    suffix, _probes = find_cut(prober, target_date, era, cut_name)
                if suffix is None:
                    continue
                rec["cuts"][cut_name]["suffix"] = suffix
                name = gcs_object_name(target_date, suffix)
                if name in present:
                    rec["cuts"][cut_name]["source"] = "ya_en_gcs"
                    skipped += 1
                    continue
                prober.check_budget()
                time.sleep(SLEEP_BETWEEN_REQUESTS)
                prober.n_requests += 1
                try:
                    pdf = download_snapshot(
                        build_url(target_date, suffix),
                        session=prober.session,
                        timeout=TIMEOUT,
                    )
                except Exception as exc:
                    # Descarga fallida: se anota y se sigue. Un PDF perdido no
                    # justifica abandonar la temporada (la regla de parada sigue
                    # cubriendo los fallos sistemicos).
                    rec["cuts"][cut_name]["source"] = "error"
                    rec["cuts"][cut_name]["error"] = repr(exc)
                    print(f"  {target_date}  {cut_name}={suffix} GET FALLIDO {exc!r}")
                    continue
                store.save_raw_injury_report(target_date, suffix, pdf)
                present.add(name)
                uploaded += 1
                uploaded_bytes += len(pdf)
                rec["cuts"][cut_name]["bytes"] = len(pdf)
                rec["cuts"][cut_name]["source"] = "subido"

            # El pre-registro pide anotar el corte mas temprano existente cuando
            # ALGUN corte se queda sin valido, no solo cuando fallan todos: es el
            # dato que explica POR QUE falto (feed arranco tarde ese dia).
            if not all(rec["cuts"][c]["suffix"] for c in CUTS):
                rec["earliest_existing"] = (
                    min(live, key=lambda x: [s2 for _h, _m, s2 in era_slots(era)].index(x))
                    if live
                    else earliest_existing(prober, target_date, era)
                )
                faltan = [c for c in CUTS if not rec["cuts"][c]["suffix"]]
                tiene = " ".join(
                    f"{c}={rec['cuts'][c]['suffix']}"
                    for c in CUTS
                    if rec["cuts"][c]["suffix"]
                )
                n_vivos = rec["live_slots"]
                vivos_txt = f", {n_vivos} cortes vivos" if n_vivos else ""
                print(f"  {target_date}  [{era}] sin corte valido: {','.join(faltan)}"
                      f"{('  ' + tiene) if tiene else ''}"
                      f"  (mas temprano existente: {rec['earliest_existing']}{vivos_txt})")
            else:
                parts = " ".join(
                    f"{c}={rec['cuts'][c]['suffix'] or '-'}"
                    f"{'(ya)' if rec['cuts'][c]['source'] == 'ya_en_gcs' else ''}"
                    for c in CUTS
                )
                print(f"  {target_date}  [{era}] {parts}")

            records.append(rec)
    except StopRule as exc:
        stopped = str(exc)
        print(f"\n!! REGLA DE PARADA ACTIVADA: {exc}")
        print("   Guardando lo acumulado y abortando sin reintentos.")

    if only:
        # Re-sondeo puntual: se FUSIONA sobre el registro existente para no
        # perder el resto de la temporada ya medida.
        prev_path = OUT_DIR / f"{season}.json"
        if prev_path.exists():
            prev = json.loads(prev_path.read_text(encoding="utf-8"))
            por_fecha = {r["date"]: r for r in prev["records"]}
            for r in records:
                por_fecha[r["date"]] = r
            records = [por_fecha[k] for k in sorted(por_fecha)]
            uploaded += prev.get("uploaded", 0)
            uploaded_bytes += prev.get("uploaded_bytes", 0)

    out = {
        "season": season,
        "summary": summarize(records),
        "records": records,
        "uploaded": uploaded,
        "uploaded_bytes": uploaded_bytes,
        "skipped_already_present": skipped,
        "total_requests": prober.n_requests,
        "budget": SEASON_REQUEST_BUDGET,
        "stopped": stopped,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{season}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    print_season_summary(out)
    return out


def print_season_summary(out: dict) -> None:
    s = out["summary"]
    print("\n" + "=" * 78)
    print(f"RESUMEN {out['season']}")
    print("=" * 78)
    print(f"  fechas de temporada regular : {s['dates']}")
    for cut in CUTS:
        print(f"  corte '{cut}' encontrado    : {s['found'][cut]:>4}"
              f"   cobertura {s['coverage_pct'][cut]:>5.1f}%")
    print(f"  eras detectadas             : {s['eras']}")
    for cut in CUTS:
        bad = s["no_valid_cut"][cut]
        print(f"  sin corte valido '{cut}'    : {len(bad)} {bad if bad else ''}")
    mb = out["uploaded_bytes"] / (1024 * 1024)
    print(f"\n  PDFs subidos                : {out['uploaded']}  ({mb:.2f} MB)")
    print(f"  omitidos (ya en GCS)        : {out['skipped_already_present']}")
    print(f"  requests                    : {out['total_requests']} / {out['budget']}")
    print(f"  regla de parada             : {out['stopped'] or 'NO activada'}")
    print(f"  escrito                     : {OUT_DIR / (out['season'] + '.json')}")


# ---------------------------------------------------------------------------
# CLI delgado
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", required=True, help="p.ej. 2023-24")
    parser.add_argument(
        "--dates",
        help="Lista separada por comas para re-sondear solo esas fechas "
             "(fusiona el resultado en el JSON de la temporada).",
    )
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    only = [d.strip() for d in args.dates.split(",")] if args.dates else None
    run_season(args.season, only=only)


if __name__ == "__main__":
    main()
