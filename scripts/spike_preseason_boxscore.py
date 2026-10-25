"""Spike D-PRE-8: ¿el boxscore de PRETEMPORADA (prefijo 001) tiene la misma
estructura que el de temporada regular (002)? ¿Se ven en él los 4 traspasos
del verano que el mensaje 2 publicó con el equipo viejo?

SOLO LECTURA. Lee el scheduleLeagueV2 archivado más reciente de
gs://.../raw/schedules/ (lectura, sin DataStore: no hay método de lectura de
schedules) y baja boxscores con CDNClient SIN persistirlos. Lo único que se
escribe es data/spike_preseason/report.json, en disco local. Por convención
de scripts spike_* se construye LocalDataStore explícito; no se usa para
escribir (ni get_datastore(), ni la nube).

Pre-registro: CLAUDE.md, bloque "D-PRE-8 (PRE-REGISTRO 2026-10-20)".

Uso:
    python scripts/spike_preseason_boxscore.py
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUT = Path("data/spike_preseason/report.json")
BUCKET = "predictorsnonprod-nba-predictors"
SCHEDULE_PREFIX = "raw/schedules/scheduleLeagueV2_"
REFERENCIA_002 = "0022600001"   # BOS@DET, primer partido de 2026-27 (jugado)
CASOS = {203944: "Julius Randle", 1627742: "Brandon Ingram",
         202695: "Kawhi Leonard", 1642262: "Cody Williams"}
EQUIPOS_CASOS = {1610612751: "BKN", 1610612750: "MIN", 1610612746: "LAC",
                 1610612761: "TOR", 1610612762: "UTA"}
MAX_DESCARGAS_D = 40
_MIN_ISO = re.compile(r"^PT\d+M\d+(\.\d+)?S$")


# ---------------------------------------------------------------------------
# Funciones puras
# ---------------------------------------------------------------------------


def preseason_games(raw_schedule: dict) -> list[dict[str, Any]]:
    """Partidos con prefijo 001 del scheduleLeagueV2, con fecha, estado y equipos."""
    out = []
    for bloque in raw_schedule.get("leagueSchedule", {}).get("gameDates", []):
        for g in bloque.get("games", []):
            gid = str(g.get("gameId", ""))
            if not gid.startswith("001"):
                continue
            out.append({
                "game_id": gid,
                "game_date": str(g.get("gameDateEst", ""))[:10],
                "status": g.get("gameStatus"),
                "home_id": int(g.get("homeTeam", {}).get("teamId") or 0),
                "away_id": int(g.get("awayTeam", {}).get("teamId") or 0),
                "home": g.get("homeTeam", {}).get("teamTricode", ""),
                "away": g.get("awayTeam", {}).get("teamTricode", ""),
            })
    return sorted(out, key=lambda x: (x["game_date"], x["game_id"]))


def summarize_preseason(games: list[dict]) -> dict[str, Any]:
    fechas = [g["game_date"] for g in games if g["game_date"]]
    return {
        "partidos_001": len(games),
        "rango_fechas": [min(fechas), max(fechas)] if fechas else None,
        "con_gameStatus_3": sum(1 for g in games if g["status"] == 3),
        "por_status": {str(s): sum(1 for g in games if g["status"] == s)
                       for s in sorted({g["status"] for g in games}, key=str)},
    }


def structure_signature(raw_box: dict) -> dict[str, Any]:
    """Firma estructural del boxscore: conjuntos de claves en cada nivel que
    consume el parser de la Decisión 9, más formato de minutes y status."""
    game = raw_box.get("game", {})
    equipos = [game.get("homeTeam", {}), game.get("awayTeam", {})]
    jugadores = [p for t in equipos for p in t.get("players", [])]
    minutos = [p.get("statistics", {}).get("minutes") for p in jugadores]
    return {
        "top": sorted(raw_box.keys()),
        "game": sorted(game.keys()),
        "team": sorted(set().union(*(t.keys() for t in equipos))),
        "team_statistics": sorted(set().union(
            *(t.get("statistics", {}).keys() for t in equipos))),
        "player": sorted(set().union(*(p.keys() for p in jugadores))) if jugadores else [],
        "player_statistics": sorted(set().union(
            *(p.get("statistics", {}).keys() for p in jugadores))) if jugadores else [],
        "status_values": sorted({str(p.get("status")) for p in jugadores}),
        "minutes_iso_8601": all(isinstance(m, str) and _MIN_ISO.match(m) for m in minutos),
        "minutes_muestra": sorted({m for m in minutos if isinstance(m, str)})[:3],
        "n_jugadores": len(jugadores),
    }


def diff_signatures(a: dict, b: dict, etiqueta_a: str, etiqueta_b: str) -> dict[str, Any]:
    """Diferencias literales entre dos firmas. Vacío = sin diferencias."""
    diff: dict[str, Any] = {}
    for k in ("top", "game", "team", "team_statistics", "player", "player_statistics",
              "status_values"):
        sa, sb = set(a.get(k, [])), set(b.get(k, []))
        if sa != sb:
            diff[k] = {f"solo_{etiqueta_a}": sorted(sa - sb), f"solo_{etiqueta_b}": sorted(sb - sa)}
    if a.get("minutes_iso_8601") != b.get("minutes_iso_8601"):
        diff["minutes_iso_8601"] = {etiqueta_a: a.get("minutes_iso_8601"),
                                    etiqueta_b: b.get("minutes_iso_8601")}
    return diff


def players_in_box(raw_box: dict, ids: set[int]) -> dict[int, dict[str, Any]]:
    """{player_id: {team_id, status, minutes}} de los ids pedidos presentes en el boxscore."""
    out = {}
    game = raw_box.get("game", {})
    for t in (game.get("homeTeam", {}), game.get("awayTeam", {})):
        for p in t.get("players", []):
            pid = int(p.get("personId", 0))
            if pid in ids:
                out[pid] = {"team_id": int(t.get("teamId", 0)), "status": p.get("status"),
                            "minutes": p.get("statistics", {}).get("minutes")}
    return out


def parser_accepts(raw_box: dict, game_id: str) -> dict[str, Any]:
    """¿Los normalizadores de la Decisión 9 aceptan este payload? (sin persistir)."""
    from nba_predictor.ingestion.cdn_client import (
        _normalize_cdn_player_stats,
        _normalize_cdn_team_stats,
    )

    try:
        t = _normalize_cdn_team_stats(raw_box["game"], game_id)
        p = _normalize_cdn_player_stats(raw_box["game"], game_id)
        return {"ok": True, "filas_equipo": len(t), "filas_jugador_activos": len(p)}
    except Exception as exc:  # noqa: BLE001 — se reporta, no se adapta
        return {"ok": False, "error": repr(exc)[:200]}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    from google.cloud import storage

    from nba_predictor.config import settings
    from nba_predictor.ingestion.cdn_client import CDNClient
    from nba_predictor.storage.local import LocalDataStore

    # Convención de scripts spike_*: almacén LOCAL explícito, nunca heredado.
    LocalDataStore(raw_dir=settings.raw_dir, db_path=settings.db_path,
                   processed_dir=settings.processed_dir)

    gcs = storage.Client(project=settings.gcp_project_id)
    blobs = sorted(b.name for b in gcs.list_blobs(BUCKET, prefix=SCHEDULE_PREFIX))
    sched_name = blobs[-1]
    raw_sched = json.loads(gcs.bucket(BUCKET).blob(sched_name).download_as_bytes())
    print(f"schedule archivado: {sched_name} (seasonYear {raw_sched['leagueSchedule']['seasonYear']})")

    games = preseason_games(raw_sched)
    tabla_a = summarize_preseason(games)
    jugados = [g for g in games if g["status"] == 3]

    cdn = CDNClient()
    descargas = 0

    def bajar(gid: str) -> dict:
        nonlocal descargas
        descargas += 1
        raw, base = cdn._fetch_with_fallback(f"liveData/boxscore/boxscore_{gid}.json",
                                             f"spike/{gid}")
        return raw

    # (b) dos 001 (primero de octubre jugado y último jugado) + un 002 de referencia
    oct_ = [g for g in jugados if g["game_date"] >= "2026-10-01"]
    primero, ultimo = oct_[0], jugados[-1]
    box_pre1, box_pre2 = bajar(primero["game_id"]), bajar(ultimo["game_id"])
    box_reg = bajar(REFERENCIA_002)
    firmas = {primero["game_id"]: structure_signature(box_pre1),
              ultimo["game_id"]: structure_signature(box_pre2),
              REFERENCIA_002: structure_signature(box_reg)}
    diff_c = {
        f"{primero['game_id']}_vs_{REFERENCIA_002}": diff_signatures(
            firmas[primero["game_id"]], firmas[REFERENCIA_002], "001", "002"),
        f"{ultimo['game_id']}_vs_{REFERENCIA_002}": diff_signatures(
            firmas[ultimo["game_id"]], firmas[REFERENCIA_002], "001", "002"),
    }
    parser = {gid: parser_accepts(b, gid) for gid, b in
              ((primero["game_id"], box_pre1), (ultimo["game_id"], box_pre2),
               (REFERENCIA_002, box_reg))}

    # (d) los 4 casos: partidos 001 jugados con alguno de sus equipos viejos o nuevos
    relevantes = [g for g in jugados
                  if g["home_id"] in EQUIPOS_CASOS or g["away_id"] in EQUIPOS_CASOS]
    ya = {primero["game_id"]: box_pre1, ultimo["game_id"]: box_pre2}
    pendientes = [g for g in relevantes if g["game_id"] not in ya]
    muestreado = len(pendientes) > MAX_DESCARGAS_D
    if muestreado:
        pendientes = sorted(random.Random(42).sample(pendientes, MAX_DESCARGAS_D),
                            key=lambda g: (g["game_date"], g["game_id"]))
    vistos: dict[int, list[dict]] = {pid: [] for pid in CASOS}
    revisados = [g for g in relevantes if g["game_id"] in ya] + pendientes
    descargas_d = 0
    for g in revisados:
        box = ya.get(g["game_id"])
        if box is None:
            box = bajar(g["game_id"])
            descargas_d += 1
        for pid, info in players_in_box(box, set(CASOS)).items():
            vistos[pid].append({"game_id": g["game_id"], "game_date": g["game_date"],
                                "team": EQUIPOS_CASOS.get(info["team_id"], info["team_id"]),
                                "status": info["status"], "minutes": info["minutes"]})
    tabla_d = {
        CASOS[pid]: {
            "player_id": pid,
            "apariciones_001": len(v),
            "equipos": sorted({str(x["team"]) for x in v}),
            "ultima": v[-1] if v else None,
        } for pid, v in vistos.items()
    }

    report = {
        "schedule": sched_name,
        "tabla_a": tabla_a,
        "boxscores_b": {"001_primero_octubre": primero, "001_ultimo_jugado": ultimo,
                        "002_referencia": REFERENCIA_002},
        "firmas": firmas,
        "diff_c": diff_c,
        "parser_decision9": parser,
        "tabla_d": tabla_d,
        "detalle_d": vistos,
        "partidos_001_relevantes": len(relevantes),
        "revisados_d": len(revisados),
        "descargas_d": descargas_d,
        "muestreado_d": muestreado,
        "descargas_totales": descargas,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("tabla_a", "boxscores_b", "diff_c",
                                              "parser_decision9", "tabla_d",
                                              "partidos_001_relevantes", "revisados_d",
                                              "descargas_d", "muestreado_d",
                                              "descargas_totales")},
                     ensure_ascii=False, indent=2, default=str))
    print(f"[reporte en {OUT}]")


if __name__ == "__main__":
    main()
