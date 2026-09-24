"""Selección y estadística de destacados (D-PROD-1c).

Cubre las funciones puras de nba_predictor/api/player_highlights.py con
fixtures sintéticos mínimos: aquí se fija la REGLA de selección (quién sale y
con qué números), no la procedencia del dato.
"""
from __future__ import annotations

import pandas as pd
import pytest
from datetime import timedelta

from nba_predictor.api.player_highlights import (
    MIN_GAMES_IN_WINDOW,
    SPECIALIST_MIN_FG3M,
    build_game_highlights,
    derive_box_stats,
    is_eligible,
    player_window_stats,
    rank_team_candidates,
    status_mark,
)


def _filas(player_id: int, team_id: int, n: int, *, minutes=30.0, pts=20.0,
           reb=5.0, ast=4.0, fg3m=1.0, desde="2026-10-01") -> pd.DataFrame:
    """n partidos jugados de un jugador, en fechas consecutivas."""
    fechas = pd.date_range(desde, periods=n, freq="D")
    return pd.DataFrame({
        "player_id": [player_id] * n,
        "team_id": [team_id] * n,
        "game_date": fechas,
        "minutes": [minutes] * n if not isinstance(minutes, list) else minutes,
        "pts": [pts] * n if not isinstance(pts, list) else pts,
        "reb": [reb] * n if not isinstance(reb, list) else reb,
        "ast": [ast] * n if not isinstance(ast, list) else ast,
        "fg3m": [fg3m] * n if not isinstance(fg3m, list) else fg3m,
    })


# ---------------------------------------------------------------------------
# derive_box_stats
# ---------------------------------------------------------------------------


def test_derive_box_stats_cuenta_los_triples_una_sola_vez():
    """fgm YA incluye los triples: 2*fgm + fg3m + ftm."""
    df = pd.DataFrame([{"fgm": 10, "fg3m": 4, "ftm": 3, "oreb": 2, "dreb": 5}])
    out = derive_box_stats(df)
    assert out.loc[0, "pts"] == 27      # 6 dobles (12) + 4 triples (12) + 3 libres
    assert out.loc[0, "reb"] == 7


def test_derive_box_stats_no_muta_la_entrada():
    df = pd.DataFrame([{"fgm": 1, "fg3m": 0, "ftm": 0, "oreb": 0, "dreb": 0}])
    derive_box_stats(df)
    assert "pts" not in df.columns


# ---------------------------------------------------------------------------
# player_window_stats — "últimos 10" tiene que ser literal
# ---------------------------------------------------------------------------


def test_window_toma_los_ULTIMOS_y_no_los_primeros():
    pts = [0.0] * 10 + [30.0] * 10        # los diez viejos son ceros
    df = _filas(1, 100, 20, pts=pts)
    s = player_window_stats(df, window=10)
    assert s["games_in_window"] == 10
    assert s["pts_median"] == 30.0
    assert s["pts_min"] == 30.0           # el 0 viejo quedó fuera de la ventana


def test_window_con_menos_partidos_reporta_el_n_real():
    s = player_window_stats(_filas(1, 100, 6), window=10)
    assert s["games_in_window"] == 6


def test_window_medianas_minimos_y_maximos():
    df = _filas(1, 100, 4, pts=[10.0, 20.0, 30.0, 40.0], reb=[1.0, 2.0, 3.0, 4.0],
                ast=[0.0, 0.0, 6.0, 6.0], fg3m=[0.0, 1.0, 2.0, 5.0])
    s = player_window_stats(df, window=10)
    assert s["pts_median"] == 25.0        # promedio de 20 y 30
    assert (s["pts_min"], s["pts_max"]) == (10.0, 40.0)
    assert s["reb_median"] == 2.5
    assert s["ast_median"] == 3.0
    assert s["fg3m_median"] == 1.5


def test_window_vacia_no_revienta():
    vacio = _filas(1, 100, 0)
    assert player_window_stats(vacio)["games_in_window"] == 0


