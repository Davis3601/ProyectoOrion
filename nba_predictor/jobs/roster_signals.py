"""Señales de plantilla (D-PRE-8): "visto con este equipo", sin números.

Dos fuentes que el sistema YA ve y que antes desechaba:
  - boxscores de PRETEMPORADA (prefijo 001): un jugador activado con un equipo
    en octubre pertenece a ese equipo antes de su primer partido oficial;
  - el injury report: el PDF lista a cada jugador bajo su equipo actual, y es
    más completo que la página oficial de movimientos (caso Devin Carter,
    2026-10-20: waivers y Exhibit 10 no aparecen allí; en el PDF sí).

FRONTERA DURA: una señal da EQUIPO, jamás números. Nada de este módulo escribe
en games, team_game_stats ni player_game_stats, ni toca las features: la
pretemporada es otro proceso generador (D-ODDS-6) y el modelo no la ve. El
único consumidor es la selección de destacados del mensaje 2.

Best-effort en todo: la misión crítica del job es la ingesta de boxscores
oficiales (misma regla que el archivo del PDF, Decisión 4 del feed). Un fallo
aquí es un WARNING, nunca un exit != 0.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import pandas as pd

from nba_predictor.storage.base import ROSTER_SIGNAL_COLUMNS

_log = logging.getLogger(__name__)

SOURCE_PRESEASON = "preseason"
SOURCE_INJURY_REPORT = "injury_report"

# Prefijo de gameId de la pretemporada en scheduleLeagueV2 (002 = regular).
PRESEASON_PREFIX = "001"


def season_window(season: str) -> tuple[date, date]:
    """[1-jul, 30-jun] de una temporada '2026-27'.

    Frontera en julio y no en octubre: la pretemporada puede empezar a finales
    de septiembre, y en julio no hay partidos ni PDFs que caigan del lado
    equivocado.
    """
    inicio = int(season[:4])
    return date(inicio, 7, 1), date(inicio + 1, 6, 30)


def preseason_played_games(raw_schedule: dict) -> list[dict[str, Any]]:
    """Partidos 001 TERMINADOS (gameStatus 3) del scheduleLeagueV2 crudo.

    TERCER lector del scheduleLeagueV2, declarado: future_schedule y el
    normalizador de cdn_client filtran por prefijo 002 y descartan la
    pretemporada por diseño, así que ninguno de los dos puede servir aquí. Se
    lee lo mínimo (gameId, gameDateEst, gameStatus) por prefijo de gameId, el
    vocabulario que el payload SÍ trae (lección de la capa 4: gameType no
    existe en este documento). gameDateEst solo vale por la fecha.
    """
    out: list[dict[str, Any]] = []
    for bloque in raw_schedule.get("leagueSchedule", {}).get("gameDates", []):
        for g in bloque.get("games", []):
            gid = str(g.get("gameId", ""))
            if not gid.startswith(PRESEASON_PREFIX) or g.get("gameStatus") != 3:
                continue
            out.append({"game_id": gid, "game_date": str(g.get("gameDateEst", ""))[:10]})
    return sorted(out, key=lambda x: (x["game_date"], x["game_id"]))


def signals_from_preseason_box(
    player_stats: pd.DataFrame,
    *,
    game_id: str,
    game_date: str,
    observed_at: datetime,
) -> pd.DataFrame:
    """Una señal por jugador ACTIVADO en el boxscore 001.

    player_stats es la salida de _normalize_cdn_player_stats (Decisión 9), que
    ya filtra status != ACTIVE: fila = activado con ese equipo, también con 0
    minutos (caso Ingram, LAC, 2026-10-14). Solo se toman player_id y team_id;
    los números del boxscore se descartan aquí, por construcción.
    """
    if player_stats.empty:
        return pd.DataFrame(columns=list(ROSTER_SIGNAL_COLUMNS))
    df = player_stats[["player_id", "team_id"]].drop_duplicates("player_id").copy()
    df["source"] = SOURCE_PRESEASON
    df["ref_id"] = game_id
    df["game_date"] = date.fromisoformat(game_date)
    df["observed_at"] = observed_at
    return df[list(ROSTER_SIGNAL_COLUMNS)].reset_index(drop=True)


def signals_from_injury_pdf(
    pdf_bytes: bytes,
    *,
    date_str: str,
    suffix: str,
    player_map: dict[int, str],
    norm_team_map: dict[str, int],
    observed_at: datetime,
) -> pd.DataFrame:
    """Una señal por jugador del PDF con match de player_id y equipo resuelto.

    ref_id = nombre del snapshot ({fecha}_{sufijo}); game_date = fecha del PDF
    (la del corte, pre-registro de D-PRE-8), no la del partido de cada fila: el
    PDF multi-fecha lista también los de mañana, y el equipo vale igual.

    Mismas piezas que el endpoint, sin una segunda verdad: parse_pdf, la guarda
    de cobertura de D-PRE-3 (si el parser lee mucho menos de lo que el
    documento contiene, NO se emiten señales: equipos de un parseo roto serían
    peores que ninguno), NameIndex sobre el catálogo players y resolve_team_id
    (odds_logic.normalize_team, D-PRE-4).
    """
    from nba_predictor.api.daily_predictions import resolve_team_id
    from nba_predictor.ingestion.injury_report import (
        NameIndex,
        check_parse_coverage,
        count_name_lines,
        parse_pdf,
    )

    player_rows, _ = parse_pdf(pdf_bytes)
    check_parse_coverage(len(player_rows), count_name_lines(pdf_bytes))
    idx = NameIndex.from_player_map(player_map)

    filas: dict[int, dict[str, Any]] = {}
    for row in player_rows:
        tid = resolve_team_id(row.team, norm_team_map)
        pid = idx.match(row.player_name)
        if tid is None or pid is None:
            continue
        # Un jugador aparece dos veces en un PDF multi-fecha (hoy y mañana);
        # la clave (player_id, source, ref_id) admite una sola fila.
        filas[pid] = {
            "player_id": pid,
            "team_id": tid,
            "source": SOURCE_INJURY_REPORT,
            "ref_id": f"{date_str}_{suffix}",
            "game_date": date.fromisoformat(date_str),
            "observed_at": observed_at,
        }
    return pd.DataFrame(list(filas.values()), columns=list(ROSTER_SIGNAL_COLUMNS))


def roster_signals_step(
    ds: Any,
    client: Any,
    raw_schedule: dict | None,
    archived_pdfs: list[tuple[str, str, bytes]],
    *,
    observed_at: datetime,
    player_map: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Paso 1b del job: señales de plantilla. BEST-EFFORT en cada mitad.

    (a) Partidos 001 terminados que aún no tienen señales → GET boxscore →
        RAW a raw/boxscores_preseason/ → señales source='preseason'. De paso,
        sus nombres al catálogo players (los novatos aún sin partido oficial).
    (b) PDFs archivados en esta corrida → señales source='injury_report'.

    Un partido que falla no detiene a los demás; una mitad que falla no
    detiene a la otra. Devuelve un resumen para el log, nunca lanza.
    """
    from nba_predictor.jobs.ingest_logic import (
        _collect_player_names,
        _persist_player_names,
        _season_from_raw_schedule,
    )

    resumen: dict[str, Any] = {
        "preseason_terminados": 0, "preseason_nuevos": 0, "preseason_fallidos": 0,
        "senales_preseason": 0, "pdfs": 0, "senales_injury_report": 0,
    }
    if observed_at.tzinfo is None:
        raise ValueError("observed_at debe llevar zona horaria (aware).")

    # ── (a) pretemporada ──
    try:
        if raw_schedule is None:
            raise RuntimeError("sin schedule crudo en esta corrida")
        season = _season_from_raw_schedule(raw_schedule)
        if season is None:
            raise RuntimeError("schedule sin seasonYear")
        jugados = preseason_played_games(raw_schedule)
        resumen["preseason_terminados"] = len(jugados)
        inicio, fin = season_window(season)
        previas = ds.load_roster_signals(start_date=inicio, end_date=fin)
        ya = set(previas.loc[previas["source"] == SOURCE_PRESEASON, "ref_id"])
        pendientes = [g for g in jugados if g["game_id"] not in ya]
        resumen["preseason_nuevos"] = len(pendientes)

        trozos: list[pd.DataFrame] = []
        payloads: list[dict] = []
        for g in pendientes:
            try:
                _, player_stats, raw = client.fetch_boxscore(g["game_id"])
                ds.save_raw_preseason_boxscore(g["game_id"], raw)
                trozos.append(signals_from_preseason_box(
                    player_stats, game_id=g["game_id"], game_date=g["game_date"],
                    observed_at=observed_at,
                ))
                payloads.append(raw)
            except Exception as exc:  # noqa: BLE001 — un partido no tumba al resto
                resumen["preseason_fallidos"] += 1
                _log.warning("  Señales de pretemporada: %s falló: %s", g["game_id"], exc)
        if trozos:
            nuevas = pd.concat(trozos, ignore_index=True)
            ds.save_roster_signals(nuevas)
            resumen["senales_preseason"] = len(nuevas)
            _persist_player_names(ds, _collect_player_names(payloads), _log.info)
    except Exception as exc:  # noqa: BLE001
        _log.warning("  Señales de pretemporada NO actualizadas: %s", exc)

    # ── (b) injury report ──
    try:
        if archived_pdfs:
            from nba_predictor.api.daily_predictions import _build_norm_team_map

            nombres = player_map if player_map is not None else ds.load_player_names()
            if not nombres:
                raise RuntimeError("catálogo de nombres vacío: ningún jugador resolvería")
            norm_team_map = _build_norm_team_map(ds.load_teams())
            trozos = []
            for date_str, suffix, pdf_bytes in archived_pdfs:
                try:
                    trozos.append(signals_from_injury_pdf(
                        pdf_bytes, date_str=date_str, suffix=suffix, player_map=nombres,
                        norm_team_map=norm_team_map, observed_at=observed_at,
                    ))
                    resumen["pdfs"] += 1
                except Exception as exc:  # noqa: BLE001
                    _log.warning("  Señales del PDF %s_%s NO extraídas: %s",
                                 date_str, suffix, exc)
            trozos = [t for t in trozos if not t.empty]
            if trozos:
                nuevas = pd.concat(trozos, ignore_index=True)
                ds.save_roster_signals(nuevas)
                resumen["senales_injury_report"] = len(nuevas)
    except Exception as exc:  # noqa: BLE001
        _log.warning("  Señales del injury report NO actualizadas: %s", exc)

    return resumen
