"""D-PRE-9 — dos destacados por equipo, rank en el log y presupuesto de longitud.

DÍA DE MAYOR CARGA DEL CALENDARIO 2026-27, contado (no supuesto) sobre
scheduleLeagueV2_2026-10-20.json archivado en GCS: 15 partidos de temporada
regular (prefijo 002) el 2026-11-02, empatado con 2026-12-23 y 2027-04-11. Son
los 30 equipos jugando a la vez: ningún día puede tener más.

Pre-registro: si el mensaje 2 excede 3 800 caracteres, recorte DECLARADO a top
1 ese día con la línea "(un destacado por equipo hoy por longitud)".
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pandas as pd

from nba_predictor.api.daily_predictions import (
    PLAYERS_MESSAGE_BUDGET,
    TELEGRAM_MAX_CHARS,
    AvailabilityFlag,
    DailyResult,
    GamePrediction,
    PlayerHighlight,
    apply_players_length_budget,
    format_players_message,
)
from nba_predictor.api.player_highlights import HIGHLIGHTS_PER_TEAM, build_game_highlights
from nba_predictor.api.player_predictions_log import build_player_log_rows
from nba_predictor.storage.local import LocalDataStore

DIA_MAS_CARGADO = "2026-11-02"
PARTIDOS_DIA_MAS_CARGADO = 15
LINEA_RECORTE = "(un destacado por equipo hoy por longitud)"
STAMP = datetime(2026, 11, 2, 19, 0, 5, tzinfo=timezone.utc)

# Nombres sintéticos LARGOS (más que cualquier nombre real de la liga) para
# medir el peor caso, no el típico.
_LARGO = "Nombreextralargo Apellidocompuesto-Larguísimo"


def _filas(pid, tid, n, *, minutes, desde="2026-03-30", gid0=0):
    return pd.DataFrame({
        "player_id": [pid] * n,
        "team_id": [tid] * n,
        "game_id": [f"00225{gid0 + i:05d}" for i in range(n)],
        "game_date": pd.date_range(desde, periods=n, freq="D"),
        "minutes": [minutes] * n,
        "pts": [20.0] * n,
        "reb": [5.0] * n,
        "ast": [4.0] * n,
        "fg3m": [1.0] * n,
    })


def _hist():
    """Equipo 100: tres jugadores (34, 32, 30 min). Equipo 200: uno solo."""
    return pd.concat([
        _filas(1, 100, 10, minutes=34.0, gid0=0),
        _filas(2, 100, 10, minutes=32.0, gid0=100),
        _filas(3, 100, 10, minutes=30.0, gid0=200),
        _filas(10, 200, 10, minutes=36.0, gid0=300),
    ], ignore_index=True)


NOMBRES = {1: "Local Uno", 2: "Local Dos", 3: "Local Tres", 10: "Visita Uno"}


# ---------------------------------------------------------------------------
# Selección: top 2 con los mismos filtros
# ---------------------------------------------------------------------------


def test_por_defecto_dos_por_equipo():
    assert HIGHLIGHTS_PER_TEAM == 2


def test_top_dos_con_rank_y_orden_local_luego_visitante():
    hs = build_game_highlights(_hist(), home_team_id=100, away_team_id=200,
                               statuses_by_tid={}, player_map=NOMBRES)
    assert [(h.player_name, h.team, h.rank) for h in hs] == [
        ("Local Uno", "home", 1), ("Local Dos", "home", 2), ("Visita Uno", "away", 1)]


def test_equipo_con_un_solo_candidato_publica_uno():
    hs = build_game_highlights(_hist(), home_team_id=100, away_team_id=200,
                               statuses_by_tid={}, player_map=NOMBRES)
    assert [h.team for h in hs].count("away") == 1


def test_mismos_filtros_el_excluido_cede_y_sube_el_tercero():
    hs = build_game_highlights(_hist(), home_team_id=100, away_team_id=200,
                               statuses_by_tid={100: {1: "Doubtful"}}, player_map=NOMBRES)
    locales = [(h.player_name, h.rank) for h in hs if h.team == "home"]
    assert locales == [("Local Dos", 1), ("Local Tres", 2)]


def test_el_segundo_tambien_exige_minimo_de_partidos():
    hist = pd.concat([_filas(1, 100, 10, minutes=34.0),
                      _filas(2, 100, 2, minutes=40.0, gid0=100)], ignore_index=True)
    hs = build_game_highlights(hist, home_team_id=100, away_team_id=200,
                               statuses_by_tid={}, player_map=NOMBRES)
    assert [h.player_name for h in hs] == ["Local Uno"]


# ---------------------------------------------------------------------------
# Formato: dos líneas por equipo
# ---------------------------------------------------------------------------


def _ph(nombre, team, rank, **kw):
    base = dict(pts_median=24.0, pts_min=11.0, pts_max=35.0, reb_median=7.0,
                ast_median=5.0, games_in_window=10, player_id=rank * 100 + len(nombre))
    base.update(kw)
    return PlayerHighlight(player_name=nombre, team=team, rank=rank, **base)


def _gp(i, players, home="BOS", away="LAL"):
    return GamePrediction(
        home_tricode=home, away_tricode=away, game_date=DIA_MAS_CARGADO,
        probability_home=0.6, home_absences=[], away_absences=[],
        availability_flag=AvailabilityFlag.OK, model_version="v1",
        tip_off_cdmx=f"{17 + i // 4}:{(i % 4) * 15:02d} CDMX", game_id=f"00226{i:05d}",
        players=players, players_data_available=bool(players),
    )


def _res(games):
    return DailyResult(target_date=DIA_MAS_CARGADO, games=games, feed_down=False,
                       feed_down_reason=None, model_version="v1")


def test_dos_lineas_por_equipo_en_el_mensaje():
    gp = _gp(0, [_ph("A Uno", "home", 1), _ph("A Dos", "home", 2),
                 _ph("B Uno", "away", 1), _ph("B Dos", "away", 2)])
    msg = format_players_message(_res([gp]))
    assert msg.splitlines()[2:7] == [
        "LAL @ BOS",
        "• A Uno (BOS): 24 pts (11-35) · 7 reb · 5 ast",
        "• A Dos (BOS): 24 pts (11-35) · 7 reb · 5 ast",
        "• B Uno (LAL): 24 pts (11-35) · 7 reb · 5 ast",
        "• B Dos (LAL): 24 pts (11-35) · 7 reb · 5 ast",
    ]
    assert LINEA_RECORTE not in msg


# ---------------------------------------------------------------------------
# Presupuesto de longitud sobre el día de mayor carga
# ---------------------------------------------------------------------------


def _peor_dia(per_team=2):
    """15 partidos, 2 por equipo, nombres largos, Q, especialista y ventana < 10
    en TODOS: todo lo que alarga una línea, a la vez."""
    games = []
    for i in range(PARTIDOS_DIA_MAS_CARGADO):
        players = []
        for team in ("home", "away"):
            for rank in range(1, per_team + 1):
                players.append(_ph(f"{_LARGO} {i}{team[0]}{rank}", team, rank,
                                   status="Q", fg3m_median=3.0, is_shooting_specialist=True,
                                   games_in_window=7, pts_median=33.5, pts_min=18.0,
                                   pts_max=51.0, reb_median=12.5, ast_median=10.5))
        games.append(_gp(i, players, home="PHI", away="POR"))
    return _res(games)


def test_el_peor_dia_con_dos_por_equipo_excede_el_presupuesto():
    """Medido, no estimado: con dos por equipo NO cabe. El recorte existe por esto."""
    largo = len(format_players_message(_peor_dia()))
    assert largo > PLAYERS_MESSAGE_BUDGET
    assert largo == 7_187          # fijado: si el formato cambia, este número avisa


def test_el_peor_dia_se_recorta_declarado_a_uno_por_equipo():
    res = _peor_dia()
    assert apply_players_length_budget(res) is True
    msg = format_players_message(res)
    assert res.players_trimmed is True
    assert msg.splitlines()[1] == LINEA_RECORTE
    assert len(msg) <= PLAYERS_MESSAGE_BUDGET < TELEGRAM_MAX_CHARS
    assert len(msg) == 3_770        # peor caso recortado: 30 bajo el presupuesto
    assert all(p.rank == 1 for gp in res.games for p in gp.players)
    assert all(len(gp.players) == 2 for gp in res.games)      # uno por equipo


def test_tras_el_recorte_el_log_registra_solo_lo_publicado():
    res = _peor_dia()
    apply_players_length_budget(res)
    filas = build_player_log_rows(res, served_by="r", model_version="v1",
                                  predicted_at_utc=STAMP)
    assert len(filas) == 2 * PARTIDOS_DIA_MAS_CARGADO
    assert {f["rank"] for f in filas} == {1}


def test_dia_que_cabe_no_se_recorta_ni_lleva_la_linea():
    res = _res([_gp(i, [_ph("A Uno", "home", 1), _ph("A Dos", "home", 2),
                        _ph("B Uno", "away", 1), _ph("B Dos", "away", 2)])
                for i in range(5)])
    antes = format_players_message(res)
    assert apply_players_length_budget(res) is False
    assert res.players_trimmed is False
    assert format_players_message(res) == antes and LINEA_RECORTE not in antes
    assert sum(len(gp.players) for gp in res.games) == 20


def test_dia_tipico_de_15_con_nombres_reales_de_largo_medio():
    """Referencia de producto: con nombres de largo típico (~15) y sin marcas,
    ¿cabe el día de 15 partidos con dos por equipo? Se fija la medida."""
    games = [_gp(i, [_ph(f"Jugador Nom{i:02d}{t}{r}", t, r)
                     for t in ("home", "away") for r in (1, 2)])
             for i in range(PARTIDOS_DIA_MAS_CARGADO)]
    largo = len(format_players_message(_res(games)))
    assert largo == 3_752           # cabe por 48: un día lleno real roza el borde


# ---------------------------------------------------------------------------
# rank en el log y en el espejo local
# ---------------------------------------------------------------------------


def test_rank_viaja_al_log_y_none_es_uno_implicito():
    gp = _gp(0, [_ph("A Uno", "home", 1), _ph("A Dos", "home", 2),
                 PlayerHighlight(player_name="Viejo", team="away", pts_median=1, pts_min=1,
                                 pts_max=1, reb_median=1, ast_median=1, games_in_window=10,
                                 player_id=7)])
    filas = build_player_log_rows(_res([gp]), served_by="r", model_version="v1",
                                  predicted_at_utc=STAMP)
    assert [f["rank"] for f in filas] == [1, 2, 1]


def test_espejo_local_existente_gana_la_columna_rank_sin_perder_filas(tmp_path):
    db = tmp_path / "nba.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE player_predictions_log (
            game_id TEXT, game_date DATE, player_id INTEGER, player_name TEXT,
            team_tricode TEXT, status_flag TEXT, pts_median REAL, pts_min REAL,
            pts_max REAL, reb_median REAL, ast_median REAL, fg3m_median REAL,
            games_in_window INTEGER, model_version TEXT, predicted_at_utc TEXT,
            served_by TEXT)""")
        conn.execute("INSERT INTO player_predictions_log (game_id, player_id) VALUES ('g', 1)")
    st = LocalDataStore(db_path=db, raw_dir=tmp_path / "raw")
    gp = _gp(0, [_ph("A Dos", "home", 2)])
    st.save_player_predictions_log(build_player_log_rows(
        _res([gp]), served_by="r", model_version="v1", predicted_at_utc=STAMP))
    with sqlite3.connect(db) as conn:
        filas = conn.execute(
            "SELECT game_id, rank FROM player_predictions_log ORDER BY rowid").fetchall()
    assert filas == [("g", None), ("0022600000", 2)]     # histórica NULL = 1 implícito