def test_minutes_rolling_es_la_media_de_la_ventana():
    df = _filas(1, 100, 4, minutes=[10.0, 20.0, 30.0, 40.0])
    assert player_window_stats(df)["minutes_rolling"] == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# Elegibilidad y marca
# ---------------------------------------------------------------------------


def test_out_y_doubtful_quedan_excluidos():
    """Doubtful sale por MEDICIÓN: D-EXP-1 midió P(juega|Doubtful) ~ 0.02."""
    assert not is_eligible("Out")
    assert not is_eligible("Doubtful")


def test_questionable_probable_available_y_no_listado_son_elegibles():
    for s in ("Questionable", "Probable", "Available", None):
        assert is_eligible(s)


def test_solo_questionable_se_marca():
    assert status_mark("Questionable") == "Q"
    for s in ("Probable", "Available", None, "Inventado"):
        assert status_mark(s) is None


# ---------------------------------------------------------------------------
# rank_team_candidates
# ---------------------------------------------------------------------------


def _equipo(*dfs) -> pd.DataFrame:
    return pd.concat(dfs, ignore_index=True)


def test_ranking_por_minutos_rolling_descendente():
    hist = _equipo(_filas(1, 100, 10, minutes=20.0),
                   _filas(2, 100, 10, minutes=35.0),
                   _filas(3, 100, 10, minutes=28.0))
    orden = [c["player_id"] for c in rank_team_candidates(hist, 100, {})]
    assert orden == [2, 3, 1]


def test_ranking_excluye_por_estatus():
    hist = _equipo(_filas(1, 100, 10, minutes=35.0), _filas(2, 100, 10, minutes=20.0))
    orden = [c["player_id"] for c in rank_team_candidates(hist, 100, {1: "Out"})]
    assert orden == [2]


def test_ranking_excluye_a_quien_no_llega_al_minimo_de_partidos():
    """Con menos de 3 jugados, mediana y rango describen ruido."""
    hist = _equipo(_filas(1, 100, MIN_GAMES_IN_WINDOW - 1, minutes=40.0),
                   _filas(2, 100, MIN_GAMES_IN_WINDOW, minutes=15.0))
    orden = [c["player_id"] for c in rank_team_candidates(hist, 100, {})]
    assert orden == [2]


def test_ranking_ignora_partidos_sin_minutos():
    """Las filas DNP (minutes 0 o nulo) no son partidos jugados."""
    dnp = _filas(1, 100, 10, minutes=0.0)
    hist = _equipo(dnp, _filas(2, 100, 5, minutes=10.0))
    orden = [c["player_id"] for c in rank_team_candidates(hist, 100, {})]
    assert orden == [2]


def test_ranking_solo_mira_al_equipo_pedido():
    hist = _equipo(_filas(1, 100, 10, minutes=20.0), _filas(2, 200, 10, minutes=40.0))
    orden = [c["player_id"] for c in rank_team_candidates(hist, 100, {})]
    assert orden == [1]


def test_desempate_determinista_por_player_id():
    """Dos servidas del mismo día no pueden publicar jugadores distintos."""
    hist = _equipo(_filas(7, 100, 10, minutes=30.0), _filas(3, 100, 10, minutes=30.0))
    orden = [c["player_id"] for c in rank_team_candidates(hist, 100, {})]
    assert orden == [3, 7]


def test_equipo_sin_historia_da_lista_vacia():
    assert rank_team_candidates(_filas(1, 100, 10), 999, {}) == []


# ---------------------------------------------------------------------------
# build_game_highlights
# ---------------------------------------------------------------------------


NOMBRES = {1: "Local Uno", 2: "Local Dos", 10: "Visita Uno", 11: "Visita Dos"}


def _hist_partido() -> pd.DataFrame:
    return _equipo(
        _filas(1, 100, 10, minutes=34.0, pts=25.0, reb=8.0, ast=5.0, fg3m=1.0),
        _filas(2, 100, 10, minutes=18.0),
        _filas(10, 200, 10, minutes=36.0, pts=30.0, reb=4.0, ast=9.0, fg3m=3.0),
        _filas(11, 200, 10, minutes=12.0),
    )


