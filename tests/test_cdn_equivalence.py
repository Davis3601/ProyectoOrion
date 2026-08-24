"""
Test de equivalencia CDN parser vs SQLite (oracle) — Decisión 9, Fase 5b.

pytest.mark.integration — requiere red real (S3 endpoint) y data/nba.sqlite.
Excluido por defecto de la suite normal; correr con: pytest -m integration

═══════════════════════════════════════════════════════════════════════════════
HALLAZGO CLAVE: CDN liveData ≠ stats.nba.com correcciones post-partido
═══════════════════════════════════════════════════════════════════════════════

El feed `liveData` (CDN/S3) conserva el marcador en tiempo real tal como
quedó al finalizar el partido. stats.nba.com aplica correcciones post-hoc
—típicamente reasignaciones de estadísticas individuales entre jugadores del
mismo equipo— que se propagan al SQLite vía BoxScoreTraditionalV2/V3 pero
nunca al feed CDN.

Verificación directa (2026-08-15, partido 0022500094, V3 fresco):
  - Player 1642276 / freeThrowsAttempted:  CDN=2  SQLite=3  V3=3  → V3 == SQLite ✅
  - Player 1626181 / assists:              CDN=1  SQLite=2  V3=2  → V3 == SQLite ✅
  - Player 1630558 / freeThrowsAttempted:  CDN=1  SQLite=0  V3=0  → V3 == SQLite ✅
Los tres frescos confirman la hipótesis: la NBA corrigió post-partido y el
feed liveData conserva el estado original (pre-corrección).

Firma de corrección oficial (encontrada en ~100 partidos de 2025-26):
  - 18 filas divergentes de ~32 500 comparaciones (0.06%)
  - Organizadas en 9 pares suma-cero: dentro del mismo (partido, stat),
    un jugador gana exactamente lo que otro pierde
  - Team stats y minutos: 0 discrepancias — las correcciones son solo
    de atribución individual, no del total del equipo
  - Impacto en el modelo: NULO — ninguna feature del pipeline consume
    contables individuales de jugador (fgm..pf). El availability_diff usa
    minutos, que son exactos.

Hallazgo colateral — BoxScoreTraditionalV2 DEPRECADO para 2025-26+:
  La NBA emite DeprecationWarning oficial para V2 en temporadas recientes.
  V3 (con nombres de campo distintos) es el reemplazo. La migración a CDN
  era necesaria independientemente del bloqueo de IPs de datacenter: sin
  ella, el pipeline hubiera caído al intentar ingestar 2026-27.

Limitación documentada hacia adelante:
  Partidos ingestados vía CDN no recibirán correcciones post-hoc de la NBA.
  El SQLite histórico (ingestado vía V2) sí las tiene. Para el modelo actual
  esto es irrelevante. Reevaluable si Camino 5 introdujera features que usen
  contables individuales de jugador.

═══════════════════════════════════════════════════════════════════════════════
CRITERIO DE CIERRE (Decisión 9)
═══════════════════════════════════════════════════════════════════════════════

  Lo que el pipeline CONSUME — criterio ESTRICTO (cero tolerancia):
    • team_game_stats: 13 campos contables + plus_minus derivado
    • player_game_stats: minutes (NaN-safe), started
    • Estructura: mismos jugadores (player_ids) en ambos lados

  Lo que el pipeline NO consume — correcciones oficiales clasificadas:
    • player_game_stats: fgm..pf (13 campos contables individuales)
    • Pares suma-cero → corrección oficial → reportado, no falla
    • Deltas no suma-cero → bug de parser → sí falla

NOTA sobre NaN/None en minutos de DNP:
  parse_minutes_cdn("PT00M00.00S") → None, pandas almacena como NaN.
  Falso positivo depurado en el spike de 2026-08-14: NaN is not None → True.
  Resuelto con pd.isna() en _minutes_equivalent.
"""
from __future__ import annotations

import sqlite3
import time
import warnings
from pathlib import Path

import pandas as pd
import pytest

from nba_predictor.ingestion.cdn_client import CDNClient

_DB_PATH = Path("data/nba.sqlite")
_SAMPLE_SIZE = 100
_SEASON = "2025-26"

# Team counting fields — consumidos por features a través de TEAM stats aggregate.
# plus_minus de equipo: CDN deriva como pts-ptsAgainst vs SQLite PLUS_MINUS.
_TEAM_INT_COLS = [
    "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
    "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "plus_minus",
]

