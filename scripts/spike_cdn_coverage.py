"""
SPIKE DE SOLO LECTURA — cobertura CDN vs parser legacy (stats.nba.com).

Descarga boxscores de cdn.nba.com para 3 partidos de 2025-26, compara campo
a campo contra nuestro esquema canónico y los valores ya almacenados en SQLite.
No modifica nada. Ejecutar con: python scripts/spike_cdn_coverage.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
import sys

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

# Force UTF-8 on Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = Path("data/nba.sqlite")
CDN_BOX = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"
CDN_SCHEDULE = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (research spike)"}

GAME_IDS = ["0022500002", "0022500001", "0022500481"]  # early, early, mid-season 2025-26


# ---------------------------------------------------------------------------
def sec(s: float):
    time.sleep(s)


def fetch_cdn(url: str) -> dict:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# A. Inventario del parser legacy
# ---------------------------------------------------------------------------
TEAM_COLS_LEGACY = [
    "game_id", "team_id", "is_home",
    "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
    "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "plus_minus",
]

PLAYER_COLS_LEGACY = [
    "game_id", "player_id", "team_id", "is_home", "minutes", "started",
    "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
    "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "plus_minus",
]

GAMES_COLS_LEGACY = [
    "game_id", "season", "season_type", "game_date",
    "home_team_id", "away_team_id",
    "home_pts", "away_pts", "home_won", "neutral_site",
]


def load_sqlite(game_id: str) -> dict:
    """Carga team_game_stats y player_game_stats de SQLite para comparación."""
    conn = sqlite3.connect(DB)
    team = pd.read_sql(
        f"SELECT * FROM team_game_stats WHERE game_id='{game_id}'", conn
    )
    player = pd.read_sql(
        f"SELECT * FROM player_game_stats WHERE game_id='{game_id}'", conn
    )
    game = pd.read_sql(
        f"SELECT * FROM games WHERE game_id='{game_id}'", conn
    )
    conn.close()
    return {"team": team, "player": player, "game": game}


# ---------------------------------------------------------------------------
# B. Parser CDN
# ---------------------------------------------------------------------------

def parse_cdn_team(game_json: dict, home_team_id: int) -> pd.DataFrame:
    """Extrae team stats del CDN boxscore."""
    teams = game_json["game"]["homeTeam"], game_json["game"]["awayTeam"]
    rows = []
    for t in teams:
        s = t["statistics"]
        rows.append({
            "team_id": int(t["teamId"]),
            "is_home": 1 if int(t["teamId"]) == home_team_id else 0,
            "fgm": int(s["fieldGoalsMade"]),
            "fga": int(s["fieldGoalsAttempted"]),
            "fg3m": int(s["threePointersMade"]),
            "fg3a": int(s["threePointersAttempted"]),
            "ftm": int(s["freeThrowsMade"]),
            "fta": int(s["freeThrowsAttempted"]),
            "oreb": int(s["reboundsOffensive"]),
            "dreb": int(s["reboundsDefensive"]),
            "ast": int(s["assists"]),
            "stl": int(s["steals"]),
            "blk": int(s["blocks"]),
            "tov": int(s["turnovers"]),
            "pf": int(s["foulsPersonal"]),
            "plus_minus": float(s.get("plusMinusPoints", 0)),
        })
    return pd.DataFrame(rows)


def parse_cdn_player(game_json: dict, game_id: str, home_team_id: int) -> pd.DataFrame:
    """Extrae player stats del CDN boxscore.

    Campos clave de disponibilidad:
      - status: "ACTIVE" / "INACTIVE" (en roster, activo o inactivo esta noche)
      - played: "1" o "0" (jugó o DNP)
      - minutes: "PT##M##.##S" (ISO8601 duration) o "" si no jugó
    """
    teams = [game_json["game"]["homeTeam"], game_json["game"]["awayTeam"]]
    rows = []
    for t in teams:
        team_id = int(t["teamId"])
        is_home = 1 if team_id == home_team_id else 0
        for p in t["players"]:
            s = p.get("statistics", {})
            status = p.get("status", "")  # ACTIVE / INACTIVE
            played_flag = p.get("played", "0")  # "1" = jugó, "0" = DNP

            # Minutos: "PT12M30.00S" → decimal; "" si no jugó
            raw_min = s.get("minutesCalculated", "") or s.get("minutes", "")
            minutes = _parse_cdn_minutes(raw_min)

            # START_POSITION equivalent
            starter_flag = p.get("starter", "0")

            rows.append({
                "player_id": int(p["personId"]),
                "team_id": team_id,
                "is_home": is_home,
                "status": status,
                "played_flag": played_flag,
                "starter_flag": starter_flag,
                "minutes_raw": raw_min,
                "minutes": minutes,
                "fgm": int(s.get("fieldGoalsMade", 0)),
                "fga": int(s.get("fieldGoalsAttempted", 0)),
                "fg3m": int(s.get("threePointersMade", 0)),
                "fg3a": int(s.get("threePointersAttempted", 0)),
                "ftm": int(s.get("freeThrowsMade", 0)),
                "fta": int(s.get("freeThrowsAttempted", 0)),
                "oreb": int(s.get("reboundsOffensive", 0)),
                "dreb": int(s.get("reboundsDefensive", 0)),
                "ast": int(s.get("assists", 0)),
                "stl": int(s.get("steals", 0)),
                "blk": int(s.get("blocks", 0)),
                "tov": int(s.get("turnovers", 0)),
                "pf": int(s.get("foulsPersonal", 0)),
                "plus_minus": float(s.get("plusMinusPoints", 0)),
            })
    df = pd.DataFrame(rows)
    df["game_id"] = game_id
    return df


def _parse_cdn_minutes(raw: str) -> float | None:
    """Convierte ISO 8601 duration 'PT12M30.00S' a minutos decimales."""
    if not raw or raw == "PT00M00.00S":
        return None
    # formato: PTxxx.xxS o PTxMxxx.xxS
    import re
    m = re.match(r"PT(?:(\d+)M)?(\d+(?:\.\d+)?)S", raw)
    if m:
        mins = int(m.group(1) or 0)
        secs = float(m.group(2))
        return mins + secs / 60.0
    return None


# ---------------------------------------------------------------------------
# C. Comparación de valores
# ---------------------------------------------------------------------------

def compare_team_stats(game_id: str, cdn_teams: pd.DataFrame, sqlite: dict) -> list[str]:
    """Compara team stats CDN vs SQLite campo a campo."""
    db_team = sqlite["team"].copy()
    issues = []
    for _, cdn_row in cdn_teams.iterrows():
        team_id = cdn_row["team_id"]
        db_row = db_team[db_team["team_id"] == team_id]
        if db_row.empty:
            issues.append(f"  TEAM {team_id}: no encontrado en SQLite")
            continue
        db_row = db_row.iloc[0]
        for col in ["fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
                    "oreb", "dreb", "ast", "stl", "blk", "tov", "pf"]:
            if int(cdn_row[col]) != int(db_row[col]):
                issues.append(
                    f"  TEAM {team_id} / {col}: CDN={cdn_row[col]} vs SQLite={db_row[col]}"
                )
        # plus_minus: puede diferir por sign convention o rounding
        cdn_pm = float(cdn_row["plus_minus"])
        db_pm = float(db_row["plus_minus"])
        if abs(cdn_pm - db_pm) > 0.5:
            issues.append(
                f"  TEAM {team_id} / plus_minus: CDN={cdn_pm} vs SQLite={db_pm}"
            )
    return issues


def compare_player_stats(game_id: str, cdn_players: pd.DataFrame, sqlite: dict) -> tuple[list[str], dict]:
    """Compara player stats CDN vs SQLite campo a campo. Solo jugadores activos."""
    db_player = sqlite["player"].copy()
    issues = []
    coverage = {
        "cdn_total_rows": len(cdn_players),
        "cdn_active": len(cdn_players[cdn_players["status"] == "ACTIVE"]),
        "cdn_played": len(cdn_players[cdn_players["minutes"].notna()]),
        "cdn_dnp": len(cdn_players[(cdn_players["status"] == "ACTIVE") & cdn_players["minutes"].isna()]),
        "cdn_inactive": len(cdn_players[cdn_players["status"] == "INACTIVE"]),
        "sqlite_rows": len(db_player),
        "sqlite_played": len(db_player[db_player["minutes"].notna()]),
        "sqlite_dnp": len(db_player[db_player["minutes"].isna()]),
    }

    # Comparar solo jugadores que tienen fila en SQLite
    for _, cdn_row in cdn_players[cdn_players["status"] == "ACTIVE"].iterrows():
        pid = cdn_row["player_id"]
        db_row = db_player[db_player["player_id"] == pid]
        if db_row.empty:
            continue  # En CDN activo pero no en SQLite legacy (roster diferente)
        db_row = db_row.iloc[0]

        # Minutos: solo si el jugador jugó
        if cdn_row["minutes"] is not None and db_row["minutes"] is not None:
            diff = abs(float(cdn_row["minutes"]) - float(db_row["minutes"]))
            if diff > 0.1:
                issues.append(
                    f"  PLAYER {pid} / minutes: CDN={cdn_row['minutes']:.3f} vs SQLite={db_row['minutes']:.3f}"
                )

        if cdn_row["minutes"] is None and db_row["minutes"] is not None:
            issues.append(
                f"  PLAYER {pid}: CDN→DNP pero SQLite tiene minutes={db_row['minutes']:.1f}"
            )

        for col in ["fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
                    "oreb", "dreb", "ast", "stl", "blk", "tov", "pf"]:
            if int(cdn_row[col]) != int(db_row[col]):
                issues.append(
                    f"  PLAYER {pid} / {col}: CDN={cdn_row[col]} vs SQLite={db_row[col]}"
                )

    return issues, coverage


# ---------------------------------------------------------------------------
# D. Schedule CDN
# ---------------------------------------------------------------------------

def analyze_schedule_cdn(schedule_json: dict) -> dict:
    """Extrae estructura del schedule CDN para una temporada de ejemplo."""
    game_dates = schedule_json.get("leagueSchedule", {}).get("gameDates", [])
    if not game_dates:
        return {"error": "No gameDates found"}

    # Tomar el primer partido disponible como muestra
    sample_game = None
    for gd in game_dates[:30]:
        games = gd.get("games", [])
        if games:
            sample_game = games[0]
            break

    if not sample_game:
        return {"error": "No sample game found"}

    available_fields = {
        "gameId": sample_game.get("gameId"),
        "gameDateUTC": sample_game.get("gameDateUTC"),
        "gameDateEst": sample_game.get("gameDateEst"),
        "gameTimeUTC": sample_game.get("gameTimeUTC"),
        "gameTimeEst": sample_game.get("gameTimeEst"),
        "homeTeamId": sample_game.get("homeTeam", {}).get("teamId"),
        "awayTeamId": sample_game.get("awayTeam", {}).get("teamId"),
        "homeScore": sample_game.get("homeTeam", {}).get("score"),
        "awayScore": sample_game.get("awayTeam", {}).get("score"),
        "homeWins": sample_game.get("homeTeam", {}).get("wins"),
        "homeLosses": sample_game.get("homeTeam", {}).get("losses"),
        "gameStatus": sample_game.get("gameStatus"),
        "gameStatusText": sample_game.get("gameStatusText"),
        "ifNecessary": sample_game.get("ifNecessary"),
        "seriesGameNumber": sample_game.get("seriesGameNumber"),
        "seasonId": schedule_json.get("leagueSchedule", {}).get("seasonYear"),
        "all_game_keys": list(sample_game.keys()),
    }
    return available_fields


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("SPIKE CDN — ANÁLISIS DE COBERTURA vs parser legacy")
    print("=" * 70)

    # ── A. Inventario ─────────────────────────────────────────────────────────
    print("\n=== A. INVENTARIO DEL PARSER LEGACY (stats.nba.com) ===\n")
    print("team_game_stats — columnas canónicas:")
    for c in TEAM_COLS_LEGACY:
        print(f"  {c}")
    print("\nplayer_game_stats — columnas canónicas:")
    for c in PLAYER_COLS_LEGACY:
        print(f"  {c}")
    print("""
