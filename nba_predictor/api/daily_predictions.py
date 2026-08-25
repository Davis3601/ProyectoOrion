"""
Núcleo del endpoint "predicciones del día" — 13e-2 (Decisión 13e-2.1).

Produce el payload {message, data} del día. Sin servidor web todavía:
el entrypoint FastAPI vive en api/app.py (tarea separada).

CONTRATO B-CON-DATA (Decisión 13e-2.1):
    message: texto Telegram FINAL, construido por format_daily_message() bajo
             unit tests que fijan el formato exacto. n8n transporta el mensaje
             sin tocarlo.
    data:    lista de GamePrediction para observabilidad y predictions_log.

DÍAS DEGRADADOS (Decisión 13e-2.5):
    1. Sin partidos  → games=[], mensaje de descanso (heartbeat).
    2. Feed caído    → FEED_DOWN en todos los partidos + línea de advertencia global.
    3. NYS al invocar → flag NYS por equipo afectado; otros partidos no se tocan.
    4. Fallo duro (modelo no carga / schedule inaccesible) → excepción ruidosa;
       NUNCA payload incompleto publicado en silencio.

FRONTERA token-PDF → equipo del schedule:
    La normalización simétrica de injury_report (_normalize_name) se aplica a
    AMBOS lados de la comparación. El endpoint NUNCA compara strings crudos.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nba_predictor.ingestion.future_schedule import ScheduledGame
    from nba_predictor.storage.base import DataStore

_log = logging.getLogger(__name__)

# Meses abreviados en español para el mensaje de Telegram.
_MESES_ES: dict[int, str] = {
    1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
    7: "jul", 8: "ago", 9: "sep", 10: "oct", 11: "nov", 12: "dic",
}


# ---------------------------------------------------------------------------
# Tipos públicos
# ---------------------------------------------------------------------------


class AvailabilityFlag(str, Enum):
    OK        = "ok"
    NYS       = "nys"       # Not Yet Submitted — disponibilidad sin confirmar
    FEED_DOWN = "feed_down" # Feed de injury report no disponible hoy


@dataclass
class GamePrediction:
    """Predicción para un partido individual."""

    home_tricode: str
    away_tricode: str
    game_date: str                      # YYYY-MM-DD
    probability_home: float             # P(local gana) ∈ (0, 1), redondeado a 4 decimales
    home_absences: list[str]            # nombres display de ausentes del local (solo Out)
    away_absences: list[str]            # nombres display de ausentes del visitante (solo Out)
    availability_flag: AvailabilityFlag
    model_version: str
    nys_tricodes: list[str] = field(default_factory=list)
    # Tricodes de equipos con NYS para target_date. Solo relevante cuando
    # availability_flag == NYS. Vacío para OK y FEED_DOWN.
    tip_off_cdmx: str | None = None
    # Hora del tip-off en hora del centro de México, formato "H:MM CDMX".
    # None si el schedule no provee hora (offseason, tests sin tip-off).


@dataclass
class DailyResult:
    """Resultado diario completo: predicciones + metadatos de degradación."""

    target_date: str                    # YYYY-MM-DD
    games: list[GamePrediction]
    feed_down: bool
    feed_down_reason: str | None        # descripción del error si feed_down
    model_version: str | None           # None solo si games está vacía (sin partidos)


# ---------------------------------------------------------------------------
# Función de orquestación principal
# ---------------------------------------------------------------------------


def build_daily_predictions(
    target_date: date,
    store: "DataStore",
    *,
    season: str,
    scheduled_games: "list[ScheduledGame] | None" = None,
    version_name: str | None = None,
    player_map: dict[int, str] | None = None,
    max_injury_requests: int = 20,
    save_injury_raw: bool = True,
) -> DailyResult:
    """
    Orquesta el pipeline completo para un día: schedule → ausencias → predicciones.

    Parameters
    ----------
    target_date       : Fecha a predecir.
    store             : DataStore activo (local o cloud).
    season            : Temporada CDN, ej. "2026-27".
    scheduled_games   : Lista de partidos del día (para tests o CDN override).
                        Si None, llama a fetch_future_schedule filtrado a target_date.
    version_name      : Versión del modelo a usar. None → la más reciente local.
    player_map        : dict player_id → nombre display (para mostrar ausentes).
                        Si None, los ausentes aparecen como "#player_id".
    max_injury_requests: Presupuesto HEAD de descubrimiento del PDF.
    save_injury_raw   : Persistir el PDF crudo via store.save_raw_injury_report().

    Returns
    -------
    DailyResult con la lista de GamePrediction y metadatos de degradación.

    Raises
    ------
    RuntimeError / FileNotFoundError si el schedule es inaccesible o el modelo
    no carga (fallo duro — escenario 4 de Decisión 13e-2.5).
    """
    player_map = player_map or {}

    # ── 1. Schedule ── (fallo duro si raises)
    games = _resolve_schedule(target_date, season, scheduled_games)

    if not games:
        _log.info("Sin partidos para %s — día de descanso.", target_date)
        return DailyResult(
            target_date=target_date.isoformat(),
            games=[],
            feed_down=False,
            feed_down_reason=None,
            model_version=None,
        )

    # ── 2. Teams catalog ──
    teams_df = store.load_teams()
    norm_team_map = _build_norm_team_map(teams_df)

    # ── 3. Injury report (best-effort) ──
    absences_by_tid: dict[int, list[int]] = {}
    nys_team_ids: set[int] = set()
    feed_down = False
    feed_down_reason: str | None = None

    try:
        absences_by_tid, nys_team_ids = _fetch_absences(
            target_date=target_date,
            store=store,
            norm_team_map=norm_team_map,
            player_map=player_map,
            max_requests=max_injury_requests,
            save_raw=save_injury_raw,
        )
    except Exception as exc:
        feed_down = True
        feed_down_reason = str(exc)
        _log.warning("Feed de injury report no disponible: %s", exc)

    # ── 4. Modelo (fallo duro si no carga) ──
    resolved_version = version_name or _discover_latest_version()
    pipeline, _ = store.load_model(resolved_version)

    # ── 5. Predicciones por partido ──
    game_preds: list[GamePrediction] = []
    for sg in games:
        gp = _predict_one(
            sg=sg,
            pipeline=pipeline,
            version=resolved_version,
            absences_by_tid=absences_by_tid,
            nys_team_ids=nys_team_ids,
            feed_down=feed_down,
            player_map=player_map,
        )
        game_preds.append(gp)

    return DailyResult(
        target_date=target_date.isoformat(),
        games=game_preds,
        feed_down=feed_down,
        feed_down_reason=feed_down_reason,
        model_version=resolved_version,
    )


# ---------------------------------------------------------------------------
# Formato del mensaje Telegram
# ---------------------------------------------------------------------------


_DISCLAIMER = "Predicciones estadísticas — no constituyen consejo de apuestas."
_FEED_DOWN_MSG = (
    "⚠️ Reporte de lesiones no disponible — predicciones sin ajuste de bajas de hoy."
)


def format_daily_message(result: DailyResult) -> str:
    """
    Genera el texto FINAL del canal de Telegram a partir de un DailyResult.

    Es una función pura: misma entrada → mismo texto.
    Cada rama tiene su unit test que fija el output exacto (Decisión 13e-2.1).

    Formato (auditado contra los 5 escenarios de Decisión 13e-2.5):

        🏀 Predicciones NBA · 15 oct 2026

        LAL @ BOS · 19:30 CDMX
        BOS 67% — LAL 33%
        Bajas BOS: Jaylen Brown
        Bajas LAL: –

        GSW @ MIA
        MIA 54% — GSW 46%
        Bajas MIA: –
        ⚠️ Disponibilidad GSW sin confirmar

        Predicciones estadísticas — no constituyen consejo de apuestas.
        Modelo: v1_logistic_bclean_2026-08-22

    Descanso:
        🏀 Predicciones NBA · 15 oct 2026
        Sin partidos hoy.
    """
    d = date.fromisoformat(result.target_date)
    date_str = f"{d.day} {_MESES_ES[d.month]} {d.year}"
    header = f"🏀 Predicciones NBA · {date_str}"

    if not result.games:
        return f"{header}\nSin partidos hoy."

    lines: list[str] = [header]

    if result.feed_down:
        lines.append(_FEED_DOWN_MSG)

    for gp in result.games:
        lines.append("")

        # Encabezado del partido: VISITANTE @ LOCAL [· HH:MM CDMX]
        matchup = f"{gp.away_tricode} @ {gp.home_tricode}"
        if gp.tip_off_cdmx:
            matchup += f" · {gp.tip_off_cdmx}"
        lines.append(matchup)

        home_pct = round(gp.probability_home * 100)
        away_pct = 100 - home_pct
        lines.append(f"{gp.home_tricode} {home_pct}% — {gp.away_tricode} {away_pct}%")

        if gp.availability_flag == AvailabilityFlag.FEED_DOWN:
            pass  # sin líneas de bajas — el disclaimer global ya cubre el escenario

        else:
            # Local
            if gp.home_tricode in gp.nys_tricodes:
                lines.append(f"⚠️ Disponibilidad {gp.home_tricode} sin confirmar")
            else:
                names = ", ".join(gp.home_absences) if gp.home_absences else "–"
                lines.append(f"Bajas {gp.home_tricode}: {names}")

            # Visitante
            if gp.away_tricode in gp.nys_tricodes:
                lines.append(f"⚠️ Disponibilidad {gp.away_tricode} sin confirmar")
            else:
                names = ", ".join(gp.away_absences) if gp.away_absences else "–"
                lines.append(f"Bajas {gp.away_tricode}: {names}")

    lines.append("")
    lines.append(_DISCLAIMER)
    lines.append(f"Modelo: {result.model_version}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _et_to_cdmx_str(dt_et: datetime) -> str:
    """Convierte un datetime ET (naive o aware) a 'H:MM CDMX'.

    Mexico City es permanentemente UTC-6 desde que abolió el horario de verano
    en abril 2023. El código usa zoneinfo para que el offset ET (EST/EDT) sea
    calculado correctamente por el sistema operativo.
    """
    from zoneinfo import ZoneInfo

    et_tz = ZoneInfo("America/New_York")
    cdmx_tz = ZoneInfo("America/Mexico_City")
    if dt_et.tzinfo is None:
        dt_et = dt_et.replace(tzinfo=et_tz)
    dt_cdmx = dt_et.astimezone(cdmx_tz)
    return f"{dt_cdmx.hour}:{dt_cdmx.strftime('%M')} CDMX"


def _resolve_schedule(
    target_date: date,
    season: str,
    scheduled_games: "list[ScheduledGame] | None",
) -> "list[ScheduledGame]":
    """Devuelve la lista de partidos para target_date."""
    if scheduled_games is not None:
        return scheduled_games

    from nba_predictor.ingestion.future_schedule import fetch_future_schedule
    all_future = fetch_future_schedule(season, from_date=target_date)
    return [g for g in all_future if g.game_date == target_date]


def _build_norm_team_map(teams_df: Any) -> dict[str, int]:
    """Devuelve normalized_team_name → team_id para todas las franquicias.

    Usa la misma normalización simétrica de injury_report para que los tokens
    PDF ("BostonCeltics") matcheen contra los nombres del catálogo ("Boston Celtics").
    """
    from nba_predictor.ingestion.injury_report import _normalize_name

    return {
        _normalize_name(str(row["name"])): int(row["team_id"])
        for _, row in teams_df.iterrows()
    }


def _fetch_absences(
    target_date: date,
    store: "DataStore",
    norm_team_map: dict[str, int],
    player_map: dict[int, str],
    max_requests: int,
    save_raw: bool,
) -> tuple[dict[int, list[int]], set[int]]:
    """Descarga el PDF, parsea y devuelve ausencias y equipos NYS para target_date.

    A diferencia de get_absences() (que devuelve un AbsenceResult compacto),
    esta función filtra explícitamente por target_date en ambos player_rows y
    nys_entries, de modo que un PDF multi-fecha (hoy + mañana) no marque como
    NYS un partido de hoy cuando el equipo ya entregó para hoy y solo tiene
    NYS para mañana.

    Returns:
        (absences_by_tid, nys_team_ids)
        absences_by_tid: team_id → [player_ids Out] solo para target_date.
        nys_team_ids:   team_ids con NYS para target_date.

    Raises:
        RuntimeError si discover_latest_snapshot agota su presupuesto.
        requests.HTTPError / ConnectionError si download_snapshot falla.
    """
    from nba_predictor.ingestion.injury_report import (
        InjuryStatus,
        NameIndex,
        _normalize_name,
        discover_latest_snapshot,
        download_snapshot,
        parse_pdf,
    )

    target_date_str = target_date.strftime("%Y-%m-%d")
    target_date_mdy = target_date.strftime("%m/%d/%Y")

    url, suffix = discover_latest_snapshot(target_date_str, max_requests=max_requests)
    pdf_bytes = download_snapshot(url)

    if save_raw:
        store.save_raw_injury_report(target_date_str, suffix, pdf_bytes)

    player_rows, nys_entries = parse_pdf(pdf_bytes)
    name_idx = NameIndex.from_player_map(player_map)

    absences_by_tid: dict[int, list[int]] = {}
    nys_team_ids: set[int] = set()

    # Solo Out, solo para la fecha objetivo
    for row in player_rows:
        if row.game_date != target_date_mdy:
            continue
        if row.status != InjuryStatus.OUT:
            continue
        tid = norm_team_map.get(_normalize_name(row.team))
        if tid is None:
            _log.warning("Equipo sin match en catálogo: %r", row.team)
            continue
        pid = name_idx.match(row.player_name)
        if pid is not None and pid not in absences_by_tid.get(tid, []):
            absences_by_tid.setdefault(tid, []).append(pid)

    # NYS solo para la fecha objetivo
    for entry in nys_entries:
        if entry.game_date != target_date_mdy:
            continue
        tid = norm_team_map.get(_normalize_name(entry.team))
        if tid is not None:
            nys_team_ids.add(tid)

    return absences_by_tid, nys_team_ids


def _predict_one(
    sg: "ScheduledGame",
    pipeline: Any,
    version: str,
    absences_by_tid: dict[int, list[int]],
    nys_team_ids: set[int],
    feed_down: bool,
    player_map: dict[int, str],
) -> GamePrediction:
    """Calcula features + predicción para un partido y devuelve un GamePrediction."""
    import pandas as pd

    from nba_predictor.features.live_lookup import compute_live_features
    from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS

    absent_home = absences_by_tid.get(sg.home_team_id, [])
    absent_away = absences_by_tid.get(sg.away_team_id, [])

    # Availability flag y tricodes NYS
    if feed_down:
        flag = AvailabilityFlag.FEED_DOWN
        nys_tricodes: list[str] = []
    else:
        nys_tricodes = []
        if sg.home_team_id in nys_team_ids:
            nys_tricodes.append(sg.home_tricode)
        if sg.away_team_id in nys_team_ids:
            nys_tricodes.append(sg.away_tricode)
        flag = AvailabilityFlag.NYS if nys_tricodes else AvailabilityFlag.OK

    # Features y predicción
    features = compute_live_features(
        home_team_id=sg.home_team_id,
        away_team_id=sg.away_team_id,
        game_date=sg.game_date,
        absent_home_ids=absent_home,
        absent_away_ids=absent_away,
    )

    X = pd.DataFrame([features])[OFFICIAL_LOGISTIC_COLS]
    prob = float(pipeline.predict_proba(X)[0, 1])

    home_absence_names = [player_map.get(pid, f"#{pid}") for pid in absent_home]
    away_absence_names = [player_map.get(pid, f"#{pid}") for pid in absent_away]

    tip_off_cdmx: str | None = None
    if sg.tip_off_et is not None:
        try:
            tip_off_cdmx = _et_to_cdmx_str(sg.tip_off_et)
        except Exception:
            pass  # fallo de conversión es no-fatal; el mensaje se publica sin hora

    return GamePrediction(
        home_tricode=sg.home_tricode,
        away_tricode=sg.away_tricode,
        game_date=sg.game_date.isoformat(),
        probability_home=round(prob, 4),
        home_absences=home_absence_names,
        away_absences=away_absence_names,
        availability_flag=flag,
        model_version=version,
        nys_tricodes=nys_tricodes,
        tip_off_cdmx=tip_off_cdmx,
    )


def _discover_latest_version() -> str:
    """Devuelve el nombre de la versión más reciente del registry local."""
    from nba_predictor.config import settings
    from nba_predictor.models.registry import VERSION_PREFIX

    models_dir = settings.processed_dir.parent / "models"
    if not models_dir.exists():
        raise FileNotFoundError(f"Directorio de modelos no encontrado: {models_dir}")

    versions = sorted(
        p.name for p in models_dir.glob(f"{VERSION_PREFIX}_*") if p.is_dir()
    )
    if not versions:
        raise FileNotFoundError(f"Sin versiones de modelo en {models_dir}")
    return versions[-1]
