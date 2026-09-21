"""
D-EXP-1 — EXPERIMENTO P(juega | estatus) SOBRE EL CORPUS GEMBOX.

*** SOLO LECTURA DE LA NUBE. *** Los PDFs salen de
gs://{bucket}/raw/injury_reports/ y la verdad de juego de BigQuery, con
CloudDataStore construido EXPLICITAMENTE aqui (convencion de scripts, pagada
con el incidente GCS del D-RES-2). No toca la red de la NBA. No escribe en GCS
ni en BigQuery. No importa nada del pipeline salvo el parser auditado y el
NameIndex.

QUE MIDE: la probabilidad de que un jugador listado con cierto estatus termine
jugando. Es MEDICION pura — ningun modelo, ninguna feature, ningun cambio de
pipeline. El uso posterior de los numeros es otra tarea.

CORPUS: SOLO temporadas GemBox (2023-24..2025-26), las que lee el parser
auditado de 13e-1. El corpus iTextSharp queda EXCLUIDO hasta que su parser pase
auditoria humana (D-RES-3 abierto): medir con un parser sin auditar produce
numeros verosimiles y falsos, que es el modo de fallo caro de este proyecto.

DEFINICIONES CONGELADAS (CLAUDE.md, D-EXP-1, antes de mirar un dato):
  instancia   (fecha, jugador, estatus) con game_date == fecha objetivo. El PDF
              es multi-fecha; las filas del dia siguiente se ignoran hoy y se
              capturan en SU dia — contarlas aqui duplicaria al jugador con su
              estatus provisional y sesgaria la medicion hacia el corte temprano.
  jugo        PRIMARIA: minutes > 0 en player_game_stats.
              SECUNDARIA: fila presente en el boxscore (activado).
  corte       PRIMARIO publish (la condicion de produccion a las 13:00).
              SECUNDARIO late (maduracion intradia).
  exclusiones partido fuera de games, jugador sin match del NameIndex, equipo
              sin match del catalogo. Contadas aparte, jamas mezcladas con los
              denominadores.

LOS AGREGADOS NO SON OFICIALES hasta que la muestra de auditoria humana se
adjudique contra el PDF y el boxscore (protocolo 13e-1: los invariantes
automaticos no detectan misatribucion).

Uso:
    python scripts/experiment_pjuega.py
    python scripts/experiment_pjuega.py --limit 5     # humo
    python scripts/experiment_pjuega.py --seasons 2025-26
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from nba_predictor.config import settings
from nba_predictor.ingestion.injury_report import (
    InjuryStatus,
    NameIndex,
    load_player_names_from_raw_json,
    parse_pdf,
)
from nba_predictor.storage.cloud import CloudDataStore, _gcs_injury_report_path

log = logging.getLogger("pjuega")

OUT_DIR = Path("data/experiment_pjuega")
CACHE_DIR = OUT_DIR / "cache"
BACKFILL_DIR = Path("data/backfill_injury")

# SOLO GemBox. El cambio de productor quedo fijado al offseason de 2023
# (D-RES-2): 2023-04-09 iTextSharp, 2023-10-24 GemBox.
SEASONS: tuple[str, ...] = ("2023-24", "2024-25", "2025-26")
CUTS: tuple[str, ...] = ("publish", "late")

# Out entra como SANITY CHECK del instrumento entero: si los Out "juegan" a
# menudo, el bug es del experimento, no de la NBA.
STATUSES: tuple[str, ...] = ("Out", "Doubtful", "Questionable", "Probable", "Available")

# El PDF escribe "LA Clippers"/"LA Lakers"; el catalogo, el nombre completo.
_TEAM_ALIAS: dict[str, str] = {
    "laclippers": "losangelesclippers",
    "lalakers": "losangeleslakers",
}


# ---------------------------------------------------------------------------
# Funciones PURAS — sin red, sin BigQuery, unit-testeables
# ---------------------------------------------------------------------------


def season_prefix(season: str) -> str:
    """'2023-24' -> '00223', prefijo de game_id de temporada regular."""
    return f"002{season[2:4]}"


def to_mdy(fecha_iso: str) -> str:
    """'2023-11-24' -> '11/24/2023', el formato de game_date del PDF."""
    y, m, d = fecha_iso.split("-")
    return f"{m}/{d}/{y}"


def normalize_team(nombre: str) -> str:
    """Normaliza un nombre de equipo a su forma comparable.

    Deliberadamente NO reusa _normalize_name del parser: ese esta hecho para
    nombres de persona y elimina numerales romanos y digitos, convirtiendo
    "Philadelphia 76ers" en "philadelphia ers". Aqui solo se conservan
    alfanumericos, que es lo que hace simetrica la comparacion contra el token
    sin espacios del PDF ("Philadelphia76ers").
    """
    clave = re.sub(r"[^a-z0-9]", "", nombre.lower())
    return _TEAM_ALIAS.get(clave, clave)


def filter_rows_for_date(rows: list[Any], fecha_iso: str) -> list[Any]:
    """Filas del PDF cuyo game_date ES la fecha objetivo (el PDF es multi-fecha)."""
    objetivo = to_mdy(fecha_iso)
    return [r for r in rows if r.game_date == objetivo]


def status_name(row: Any) -> str:
    return row.status.value if isinstance(row.status, InjuryStatus) else str(row.status)


def game_id_for_team(
    juegos_del_dia: list[tuple[str, int, int]],
    team_id: int | None,
) -> str | None:
    """game_id del partido de ESE equipo ESE dia, o None."""
    if team_id is None:
        return None
    for gid, home, away in juegos_del_dia:
        if team_id in (home, away):
            return gid
    return None


def classify_instance(
    team_id: int | None,
    player_id: int | None,
    game_id: str | None,
    minutes_by_key: dict[tuple[str, int], float | None],
) -> tuple[str, bool, bool]:
    """(veredicto, jugo_primaria, jugo_secundaria) de una instancia.

    veredicto: 'incluida' | 'sin_match_equipo' | 'sin_match_nombre' | 'sin_partido'.

    La ausencia del jugador en el boxscore NO es exclusion: el partido existe y
    el jugador estaba en el reporte, asi que es un NO-jugo legitimo (no fue
    activado). Solo se excluye lo que el instrumento no supo resolver.
    """
    if team_id is None:
        return "sin_match_equipo", False, False
    if player_id is None:
        return "sin_match_nombre", False, False
    if game_id is None:
        return "sin_partido", False, False

    clave = (game_id, player_id)
    if clave not in minutes_by_key:
        return "incluida", False, False
    minutos = minutes_by_key[clave]
    return "incluida", bool(minutos is not None and minutos > 0), True


def summarize(instancias: list[dict]) -> dict:
    """Agregados por temporada x corte x estatus. Funcion pura."""
    agg: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(
        lambda: {"instancias": 0, "jugo_primaria": 0, "jugo_secundaria": 0}
    )))
    excl: dict = defaultdict(lambda: defaultdict(Counter))

    for i in instancias:
        s, c, st = i["season"], i["cut"], i["status"]
        if i["veredicto"] != "incluida":
            excl[s][c][i["veredicto"]] += 1
            continue
        celda = agg[s][c][st]
        celda["instancias"] += 1
        celda["jugo_primaria"] += int(i["jugo_primaria"])
        celda["jugo_secundaria"] += int(i["jugo_secundaria"])

    salida: dict = {"por_temporada": {}, "exclusiones": {}}
    for s in sorted(agg):
        salida["por_temporada"][s] = {}
        for c in CUTS:
            if c not in agg[s]:
                continue
            salida["por_temporada"][s][c] = {}
            for st in STATUSES:
                if st not in agg[s][c]:
                    continue
                v = agg[s][c][st]
                n = v["instancias"]
                salida["por_temporada"][s][c][st] = {
                    **v,
                    "p_juega_primaria": round(v["jugo_primaria"] / n, 4) if n else None,
                    "p_juega_secundaria": round(v["jugo_secundaria"] / n, 4) if n else None,
                }
    for s in sorted(excl):
        salida["exclusiones"][s] = {c: dict(v) for c, v in excl[s].items()}
    return salida


def render_table(resumen: dict) -> str:
    """Tabla de texto por temporada x corte x estatus."""
    out: list[str] = []
    cab = (f"  {'estatus':<13}{'n':>7}{'jugo(min>0)':>13}{'P(juega)':>10}"
           f"{'activado':>10}{'P(activ)':>10}")
    for s, cortes in resumen["por_temporada"].items():
        for c, filas in cortes.items():
            out.append(f"\n{s}  corte={c}")
            out.append(cab)
            for st, v in filas.items():
                out.append(
                    f"  {st:<13}{v['instancias']:>7}{v['jugo_primaria']:>13}"
                    f"{v['p_juega_primaria']:>10.3f}{v['jugo_secundaria']:>10}"
                    f"{v['p_juega_secundaria']:>10.3f}"
                )
            ex = resumen["exclusiones"].get(s, {}).get(c, {})
            if ex:
                out.append("  exclusiones: " + ", ".join(
                    f"{k}={v}" for k, v in sorted(ex.items())))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Lectura (GCS / BigQuery)
# ---------------------------------------------------------------------------


def make_store() -> CloudDataStore:
    """CloudDataStore EXPLICITO. Nunca get_datastore(): el modo no se hereda."""
    return CloudDataStore(
        project_id=settings.gcp_project_id,
        dataset=settings.bq_dataset,
        bucket_name=settings.gcs_bucket,
    )


def load_teams(store: CloudDataStore) -> dict[str, int]:
    """nombre normalizado -> team_id.

    Se ITERAN las filas: .to_dataframe() bajaria por la Storage Read API y
    exigiria bigquery.readsessions.create (capa 5 de la cebolla, ya cobrada dos
    veces en este proyecto).
    """
    sql = f"SELECT team_id, name FROM `{settings.gcp_project_id}.{settings.bq_dataset}.teams`"
    return {normalize_team(str(r.name)): int(r.team_id) for r in store._bq.query(sql).result()}


def load_truth(
    store: CloudDataStore, season: str
) -> tuple[dict[str, list[tuple[str, int, int]]], dict[tuple[str, int], float | None]]:
    """(partidos por fecha, minutos por (game_id, player_id)) de una temporada."""
    ds = f"{settings.gcp_project_id}.{settings.bq_dataset}"
    juegos: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    for r in store._bq.query(
        f"SELECT game_id, game_date, home_team_id, away_team_id "
        f"FROM `{ds}.games` WHERE season = '{season}'"
    ).result():
        juegos[str(r.game_date)].append(
            (str(r.game_id), int(r.home_team_id), int(r.away_team_id))
        )

    minutos: dict[tuple[str, int], float | None] = {}
    for r in store._bq.query(
        f"SELECT p.game_id, p.player_id, p.minutes FROM `{ds}.player_game_stats` p "
        f"JOIN `{ds}.games` g ON g.game_id = p.game_id WHERE g.season = '{season}'"
    ).result():
        minutos[(str(r.game_id), int(r.player_id))] = (
            float(r.minutes) if r.minutes is not None else None
        )
    return dict(juegos), minutos


def fetch_pdf(store: CloudDataStore, fecha: str, sufijo: str) -> bytes:
    """PDF de GCS con cache en disco: re-correr el experimento no re-descarga."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local = CACHE_DIR / f"{fecha}_{sufijo}.pdf"
    if local.exists():
        return local.read_bytes()
    raw = store._gcs.bucket(store.bucket_name).blob(
        store._gcs_path(_gcs_injury_report_path(fecha, sufijo))
    ).download_as_bytes()
    local.write_bytes(raw)
    return raw