Derivaciones en parser:
  - is_home   : (team_id == home_team_id) desde tabla games → no viene de la API
  - minutes   : 'MM:SS' → decimal (e.g. '32:30' → 32.5)
  - started   : START_POSITION.strip() != '' → 1, else 0
  - tov       : API columna 'TO'
  - plus_minus: API columna 'PLUS_MINUS'
  - fgm/fga etc: renombrado directo de API

games — columnas canónicas:
  - game_id, season, season_type, game_date
  - home_team_id, away_team_id, home_pts, away_pts, home_won, neutral_site
  - neutral_site derivado por fecha (burbuja COVID 2020-07-30..2020-08-14)
  - home_won  : (WL == 'W') del equipo local
  - home_team_id: equipo cuyo MATCHUP contiene 'vs.' (no '@')
""")

    # ── B. Descarga CDN + Mapeo ───────────────────────────────────────────────
    print("\n=== B. COBERTURA CDN (campo a campo) ===\n")

    sample_game_id = GAME_IDS[0]
    print(f"Descargando CDN boxscore para muestra: {sample_game_id}...")
    sec(1)
    cdn_json = fetch_cdn(CDN_BOX.format(game_id=sample_game_id))

    # Explorar estructura
    game = cdn_json["game"]
    home = game["homeTeam"]
    away = game["awayTeam"]
    home_player = home["players"][0]

    print(f"\nEstructura top-level CDN boxscore:")
    print(f"  game_id en CDN      : {game.get('gameId')}")
    print(f"  gameStatus          : {game.get('gameStatus')}  (2=final)")
    print(f"  gameDateEst         : {game.get('gameDateEst')}")
    print(f"  homeTeam.teamId     : {home.get('teamId')}")
    print(f"  awayTeam.teamId     : {away.get('teamId')}")
    print(f"\nEjemplo campos team.statistics:")
    ts = home["statistics"]
    print(f"  {list(ts.keys())[:20]}")
    print(f"\nEjemplo fields un jugador (keys):")
    print(f"  player-level: {list(home_player.keys())}")
    print(f"  statistics:   {list(home_player.get('statistics', {}).keys())[:20]}")

    # Tabla de mapeo
    print("\n" + "-"*70)
    print("TABLA DE MAPEO — campo canónico | path CDN | veredicto")
    print("-"*70)

    mappings_team = [
        ("team_id",    "homeTeam.teamId / awayTeam.teamId",         "EXACTO — int"),
        ("is_home",    "(derivado: game.homeTeam.teamId == team_id)","DERIVABLE"),
        ("fgm",        "team.statistics.fieldGoalsMade",             "EXACTO"),
        ("fga",        "team.statistics.fieldGoalsAttempted",        "EXACTO"),
        ("fg3m",       "team.statistics.threePointersMade",          "EXACTO"),
        ("fg3a",       "team.statistics.threePointersAttempted",     "EXACTO"),
        ("ftm",        "team.statistics.freeThrowsMade",             "EXACTO"),
        ("fta",        "team.statistics.freeThrowsAttempted",        "EXACTO"),
        ("oreb",       "team.statistics.reboundsOffensive",          "EXACTO"),
        ("dreb",       "team.statistics.reboundsDefensive",          "EXACTO"),
        ("ast",        "team.statistics.assists",                    "EXACTO"),
        ("stl",        "team.statistics.steals",                     "EXACTO"),
        ("blk",        "team.statistics.blocks",                     "EXACTO"),
        ("tov",        "team.statistics.turnovers",                  "EXACTO"),
        ("pf",         "team.statistics.foulsPersonal",              "EXACTO"),
        ("plus_minus", "team.statistics.plusMinusPoints",            "EXACTO (float)"),
    ]
    print("\nTEAM_GAME_STATS:")
    for field, path, verdict in mappings_team:
        status = "✓" if "EXACTO" in verdict or "DERIVABLE" in verdict else "✗"
        print(f"  {status} {field:<20} | {path:<45} | {verdict}")

    # Check actual keys
    print(f"\n  Claves reales en team.statistics:")
    for k in sorted(ts.keys()):
        print(f"    {k}")

    # plus_minus check
    pm = ts.get("plusMinusPoints", "AUSENTE")
    print(f"\n  plusMinusPoints en CDN: {pm}")

    # Player fields
    sample_ps = home_player.get("statistics", {})
    print(f"\n  Claves reales en player.statistics:")
    for k in sorted(sample_ps.keys()):
        print(f"    {k}")

    print(f"\n  Clave 'status' en player: {home_player.get('status', 'AUSENTE')}")
    print(f"  Clave 'played' en player: {home_player.get('played', 'AUSENTE')}")
    print(f"  Clave 'starter' en player: {home_player.get('starter', 'AUSENTE')}")
    print(f"  Clave 'active' en player: {home_player.get('active', 'AUSENTE')}")
    print(f"  Minutos raw ejemplo: {sample_ps.get('minutesCalculated', 'AUSENTE')}")

    # Buscar un DNP explícito
    print(f"\n  Muestra de status/played de todos los jugadores locales:")
    for p in home["players"]:
        ps = p.get("statistics", {})
        print(f"    player {p['personId']}: status={p.get('status','?')} "
              f"played={p.get('played','?')} "
              f"starter={p.get('starter','?')} "
              f"min={ps.get('minutesCalculated','?')}")

    mappings_player = [
        ("player_id",  "player.personId",                              "EXACTO"),
        ("team_id",    "parent team.teamId",                           "EXACTO"),
        ("is_home",    "derivado de posición home/away",               "DERIVABLE"),
        ("minutes",    "player.statistics.minutesCalculated (PTMS)",   "DERIVABLE (ISO→decimal)"),
        ("started",    "player.starter ('1'/'0')",                     "EXACTO"),
        ("fgm",        "player.statistics.fieldGoalsMade",             "EXACTO"),
        ("fga",        "player.statistics.fieldGoalsAttempted",        "EXACTO"),
        ("fg3m",       "player.statistics.threePointersMade",          "EXACTO"),
        ("fg3a",       "player.statistics.threePointersAttempted",     "EXACTO"),
        ("ftm",        "player.statistics.freeThrowsMade",             "EXACTO"),
        ("fta",        "player.statistics.freeThrowsAttempted",        "EXACTO"),
        ("oreb",       "player.statistics.reboundsOffensive",          "EXACTO"),
        ("dreb",       "player.statistics.reboundsDefensive",          "EXACTO"),
        ("ast",        "player.statistics.assists",                    "EXACTO"),
        ("stl",        "player.statistics.steals",                     "EXACTO"),
        ("blk",        "player.statistics.blocks",                     "EXACTO"),
        ("tov",        "player.statistics.turnovers",                  "EXACTO"),
        ("pf",         "player.statistics.foulsPersonal",              "EXACTO"),
        ("plus_minus", "player.statistics.plusMinusPoints",            "EXACTO"),
    ]
    print("\nPLAYER_GAME_STATS:")
    for field, path, verdict in mappings_player:
        status = "✓" if "EXACTO" in verdict or "DERIVABLE" in verdict else "✗"
        print(f"  {status} {field:<20} | {path:<50} | {verdict}")

    # ── DISPONIBILIDAD (crítico para G5) ─────────────────────────────────────
    print("\n=== DISPONIBILIDAD — LOS 3 ESTADOS (crítico para G5) ===")
    print("""