def test_schema_cloud_lleva_rank_al_final():
    from nba_predictor.storage.cloud import _PLAYER_LOG_SCHEMA

    assert _PLAYER_LOG_SCHEMA[-1] == ("rank", "INTEGER") and len(_PLAYER_LOG_SCHEMA) == 17


# ---------------------------------------------------------------------------
# Segunda guarda: ni con uno por equipo cabe → mensaje 2 omitido, declarado
# ---------------------------------------------------------------------------

LINEA_OMISION = "🏀 Destacados · 2 nov 2026: omitidos hoy por longitud del día."


def _dia_extremo():
    """15 partidos con nombres sintéticos EXTREMOS (~150 caracteres): ni con
    uno por equipo cabe bajo 4 000."""
    largo = "Nombre" + "x" * 140
    games = []
    for i in range(PARTIDOS_DIA_MAS_CARGADO):
        players = [_ph(f"{largo} {i}{t}{r}", t, r, status="Q", fg3m_median=3.0,
                       is_shooting_specialist=True, games_in_window=7)
                   for t in ("home", "away") for r in (1, 2)]
        games.append(_gp(i, players, home="PHI", away="POR"))
    return _res(games)


def test_dia_extremo_se_omite_con_la_linea_declarada():
    from nba_predictor.api.daily_predictions import PLAYERS_MESSAGE_HARD_LIMIT

    res = _dia_extremo()
    assert apply_players_length_budget(res) is True
    assert res.highlights_truncated == "omitted"
    msg = format_players_message(res)
    assert msg == LINEA_OMISION
    assert PLAYERS_MESSAGE_HARD_LIMIT == 4000 < TELEGRAM_MAX_CHARS


def test_dia_extremo_no_registra_destacados_en_el_log():
    """Nada se publicó: el log de destacados queda vacío ese día."""
    res = _dia_extremo()
    apply_players_length_budget(res)
    assert build_player_log_rows(res, served_by="r", model_version="v1",
                                 predicted_at_utc=STAMP) == []


def test_dia_extremo_conserva_los_calculados_en_data():
    """Observabilidad: data sigue mostrando quién se habría publicado (top 1)."""
    from dataclasses import asdict

    res = _dia_extremo()
    apply_players_length_budget(res)
    data = asdict(res)
    assert data["highlights_truncated"] == "omitted"
    assert all(len(g["players"]) == 2 for g in data["games"])


def test_escalera_de_estados_none_top1_omitted():
    normal = _res([_gp(0, [_ph("A Uno", "home", 1)])])
    apply_players_length_budget(normal)
    recortado = _peor_dia()
    apply_players_length_budget(recortado)
    extremo = _dia_extremo()
    apply_players_length_budget(extremo)
    assert [normal.highlights_truncated, recortado.highlights_truncated,
            extremo.highlights_truncated] == [None, "top1", "omitted"]