def cuts_of_season(season: str) -> list[dict]:
    """Registros del backfill: fecha -> sufijos publish/late ya archivados."""
    data = json.loads((BACKFILL_DIR / f"{season}.json").read_text(encoding="utf-8"))
    return sorted(data["records"], key=lambda r: r["date"])


# ---------------------------------------------------------------------------
# Listados de auditoria humana
# ---------------------------------------------------------------------------


def render_audit(instancias: list[dict], fecha: str) -> str:
    """Listado instancia por instancia de una fecha, para cotejo humano.

    El protocolo de 13e-1 (cobrado cinco veces) exige que la CORRECCION la
    establezca la lectura humana del listado contra el documento fuente; los
    invariantes automaticos no detectan misatribucion. Cada linea trae todo lo
    necesario para el cotejo: lo que dijo el PDF (equipo, jugador, estatus,
    razon) y lo que dice el boxscore (game_id, minutos), sin resumir.
    """
    del_dia = [i for i in instancias if i["date"] == fecha]
    out = [f"AUDITORIA D-EXP-1 — fecha {fecha}", "=" * 78]
    for cut in CUTS:
        filas = [i for i in del_dia if i["cut"] == cut]
        if not filas:
            out.append(f"\n--- corte {cut}: sin PDF archivado ---")
            continue
        suf = filas[0]["suffix"]
        out.append(f"\n--- corte {cut} (PDF {fecha}_{suf}.pdf) — {len(filas)} instancias ---")
        out.append(f"{'#':>3} {'EQUIPO PDF':<24}{'JUGADOR PDF':<26}{'ESTATUS':<13}"
                   f"{'MIN':>6}  {'JUGO':<7}{'ACTIV':<7}{'VEREDICTO':<17}RAZON PDF")
        for n, i in enumerate(sorted(filas, key=lambda x: (x["team"], x["player_name"])), 1):
            minutos = "-" if i["minutes"] is None else f"{i['minutes']:.1f}"
            out.append(
                f"{n:>3} {i['team']:<24}{i['player_name']:<26}{i['status']:<13}"
                f"{minutos:>6}  {str(i['jugo_primaria']):<7}{str(i['jugo_secundaria']):<7}"
                f"{i['veredicto']:<17}{(i['reason'] or '')[:40]}"
            )
        out.append(f"    game_ids del dia: "
                   f"{sorted({i['game_id'] for i in filas if i['game_id']})}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="D-EXP-1: P(juega | estatus), corpus GemBox")
    ap.add_argument("--limit", type=int, default=None, help="N fechas por temporada (humo)")
    ap.add_argument("--seasons", nargs="+", default=list(SEASONS))
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    store = make_store()
    equipos = load_teams(store)
    print(f"Catalogo de equipos: {len(equipos)}")

    instancias: list[dict] = []
    sin_match_nombres: dict[str, set[str]] = defaultdict(set)
    sin_match_equipos: set[str] = set()
    pdfs_ilegibles: list[str] = []

    for season in args.seasons:
        print(f"\n=== {season} ===", flush=True)
        juegos, minutos = load_truth(store, season)
        nombres = load_player_names_from_raw_json(
            settings.raw_dir, glob_pattern=f"{season_prefix(season)}*.json"
        )
        idx = NameIndex.from_player_map(nombres)
        print(f"  {len(juegos)} fechas con partidos | {len(nombres)} jugadores en el indice",
              flush=True)

        registros = cuts_of_season(season)
        if args.limit:
            registros = registros[: args.limit]

        for n, rec in enumerate(registros, 1):
            fecha = rec["date"]
            for cut in CUTS:
                sufijo = rec["cuts"].get(cut, {}).get("suffix")
                if not sufijo:
                    continue
                try:
                    rows, _nys = parse_pdf(fetch_pdf(store, fecha, sufijo))
                except Exception as exc:  # noqa: BLE001 — se registra y se sigue
                    pdfs_ilegibles.append(f"{fecha}_{sufijo}: {exc!r}")
                    continue

                for row in filter_rows_for_date(rows, fecha):
                    st = status_name(row)
                    if st not in STATUSES:
                        continue
                    tid = equipos.get(normalize_team(row.team))
                    pid = idx.match(row.player_name)
                    gid = game_id_for_team(juegos.get(fecha, []), tid)
                    veredicto, p1, p2 = classify_instance(tid, pid, gid, minutos)
                    if veredicto == "sin_match_nombre":
                        sin_match_nombres[season].add(row.player_name)
                    elif veredicto == "sin_match_equipo":
                        sin_match_equipos.add(row.team)
                    instancias.append({
                        "season": season, "cut": cut, "date": fecha, "suffix": sufijo,
                        "team": row.team, "player_name": row.player_name, "status": st,
                        "reason": row.reason, "team_id": tid, "player_id": pid,
                        "game_id": gid,
                        "minutes": minutos.get((gid, pid)) if gid and pid else None,
                        "veredicto": veredicto,
                        "jugo_primaria": p1, "jugo_secundaria": p2,
                    })
            if n % 20 == 0:
                print(f"  {n}/{len(registros)} fechas", flush=True)

    resumen = summarize(instancias)
    resumen["sin_match_nombres_distintos"] = {s: len(v) for s, v in sin_match_nombres.items()}
    resumen["equipos_sin_match"] = sorted(sin_match_equipos)
    resumen["pdfs_ilegibles"] = pdfs_ilegibles

    (OUT_DIR / "results.json").write_text(
        json.dumps({"n_instancias": len(instancias), "resumen": resumen},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / "instancias.json").write_text(
        json.dumps(instancias, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    # Listados de la muestra de auditoria, congelada con seed 42 ANTES de
    # computar nada (data/experiment_pjuega/audit_dates.json).
    audit_file = OUT_DIR / "audit_dates.json"
    if audit_file.exists():
        elegidas = json.loads(audit_file.read_text(encoding="utf-8"))[
            "elegidas_antes_de_computar"
        ]
        for temporada, fecha in elegidas.items():
            if temporada not in args.seasons:
                continue
            destino = OUT_DIR / f"auditoria_{fecha}.txt"
            destino.write_text(render_audit(instancias, fecha), encoding="utf-8")
            print(f"Listado de auditoria: {destino}")

    tabla = render_table(resumen)
    (OUT_DIR / "tabla.txt").write_text(tabla, encoding="utf-8")
    print(tabla)
    print(f"\nInstancias: {len(instancias)} | PDFs ilegibles: {len(pdfs_ilegibles)}")
    print(f"Equipos sin match: {resumen['equipos_sin_match'] or 'ninguno'}")
    print(f"Escrito en {OUT_DIR}/: results.json, instancias.json, tabla.txt")


if __name__ == "__main__":
    main()
