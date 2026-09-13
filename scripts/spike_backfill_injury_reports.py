"""
SPIKE DE SOLO LECTURA — cobertura del archivo histórico de injury reports (D-RES-2).

Mide, por muestreo estratificado, qué fracción de PDFs históricos sigue viva en
ak-static.cms.nba.com. NO es el backfill completo: es la medición que decide si
el backfill completo se hace (criterio de promoción pre-registrado en CLAUDE.md).

Pertenece al Camino 5. NO toca el pipeline de producción: importa de
injury_report.py (cliente HTTP, plantilla de URL, descarga) y del DataStore
(método 15) sin modificar nada. Borrable sin residuo.

Fases:
    --phase0   Calibración de sufijos sobre 3 fechas de hit conocido.
               Barrido amplio (viejo 24 + nuevo 96), cap 150 HEAD por fecha.
               Registra TODOS los hits para aprender horas/minutos por era.
    --phase1   Diseño ENMENDADO tras fase 0 (ver CLAUDE.md, "D-RES-2 — ENMIENDA
               de metodo"): bisección de la frontera de formato, era por FECHA,
               corte canónico + dos vecinos, archivado de UN PDF por fecha y
               submuestra de densidad.

Conducta de red (no negociable, pre-registrada):
    sleep 0.5s entre requests, timeout 10s, sin cambiar User-Agent.
    Regla de parada: 429, o 403 sobre una URL que antes dio 200, o 10 errores
    de red consecutivos → abortar, guardar lo acumulado, reportar.
    Presupuesto total del spike: TOTAL_REQUEST_BUDGET requests.

Uso:
    python scripts/spike_backfill_injury_reports.py --phase0
    python scripts/spike_backfill_injury_reports.py --phase1
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from nba_predictor.config import settings
from nba_predictor.ingestion.injury_report import (
    INJURY_REPORT_URL_TEMPLATE,
    download_snapshot,
)

log = logging.getLogger("spike_backfill")

# ---------------------------------------------------------------------------
# Constantes del spike (pre-registradas)
# ---------------------------------------------------------------------------

OUT_DIR = Path("data/spike_backfill")
DATES_PATH = OUT_DIR / "dates.json"
PHASE0_PATH = OUT_DIR / "phase0.json"
RESULTS_PATH = OUT_DIR / "results.json"
BISECT_PATH = OUT_DIR / "era_boundary.json"
DENSITY_PATH = OUT_DIR / "density.json"

SLEEP_BETWEEN_REQUESTS = 0.5
TIMEOUT = 10
PHASE0_CAP = 150
DATES_PER_SEASON = 30
SEED = 42
MAX_CONSECUTIVE_NET_ERRORS = 10

PHASE1_MAX_HEAD_PER_DATE = 5     # 2 probes de era + canónico + 2 vecinos
BISECT_MAX_REQUESTS = 12
TOTAL_REQUEST_BUDGET = 700

# Fechas de calibración de fase 0. Las dos primeras tienen hit conocido y
# documentado en CLAUDE.md (13e-1); la tercera se eligió aquí y quedó
# registrada en el pre-registro para cubrir la era 2024-25.
PHASE0_DATES: tuple[str, ...] = ("2026-03-13", "2024-03-13", "2025-01-15")

SEASONS: tuple[str, ...] = ("2023-24", "2024-25", "2025-26")


class StopRule(Exception):
    """Regla de parada pre-registrada: abortar el spike sin reintentos."""


# ---------------------------------------------------------------------------
# Funciones puras — sufijos por era
# ---------------------------------------------------------------------------


def suffixes_old_full() -> list[str]:
    """Barrido amplio formato pre-2025: {01..12}{AM,PM} = 24 probes."""
    return [f"{h:02d}{ampm}" for ampm in ("PM", "AM") for h in range(1, 13)]


def suffixes_new_full() -> list[str]:
    """Barrido amplio formato 2026+: {01..12}_{00,15,30,45}{AM,PM} = 96 probes."""
    return [
        f"{h:02d}_{m:02d}{ampm}"
        for ampm in ("PM", "AM")
        for h in range(1, 13)
        for m in (45, 30, 15, 0)
    ]


def phase0_suffix_sweep() -> list[str]:
    """Set completo de fase 0: viejo primero (barato, 24) y luego nuevo (96)."""
    return suffixes_old_full() + suffixes_new_full()


def build_url(target_date: str, suffix: str) -> str:
    return INJURY_REPORT_URL_TEMPLATE.format(date=target_date, suffix=suffix)


def classify_suffix(suffix: str) -> str:
    """'new' si lleva minutos (HH_MM…), 'old' si es solo hora (HH…)."""
    return "new" if "_" in suffix else "old"


def reduced_set_from_hits(
    hits_by_era: dict[str, list[str]], cap: int = 30
) -> dict[str, list[str]]:
    """Deriva un set reducido a partir de los hits de fase 0.

    Función pura: la política es "los sufijos que realmente aparecieron,
    ordenados por frecuencia observada y luego de más tarde a más temprano,
    recortados al cap". No inventa sufijos que fase 0 no vio.
    """
    reduced: dict[str, list[str]] = {}
    for era, hits in hits_by_era.items():
        if not hits:
            reduced[era] = []
            continue
        freq = Counter(hits)
        full_order = suffixes_new_full() if era == "new" else suffixes_old_full()
        rank = {s: i for i, s in enumerate(full_order)}
        ordered = sorted(freq, key=lambda s: (-freq[s], rank.get(s, 10_000)))
        reduced[era] = ordered[:cap]
    return reduced


def stratified_sample(
    dates: list[str], n: int = DATES_PER_SEASON, seed: int = SEED
) -> list[str]:
    """n fechas estratificadas por mes (~n/meses por mes), determinista con seed.

    Reparte la cuota entre los meses presentes; los meses con menos fechas que
    su cuota ceden el sobrante a los demás en orden cronológico, de modo que el
    total siempre sea n mientras haya fechas suficientes.
    """
    by_month: dict[str, list[str]] = defaultdict(list)
    for d in sorted(dates):
        by_month[d[:7]].append(d)

    months = sorted(by_month)
    rng = random.Random(seed)
    base = n // len(months)
    remainder = n - base * len(months)

    quota = {m: base for m in months}
    for i in range(remainder):
        quota[months[i % len(months)]] += 1

    picked: list[str] = []
    deficit = 0
    for m in months:
        want = quota[m] + deficit
        pool = by_month[m]
        take = min(want, len(pool))
        deficit = want - take
        picked.extend(rng.sample(pool, take))

    if len(picked) < n:
        rest = [d for d in sorted(dates) if d not in set(picked)]
        picked.extend(rng.sample(rest, min(n - len(picked), len(rest))))

    return sorted(picked)


# ---------------------------------------------------------------------------
# Diseño ENMENDADO tras fase 0 — corte canónico
#
# REGLA DE MAPEO SUFIJO -> HORA DE CREACION (evidencia de fase 0, no supuesto):
#   era vieja  "HH{AM|PM}"     -> creacion HH:30  (2024-03-13_04AM trajo
#                                 /CreationDate D:20240313043002-04'00')
#   era nueva  "HH_MM{AM|PM}"  -> creacion HH:MM  (2026-03-13_12_00AM trajo
#                                 /CreationDate D:20260313000004-04'00')
#
# CORTE CANONICO = el ultimo corte cuya hora de creacion ET no pasa de las
# 13:00 CDMX de esa fecha, convertidas a ET. La conversion va con zoneinfo y
# NUNCA con offset fijo: CDMX abolio el horario de verano en 2022 (UTC-6 todo
# el año) pero ET sigue alternando EST/EDT, asi que el corte cae en 14:00 ET
# en invierno y 15:00 ET en octubre y abril. Un offset fijo desplazaria el
# corte una hora justo en los bordes de temporada.
# ---------------------------------------------------------------------------

CDMX_TZ = ZoneInfo("America/Mexico_City")
ET_TZ = ZoneInfo("America/New_York")
PUBLISH_CUTOFF_CDMX_HOUR = 13

ERA_PROBES: dict[str, str] = {"old": "07PM", "new": "07_30PM"}

BISECT_LO = "2025-12-15"   # era vieja confirmada en la calibracion de fase 0
BISECT_HI = "2026-01-15"   # era nueva confirmada


def _fmt12(hour24: int, minute: int | None) -> str:
    """(hora 24h, minuto) -> sufijo. minute=None produce la forma vieja."""
    ampm = "AM" if hour24 < 12 else "PM"
    h12 = hour24 % 12 or 12
    if minute is None:
        return f"{h12:02d}{ampm}"
    return f"{h12:02d}_{minute:02d}{ampm}"


def era_slots(era: str) -> list[tuple[int, int, str]]:
    """Cortes de la era en orden cronologico: (hora_creacion, minuto, sufijo).

    La era vieja publica a y media (HH:30) una vez por hora; la nueva, cada
    cuarto de hora exacto.
    """
    if era == "old":
        return [(h, 30, _fmt12(h, None)) for h in range(24)]
    return [(h, m, _fmt12(h, m)) for h in range(24) for m in (0, 15, 30, 45)]


def cutoff_et(target_date: str) -> datetime:
    """13:00 CDMX de target_date, expresadas en ET (zoneinfo, no offset fijo)."""
    y, m, d = (int(x) for x in target_date.split("-"))
    cdmx = datetime(y, m, d, PUBLISH_CUTOFF_CDMX_HOUR, 0, tzinfo=CDMX_TZ)
    return cdmx.astimezone(ET_TZ)


def canonical_and_neighbors(target_date: str, era: str) -> tuple[str, list[str]]:
    """(sufijo canonico, [vecino anterior, vecino posterior]) para la fecha/era.

    Canonico = ultimo corte con creacion ET <= cutoff. Los vecinos son los
    cortes inmediatamente anterior y posterior de la MISMA familia.
    """
    y, mo, d = (int(x) for x in target_date.split("-"))
    limit = cutoff_et(target_date)
    slots = era_slots(era)

    idx = 0
    for i, (h, mi, _) in enumerate(slots):
        if datetime(y, mo, d, h, mi, tzinfo=ET_TZ) <= limit:
            idx = i
        else:
            break

    neighbors: list[str] = []
    if idx - 1 >= 0:
        neighbors.append(slots[idx - 1][2])
    if idx + 1 < len(slots):
        neighbors.append(slots[idx + 1][2])
    return slots[idx][2], neighbors


def midpoint(lo: str, hi: str) -> str:
    """Fecha intermedia entre lo y hi (exclusivos), o '' si son contiguas."""
    a = date.fromisoformat(lo)
    b = date.fromisoformat(hi)
    if (b - a).days <= 1:
        return ""
    return (a + timedelta(days=(b - a).days // 2)).isoformat()


def coverage_table_amended(results: list[dict]) -> list[dict]:
    """Resumen por temporada. Funcion pura sobre los registros de fase 1."""
    by_season: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_season[r["season"]].append(r)

    rows = []
    for season in SEASONS:
        recs = by_season.get(season, [])
        if not recs:
            continue
        canon = sum(1 for r in recs if r["hit_kind"] == "canonical")
        neigh = sum(1 for r in recs if r["hit_kind"] == "neighbor")
        rows.append({
            "season": season,
            "probed": len(recs),
            "canonical": canon,
            "neighbor": neigh,
            "no_pdf": len(recs) - canon - neigh,
            "coverage_pct": round(100.0 * (canon + neigh) / len(recs), 1),
            "eras": dict(Counter(r["era"] or "ambigua" for r in recs)),
        })
    return rows


# ---------------------------------------------------------------------------
# Estado de red con regla de parada
# ---------------------------------------------------------------------------


@dataclass
class Prober:
    """HEAD probing con la conducta de red pre-registrada.

    Reutiliza requests.Session tal como lo hace injury_report.py — ese módulo
    NO fija headers propios, así que aquí tampoco (cambiar el User-Agent para
    evadir está explícitamente prohibido).
    """

    session: requests.Session = field(default_factory=requests.Session)
    seen_200: set[str] = field(default_factory=set)
    consecutive_errors: int = 0
    n_requests: int = 0

    def check_budget(self) -> None:
        if self.n_requests >= TOTAL_REQUEST_BUDGET:
            raise StopRule(f"presupuesto de {TOTAL_REQUEST_BUDGET} requests agotado")

    def head(self, url: str) -> int | None:
        """Status code, o None si hubo error de red.

        Aplica la regla de parada: 429, 403 sobre una URL que ya dio 200, o
        MAX_CONSECUTIVE_NET_ERRORS errores seguidos -> StopRule.
        """
        self.check_budget()
        time.sleep(SLEEP_BETWEEN_REQUESTS)
        self.n_requests += 1
        try:
            resp = self.session.head(url, timeout=TIMEOUT)
        except requests.exceptions.RequestException as exc:
            self.consecutive_errors += 1
            if self.consecutive_errors >= MAX_CONSECUTIVE_NET_ERRORS:
                raise StopRule(
                    f"{self.consecutive_errors} errores de red consecutivos "
                    f"(ultimo: {exc!r})"
                ) from exc
            return None

        self.consecutive_errors = 0
        code = resp.status_code

        if code == 429:
            raise StopRule(f"429 Too Many Requests en {url} — rate limit del servidor")
        if code == 403 and url in self.seen_200:
            raise StopRule(f"403 sobre una URL que antes dio 200: {url}")
        if code == 200:
            self.seen_200.add(url)
        return code


# ---------------------------------------------------------------------------
# Fase 0 — calibración de sufijos
# ---------------------------------------------------------------------------


def run_phase0() -> dict:
    sweep = phase0_suffix_sweep()[:PHASE0_CAP]
    prober = Prober()
    out: dict = {"cap_per_date": PHASE0_CAP, "dates": {}, "stopped": None}

    print(f"FASE 0 — calibracion de sufijos ({len(sweep)} probes/fecha, cap {PHASE0_CAP})")
    print("=" * 78)

    try:
        for target_date in PHASE0_DATES:
            hits: list[dict] = []
            codes: Counter = Counter()
            print(f"\n{target_date}: sondeando {len(sweep)} sufijos...")
            for suffix in sweep:
                code = prober.head(build_url(target_date, suffix))
                codes[str(code)] += 1
                if code == 200:
                    hits.append({"suffix": suffix, "era": classify_suffix(suffix)})
            out["dates"][target_date] = {
                "probed": len(sweep),
                "hits": hits,
                "status_counts": dict(codes),
            }
            by_era = Counter(h["era"] for h in hits)
            print(f"  hits: {len(hits)}/{len(sweep)}  "
                  f"(old={by_era['old']}/24, new={by_era['new']}/96)")
            print(f"  status: {dict(codes)}")
            print(f"  sufijos con 200: {[h['suffix'] for h in hits]}")
    except StopRule as exc:
        out["stopped"] = str(exc)
        print(f"\n!! REGLA DE PARADA ACTIVADA: {exc}")

    hits_by_era: dict[str, list[str]] = {"old": [], "new": []}
    for rec in out["dates"].values():
        for h in rec["hits"]:
            hits_by_era[h["era"]].append(h["suffix"])

    out["hits_by_era"] = hits_by_era
    out["reduced_set"] = reduced_set_from_hits(hits_by_era)
    out["total_requests"] = prober.n_requests

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PHASE0_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print("\n" + "=" * 78)
    print("SET REDUCIDO DERIVADO:")
    for era, suffixes in out["reduced_set"].items():
        print(f"  {era:>3}: {len(suffixes)} sufijos -> {suffixes}")
    print(f"\nHEAD requests totales: {prober.n_requests}")
    print(f"Escrito: {PHASE0_PATH}")
    return out


# ---------------------------------------------------------------------------
# Muestreo: dates.json inmutable
# ---------------------------------------------------------------------------


def load_regular_season_dates(season: str) -> list[str]:
    """Fechas reales de temporada regular desde la tabla games ya ingestada.

    No se inventan rangos: el calendario sale del store local.
    """
    with sqlite3.connect(settings.db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT game_date FROM games "
            "WHERE season = ? AND season_type = 'Regular Season' "
            "ORDER BY game_date",
            (season,),
        ).fetchall()
    return [str(r[0])[:10] for r in rows]


def build_or_load_dates() -> dict[str, list[str]]:
    """La lista de fechas es INMUTABLE: si ya existe, se reutiliza tal cual."""
    if DATES_PATH.exists():
        print(f"dates.json ya existe — lista INMUTABLE reutilizada ({DATES_PATH})")
        return json.loads(DATES_PATH.read_text(encoding="utf-8"))

    dates = {
        season: stratified_sample(load_regular_season_dates(season))
        for season in SEASONS
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DATES_PATH.write_text(json.dumps(dates, indent=2), encoding="utf-8")
    print(f"dates.json CONGELADO antes de sondear ({DATES_PATH})")
    return dates


# ---------------------------------------------------------------------------
# Detección de era + bisección de la frontera
# ---------------------------------------------------------------------------


def detect_era(prober: Prober, target_date: str) -> tuple[str | None, dict]:
    """2 probes (07PM / 07_30PM). Devuelve ('old'|'new'|None, codigos)."""
    codes: dict[str, int | None] = {}
    for era, suffix in ERA_PROBES.items():
        codes[suffix] = prober.head(build_url(target_date, suffix))
    old_hit = codes[ERA_PROBES["old"]] == 200
    new_hit = codes[ERA_PROBES["new"]] == 200
    if old_hit and not new_hit:
        return "old", codes
    if new_hit and not old_hit:
        return "new", codes
    return None, codes


def run_bisect(prober: Prober) -> dict:
    """Bisección de la frontera de formato entre BISECT_LO y BISECT_HI."""
    lo, hi = BISECT_LO, BISECT_HI
    steps: list[dict] = []
    budget_start = prober.n_requests

    print("\nBISECCION DE LA FRONTERA DE FORMATO")
    print("=" * 78)
    print(f"  {lo} = old (confirmada en fase 0) ... {hi} = new (confirmada en fase 0)")

    while prober.n_requests - budget_start < BISECT_MAX_REQUESTS:
        mid = midpoint(lo, hi)
        if not mid:
            break
        era, codes = detect_era(prober, mid)
        steps.append({"date": mid, "era": era, "codes": codes})
        print(f"  {mid}: {era or 'AMBIGUO'}  {codes}")
        if era == "old":
            lo = mid
        elif era == "new":
            hi = mid
        else:
            break

    out = {
        "last_old": lo,
        "first_new": hi,
        "contiguous": midpoint(lo, hi) == "",
        "steps": steps,
        "requests": prober.n_requests - budget_start,
    }
    print(f"  -> ultimo dia VIEJO: {lo} | primer dia NUEVO: {hi} "
          f"({'contiguas' if out['contiguous'] else 'sin cerrar'})")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    BISECT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Submuestra de densidad
# ---------------------------------------------------------------------------


def density_sample(dates: dict[str, list[str]]) -> list[str]:
    """3 fechas nuevas: una por temporada; la de 2025-26 en oct-dic (era vieja)."""
    picked = [dates["2023-24"][0], dates["2024-25"][0]]
    early = [d for d in dates["2025-26"] if d[:7] in ("2025-10", "2025-11", "2025-12")]
    picked.append(early[0] if early else dates["2025-26"][0])
    return picked


def run_density(prober: Prober, dates: dict[str, list[str]]) -> dict:
    """Barrido completo de la familia de cada fecha. Solo HEAD, sin archivar."""
    print("\nSUBMUESTRA DE DENSIDAD (barrido completo, solo HEAD, sin archivar)")
    print("=" * 78)
    out: dict = {}
    try:
        for target_date in density_sample(dates):
            era, _ = detect_era(prober, target_date)
            if era is None:
                out[target_date] = {"era": None, "hits": 0, "slots": 0}
                print(f"  {target_date}: era AMBIGUA")
                continue
            slots = [s for _, _, s in era_slots(era)]
            if prober.n_requests + len(slots) > TOTAL_REQUEST_BUDGET:
                print(f"  {target_date}: omitida (presupuesto insuficiente)")
                break
            hits = sum(
                1 for s in slots if prober.head(build_url(target_date, s)) == 200
            )
            out[target_date] = {"era": era, "hits": hits, "slots": len(slots)}
            print(f"  {target_date}: era {era}  {hits}/{len(slots)} cortes vivos")
    except StopRule as exc:
        print(f"  !! REGLA DE PARADA: {exc}")
    DENSITY_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Fase 1 enmendada
# ---------------------------------------------------------------------------


def run_phase1_amended() -> dict:
    dates = build_or_load_dates()
    for season, ds in dates.items():
        print(f"  {season}: {len(ds)} fechas")

    prober = Prober()
    # MODO LOCAL FORZADO, no get_datastore(): el .env del proyecto trae
    # NBA_PREDICTOR_MODE=cloud, asi que la factory devolveria un CloudDataStore
    # y el spike escribiria en GCS. Este spike archiva SOLO en disco local
    # (restriccion del encargo). Se cobro una vez el 2026-09-13: 89 PDFs
    # acabaron en gs://.../raw/injury_reports/ por confiar en la factory.
    from nba_predictor.storage.local import LocalDataStore
    store = LocalDataStore(
        db_path=settings.db_path,
        raw_dir=settings.raw_dir,
        processed_dir=settings.processed_dir,
    )

    results: list[dict] = []
    archived = 0
    archived_bytes = 0
    stopped: str | None = None
    boundary: dict = {}
    density: dict = {}

    try:
        boundary = run_bisect(prober)

        print(f"\nFASE 1 ENMENDADA (max {PHASE1_MAX_HEAD_PER_DATE} HEAD/fecha, "
              f"presupuesto total {TOTAL_REQUEST_BUDGET})")
        print("=" * 78)

        for season in SEASONS:
            print(f"\n--- {season} ---")
            for target_date in dates[season]:
                era, era_codes = detect_era(prober, target_date)
                rec: dict = {
                    "season": season,
                    "date": target_date,
                    "era": era,
                    "era_probe_codes": era_codes,
                    "canonical": None,
                    "tried": {},
                    "hit": None,
                    "hit_kind": None,
                    "bytes": None,
                }

                if era is None:
                    results.append(rec)
                    print(f"  {target_date}  era AMBIGUA {era_codes} -> sin PDF")
                    continue

                canonical, neighbors = canonical_and_neighbors(target_date, era)
                rec["canonical"] = canonical

                hit_suffix = None
                hit_kind = None
                for kind, suffix in [("canonical", canonical)] + [
                    ("neighbor", n) for n in neighbors
                ]:
                    code = prober.head(build_url(target_date, suffix))
                    rec["tried"][suffix] = code
                    if code == 200:
                        hit_suffix, hit_kind = suffix, kind
                        break

                if hit_suffix:
                    url = build_url(target_date, hit_suffix)
                    try:
                        prober.check_budget()
                        time.sleep(SLEEP_BETWEEN_REQUESTS)
                        prober.n_requests += 1
                        pdf = download_snapshot(url, session=prober.session, timeout=TIMEOUT)
                        store.save_raw_injury_report(target_date, hit_suffix, pdf)
                        archived += 1
                        archived_bytes += len(pdf)
                        rec["hit"] = hit_suffix
                        rec["hit_kind"] = hit_kind
                        rec["bytes"] = len(pdf)
                        print(f"  {target_date}  [{era}] {hit_kind:<9} {hit_suffix:<10}"
                              f"{len(pdf):>8} bytes")
                    except StopRule:
                        results.append(rec)
                        raise
                    except Exception as exc:
                        rec["error"] = repr(exc)
                        print(f"  {target_date}  [{era}] {hit_suffix} GET FALLIDO {exc!r}")
                else:
                    print(f"  {target_date}  [{era}] sin PDF  {rec['tried']}")

                results.append(rec)

        density = run_density(prober, dates)
    except StopRule as exc:
        stopped = str(exc)
        print(f"\n!! REGLA DE PARADA ACTIVADA: {exc}")
        print("   Guardando lo acumulado y abortando sin reintentos.")

    out = {
        "boundary": boundary,
        "results": results,
        "summary": coverage_table_amended(results),
        "density": density,
        "archived_pdfs": archived,
        "archived_bytes": archived_bytes,
        "total_requests": prober.n_requests,
        "budget": TOTAL_REQUEST_BUDGET,
        "stopped": stopped,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print_summary_amended(out)
    return out


def print_summary_amended(out: dict) -> None:
    print("\n" + "=" * 78)
    print("RESUMEN POR TEMPORADA (cobertura = canonico o vecino inmediato)")
    print("=" * 78)
    print(f"{'temporada':<12}{'sondeadas':>10}{'canonico':>10}{'vecino':>8}"
          f"{'sin PDF':>9}{'cobertura':>11}")
    print("-" * 78)
    for row in out["summary"]:
        print(f"{row['season']:<12}{row['probed']:>10}{row['canonical']:>10}"
              f"{row['neighbor']:>8}{row['no_pdf']:>9}{row['coverage_pct']:>10.1f}%")
    print("-" * 78)
    for row in out["summary"]:
        print(f"  {row['season']} eras detectadas: {row['eras']}")

    b = out.get("boundary") or {}
    if b:
        print(f"\nFRONTERA DE FORMATO: ultimo dia VIEJO {b['last_old']} | "
              f"primer dia NUEVO {b['first_new']}"
              f"{' (contiguas)' if b['contiguous'] else ' (sin cerrar)'}")

    print("\nDENSIDAD DE CORTES (3 de fase 1 + 3 de fase 0 = 6 fechas):")
    for d, info in (out.get("density") or {}).items():
        print(f"  {d}: era {info['era']}  {info['hits']}/{info['slots']} cortes vivos")
    print("  2026-03-13: era new  96/96 cortes vivos   (fase 0)")
    print("  2024-03-13: era old  24/24 cortes vivos   (fase 0)")
    print("  2025-01-15: era old  24/24 cortes vivos   (fase 0)")

    mb = out["archived_bytes"] / (1024 * 1024)
    print(f"\nPDFs archivados: {out['archived_pdfs']}  ({mb:.2f} MB)")
    print(f"Requests totales: {out['total_requests']} / {out['budget']}")
    print(f"Regla de parada: {out['stopped'] or 'NO activada'}")
    print(f"Escrito: {RESULTS_PATH}")


# ---------------------------------------------------------------------------
# CLI delgado
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase0", action="store_true", help="Calibracion de sufijos")
    parser.add_argument("--phase1", action="store_true", help="Muestreo enmendado")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if args.phase0:
        run_phase0()
    elif args.phase1:
        run_phase1_amended()
    else:
        parser.error("Elige --phase0 o --phase1")


if __name__ == "__main__":
    main()
