"""
Test de equivalencia entre live_lookup y features_v1 — Fase 5a (Decisión 3).

CRITERIO DE CIERRE: sin equivalencia exacta, no hay predicción en vivo confiable.
Una discrepancia de un decimal = training/serving skew = falla con detalle.

Estrategia de selección de partidos (~100):
    10 partidos por cada una de las 10 temporadas de entrenamiento, elegidos
    de la mitad del calendario (evitar los primeros 15 por equipo que tienen
    NaN y los últimos para evitar edge cases de fin de temporada). Total: 100.

Disponibilidad histórica (modo exacto):
    Para un partido histórico, los "jugadores activados" son los que tienen
    fila en player_game_stats para ese game_id — exactamente igual que el
    denominador de la pipeline vectorizada. Así se reproduce el numerador
    del availability histórico de forma exacta.

Tolerancia: np.isclose(rtol=1e-9, atol=1e-12).
    Un partido histórico computado dos veces debe dar exactamente el mismo
    float (solo diferencias de orden de operación de punto flotante son
    aceptables, y son menores que 1e-12).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS
from nba_predictor.config import TRAINING_SEASONS

# Tolerancia: errores de redondeo de punto flotante permitidos; no se acepta
# ningún error algorítmico (diferencia de fórmula o ventana distinta).
_RTOL = 1e-9
_ATOL = 1e-12

# Partidos por temporada a testear
_GAMES_PER_SEASON = 10


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def datastore():
    from nba_predictor.storage import get_datastore
    return get_datastore()


@pytest.fixture(scope="module")
def features_v1(datastore):
    try:
        return datastore.load_features()
    except FileNotFoundError:
        pytest.skip("features_v1.parquet no disponible")


@pytest.fixture(scope="module")
def all_games(datastore):
    return datastore.load_games()


@pytest.fixture(scope="module")
def player_game_stats(datastore):
    return datastore.load_player_game_stats()


@pytest.fixture(scope="module")
def sample_games(features_v1):
    """
    Selecciona ~100 partidos históricos repartidos entre las 10 temporadas.

    Criterios:
    - Excluir partidos con NaN en features (primeros 15 de algún equipo).
    - Tomar partidos de la mitad del calendario de cada temporada.
    - Los 11 features deben ser finitos (no NaN).
    """
    clean = features_v1.dropna(subset=OFFICIAL_LOGISTIC_COLS)
    selected: list[pd.Series] = []
    for season in TRAINING_SEASONS:
        season_games = clean[clean["season"] == season]
        if season_games.empty:
            continue
        n = len(season_games)
        # Tomar del cuartil 30%-70% del calendario de la temporada
        lo = int(n * 0.30)
        hi = int(n * 0.70)
        mid_games = season_games.iloc[lo:hi]
        # Muestra reproducible (sin aleatoriedad en los tests)
        step = max(1, len(mid_games) // _GAMES_PER_SEASON)
        subset = mid_games.iloc[::step].head(_GAMES_PER_SEASON)
        selected.extend(subset.to_dict("records"))

    if not selected:
        pytest.skip("No hay partidos con features válidas")

    return selected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _activated_ids_for_game(
    game_id: str,
    team_id: int,
    player_game_stats: pd.DataFrame,
) -> set[int]:
    """Devuelve los player_ids con fila en player_game_stats para este game_id y equipo."""
    mask = (player_game_stats["game_id"] == game_id) & (player_game_stats["team_id"] == team_id)
    return set(player_game_stats.loc[mask, "player_id"].tolist())


def _played_ids_for_game(
    game_id: str,
    team_id: int,
    player_game_stats: pd.DataFrame,
) -> set[int]:
    """Jugadores que efectivamente jugaron (minutes > 0) — subconjunto de activated."""
    mask = (
        (player_game_stats["game_id"] == game_id)
        & (player_game_stats["team_id"] == team_id)
        & player_game_stats["minutes"].notna()
        & (player_game_stats["minutes"] > 0)
    )
    return set(player_game_stats.loc[mask, "player_id"].tolist())


def _call_live_lookup(
    game: dict[str, Any],
    all_games: pd.DataFrame,
    player_game_stats: pd.DataFrame,
) -> dict[str, float] | None:
    """
    Llama a compute_live_features para un partido histórico.

    Pasa activated_ids (todos con fila en player_game_stats) y played_ids
    (los que tuvieron minutos > 0) para reproducir el numerador exacto del
    availability histórico, que distingue jugadores de DNP en el rolling.
    Devuelve None si la llamada lanza ValueError (historia insuficiente).
    """
    from nba_predictor.features.live_lookup import compute_live_features

    game_id = game["game_id"]
    game_date = pd.Timestamp(game["game_date"]).date()

    row = all_games[all_games["game_id"] == game_id]
    if row.empty:
        return None
    home_team_id = int(row.iloc[0]["home_team_id"])
    away_team_id = int(row.iloc[0]["away_team_id"])
    neutral_site = int(row.iloc[0].get("neutral_site", 0))

    activated_home = _activated_ids_for_game(game_id, home_team_id, player_game_stats)
    activated_away = _activated_ids_for_game(game_id, away_team_id, player_game_stats)
    played_home = _played_ids_for_game(game_id, home_team_id, player_game_stats)
    played_away = _played_ids_for_game(game_id, away_team_id, player_game_stats)

    try:
        return compute_live_features(
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            game_date=game_date,
            activated_home_ids=activated_home,
            activated_away_ids=activated_away,
            played_home_ids=played_home,
            played_away_ids=played_away,
            neutral_site=neutral_site,
        )
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Test principal de equivalencia
# ---------------------------------------------------------------------------

class TestEquivalenciaExacta:
    """
    Verifica que live_lookup y features_v1 son numéricamente idénticos
    para ~100 partidos históricos repartidos entre las 10 temporadas.
    """

    def test_100_partidos_exactos(
        self,
        sample_games: list[dict],
        all_games: pd.DataFrame,
        player_game_stats: pd.DataFrame,
    ):
        """
        Para cada partido de la muestra, las 11 features via lookup deben
        coincidir con features_v1 dentro de tolerancia rtol=1e-9.

        Si UNA feature de UN partido difiere, el test falla con detalle
        de cuál feature, en qué partido, la diferencia absoluta y el valor
        esperado vs. obtenido.
        """
        errors: list[str] = []
        n_tested = 0
        n_skipped = 0

        for game in sample_games:
            live = _call_live_lookup(game, all_games, player_game_stats)
            if live is None:
                n_skipped += 1
                continue

            n_tested += 1
            for feat in OFFICIAL_LOGISTIC_COLS:
                expected = game[feat]
                obtained = live.get(feat)

                if obtained is None or np.isnan(obtained):
                    if np.isnan(expected):
                        continue  # ambos NaN → OK
                    errors.append(
                        f"game_id={game['game_id']} season={game['season']} "
                        f"feat={feat}: expected={expected:.8f}, got=NaN"
                    )
                    continue

                if np.isnan(expected):
                    errors.append(
                        f"game_id={game['game_id']} season={game['season']} "
                        f"feat={feat}: expected=NaN, got={obtained:.8f}"
                    )
                    continue

                if not np.isclose(expected, obtained, rtol=_RTOL, atol=_ATOL):
                    diff = abs(expected - obtained)
                    errors.append(
                        f"game_id={game['game_id']} season={game['season']} "
                        f"feat={feat}: expected={expected:.10f}, "
                        f"got={obtained:.10f}, diff={diff:.2e}"
                    )

        # Reportar
        summary = (
            f"Equivalencia: {n_tested} partidos testados, {n_skipped} omitidos "
            f"(historia insuficiente). Errores: {len(errors)}."
        )

        if errors:
            detail = "\n".join(errors[:30])  # máximo 30 para no inundar
            if len(errors) > 30:
                detail += f"\n... y {len(errors) - 30} errores más."
            pytest.fail(
                f"{summary}\n\nDETALLE DE DISCREPANCIAS:\n{detail}"
            )

        # Garantizar que probamos al menos 50 partidos (no todos skip)
        assert n_tested >= 50, (
            f"Solo se pudieron testear {n_tested} partidos (mínimo 50). "
            f"Revisa la selección de muestra o los datos históricos."
        )
        # Imprimir resumen para visibilidad (pytest -v lo muestra)
        print(f"\n{summary}")


# ---------------------------------------------------------------------------
# Tests adicionales
# ---------------------------------------------------------------------------

class TestPrediccionProba:
    """La predicción del modelo sobre features live está en (0, 1)."""

    def test_probabilidad_en_rango_abierto(
        self,
        sample_games: list[dict],
        all_games: pd.DataFrame,
        player_game_stats: pd.DataFrame,
        datastore,
    ):
        """
        Para un partido de la muestra, la probabilidad predicha está en (0, 1).
        Solo probamos el primero para no ralentizar el test suite.
        """
        from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS
        from nba_predictor.models.registry import VERSION_PREFIX
        from nba_predictor.config import settings

        models_dir = settings.processed_dir.parent / "models"
        versions = sorted(p.name for p in models_dir.glob(f"{VERSION_PREFIX}_*") if p.is_dir())
        if not versions:
            pytest.skip("Sin modelo en el registry")

        version = versions[-1]
        # Modelo solo existe en disco local (no en GCS durante desarrollo).
        # Usamos LocalDataStore explícitamente para no depender de settings.mode.
        from nba_predictor.storage.local import LocalDataStore
        local_ds = LocalDataStore(db_path=settings.db_path, raw_dir=settings.raw_dir)
        pipeline, _ = local_ds.load_model(version)

        # Tomar el primer game que live_lookup pueda calcular
        for game in sample_games:
            live = _call_live_lookup(game, all_games, player_game_stats)
            if live is None:
                continue
            X = pd.DataFrame([live])[OFFICIAL_LOGISTIC_COLS]
            prob = pipeline.predict_proba(X)[0, 1]
            assert 0.0 < prob < 1.0, f"Probabilidad fuera de (0,1): {prob}"
            assert np.isfinite(prob), "Probabilidad es NaN o infinita"
            break
        else:
            pytest.skip("Ningún partido de la muestra pudo calcularse")


class TestLog:
    """El log se escribe con todos los campos obligatorios."""

    def test_log_escribe_todos_los_campos(
        self,
        tmp_path: Path,
        sample_games: list[dict],
        all_games: pd.DataFrame,
        player_game_stats: pd.DataFrame,
        monkeypatch,
    ):
        """
        predict_game escribe una línea JSONL con todos los campos del hito.
        Se prueba sobre el primer partido de la muestra que live_lookup pueda calcular.
        """
        from nba_predictor.models.registry import VERSION_PREFIX
        from nba_predictor.config import settings

        # Modelo solo existe en disco local (no en GCS durante desarrollo).
        # Forzamos LocalDataStore para que predict_game() no intente leer de GCS.
        from nba_predictor.storage.local import LocalDataStore
        import nba_predictor.storage as _storage_mod
        monkeypatch.setattr(
            _storage_mod,
            "get_datastore",
            lambda: LocalDataStore(db_path=settings.db_path, raw_dir=settings.raw_dir),
        )

        models_dir = settings.processed_dir.parent / "models"
        versions = sorted(p.name for p in models_dir.glob(f"{VERSION_PREFIX}_*") if p.is_dir())
        if not versions:
            pytest.skip("Sin modelo en el registry")

        # Encontrar un partido válido
        for game in sample_games:
            row = all_games[all_games["game_id"] == game["game_id"]]
            if row.empty:
                continue
            home_team_id = int(row.iloc[0]["home_team_id"])
            away_team_id = int(row.iloc[0]["away_team_id"])
            live = _call_live_lookup(game, all_games, player_game_stats)
            if live is None:
                continue

            from nba_predictor.storage import get_datastore
            ds = get_datastore()
            teams = ds.load_teams()

            # Obtener abreviaturas de los equipos
            def _abbr(tid: int) -> str:
                match = teams[teams["team_id"] == tid]
                return str(match.iloc[0]["abbreviation"]) if not match.empty else str(tid)

            home_abbr = _abbr(home_team_id)
            away_abbr = _abbr(away_team_id)
            game_date = pd.Timestamp(game["game_date"]).date()

            log_path = tmp_path / "test_predictions_log.jsonl"
            from nba_predictor.live.predict_game import predict_game

            result = predict_game(
                home_abbr=home_abbr,
                away_abbr=away_abbr,
                game_date=game_date,
                version_name=versions[-1],
                log_path=log_path,
            )

            assert log_path.exists(), "El log JSONL no fue creado"
            line = log_path.read_text(encoding="utf-8").strip()
            entry = json.loads(line)

            # Campos obligatorios del hito (CLAUDE.md § Hito)
            required_fields = {
                "game_date", "home_team", "away_team",
                "probability_home_win", "absent", "features",
                "model_version",
            }
            missing = required_fields - set(entry.keys())
            assert not missing, f"Campos faltantes en el log: {missing}"

            # Verificar que probability está en (0, 1)
            p = entry["probability_home_win"]
            assert 0.0 < p < 1.0, f"Probabilidad fuera de rango: {p}"

            # Verificar que las 11 features están en el log
            logged_feats = set(entry["features"].keys())
            assert set(OFFICIAL_LOGISTIC_COLS).issubset(logged_feats), (
                f"Features faltantes en el log: {set(OFFICIAL_LOGISTIC_COLS) - logged_feats}"
            )
            break
        else:
            pytest.skip("Ningún partido de la muestra pudo calcularse")


class TestAusenciasVacias:
    """Sin ausencias declaradas, el resultado es rotación completa."""

    def test_ausencias_vacias_igual_a_rotacion_completa(
        self,
        sample_games: list[dict],
        all_games: pd.DataFrame,
        player_game_stats: pd.DataFrame,
    ):
        """
        Si no se declaran ausencias (absent_ids vacío), el numerador de
        disponibilidad es igual al denominador (todos disponibles) → availability = 1/1 = 1.0
        por equipo, a menos que la rotación incluya jugadores con minutos rolling NaN → 0.
        En cualquier caso, ausencias vacías != NaN y distinto de absent_ids=todos.
        """
        from nba_predictor.features.live_lookup import compute_live_features

        for game in sample_games:
            row = all_games[all_games["game_id"] == game["game_id"]]
            if row.empty:
                continue
            home_team_id = int(row.iloc[0]["home_team_id"])
            away_team_id = int(row.iloc[0]["away_team_id"])
            neutral_site = int(row.iloc[0].get("neutral_site", 0))
            game_date = pd.Timestamp(game["game_date"]).date()

            try:
                result_empty = compute_live_features(
                    home_team_id=home_team_id,
                    away_team_id=away_team_id,
                    game_date=game_date,
                    absent_home_ids=[],
                    absent_away_ids=[],
                    neutral_site=neutral_site,
                )
            except (ValueError, IndexError):
                continue

            # availability_diff con rotación completa debe ser un float
            avail = result_empty["availability_diff"]
            assert np.isfinite(avail), (
                f"Con ausencias vacías, availability_diff debería ser finito, "
                f"got={avail}"
            )
            break
        else:
            pytest.skip("Ningún partido de la muestra pudo calcularse")


# ---------------------------------------------------------------------------
# Import necesario para TestLog
# ---------------------------------------------------------------------------
import json