def test_top_uno_por_equipo_con_lado_correcto():
    hs = build_game_highlights(_hist_partido(), home_team_id=100, away_team_id=200,
                               statuses_by_tid={}, player_map=NOMBRES)
    assert [(h.player_name, h.team) for h in hs] == [
        ("Local Uno", "home"), ("Visita Uno", "away")]


def test_estatus_questionable_llega_marcado():
    hs = build_game_highlights(_hist_partido(), home_team_id=100, away_team_id=200,
                               statuses_by_tid={100: {1: "Questionable"}},
                               player_map=NOMBRES)
    assert hs[0].status == "Q"
    assert hs[1].status is None


def test_el_excluido_cede_el_puesto_al_siguiente():
    hs = build_game_highlights(_hist_partido(), home_team_id=100, away_team_id=200,
                               statuses_by_tid={100: {1: "Out"}}, player_map=NOMBRES)
    assert hs[0].player_name == "Local Dos"


def test_especialista_por_umbral_de_triples():
    hs = build_game_highlights(_hist_partido(), home_team_id=100, away_team_id=200,
                               statuses_by_tid={}, player_map=NOMBRES)
    local, visita = hs
    assert local.fg3m_median == 1.0 and local.is_shooting_specialist is False
    assert visita.fg3m_median == 3.0 and visita.is_shooting_specialist is True
    assert SPECIALIST_MIN_FG3M == 2.0


def test_equipo_sin_candidato_no_aporta_jugador_y_el_otro_si():
    """El contrato es top 1 POR EQUIPO: el hueco no se rellena con el rival."""
    hist = _equipo(_filas(1, 100, 10, minutes=34.0), _filas(10, 200, 1, minutes=36.0))
    hs = build_game_highlights(hist, home_team_id=100, away_team_id=200,
                               statuses_by_tid={}, player_map=NOMBRES)
    assert [h.team for h in hs] == ["home"]


def test_sin_candidatos_en_ninguno_da_lista_vacia():
    hs = build_game_highlights(_filas(1, 100, 1), home_team_id=100, away_team_id=200,
                               statuses_by_tid={}, player_map=NOMBRES)
    assert hs == []


def test_player_id_viaja_para_el_log():
    hs = build_game_highlights(_hist_partido(), home_team_id=100, away_team_id=200,
                               statuses_by_tid={}, player_map=NOMBRES)
    assert [h.player_id for h in hs] == [1, 10]


def test_nombre_desconocido_cae_al_id():
    hs = build_game_highlights(_hist_partido(), home_team_id=100, away_team_id=200,
                               statuses_by_tid={}, player_map={})
    assert hs[0].player_name == "#1"


def test_games_in_window_real_viaja_al_highlight():
    hist = _equipo(_filas(1, 100, 6, minutes=34.0), _filas(10, 200, 10, minutes=36.0))
    hs = build_game_highlights(hist, home_team_id=100, away_team_id=200,
                               statuses_by_tid={}, player_map=NOMBRES)
    assert hs[0].games_in_window == 6
    assert hs[1].games_in_window == 10


# ---------------------------------------------------------------------------
# RECENCIA (D-PROD-1f) — el universo es el roster vigente, no la historia
# ---------------------------------------------------------------------------


from datetime import date  # noqa: E402

from nba_predictor.api.player_highlights import (  # noqa: E402
    HIGHLIGHT_RECENCY_DAYS,
    recent_seasons,
    season_of,
)

TARGET = date(2026, 10, 21)


def _hist_miami_real() -> pd.DataFrame:
    """Réplica del universo REAL que el dry-run 2026-10-21 encontró para MIA.

    Luol Deng promedió 34.9 min en su último tramo con Miami y Bam Adebayo
    34.2: sin filtro de recencia gana Deng, cuyo último partido con el equipo
    fue el 2016-04-13 — 3 843 días antes del partido a publicar.
    """
    deng = _filas(2736, 1610612748, 10, minutes=34.9, pts=16.0, desde="2016-04-01")
    bam = _filas(1628389, 1610612748, 10, minutes=34.2, pts=19.0, desde="2026-04-01")
    return _equipo(deng, bam)


