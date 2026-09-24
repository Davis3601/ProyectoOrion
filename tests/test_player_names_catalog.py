"""Catálogo de nombres de jugador y su regla de degradación (D-PROD-1d).

El bug 2 de D-PROD-1c: server.py nunca pasaba player_map, el NameIndex nacía
vacío, NINGUNA ausencia hacía match y el resultado tenía el aspecto sano de un
día sin bajas. Estos tests cubren el catálogo nuevo y, sobre todo, la guarda
para que ese silencio no pueda repetirse.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from nba_predictor.ingestion.injury_report import (
    load_player_names_from_cdn_json,
    load_player_names_from_raw_json,
    player_names_from_cdn_payload,
    player_names_from_legacy_payload,
)
from nba_predictor.jobs.ingest_logic import _collect_player_names, _persist_player_names
from nba_predictor.storage.local import LocalDataStore

# --------------------------------------------------------------------------
# Extractores por payload — la fuente ÚNICA que todos reusan
# --------------------------------------------------------------------------


LEGACY = {
    "resultSets": [{
        "headers": ["GAME_ID", "PLAYER_ID", "PLAYER_NAME", "MIN"],
        "rowSet": [
            ["0022300001", 1628369, "Jayson Tatum", "34:12"],
            ["0022300001", 201939, "Stephen Curry", "31:05"],
        ],
    }]
}

CDN = {
    "game": {
        "homeTeam": {"players": [{"personId": 1628369, "name": "Jayson Tatum"}]},
        "awayTeam": {"players": [{"personId": 201939, "name": "Stephen Curry"}]},
    }
}


def test_extractor_legacy():
    assert player_names_from_legacy_payload(LEGACY) == {
        1628369: "Jayson Tatum", 201939: "Stephen Curry"}


def test_extractor_cdn():
    assert player_names_from_cdn_payload(CDN) == {
        1628369: "Jayson Tatum", 201939: "Stephen Curry"}


def test_extractor_legacy_sin_las_columnas_devuelve_vacio():
    assert player_names_from_legacy_payload({"resultSets": [{"headers": ["X"], "rowSet": [[1]]}]}) == {}


def test_extractor_cdn_payload_ajeno_devuelve_vacio():
    assert player_names_from_cdn_payload({"otra": "cosa"}) == {}


def test_extractores_ignoran_ids_o_nombres_vacios():
    payload = {"game": {"homeTeam": {"players": [
        {"personId": None, "name": "Fantasma"}, {"personId": 7, "name": ""}]}}}
    assert player_names_from_cdn_payload(payload) == {}


def test_los_loaders_de_directorio_usan_los_extractores(tmp_path):
    """Una sola implementación: si divergieran habría dos verdades del nombre."""
    (tmp_path / "0022300001.json").write_text(json.dumps(LEGACY), encoding="utf-8")
    assert load_player_names_from_raw_json(tmp_path) == player_names_from_legacy_payload(LEGACY)

    live = tmp_path / "live"
    live.mkdir()
    (live / "0022600001.json").write_text(json.dumps(CDN), encoding="utf-8")
    assert load_player_names_from_cdn_json(live) == player_names_from_cdn_payload(CDN)


# --------------------------------------------------------------------------
# Adapter local
# --------------------------------------------------------------------------


@pytest.fixture()
def store_local(tmp_path) -> LocalDataStore:
    return LocalDataStore(db_path=tmp_path / "t.sqlite", raw_dir=tmp_path / "raw",
                          processed_dir=tmp_path / "proc")


def test_local_lee_nombres_de_los_json_crudos(store_local):
    (store_local.raw_dir / "0022300001.json").write_text(json.dumps(LEGACY), encoding="utf-8")
    assert store_local.load_player_names() == {
        1628369: "Jayson Tatum", 201939: "Stephen Curry"}


def test_local_une_legacy_y_cdn_con_el_cdn_ganando(store_local):
    """El nombre más reciente gana: el CDN se aplica al final."""
    (store_local.raw_dir / "0022300001.json").write_text(json.dumps(LEGACY), encoding="utf-8")
    live = store_local.raw_dir / "boxscores_live"
    live.mkdir()
    (live / "0022600001.json").write_text(json.dumps({"game": {"homeTeam": {"players": [
        {"personId": 1628369, "name": "Jayson Tatum Jr."}]}}}), encoding="utf-8")
    assert store_local.load_player_names()[1628369] == "Jayson Tatum Jr."


def test_local_sin_json_devuelve_vacio(store_local):
    assert store_local.load_player_names() == {}


def test_local_save_es_no_op_documentado(store_local):
    """En local los JSON SON el catálogo; una copia podría divergir."""
    assert store_local.save_player_names({1: "X"}) is None
    assert store_local.load_player_names() == {}


# --------------------------------------------------------------------------
# Mantenimiento incremental del ingest job
# --------------------------------------------------------------------------


def test_collect_player_names_une_varios_payloads():
    otro = {"game": {"homeTeam": {"players": [{"personId": 2544, "name": "LeBron James"}]}}}
    assert _collect_player_names([CDN, otro]) == {
        1628369: "Jayson Tatum", 201939: "Stephen Curry", 2544: "LeBron James"}


def test_collect_player_names_salta_payloads_rotos():
    """Un payload raro no puede tumbar el catálogo entero."""
    assert _collect_player_names([None, CDN]) == player_names_from_cdn_payload(CDN)


def test_collect_player_names_sin_payloads():
    assert _collect_player_names([]) == {}


class _StoreQueRevienta:
    def save_player_names(self, mapping):
        raise RuntimeError("BigQuery caído")


class _StoreQueRegistra:
    def __init__(self):
        self.recibido = None

    def save_player_names(self, mapping):
        self.recibido = mapping


def test_persist_es_best_effort_y_no_tumba_la_ingesta():
    """La misión crítica del job es la ingesta de boxscores."""
    mensajes = []
    ok = _persist_player_names(_StoreQueRevienta(), {1: "X"}, lambda *a: mensajes.append(a))
    assert ok is False
    assert any("catálogo" in str(m[0]) for m in mensajes)


def test_persist_exitoso():
    store = _StoreQueRegistra()
    assert _persist_player_names(store, {1: "X"}, lambda *a: None) is True
    assert store.recibido == {1: "X"}


def test_persist_sin_nombres_no_toca_el_store():
    store = _StoreQueRegistra()
    assert _persist_player_names(store, {}, lambda *a: None) is True
    assert store.recibido is None


# --------------------------------------------------------------------------
# REGLA DE DEGRADACIÓN — la guarda del bug 2
# --------------------------------------------------------------------------


def test_player_map_vacio_declara_feed_caido(monkeypatch, caplog):
    """La regla adjudicada: catálogo vacío NO es 'nadie lesionado'."""
    import nba_predictor.api.daily_predictions as dp

    llamadas = []
    monkeypatch.setattr(dp, "_fetch_absences",
                        lambda **k: llamadas.append(k) or ({}, set(), {}))

    from nba_predictor.ingestion.future_schedule import ScheduledGame
    sg = ScheduledGame(game_id="0022600001", game_date=date(2026, 10, 21),
                       home_team_id=1, away_team_id=2, home_tricode="BOS",
                       away_tricode="LAL", season="2026-27")
    monkeypatch.setattr(dp, "_resolve_schedule", lambda *a, **k: [sg])
    monkeypatch.setattr(dp, "_predict_one", lambda **k: dp.GamePrediction(
        home_tricode="BOS", away_tricode="LAL", game_date="2026-10-21",
        probability_home=0.6, home_absences=[], away_absences=[],
        availability_flag=dp.AvailabilityFlag.FEED_DOWN, model_version="v1"))
    monkeypatch.setattr(dp, "_attach_highlights", lambda **k: None)

    class _S:
        def load_teams(self):
            import pandas as pd
            return pd.DataFrame([{"team_id": 1, "name": "Boston Celtics"},
                                 {"team_id": 2, "name": "Los Angeles Lakers"}])

        def load_model(self, v):
            return (None, {})

    res = dp.build_daily_predictions(date(2026, 10, 21), _S(), season="2026-27",
                                     player_map={}, version_name="v1")
    assert res.feed_down is True
    assert "Catálogo de nombres" in res.feed_down_reason
    assert llamadas == [], "con catálogo vacío no tiene sentido descargar el PDF"
    assert "player_map VACÍO" in caplog.text


def test_el_mensaje_declara_la_degradacion_del_catalogo():
    """El lector tiene que verlo, no solo los logs."""
    from nba_predictor.api.daily_predictions import (
        AvailabilityFlag,
        DailyResult,
        GamePrediction,
        format_daily_message,
    )
    gp = GamePrediction(home_tricode="BOS", away_tricode="LAL",
                        game_date="2026-10-21", probability_home=0.6,
                        home_absences=[], away_absences=[],
                        availability_flag=AvailabilityFlag.FEED_DOWN,
                        model_version="v1")
    msg = format_daily_message(DailyResult(
        target_date="2026-10-21", games=[gp], feed_down=True,
        feed_down_reason="Catálogo de nombres de jugador vacío", model_version="v1"))
    assert "Reporte de lesiones no disponible" in msg


def test_server_pasa_player_map_a_build_daily_predictions():
    """GUARDA DEL BUG 2: el argumento no puede volver a perderse en silencio.

    El bug no vivía en ninguna función: vivía en un argumento que el call site
    nunca pasaba, y por eso ni los tests ni el type checker lo veían. Esta
    guarda mira el call site.
    """
    import inspect

    from nba_predictor.api import server

    fuente = inspect.getsource(server.predictions_today)
    assert "build_daily_predictions(" in fuente
    assert "player_map=" in fuente, (
        "server.py debe pasar player_map explícitamente: sin él el NameIndex "
        "nace vacío y ninguna ausencia hace match (bug 2 de D-PROD-1c)"
    )
