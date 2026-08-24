"""
Cliente CDN/S3 de la NBA para boxscores y calendario.

DISEÑO DE DOBLE URL BASE CON FALLBACK
======================================
Ante el bloqueo de stats.nba.com desde IPs de datacenter (Akamai detecta AWS/GCP/Azure
y descarta conexiones silenciosamente — confirmado 3/3 intentos desde Cloud Run, Fase 5b),
la ingesta migra a dos endpoints públicos alternativos, en orden de preferencia:

1. cdn.nba.com/static/json  (PREFERENTE)
   CDN oficial de la NBA. URL semánticamente canónica. Riesgo: Akamai puede discriminar
   IPs de datacenter de la misma forma que stats.nba.com — comportamiento desde Cloud Run
   es incógnita (403 observado desde máquinas locales puede deberse a headers CDN distintos
   a los que usa Akamai en cloud). Si funciona desde Cloud Run, es la mejor opción.

2. nba-prod-us-east-1-mediaops-stats.s3.amazonaws.com/NBA  (FALLBACK)
   Bucket S3 público que es el backend real del CDN. Verificado estable en el spike
   de 2026-08-13/14: responde 200 desde cualquier IP para cualquier game_id histórico.
   Riesgo: infraestructura no documentada por la NBA; URL puede cambiar sin aviso.

Ambas URLs sirven JSON idéntico. El cliente prueba cada base con retry tenacity
(solo errores de conexión/timeout), cae a la siguiente ante 403/5xx, y loggea
qué base sirvió cada petición para facilitar el diagnóstico en Cloud Logging.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

import pandas as pd
import requests
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

_log = logging.getLogger(__name__)

# URL bases en orden de preferencia (ver docstring del módulo).
CDN_BASE_URLS: list[str] = [
    "https://cdn.nba.com/static/json",
    "https://nba-prod-us-east-1-mediaops-stats.s3.amazonaws.com/NBA",
]

# game_id de 2025-26 hardcodeado para --check-endpoints (primer partido de la temporada).
DIAGNOSTIC_GAME_ID: str = "0022500002"

# PDF de injury report histórico de fecha conocida para el tercer diagnóstico de
# --check-endpoints. Servidor: AmazonS3 (ak-static.cms.nba.com/referee/injury/),
# sin WAF desde IP local (confirmado en spike 2026-08-21). El tercer diagnóstico
# verifica si el servidor también es accesible desde IPs de datacenter Cloud Run.
INJURY_REPORT_DIAG_URL: str = (
    "https://ak-static.cms.nba.com/referee/injury/"
    "Injury-Report_2026-03-13_01_15PM.pdf"
)

# Mapeo gameType CDN → season_type canónico del proyecto.
# Valores int y string: el CDN puede enviar cualquiera de los dos.
# Fuente: convención NBA (1=Pre, 2=Regular, 3=AllStar, 4=Post, 5=PlayIn).
_GAME_TYPE_MAP: dict[Any, str] = {
    1: "Pre Season",     "1": "Pre Season",
    2: "Regular Season", "2": "Regular Season",
    3: "All Star",       "3": "All Star",
    4: "Post Season",    "4": "Post Season",
    5: "Play In",        "5": "Play In",
}

# Errores que justifican reintentar DENTRO de la misma base URL.
# HTTPError (403/5xx) NO está aquí: 403 es señal de bloqueo → caer a la siguiente base.
_RETRYABLE = (requests.exceptions.Timeout, requests.exceptions.ConnectionError)

# Regex para ISO 8601 duration: PT[M]M[S.S]S
# Cubre: PT36M20.00S (con segundos) y PT36M (sin segundos, minutesCalculated).
# NUNCA usar minutesCalculated para minutos: es redondeado al minuto.
_MIN_RE = re.compile(r"^PT(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$")


# ---------------------------------------------------------------------------
# Funciones puras — testables sin red
# ---------------------------------------------------------------------------


def parse_minutes_cdn(raw: str | None) -> float | None:
    """
    Convierte duración ISO 8601 (campo 'minutes') a minutos decimales.

    PT36M20.00S → 36.3333...  (36 min 20 seg)
    PT45M15.10S → 45.2517...
    PT00M00.00S → None  (DNP — misma semántica que None en la tabla legacy)
    PT36M       → 36.0  (minutesCalculated, redondeado — no usar para precisión)
    ''  / None  → None

    IMPORTANTE: usar siempre el campo 'minutes', no 'minutesCalculated'.
    'minutesCalculated' es redondeado al minuto; 'minutes' tiene precisión exacta
    igual al formato MM:SS del legacy.
    """
    if not raw:
        return None
    m = _MIN_RE.match(raw)
    if not m:
        return None
    mins = int(m.group(1) or 0)
    secs = float(m.group(2) or 0)
    total = mins + secs / 60.0
    return total if total > 0 else None


def _derive_team_plus_minus(points: int, points_against: int) -> int:
    """
    Deriva plus_minus de equipo como points − pointsAgainst.

    El CDN no expone PLUS_MINUS en team.statistics; pero sí expone los puntos de
    ambos equipos. Verificado contra SQLite en 3 partidos (spike 2026-08-14):
    CDN derivado == PLUS_MINUS del legacy en los 6 filas de equipo sin excepción.
    """
    return points - points_against


def _season_from_year(season_year: str) -> str:
    """
    Convierte el campo CDN 'seasonYear' al formato canónico del proyecto.
    CDN "2026-27" → "2026-27" (mismo formato — pass-through).
    """
    return season_year


def _normalize_cdn_team_stats(game: dict, game_id: str) -> pd.DataFrame:
    """
    Convierte team stats del JSON CDN al esquema canónico de team_game_stats.

    plus_minus: derivado de points − pointsAgainst (campo PLUS_MINUS ausente en
    team.statistics del CDN; derivación verificada exacta en spike).
    is_home: derivado de la posición homeTeam/awayTeam en el JSON.

    Returns exactamente 2 filas (una por equipo). Lanza ValueError si no.
    """
    home_team_id = int(game["homeTeam"]["teamId"])
    rows = []

    for team in [game["homeTeam"], game["awayTeam"]]:
        s = team["statistics"]
        team_id = int(team["teamId"])
        pts = int(s["points"])
        pts_against = int(s["pointsAgainst"])

        rows.append({
            "game_id": game_id,
            "team_id": team_id,
            "is_home": int(team_id == home_team_id),
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
            "plus_minus": _derive_team_plus_minus(pts, pts_against),
        })

    if len(rows) != 2:
        raise ValueError(
            f"game_id={game_id}: se esperaban 2 equipos en el JSON CDN, "
            f"se obtuvieron {len(rows)}"
        )

    cols = [
        "game_id", "team_id", "is_home",
        "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
        "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "plus_minus",
    ]
    return pd.DataFrame(rows)[cols].reset_index(drop=True)


def _normalize_cdn_player_stats(game: dict, game_id: str) -> pd.DataFrame:
    """
    Convierte player stats del JSON CDN al esquema canónico de player_game_stats.

    FILTRO CRÍTICO — status != 'ACTIVE':
    El CDN incluye jugadores con status='INACTIVE' (lesionados/suspendidos) que
    NO tienen fila en el legacy (BoxScoreTraditionalV2 solo trae el roster activado).
    Filtrar a ACTIVE preserva la semántica: fila = jugador activado para el partido.

    Estados en CDN (3):
      ACTIVE + played=1 + minutes≠PT00M → jugó
      ACTIVE + played=0 + minutes=PT00M → DNP (activado, no usó)
      INACTIVE                           → fuera del roster activo → se filtra

    minutes: campo 'minutes' (PT##M##.##S, precisión exacta), NO 'minutesCalculated'.
    started: player.starter == '1'.
    """
    home_team_id = int(game["homeTeam"]["teamId"])
    rows = []

    for team in [game["homeTeam"], game["awayTeam"]]:
        team_id = int(team["teamId"])
        is_home = int(team_id == home_team_id)
        for p in team["players"]:
            if p.get("status") != "ACTIVE":
                continue  # INACTIVE: lesión/suspensión — no aparece en legacy
            s = p.get("statistics", {})
            minutes = parse_minutes_cdn(s.get("minutes", ""))
            rows.append({
                "game_id": game_id,
                "player_id": int(p["personId"]),
                "team_id": team_id,
                "is_home": is_home,
                "minutes": minutes,
                "started": int(p.get("starter", "0") == "1"),
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

    cols = [
        "game_id", "player_id", "team_id", "is_home", "minutes", "started",
        "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
        "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "plus_minus",
    ]
    return pd.DataFrame(rows, columns=cols).reset_index(drop=True)


def _normalize_cdn_schedule(raw: dict) -> pd.DataFrame:
    """
    Convierte el JSON de scheduleLeagueV2 al esquema canónico de la tabla games.

    Filtrado:
    - Solo gameType == 2 (Regular Season). El CDN incluye preseason/playoffs.
    - gameStatus == 3 (Final) → scores válidos → home_pts/away_pts/home_won poblados.
    - Resto → home_pts = pd.NA, igual que en el flujo legacy (filter "completed" en el job).

    neutral_site = 0 siempre: la burbuja COVID 2020 ya está en el histórico SQLite;
    para 2026-27+ no hay sedes neutras en temporada regular. Documentado en Decisión 9.

    game_date de 'gameDateEst' (YYYY-MM-DDTHH:MM:SS) truncado a fecha.
    season de leagueSchedule.seasonYear ("2026-27").

    MAPEO de season_type: gameType int/str → _GAME_TYPE_MAP. El CDN puede enviar
    el campo como int o string; el mapa cubre ambos (ver arriba).
    """
    league = raw.get("leagueSchedule", {})
    season = _season_from_year(league.get("seasonYear", ""))
    rows = []

    for game_date_block in league.get("gameDates", []):
        for g in game_date_block.get("games", []):
            game_type = g.get("gameType")
            if game_type not in _GAME_TYPE_MAP or _GAME_TYPE_MAP[game_type] != "Regular Season":
                continue  # preseason / playoffs / all-star

            raw_date = g.get("gameDateEst") or ""
            game_date = pd.to_datetime(raw_date).date() if raw_date else None

            ht = g["homeTeam"]
            at = g["awayTeam"]

            game_status = g.get("gameStatus", 0)
            if game_status == 3:  # Final — scores válidos
                try:
                    home_pts: Any = int(ht.get("score", 0) or 0)
                    away_pts: Any = int(at.get("score", 0) or 0)
                    home_won: Any = int(home_pts > away_pts)
                except (ValueError, TypeError):
                    home_pts = pd.NA
                    away_pts = pd.NA
                    home_won = pd.NA
            else:
                home_pts = pd.NA
                away_pts = pd.NA
                home_won = pd.NA

            rows.append({
                "game_id": g["gameId"],
                "season": season,
                "season_type": "Regular Season",
                "game_date": game_date,
                "home_team_id": int(ht["teamId"]),
                "away_team_id": int(at["teamId"]),
                "home_pts": home_pts,
                "away_pts": away_pts,
                "home_won": home_won,
                "neutral_site": 0,
            })

    cols = [
        "game_id", "season", "season_type", "game_date",
        "home_team_id", "away_team_id",
        "home_pts", "away_pts", "home_won",
        "neutral_site",
    ]
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df = df.sort_values("game_date").reset_index(drop=True)
        for col in ["home_pts", "away_pts", "home_won"]:
            df[col] = df[col].astype("Int64")
    return df


# ---------------------------------------------------------------------------
# Cliente HTTP con fallback
# ---------------------------------------------------------------------------


class CDNClient:
    """
    Cliente HTTP para la capa CDN/S3 de la NBA.

    Contrato idéntico al NBAClient en cuanto al valor de retorno de fetch_boxscore
    (team_stats, player_stats, raw_payload) para intercambiabilidad en ingest_job.
    fetch_season_schedule devuelve además el raw_payload para persistirlo como crudo.

    Ver docstring del módulo para el diseño de doble URL con fallback.
    """

    def __init__(
        self,
        base_urls: list[str] | None = None,
        timeout: float = 20.0,
        request_delay: float = 0.3,
    ) -> None:
        self.base_urls = base_urls or CDN_BASE_URLS
        self.timeout = timeout
        self.request_delay = request_delay
        self._last_request_time = 0.0
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "Mozilla/5.0 (NBA-Predictor/1.0)"})

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_request_time = time.monotonic()

    def _fetch_one_base(self, base_url: str, path: str) -> dict:
        """
        Fetch desde una base URL con reintentos tenacity para errores transitorios.

        Reintenta en Timeout y ConnectionError (hasta 3 intentos, backoff exponencial).
        HTTPError (403, 5xx) NO se reintenta: 403 = bloqueo, propagar inmediatamente
        para que _fetch_with_fallback caiga a la siguiente base.
        """
        url = f"{base_url}/{path}"
        for attempt in Retrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=2, min=2, max=10),
            retry=retry_if_exception_type(_RETRYABLE),
            reraise=True,
        ):
            with attempt:
                self._throttle()
                resp = self._session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()

    def _fetch_with_fallback(self, path: str, label: str) -> tuple[dict, str]:
        """
        Intenta cada base URL en orden; loggea cuál sirvió la petición.

        Returns (data_dict, base_url_used).
        Raises RuntimeError si ninguna base sirve (fallo ruidoso — Filosofía del proyecto).
        """
        last_exc: Exception | None = None
        for base_url in self.base_urls:
            try:
                data = self._fetch_one_base(base_url, path)
                _log.info("  [%s] servido por: %s", label, base_url)
                return data, base_url
            except (
                requests.HTTPError,
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                RuntimeError,  # reraise de tenacity tras agotar reintentos
            ) as exc:
                status = getattr(getattr(exc, "response", None), "status_code", "?")
                _log.warning(
                    "  [%s] falló en %s (status=%s): %s — probando siguiente base",
                    label, base_url, status, type(exc).__name__,
                )
                last_exc = exc

        raise RuntimeError(
            f"[{label}] Todas las URLs base fallaron. Último error: {last_exc!r}. "
            f"Bases intentadas: {self.base_urls}"
        ) from last_exc

    def fetch_boxscore(self, game_id: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
        """
        Descarga y parsea el boxscore de un partido desde CDN/S3.

        Returns (team_stats, player_stats, raw_payload) — mismo contrato que
        NBAClient.fetch_boxscore para intercambiabilidad directa en ingest_job.
        raw_payload es el JSON CDN completo (estructura diferente al legacy).
        """
        path = f"liveData/boxscore/boxscore_{game_id}.json"
        raw, _ = self._fetch_with_fallback(path, f"boxscore/{game_id}")
        game = raw["game"]
        team_stats = _normalize_cdn_team_stats(game, game_id)
        player_stats = _normalize_cdn_player_stats(game, game_id)
        return team_stats, player_stats, raw

    def fetch_season_schedule(self, season: str) -> tuple[pd.DataFrame, dict]:
        """
        Descarga y parsea el calendario desde CDN/S3.

        El CDN sirve el schedule de la temporada activa (scheduleLeagueV2_1.json).
        Si la temporada solicitada no coincide con la del CDN (e.g. en transición
        de temporada), games_df tiene 0 filas — el job loggeará 0 partidos nuevos.

        Returns (games_df, raw_payload).
        games_df: columnas idénticas a la tabla games, solo Regular Season.
        raw_payload: JSON crudo para persistir en raw/schedules/ (paga la deuda
        documentada en Fase 5b Decisión 1 — schedules como raw histórico).
        """
        path = "staticData/scheduleLeagueV2_1.json"
        raw, _ = self._fetch_with_fallback(path, f"schedule/{season}")
        games_df = _normalize_cdn_schedule(raw)

        season_in_cdn = raw.get("leagueSchedule", {}).get("seasonYear", "?")
        filtered = games_df[games_df["season"] == season].copy()

        if len(filtered) == 0 and len(games_df) > 0:
            _log.warning(
                "  Schedule CDN es para temporada '%s', se solicitó '%s' — "
                "0 partidos para esa temporada. Normal en transición de temporada.",
                season_in_cdn, season,
            )
        else:
            _log.info(
                "  Schedule CDN: %d partidos de temporada regular para '%s' (CDN=%s)",
                len(filtered), season, season_in_cdn,
            )

        return filtered.reset_index(drop=True), raw

    def run_diagnostics(self, game_id: str = DIAGNOSTIC_GAME_ID) -> dict[str, dict]:
        """
        Prueba conectividad contra cada URL base configurada.

        Para cada base, intenta fetch del schedule y de un boxscore conocido;
        reporta OK/403/timeout/error con latencia en ms. No toca GCS ni BigQuery.

        Returns dict keyed by base_url:
          {"schedule": {"ok": bool, "status": int|str, "latency_ms": int},
           "boxscore": {"ok": bool, "status": int|str, "latency_ms": int}}
        """
        schedule_path = "staticData/scheduleLeagueV2_1.json"
        boxscore_path = f"liveData/boxscore/boxscore_{game_id}.json"
        results: dict[str, dict] = {}

        for base_url in self.base_urls:
            base_result: dict[str, Any] = {}
            for label, path in [("schedule", schedule_path), ("boxscore", boxscore_path)]:
                url = f"{base_url}/{path}"
                t0 = time.monotonic()
                try:
                    self._throttle()
                    resp = self._session.get(url, timeout=self.timeout)
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    if resp.ok:
                        _log.info(
                            "  DIAG [%s] %s: OK (%d) %dms",
                            base_url, label, resp.status_code, latency_ms,
                        )
                        base_result[label] = {
                            "ok": True, "status": resp.status_code, "latency_ms": latency_ms,
                        }
                    else:
                        _log.warning(
                            "  DIAG [%s] %s: FAIL (%d) %dms",
                            base_url, label, resp.status_code, latency_ms,
                        )
                        base_result[label] = {
                            "ok": False, "status": resp.status_code, "latency_ms": latency_ms,
                        }
                except requests.exceptions.Timeout:
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    _log.warning("  DIAG [%s] %s: TIMEOUT %dms", base_url, label, latency_ms)
                    base_result[label] = {
                        "ok": False, "status": "timeout", "latency_ms": latency_ms,
                    }
                except Exception as exc:
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    _log.warning(
                        "  DIAG [%s] %s: ERROR %r %dms", base_url, label, exc, latency_ms,
                    )
                    base_result[label] = {
                        "ok": False,
                        "status": f"error:{type(exc).__name__}",
                        "latency_ms": latency_ms,
                    }
            results[base_url] = base_result

        return results

    def diagnose_injury_report(self, url: str = INJURY_REPORT_DIAG_URL) -> dict:
        """
        Diagnóstico de acceso al servidor de injury reports de la NBA.

        Hace HEAD (verifica existencia + Content-Length, sin descargar el PDF)
        y GET (verifica Content-Type + magic bytes %PDF). Sin pdfplumber — solo
        requests. El servidor ak-static.cms.nba.com es independiente de
        CDN_BASE_URLS; no hay dual-URL ni fallback: es un check puntual de una
        URL conocida. No toca GCS ni BigQuery.

        Returns:
          {"url": str,
           "head": {"status": int|str, "content_length": int|None, "latency_ms": int},
           "get":  {"status": int|str, "content_type": str|None,
                    "first_bytes": str, "is_pdf": bool, "latency_ms": int},
           "accessible": bool}

        accessible=True solo si GET 200 Y primeros 4 bytes == b'%PDF'.
        HEAD 200 sin GET exitoso no cuenta: algunos servidores S3 responden
        200 a HEAD en paths inexistentes pero sirven XML de error en GET.
        """
        result: dict[str, Any] = {"url": url}

        # ── HEAD — barato: confirma existencia y Content-Length ──────────────
        t0 = time.monotonic()
        try:
            self._throttle()
            resp = self._session.head(url, timeout=self.timeout)
            head_ms = int((time.monotonic() - t0) * 1000)
            content_length: int | None = None
            raw_cl = resp.headers.get("Content-Length")
            if raw_cl is not None:
                try:
                    content_length = int(raw_cl)
                except ValueError:
                    pass
            result["head"] = {
                "status": resp.status_code,
                "content_length": content_length,
                "latency_ms": head_ms,
            }
            _log.info(
                "  DIAG-IR HEAD: status=%d  Content-Length=%s  %dms",
                resp.status_code, content_length, head_ms,
            )
        except requests.exceptions.Timeout:
            head_ms = int((time.monotonic() - t0) * 1000)
            result["head"] = {"status": "timeout", "content_length": None, "latency_ms": head_ms}
            _log.warning("  DIAG-IR HEAD: TIMEOUT %dms", head_ms)
        except Exception as exc:
            head_ms = int((time.monotonic() - t0) * 1000)
            result["head"] = {
                "status": f"error:{type(exc).__name__}", "content_length": None,
                "latency_ms": head_ms,
            }
            _log.warning("  DIAG-IR HEAD: ERROR %r %dms", exc, head_ms)

        # ── GET — verifica Content-Type y magic bytes %PDF ───────────────────
        # stream=True para leer solo los primeros bytes sin descargar el PDF.
        t0 = time.monotonic()
        try:
            self._throttle()
            resp = self._session.get(url, timeout=self.timeout, stream=True)
            get_ms = int((time.monotonic() - t0) * 1000)
            content_type = resp.headers.get("Content-Type", "")
            first_chunk = next(resp.iter_content(chunk_size=8), b"")
            resp.close()
            first_bytes_repr = repr(first_chunk[:8].decode("latin-1", errors="replace"))
            is_pdf = first_chunk[:4] == b"%PDF"
            result["get"] = {
                "status": resp.status_code,
                "content_type": content_type,
                "first_bytes": first_bytes_repr,
                "is_pdf": is_pdf,
                "latency_ms": get_ms,
            }
            _log.info(
                "  DIAG-IR GET:  status=%d  Content-Type=%s  first_bytes=%s  is_pdf=%s  %dms",
                resp.status_code, content_type, first_bytes_repr, is_pdf, get_ms,
            )
        except requests.exceptions.Timeout:
            get_ms = int((time.monotonic() - t0) * 1000)
            result["get"] = {
                "status": "timeout", "content_type": None,
                "first_bytes": "", "is_pdf": False, "latency_ms": get_ms,
            }
            _log.warning("  DIAG-IR GET:  TIMEOUT %dms", get_ms)
        except Exception as exc:
            get_ms = int((time.monotonic() - t0) * 1000)
            result["get"] = {
                "status": f"error:{type(exc).__name__}", "content_type": None,
                "first_bytes": "", "is_pdf": False, "latency_ms": get_ms,
            }
            _log.warning("  DIAG-IR GET:  ERROR %r %dms", exc, get_ms)

        get_info = result.get("get", {})
        result["accessible"] = (
            isinstance(get_info.get("status"), int)
            and get_info["status"] == 200
            and get_info.get("is_pdf", False)
        )
        return result