# Player counting fields — NO consumidos por ninguna feature.
# Sujetos a correcciones post-partido (ver hallazgo en docstring del módulo).
_PLAYER_INT_COLS = [
    "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
    "oreb", "dreb", "ast", "stl", "blk", "tov", "pf",
]


def _load_game_ids(season: str, limit: int) -> list[str]:
    """Muestrea game_ids de una temporada del SQLite, distribuidos entre fechas."""
    conn = sqlite3.connect(_DB_PATH)
    df = pd.read_sql(
        "SELECT game_id FROM games WHERE season = ? ORDER BY game_date",
        conn,
        params=(season,),
    )
    conn.close()
    if df.empty:
        return []
    step = max(1, len(df) // limit)
    return df["game_id"].iloc[::step].tolist()[:limit]


def _load_sqlite_team_stats(conn: sqlite3.Connection, game_id: str) -> pd.DataFrame:
    return pd.read_sql(
        "SELECT * FROM team_game_stats WHERE game_id = ?", conn, params=(game_id,)
    )


def _load_sqlite_player_stats(conn: sqlite3.Connection, game_id: str) -> pd.DataFrame:
    return pd.read_sql(
        "SELECT * FROM player_game_stats WHERE game_id = ?", conn, params=(game_id,)
    )


def _minutes_equivalent(cdn_min, db_min) -> bool:
    """
    Compara minutos tratando NaN y None como equivalentes.

    Falso positivo del spike (2026-08-14): NaN is not None → True causaba
    falsos positivos para DNP con minutes=None/NaN. Resuelto con pd.isna().
    Tolerancia 0.05 min (~3 s) para diferencias de redondeo ISO 8601 vs MM:SS.
    """
    cdn_na = cdn_min is None or (isinstance(cdn_min, float) and pd.isna(cdn_min))
    db_na = db_min is None or (isinstance(db_min, float) and pd.isna(db_min))
    if cdn_na and db_na:
        return True
    if cdn_na != db_na:
        return False
    return abs(float(cdn_min) - float(db_min)) <= 0.05


@pytest.mark.integration
class TestCDNParserEquivalence:
    """
    Equivalencia CDN parser vs SQLite para ~100 partidos de 2025-26.

    Tier ESTRICTO (lo que el pipeline consume): team stats, minutes, started,
    estructura de filas. Cualquier discrepancia aquí es un bug de parser.

    Tier SOFT (no consumido por el pipeline): contables individuales fgm..pf.
    Pares suma-cero dentro del mismo (partido, stat) se clasifican como
    correcciones oficiales post-partido — reportados, no fallan.
    """

    @pytest.fixture(scope="class")
    def conn(self):
        c = sqlite3.connect(_DB_PATH)
        yield c
        c.close()

    @pytest.fixture(scope="class")
    def cdn_client(self):
        return CDNClient(request_delay=0.4)

    @pytest.fixture(scope="class")
    def game_ids(self):
        ids = _load_game_ids(_SEASON, _SAMPLE_SIZE)
        assert len(ids) > 0, f"No hay partidos de {_SEASON} en el SQLite"
        return ids

    @pytest.fixture(scope="class")
    def cdn_results(self, cdn_client, game_ids):
        """Descarga todos los boxscores CDN una sola vez para toda la clase."""
        results: dict[str, tuple] = {}
        for gid in game_ids:
            team_stats, player_stats, _ = cdn_client.fetch_boxscore(gid)
            results[gid] = (team_stats, player_stats)
            time.sleep(0.1)
        return results

    # ── TIER ESTRICTO: team stats ─────────────────────────────────────────────

    def test_team_stats_row_count(self, conn, cdn_results):
        """[ESTRICTO] CDN devuelve exactamente 2 filas de equipo por partido."""
        for gid, (cdn_teams, _) in cdn_results.items():
            assert len(cdn_teams) == 2, f"{gid}: CDN team rows = {len(cdn_teams)}"

    def test_team_stats_counting_fields_exact(self, conn, cdn_results):
        """[ESTRICTO] 13 campos contables + plus_minus de equipo coinciden exactamente."""
        discrepancies: list[str] = []

        for gid, (cdn_teams, _) in cdn_results.items():
            db_teams = _load_sqlite_team_stats(conn, gid)
            for _, cdn_row in cdn_teams.iterrows():
                tid = int(cdn_row["team_id"])
                db_row_df = db_teams[db_teams["team_id"] == tid]
                if db_row_df.empty:
                    discrepancies.append(f"{gid} team {tid}: ausente en SQLite")
                    continue
                db_row = db_row_df.iloc[0]
                for col in _TEAM_INT_COLS:
                    cdn_val = int(cdn_row[col])
                    db_val_raw = db_row[col]
                    db_val = int(float(db_val_raw)) if not pd.isna(db_val_raw) else None
                    if db_val is None:
                        continue
                    if cdn_val != db_val:
                        discrepancies.append(
                            f"{gid} team {tid}/{col}: CDN={cdn_val} SQLite={db_val}"
                        )

        assert not discrepancies, (
            f"{len(discrepancies)} discrepancias en team_game_stats:\n"
            + "\n".join(discrepancies[:20])
        )

    def test_team_stats_plus_minus_exact(self, conn, cdn_results):
        """[ESTRICTO] plus_minus derivado (pts-ptsAgainst) == SQLite PLUS_MINUS."""
        discrepancies: list[str] = []

        for gid, (cdn_teams, _) in cdn_results.items():
            db_teams = _load_sqlite_team_stats(conn, gid)
            for _, cdn_row in cdn_teams.iterrows():
                tid = int(cdn_row["team_id"])
                db_row_df = db_teams[db_teams["team_id"] == tid]
                if db_row_df.empty:
                    continue
                db_pm_raw = db_row_df.iloc[0]["plus_minus"]
                if pd.isna(db_pm_raw):
                    continue
                if int(cdn_row["plus_minus"]) != int(float(db_pm_raw)):
                    discrepancies.append(
                        f"{gid} team {tid}/plus_minus: "
                        f"CDN={cdn_row['plus_minus']} SQLite={db_pm_raw}"
                    )

        assert not discrepancies, (
            f"{len(discrepancies)} discrepancias plus_minus:\n"
            + "\n".join(discrepancies[:10])
        )

    # ── TIER ESTRICTO: player stats (consumidos por el pipeline) ─────────────

    def test_player_stats_row_structure(self, conn, cdn_results):
        """[ESTRICTO] Mismos player_ids en CDN y SQLite por partido (no solo conteo)."""
        discrepancies: list[str] = []

        for gid, (_, cdn_players) in cdn_results.items():
            db_players = _load_sqlite_player_stats(conn, gid)
            cdn_ids = set(cdn_players["player_id"].astype(int))
            db_ids = set(db_players["player_id"].astype(int))

            only_cdn = cdn_ids - db_ids
            only_db = db_ids - cdn_ids
            if only_cdn:
                discrepancies.append(f"{gid}: en CDN pero no SQLite → {sorted(only_cdn)}")
            if only_db:
                discrepancies.append(f"{gid}: en SQLite pero no CDN → {sorted(only_db)}")

        assert not discrepancies, (
            f"{len(discrepancies)} partidos con player_ids asimétricos:\n"
            + "\n".join(discrepancies[:10])
        )

    def test_player_stats_started_exact(self, conn, cdn_results):
        """[ESTRICTO] Flag started coincide exactamente (titular vs suplente)."""
        discrepancies: list[str] = []

        for gid, (_, cdn_players) in cdn_results.items():
            db_players = _load_sqlite_player_stats(conn, gid)
            for _, cdn_row in cdn_players.iterrows():
                pid = int(cdn_row["player_id"])
                db_row_df = db_players[db_players["player_id"] == pid]
                if db_row_df.empty:
                    continue
                db_val_raw = db_row_df.iloc[0]["started"]
                if pd.isna(db_val_raw):
                    continue
                cdn_started = int(cdn_row["started"])
                db_started = int(float(db_val_raw))
                if cdn_started != db_started:
                    discrepancies.append(
                        f"{gid} player {pid}/started: CDN={cdn_started} SQLite={db_started}"
                    )

        assert not discrepancies, (
            f"{len(discrepancies)} discrepancias en started:\n"
            + "\n".join(discrepancies[:20])
        )

    def test_player_stats_minutes_equivalence(self, conn, cdn_results):
        """
        [ESTRICTO] Minutos coinciden (NaN/None equivalentes para DNP).

        Tolerancia 0.05 min (~3 s) para redondeo ISO 8601 vs MM:SS.
        """
        discrepancies: list[str] = []

        for gid, (_, cdn_players) in cdn_results.items():
            db_players = _load_sqlite_player_stats(conn, gid)
            for _, cdn_row in cdn_players.iterrows():
                pid = int(cdn_row["player_id"])
                db_row_df = db_players[db_players["player_id"] == pid]
                if db_row_df.empty:
                    continue
                if not _minutes_equivalent(cdn_row["minutes"], db_row_df.iloc[0]["minutes"]):
                    discrepancies.append(
                        f"{gid} player {pid}/minutes: "
                        f"CDN={cdn_row['minutes']} SQLite={db_row_df.iloc[0]['minutes']}"
                    )

        assert not discrepancies, (
            f"{len(discrepancies)} discrepancias en minutos:\n"
            + "\n".join(discrepancies[:20])
        )

    # ── TIER SOFT: contables individuales con clasificación de correcciones ───

    def test_player_stats_counting_fields_correction_aware(self, conn, cdn_results):
        """
        [SOFT] fgm..pf: pares suma-cero se reportan como correcciones oficiales, no fallan.

        Mecanismo de detección: agrupar discrepancias por (game_id, col). Si la
        suma de deltas (CDN-SQLite) es 0 dentro del grupo, es una reasignación
        post-partido — la NBA movió crédito de un jugador a otro conservando el
        total de equipo. Estos grupos se reportan con warnings.warn (visible en
        el resumen de pytest) pero NO fallan.

        Falla únicamente si el delta neto ≠ 0 en algún grupo: eso sería un error
        de parser, no una corrección oficial (el total de equipo no cuadraría).
        """
        # Recoger discrepancias indexadas por (game_id, col)
        groups: dict[tuple[str, str], list[tuple[int, int, int]]] = {}
        # ^ clave: (game_id, col), valor: lista de (player_id, cdn_val, db_val)

        for gid, (_, cdn_players) in cdn_results.items():
            db_players = _load_sqlite_player_stats(conn, gid)
            for _, cdn_row in cdn_players.iterrows():
                pid = int(cdn_row["player_id"])
                db_row_df = db_players[db_players["player_id"] == pid]
                if db_row_df.empty:
                    continue
                db_row = db_row_df.iloc[0]
                for col in _PLAYER_INT_COLS:
                    cdn_val = int(cdn_row[col])
                    db_val_raw = db_row[col]
                    if pd.isna(db_val_raw):
                        continue
                    db_val = int(float(db_val_raw))
                    if cdn_val != db_val:
                        key = (gid, col)
                        groups.setdefault(key, []).append((pid, cdn_val, db_val))

        if not groups:
            return  # sin discrepancias — ideal

        # Clasificar en correcciones oficiales vs bugs de parser
        corrections: dict[tuple[str, str], list] = {}
        parser_bugs: list[str] = []

        for (gid, col), entries in groups.items():
            net_delta = sum(c - d for _, c, d in entries)
            if net_delta == 0:
                corrections[(gid, col)] = entries
            else:
                for pid, cdn_val, db_val in entries:
                    parser_bugs.append(
                        f"{gid} player {pid}/{col}: CDN={cdn_val} SQLite={db_val} "
                        f"(net_delta del grupo={net_delta:+d} — no es corrección oficial)"
                    )

        # Reportar correcciones oficiales como warning informativo (no falla)
        if corrections:
            total_rows = sum(len(v) for v in corrections.values())
            lines = [
                f"  {gid}/{col}: " + ", ".join(
                    f"p{pid}[CDN={c} SQLite={d}]" for pid, c, d in entries
                )
                for (gid, col), entries in sorted(corrections.items())
            ]
            warnings.warn(
                f"\n[INFO — CORRECCIONES OFICIALES NBA] "
                f"{total_rows} filas en {len(corrections)} pares suma-cero "
                f"(CDN=feed en vivo, SQLite=corregido post-partido). "
                f"Impacto en modelo: NULO (ninguna feature usa contables individuales).\n"
                + "\n".join(lines),
                UserWarning,
                stacklevel=2,
            )

        assert not parser_bugs, (
            f"{len(parser_bugs)} discrepancias con delta neto ≠ 0 "
            f"(no atribuibles a correcciones oficiales):\n"
            + "\n".join(parser_bugs[:20])
        )

    # ── Cobertura ─────────────────────────────────────────────────────────────

    def test_coverage_sample_size(self, game_ids):
        """Verifica que se analizaron suficientes partidos (no hubo skip silencioso)."""
        assert len(game_ids) >= 50, (
            f"Se esperaban ≥50 partidos de muestra, se obtuvieron {len(game_ids)}"
        )