def test_luol_deng_no_juega_en_2026():
    """GUARDA CON CONOCIMIENTO EXTERNO (protocolo 13e-1).

    Los invariantes automáticos no cazan una misatribución: los números de Deng
    son perfectamente válidos, solo que de hace una década. Lo que la caza es
    saber que Luol Deng no juega en Miami en 2026 — así que ese saber se
    codifica aquí.
    """
    hs = build_game_highlights(
        _hist_miami_real(), home_team_id=1610612748, away_team_id=999,
        statuses_by_tid={}, player_map={2736: "Luol Deng", 1628389: "Bam Adebayo"},
        target_date=TARGET,
    )
    assert [h.player_name for h in hs] == ["Bam Adebayo"]


def test_sin_target_date_el_filtro_no_se_aplica():
    """Demuestra que el bug era exactamente la ausencia del filtro."""
    hs = build_game_highlights(
        _hist_miami_real(), home_team_id=1610612748, away_team_id=999,
        statuses_by_tid={}, player_map={2736: "Luol Deng", 1628389: "Bam Adebayo"},
    )
    assert [h.player_name for h in hs] == ["Luol Deng"]


def test_el_corte_de_recencia_es_el_esperado():
    """Un jugador justo dentro entra; justo fuera, no."""
    dentro = TARGET - timedelta(days=HIGHLIGHT_RECENCY_DAYS - 1)
    fuera = TARGET - timedelta(days=HIGHLIGHT_RECENCY_DAYS + 1)
    for limite, esperado in ((dentro, [7]), (fuera, [])):
        hist = _filas(7, 100, 5, minutes=30.0,
                      desde=(limite - timedelta(days=4)).isoformat())
        cands = rank_team_candidates(hist, 100, {}, target_date=TARGET)
        assert [c["player_id"] for c in cands] == esperado


def test_la_recencia_es_por_EQUIPO_no_por_liga():
    """Un traspasado ya no representa a su equipo anterior aunque siga jugando."""
    hist = _equipo(
        _filas(1, 100, 10, minutes=35.0, desde="2021-01-01"),   # jugó en 100 hace años
        _filas(1, 200, 10, minutes=35.0, desde="2026-04-01"),   # hoy juega en 200
        _filas(2, 100, 10, minutes=20.0, desde="2026-04-01"),
    )
    assert [c["player_id"] for c in rank_team_candidates(
        hist, 100, {}, target_date=TARGET)] == [2]


def test_equipo_sin_vigentes_se_omite_con_warning(caplog):
    """Plantel renovado: se omite, jamás un jugador rancio con aspecto sano."""
    hs = build_game_highlights(
        _filas(1, 100, 10, minutes=35.0, desde="2016-01-01"),
        home_team_id=100, away_team_id=200, statuses_by_tid={},
        player_map={1: "Fantasma"}, target_date=TARGET,
    )
    assert hs == []
    assert "Sin destacado VIGENTE" in caplog.text


# ---------------------------------------------------------------------------
# Ventana de historial que se CARGA — distinta de la recencia
# ---------------------------------------------------------------------------


def test_season_of_deriva_de_la_fecha():
    assert season_of(date(2026, 10, 21)) == "2026-27"   # octubre ya es temporada nueva
    assert season_of(date(2026, 4, 12)) == "2025-26"    # abril sigue siendo la anterior
    assert season_of(date(2026, 9, 30)) == "2025-26"    # septiembre es offseason


def test_recent_seasons_devuelve_n_temporadas_hasta_la_actual():
    assert recent_seasons(date(2026, 10, 21), n=3) == ["2024-25", "2025-26", "2026-27"]
    assert recent_seasons(date(2026, 4, 12), n=2) == ["2024-25", "2025-26"]
