"""
Orquestador de predicción en vivo — Fase 5a.

Flujo: lookup de features → modelo del registry → probabilidad → log.

Resolución de identidades
-------------------------
    Equipos : abreviatura (p.ej. "LAL") → team_id vía teams del DataStore.
    Jugadores: nombre parcial → player_id vía búsqueda en player_game_stats.
               La búsqueda es insensible a mayúsculas; si hay ambigüedad
               se lanza ValueError con las opciones encontradas.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Ruta canónica del log de predicciones (se crea al primer uso)
DEFAULT_LOG_PATH = Path("data/predictions_log.jsonl")


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def predict_game(
    home_abbr: str,
    away_abbr: str,
    game_date: date,
    out_names: list[str] | None = None,
    version_name: str | None = None,
    neutral_site: int = 0,
    log_path: Path | None = None,
) -> dict[str, Any]:
    """
    Predice la probabilidad de victoria del equipo local para un partido.

    Parameters
    ----------
    home_abbr   : Abreviatura del equipo local (p.ej. "BOS").
    away_abbr   : Abreviatura del equipo visitante (p.ej. "LAL").
    game_date   : Fecha del partido.
    out_names   : Nombres de jugadores ausentes (v0: manual, Decisión 2).
                  Parciales son válidos si son únicos (p.ej. "Tatum").
    version_name: Versión del registry a usar. Si None, usa la más reciente.
    neutral_site: 0/1; 1 para partidos en sede neutral.
    log_path    : Ruta del JSONL de predicciones. Default: data/predictions_log.jsonl.

    Returns
    -------
    dict con claves:
        "home_team"   : abreviatura del local
        "away_team"   : abreviatura del visitante
        "game_date"   : fecha ISO
        "probability" : P(local gana) en (0, 1)
        "features"    : dict con las 11 features calculadas
        "model_version": versión del registry usada
        "absent"      : lista de ausentes resueltos (nombres, player_ids)

    La entrada de log también se escribe en log_path (JSONL, append).
    """
    from nba_predictor.features.live_lookup import compute_live_features
    from nba_predictor.storage import get_datastore

    out_names = out_names or []
    ds = get_datastore()

    # ── Resolver equipos ──
    teams = ds.load_teams()
    home_team_id = _resolve_team(teams, home_abbr)
    away_team_id = _resolve_team(teams, away_abbr)

    # ── Resolver jugadores ausentes ──
    absent_resolved: list[dict] = []
    absent_home_ids: list[int] = []
    absent_away_ids: list[int] = []

    if out_names:
        # Cargar catálogo de jugadores (player_id → name) de la BD
        player_catalog = _build_player_catalog(ds)
        for name in out_names:
            pid, full_name, team_id = _resolve_player(player_catalog, name, home_team_id, away_team_id)
            absent_resolved.append({"name": full_name, "player_id": pid, "team_id": team_id})
            if team_id == home_team_id:
                absent_home_ids.append(pid)
            else:
                absent_away_ids.append(pid)

    # ── Calcular features ──
    features = compute_live_features(
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        game_date=game_date,
        absent_home_ids=absent_home_ids,
        absent_away_ids=absent_away_ids,
        neutral_site=neutral_site,
    )

    # ── Cargar modelo ──
    if version_name is None:
        version_name = _latest_model_version(ds)
    pipeline, metadata = ds.load_model(version_name)

    # ── Predecir ──
    import numpy as np
    import pandas as pd
    from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS

    X = pd.DataFrame([features])[OFFICIAL_LOGISTIC_COLS]
    prob = float(pipeline.predict_proba(X)[0, 1])

    # ── Construir entrada de log ──
    log_entry: dict[str, Any] = {
        "game_date": game_date.isoformat(),
        "home_team": home_abbr.upper(),
        "away_team": away_abbr.upper(),
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "probability_home_win": round(prob, 6),
        "absent": absent_resolved,
        "features": {k: (round(v, 8) if not np.isnan(v) else None) for k, v in features.items()},
        "model_version": version_name,
        "neutral_site": neutral_site,
    }

    _append_log(log_entry, log_path or DEFAULT_LOG_PATH)
    _log.info(
        f"Predicción: {home_abbr} vs {away_abbr} ({game_date.isoformat()}) → "
        f"P(local) = {prob:.1%} [modelo {version_name}]"
    )

    return {
        "home_team": home_abbr.upper(),
        "away_team": away_abbr.upper(),
        "game_date": game_date.isoformat(),
        "probability": prob,
        "features": features,
        "model_version": version_name,
        "absent": absent_resolved,
    }


# ---------------------------------------------------------------------------
# Resolución de identidades
# ---------------------------------------------------------------------------

def _resolve_team(teams: Any, abbr: str) -> int:
    """Devuelve team_id para una abreviatura. Falla ruidosamente si no existe."""
    import pandas as pd
    match = teams[teams["abbreviation"].str.upper() == abbr.upper()]
    if match.empty:
        available = sorted(teams["abbreviation"].tolist())
        raise ValueError(
            f"Equipo '{abbr}' no encontrado. Disponibles: {available}"
        )
    return int(match.iloc[0]["team_id"])


def _build_player_catalog(ds: Any) -> Any:
    """
    Construye un catálogo player_id → nombre completo desde player_game_stats.

    Usa los nombres más recientes del historial (por si un jugador cambió de
    alias entre temporadas).
    """
    import pandas as pd

    # Cargamos un sample de stats para obtener player_id y nombre
    # La BD no tiene tabla separada de jugadores, usamos player_game_stats
    # que no incluye nombre — necesitamos ir a la API o usar un workaround.
    # Workaround: consultar la tabla teams del DataStore + nba_api commonallplayers
    # Para v0, resolución simple: cargamos stats y buscamos por player_id

    # El DataStore no tiene tabla players separada; la resolución por nombre
    # requiere la API. Para v0, aceptamos player_id directamente como cadena
    # numérica además del nombre parcial.
    return None  # señal para _resolve_player de que usamos la API


def _resolve_player(
    catalog: Any,
    name_or_id: str,
    home_team_id: int,
    away_team_id: int,
) -> tuple[int, str, int]:
    """
    Resuelve nombre o ID de jugador → (player_id, full_name, team_id).

    Si name_or_id es un entero (como string), lo devuelve directamente
    asignándolo al equipo que se infiere del historial.

    Si es un nombre parcial, busca en commonallplayers de nba_api.
    Devuelve (player_id, nombre_encontrado, team_id).
    """
    # Caso 1: es un player_id numérico
    if name_or_id.strip().isdigit():
        pid = int(name_or_id.strip())
        team_id = _infer_team_for_player(pid, home_team_id, away_team_id)
        return pid, name_or_id.strip(), team_id

    # Caso 2: nombre parcial — buscar vía nba_api
    try:
        from nba_api.stats.static import players as nba_players
        all_players = nba_players.get_players()
    except Exception as exc:
        raise RuntimeError(
            f"No se pudo cargar el catálogo de jugadores: {exc}"
        ) from exc

    name_lower = name_or_id.strip().lower()
    matches = [
        p for p in all_players
        if name_lower in p["full_name"].lower()
    ]

    if not matches:
        raise ValueError(
            f"Jugador '{name_or_id}' no encontrado en el catálogo NBA. "
            f"Prueba con un nombre más largo o el player_id numérico."
        )
    if len(matches) > 1:
        options = [f"{p['full_name']} (id={p['id']})" for p in matches[:10]]
        raise ValueError(
            f"Nombre ambiguo '{name_or_id}'. Coincidencias:\n  " + "\n  ".join(options)
        )

    pid = int(matches[0]["id"])
    full_name = matches[0]["full_name"]
    team_id = _infer_team_for_player(pid, home_team_id, away_team_id)
    return pid, full_name, team_id


def _infer_team_for_player(player_id: int, home_team_id: int, away_team_id: int) -> int:
    """
    Determina si el jugador pertenece al local o visitante según su equipo más
    reciente en la BD. Si no se puede determinar, lanza ValueError.
    """
    from nba_predictor.storage import get_datastore
    import pandas as pd

    ds = get_datastore()
    # Último registro del jugador en la temporada más reciente
    pgs = ds.load_player_game_stats(player_id=player_id)
    if pgs.empty:
        raise ValueError(
            f"player_id {player_id} no tiene historial en la BD. "
            f"Verifica que el ID sea correcto o usa el nombre completo."
        )
    latest_team = int(pgs.iloc[-1]["team_id"])
    if latest_team == home_team_id:
        return home_team_id
    if latest_team == away_team_id:
        return away_team_id
    # El jugador no está en ninguno de los dos equipos de hoy (transfer reciente)
    # Asignarlo al equipo más cercano en la rotación (o lanzar error informativo)
    raise ValueError(
        f"El jugador (id={player_id}) tiene team_id={latest_team} en su último registro, "
        f"pero los equipos del partido son {home_team_id} y {away_team_id}. "
        f"Especifica el equipo explícitamente o usa la API para confirmar el transfer."
    )


def _latest_model_version(ds: Any) -> str:
    """Devuelve el nombre de la versión más reciente disponible en el registry."""
    from nba_predictor.config import settings
    from nba_predictor.models.registry import VERSION_PREFIX

    models_dir = settings.processed_dir.parent / "models"
    if not models_dir.exists():
        raise FileNotFoundError(f"Directorio de modelos no encontrado: {models_dir}")

    versions = sorted(
        p.name for p in models_dir.glob(f"{VERSION_PREFIX}_*") if p.is_dir()
    )
    if not versions:
        raise FileNotFoundError(
            f"Sin versiones de modelo en {models_dir}. "
            f"Ejecuta scripts/train_production_model.py primero."
        )
    return versions[-1]  # alfabéticamente = fecha más reciente (YYYY-MM-DD)


# ---------------------------------------------------------------------------
# Log JSONL
# ---------------------------------------------------------------------------

def _append_log(entry: dict[str, Any], log_path: Path) -> None:
    """Añade una predicción al log JSONL (una línea JSON por predicción)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
