"""Evidencia de destacados: player_predictions_log (D-PROD-1c).

Espejo de los tests de predictions_log: schema, pureza de la construcción de
filas, best-effort de la escritura y semántica append-only del adapter local.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from nba_predictor.api.daily_predictions import (
    AvailabilityFlag,
    DailyResult,
    GamePrediction,
    PlayerHighlight,
)
from nba_predictor.api.player_predictions_log import (
    PLAYER_LOG_FIELDS,
    build_player_log_rows,
    resolve_served_by,
    write_player_predictions_log,
)
from nba_predictor.storage.local import LocalDataStore

STAMP = datetime(2026, 10, 21, 19, 0, 2, tzinfo=timezone.utc)
MODELO = "v1_logistic_bclean_2026-08-22@abc123"


def _jugador(**kw) -> PlayerHighlight:
    base = dict(
        player_name="Jayson Tatum", team="home", pts_median=27.0, pts_min=14.0,
        pts_max=41.0, reb_median=8.0, ast_median=5.0, games_in_window=10,
        player_id=1628369, fg3m_median=3.0, is_shooting_specialist=True,
    )
    base.update(kw)
    return PlayerHighlight(**base)


def _partido(**kw) -> GamePrediction:
    base = dict(
        home_tricode="BOS", away_tricode="LAL", game_date="2026-10-21",
        probability_home=0.67, home_absences=[], away_absences=[],
        availability_flag=AvailabilityFlag.OK, model_version="v1",
        game_id="0022600001",
    )
    base.update(kw)
    return GamePrediction(**base)


def _resultado(games) -> DailyResult:
    return DailyResult(target_date="2026-10-21", games=games, feed_down=False,
                       feed_down_reason=None, model_version="v1")


# ---------------------------------------------------------------------------
# Schema y construcción de filas
# ---------------------------------------------------------------------------


def test_schema_tiene_dieciseis_campos_en_orden():
    assert len(PLAYER_LOG_FIELDS) == 16
    assert PLAYER_LOG_FIELDS[0] == "game_id"
    assert PLAYER_LOG_FIELDS[-1] == "served_by"


def test_fila_lleva_exactamente_las_claves_del_schema():
    gp = _partido(players=[_jugador()], players_data_available=True)
    fila = build_player_log_rows(_resultado([gp]), served_by="rev-1",
                                 model_version=MODELO, predicted_at_utc=STAMP)[0]
    assert set(fila) == set(PLAYER_LOG_FIELDS)


def test_contenido_de_la_fila():
    gp = _partido(players=[_jugador()], players_data_available=True)
    fila = build_player_log_rows(_resultado([gp]), served_by="rev-1",
                                 model_version=MODELO, predicted_at_utc=STAMP)[0]
    assert fila["game_id"] == "0022600001"
    assert fila["player_id"] == 1628369
    assert fila["team_tricode"] == "BOS"
    assert fila["pts_median"] == 27.0
    assert fila["games_in_window"] == 10
    assert fila["model_version"] == MODELO
    assert fila["predicted_at_utc"] is STAMP
    assert fila["served_by"] == "rev-1"


def test_tricode_se_resuelve_por_lado():
    gp = _partido(players=[_jugador(team="home"), _jugador(team="away")],
                  players_data_available=True)
    filas = build_player_log_rows(_resultado([gp]), served_by="r",
                                  model_version=MODELO, predicted_at_utc=STAMP)
    assert [f["team_tricode"] for f in filas] == ["BOS", "LAL"]


def test_guarda_el_float_y_no_el_entero_publicado():
    """El mensaje redondea; la evidencia guarda el dato observado."""
    gp = _partido(players=[_jugador(pts_median=23.5)], players_data_available=True)
    fila = build_player_log_rows(_resultado([gp]), served_by="r",
                                 model_version=MODELO, predicted_at_utc=STAMP)[0]
    assert fila["pts_median"] == 23.5


def test_status_flag_viaja_tal_cual():
    gp = _partido(players=[_jugador(status="Q"), _jugador(status=None, team="away")],
                  players_data_available=True)
    filas = build_player_log_rows(_resultado([gp]), served_by="r",
                                  model_version=MODELO, predicted_at_utc=STAMP)
    assert [f["status_flag"] for f in filas] == ["Q", None]


def test_fg3m_nulo_se_conserva_como_nulo():
    gp = _partido(players=[_jugador(fg3m_median=None)], players_data_available=True)
    fila = build_player_log_rows(_resultado([gp]), served_by="r",
                                 model_version=MODELO, predicted_at_utc=STAMP)[0]
    assert fila["fg3m_median"] is None


def test_partido_sin_seccion_publicada_no_genera_filas():
    """El log registra lo PUBLICADO, no lo que se pudo haber publicado."""
    gp = _partido(players=[_jugador()], players_data_available=False)
    assert build_player_log_rows(_resultado([gp]), served_by="r",
                                 model_version=MODELO) == []


def test_dia_sin_partidos_no_genera_filas():
    assert build_player_log_rows(_resultado([]), served_by="r",
                                 model_version=MODELO) == []


def test_un_solo_sello_para_todas_las_filas_de_la_servida():
    gps = [_partido(players=[_jugador(), _jugador(team="away")],
                    players_data_available=True),
           _partido(game_id="0022600002", home_tricode="MIA", away_tricode="GSW",
                    players=[_jugador()], players_data_available=True)]
    filas = build_player_log_rows(_resultado(gps), served_by="r",
                                  model_version=MODELO, predicted_at_utc=STAMP)
    assert len(filas) == 3
    assert {f["predicted_at_utc"] for f in filas} == {STAMP}


def test_sin_sello_inyectado_se_usa_el_reloj_en_utc():
    gp = _partido(players=[_jugador()], players_data_available=True)
    fila = build_player_log_rows(_resultado([gp]), served_by="r",
                                 model_version=MODELO)[0]
    assert fila["predicted_at_utc"].tzinfo is timezone.utc


def test_resolve_served_by_fuera_de_cloud_run(monkeypatch):
    monkeypatch.delenv("K_REVISION", raising=False)
    assert resolve_served_by() == "local"


def test_resolve_served_by_en_cloud_run(monkeypatch):
    monkeypatch.setenv("K_REVISION", "predictions-api-00008-abc")
    assert resolve_served_by() == "predictions-api-00008-abc"


# ---------------------------------------------------------------------------
# write_player_predictions_log — BEST-EFFORT
# ---------------------------------------------------------------------------


class _StoreQueRevienta:
    def save_player_predictions_log(self, rows):
        raise RuntimeError("BigQuery caído")


class _StoreQueRegistra:
    def __init__(self):
        self.recibido = None

    def save_player_predictions_log(self, rows):
        self.recibido = rows


def test_escritura_fallida_no_propaga_y_devuelve_false(caplog):
    """La misión del endpoint es la predicción; el log es evidencia secundaria."""
    filas = [{"served_by": "rev-1"}]
    assert write_player_predictions_log(_StoreQueRevienta(), filas) is False
    assert "player_predictions_log" in caplog.text


def test_escritura_exitosa_devuelve_true_y_pasa_las_filas():
    store = _StoreQueRegistra()
    filas = [{"served_by": "rev-1"}]
    assert write_player_predictions_log(store, filas) is True
    assert store.recibido == filas


def test_sin_filas_no_toca_el_store():
    store = _StoreQueRegistra()
    assert write_player_predictions_log(store, []) is True
    assert store.recibido is None


# ---------------------------------------------------------------------------
# Adapter local — append-only de verdad
# ---------------------------------------------------------------------------


@pytest.fixture()
def store_local(tmp_path) -> LocalDataStore:
    return LocalDataStore(db_path=tmp_path / "t.sqlite", raw_dir=tmp_path / "raw",
                          processed_dir=tmp_path / "proc")


def _fila(**kw) -> dict:
    base = dict(
        game_id="0022600001", game_date="2026-10-21", player_id=1628369,
        player_name="Jayson Tatum", team_tricode="BOS", status_flag=None,
        pts_median=27.0, pts_min=14.0, pts_max=41.0, reb_median=8.0,
        ast_median=5.0, fg3m_median=3.0, games_in_window=10,
        model_version=MODELO, predicted_at_utc=STAMP, served_by="rev-1",
    )
    base.update(kw)
    return base


def test_local_guarda_y_serializa_el_timestamp(store_local):
    store_local.save_player_predictions_log([_fila()])
    with sqlite3.connect(store_local.db_path) as conn:
        filas = conn.execute(
            "SELECT player_name, pts_median, predicted_at_utc FROM player_predictions_log"
        ).fetchall()
    assert filas == [("Jayson Tatum", 27.0, STAMP.isoformat())]


def test_local_dos_servidas_del_mismo_jugador_son_dos_filas(store_local):
    """Append-only: no es una colisión que resolver, son dos hechos."""
    store_local.save_player_predictions_log([_fila()])
    store_local.save_player_predictions_log([_fila(predicted_at_utc=datetime(
        2026, 10, 21, 23, 0, 0, tzinfo=timezone.utc))])
    with sqlite3.connect(store_local.db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM player_predictions_log").fetchone()[0]
    assert n == 2


def test_local_sin_filas_no_escribe(store_local):
    store_local.save_player_predictions_log([])
    with sqlite3.connect(store_local.db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM player_predictions_log").fetchone()[0]
    assert n == 0


# ---------------------------------------------------------------------------
# Adapter cloud — guarda del hallazgo D-PROD-1c
# ---------------------------------------------------------------------------


def test_cloud_usa_create_never_en_ambos_logs():
    """CREATE_IF_NEEDED exige bigquery.tables.create a nivel DATASET.

    Ese permiso es incompatible con el dataEditor A NIVEL TABLA con el que
    corre predictions-api-sa, así que la escritura devolvía 403 siempre — y
    era invisible porque en offseason cero filas es el resultado esperado.
    Esta guarda impide que vuelva: si alguien restaura CREATE_IF_NEEDED, el
    test se pone rojo antes de que el silencio llegue a producción.

    La guarda se acota a los DOS escritores de evidencia: _save_tabular sigue
    usando CREATE_IF_NEEDED legítimamente, porque el ingest job sí tiene
    permiso de dataset y necesita crear sus tablas staging.
    """
    import inspect

    from nba_predictor.storage.cloud import CloudDataStore

    for metodo in ("save_predictions_log", "save_player_predictions_log"):
        fuente = inspect.getsource(getattr(CloudDataStore, metodo))
        # Se compara el token CUALIFICADO: los comentarios del método explican
        # por qué CREATE_IF_NEEDED estaba mal, y una búsqueda del nombre a
        # secas se dispararía con la propia explicación.
        assert "CreateDisposition.CREATE_NEVER" in fuente, metodo
        assert "CreateDisposition.CREATE_IF_NEEDED" not in fuente, metodo