Necesitamos distinguir:
  Estado 1: JUGÓ      — tiene fila, minutes > 0
  Estado 2: DNP-banca — tiene fila, minutes = NULL (activado pero no jugó)
  Estado 3: INACTIVO  — no en el roster activo (lesionado, suspenso, etc.)

En el parser legacy (stats.nba.com / BoxScoreTraditionalV2):
  - El resultSet PlayerStats solo incluye jugadores en el roster activo
    (estados 1 y 2). Inactivos no tienen fila.
  - minutes = NULL distingue DNP del estado 1.
  - La ausencia de fila implica el estado 3.
""")

    # Contar estados en CDN
    active_count = sum(1 for p in home["players"] if p.get("status") == "ACTIVE")
    inactive_count = sum(1 for p in home["players"] if p.get("status") == "INACTIVE")
    played_count = sum(1 for p in home["players"]
                       if p.get("played") == "1" or
                          (p.get("statistics", {}).get("minutesCalculated", "") not in ("", "PT00M00.00S")))
    print(f"  Partido {sample_game_id} — equipo local ({home['teamId']}):")
    print(f"    total players en CDN : {len(home['players'])}")
    print(f"    status=ACTIVE        : {active_count}")
    print(f"    status=INACTIVE      : {inactive_count}")
    print(f"    played='1'           : {played_count}")

    # ── C. Verificación de valores ────────────────────────────────────────────
    print("\n=== C. VERIFICACIÓN DE VALORES (CDN vs SQLite) ===\n")

    for game_id in GAME_IDS:
        print(f"\n--- Partido {game_id} ---")
        sqlite_data = load_sqlite(game_id)

        if sqlite_data["game"].empty:
            print(f"  SKIP: no en SQLite")
            continue

        game_row = sqlite_data["game"].iloc[0]
        home_team_id = int(game_row["home_team_id"])

        sec(1.0)
        cdn = fetch_cdn(CDN_BOX.format(game_id=game_id))

        # Verificar game_id match
        cdn_game_id = cdn["game"].get("gameId")
        print(f"  CDN gameId: {cdn_game_id} (esperado: {game_id}) → {'OK' if cdn_game_id == game_id else 'MISMATCH!'}")

        # Team stats
        cdn_teams = parse_cdn_team(cdn, home_team_id)
        team_issues = compare_team_stats(game_id, cdn_teams, sqlite_data)
        if team_issues:
            print(f"  DISCREPANCIAS team_game_stats ({len(team_issues)}):")
            for issue in team_issues:
                print(issue)
        else:
            print(f"  team_game_stats: ✓ 0 discrepancias (16 campos × 2 equipos)")

        # Player stats
        cdn_players = parse_cdn_player(cdn, game_id, home_team_id)
        player_issues, coverage = compare_player_stats(game_id, cdn_players, sqlite_data)
        print(f"  player_game_stats cobertura:")
        print(f"    CDN total filas  : {coverage['cdn_total_rows']} "
              f"(active={coverage['cdn_active']}, inactive={coverage['cdn_inactive']})")
        print(f"    CDN played/DNP   : {coverage['cdn_played']} / {coverage['cdn_dnp']}")
        print(f"    SQLite filas     : {coverage['sqlite_rows']} "
              f"(played={coverage['sqlite_played']}, dnp={coverage['sqlite_dnp']})")

        if player_issues:
            print(f"  DISCREPANCIAS player ({len(player_issues)}):")
            for issue in player_issues[:10]:
                print(issue)
        else:
            print(f"  player_game_stats stats: ✓ 0 discrepancias en jugadores comunes")

        # Fila count mismatch analysis
        active_in_cdn = coverage['cdn_active']
        sqlite_rows = coverage['sqlite_rows']
        if active_in_cdn != sqlite_rows:
            diff = active_in_cdn - sqlite_rows
            print(f"  NOTA: CDN active={active_in_cdn} vs SQLite filas={sqlite_rows} "
                  f"(delta={diff:+d}) — "
                  f"{'CDN incluye inactivos en active' if diff > 0 else 'SQLite tiene más filas'}")

    # ── D. Schedule CDN ───────────────────────────────────────────────────────
    print("\n=== D. SCHEDULE CDN ===\n")
    print("Descargando scheduleLeagueV2_1.json (2025-26)...")
    sec(1.5)
    try:
        sched = fetch_cdn(CDN_SCHEDULE)
        fields = analyze_schedule_cdn(sched)
        print("Campos disponibles en un game del schedule CDN:")
        for k, v in fields.items():
            if k == "all_game_keys":
                print(f"  all_game_keys: {v}")
            else:
                print(f"  {k}: {v}")

        # Verificar cobertura para tabla games
        print("\nMapeo schedule CDN → tabla games:")
        games_mappings = [
            ("game_id",       "game.gameId",                "EXACTO"),
            ("season",        "leagueSchedule.seasonYear",  "DERIVABLE (formato '2025-26')"),
            ("season_type",   "game.seasonType? / gameType","INVESTIGAR"),
            ("game_date",     "game.gameDateEst",           "EXACTO (YYYY-MM-DD)"),
            ("home_team_id",  "game.homeTeam.teamId",       "EXACTO"),
            ("away_team_id",  "game.awayTeam.teamId",       "EXACTO"),
            ("home_pts",      "game.homeTeam.score",        "EXACTO (vacío si no jugado)"),
            ("away_pts",      "game.awayTeam.score",        "EXACTO (vacío si no jugado)"),
            ("home_won",      "home.score > away.score",    "DERIVABLE"),
            ("neutral_site",  "no field detectado",         "AUSENTE — solo burbuja 2020, cero en 2025-26"),
        ]
        for field, path, verdict in games_mappings:
            status = "✓" if "EXACTO" in verdict or "DERIVABLE" in verdict else "✗ o ~"
            print(f"  {status} {field:<18} | {path:<35} | {verdict}")

        # Ver un game completo
        game_dates = sched.get("leagueSchedule", {}).get("gameDates", [])
        for gd in game_dates[:30]:
            if gd.get("games"):
                sg = gd["games"][0]
                print(f"\n  Muestra de un game en schedule CDN (keys):")
                print(f"    {json.dumps({k: sg[k] for k in list(sg.keys())[:15]}, indent=4)[:800]}")
                break
    except Exception as e:
        print(f"  ERROR descargando schedule: {e}")

    # ── E. Veredicto ──────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("=== E. VEREDICTO ===")
    print("=" * 70)
    print("""
Para ver el veredicto completo después de los datos — pendiente de C y D.
(se imprime al final del script)
""")


if __name__ == "__main__":
    main()
