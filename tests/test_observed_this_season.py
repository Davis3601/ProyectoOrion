"""D-PRE-10 — un destacado debe haber sido VISTO en la temporada en curso.

Caso real que lo destapó (cotejo de Antonio contra box scores de RealGM,
2026-10-25): Toby Okani salió como destacado de MEM el 21 y el 23 sin equipo
NBA (NYK lo cortó el 30/09), y Andersson Garcia de UTA el 23 jugando en el Río
Breogán. Verificado en BigQuery el 2026-10-25: los dos, y Lucas Williamson,
con CERO señales de 2026-27 y CERO filas de esta temporada; su última fila es
de marzo-abril de 2026, dentro de los 250 días de recencia.

Ids reales: Okani 1643253, Williamson 1631351 (MEM 1610612763); Garcia
1643158 (UTA 1610612762); Ja Morant 1629630; Ingram 1627742 (LAC 1610612746).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from nba_predictor.api.player_highlights import (
    build_game_highlights,
    observed_this_season,
    rank_team_candidates,
)

MEM, UTA, LAC, TOR = 1610612763, 1610612762, 1610612746, 1610612761
OKANI, WILLIAMSON, GARCIA, MORANT, INGRAM = 1643253, 1631351, 1643158, 1629630, 1627742
JUGADOR_2026 = 1642999   # sintético: jugó un partido oficial de 2026-27, sin señal
TARGET = date(2026, 10, 25)
T_OBS = datetime(2026, 10, 25, 12, 3, tzinfo=timezone.utc)


def _filas(pid, tid, n, *, minutes, desde="2026-04-01", gid0=0, prefijo="00225"):
    return pd.DataFrame({
        "player_id": [pid] * n,
        "team_id": [tid] * n,
        "game_id": [f"{prefijo}{gid0 + i:05d}" for i in range(n)],
        "game_date": pd.date_range(desde, periods=n, freq="D"),
        "minutes": [minutes] * n,
        "pts": [15.0] * n, "reb": [4.0] * n, "ast": [3.0] * n, "fg3m": [1.0] * n,
    })


def _senal(pid, tid, d, *, source="preseason", ref="0012600001"):
    return pd.DataFrame([{"player_id": pid, "team_id": tid, "source": source,
                          "ref_id": ref, "game_date": date.fromisoformat(d),
                          "observed_at": T_OBS}])


def _hist():
    """Abril de 2026: Okani y Williamson en MEM (los de MÁS minutos, el caso
    real de suplentes de abril), Morant en MEM, Garcia en UTA."""
    return pd.concat([
        _filas(OKANI, MEM, 6, minutes=36.2, gid0=0),
        _filas(WILLIAMSON, MEM, 7, minutes=32.0, gid0=100),
        _filas(MORANT, MEM, 10, minutes=30.0, desde="2026-03-20", gid0=200),
        _filas(GARCIA, UTA, 5, minutes=33.8, gid0=300),
    ], ignore_index=True)


def _senales():
    return pd.concat([_senal(MORANT, MEM, "2026-10-10"),
                      _senal(INGRAM, LAC, "2026-10-14", ref="0012600064")],
                     ignore_index=True)


def _ids(c):
    return [x["player_id"] for x in c]


def test_okani_y_williamson_no_son_candidatos_de_mem():
    cands = rank_team_candidates(_hist(), MEM, {}, target_date=TARGET,
                                 roster_signals=_senales())
    assert _ids(cands) == [MORANT]


def test_garcia_no_es_candidato_de_uta():
    assert rank_team_candidates(_hist(), UTA, {}, target_date=TARGET,
                                roster_signals=_senales()) == []


def test_mensaje_2_de_mem_sin_okani_ni_williamson():
    hs = build_game_highlights(_hist(), home_team_id=MEM, away_team_id=UTA,
                               statuses_by_tid={}, player_map={MORANT: "Ja Morant"},
                               target_date=TARGET, roster_signals=_senales())
    assert [(h.player_id, h.team) for h in hs] == [(MORANT, "home")]


def test_fila_oficial_de_la_temporada_basta_sin_senal():
    hist = pd.concat([_hist(),
                      _filas(JUGADOR_2026, MEM, 1, minutes=20.0, desde="2026-10-21",
                             gid0=900, prefijo="00226"),
                      _filas(JUGADOR_2026, MEM, 3, minutes=20.0, desde="2026-04-05",
                             gid0=950)], ignore_index=True)
    cands = rank_team_candidates(hist, MEM, {}, target_date=TARGET,
                                 roster_signals=_senales())
    assert JUGADOR_2026 in _ids(cands)


def test_senal_de_la_temporada_pasada_no_cuenta():
    """Un PDF de abril de 2026 es temporada 2025-26: no prueba que siga."""
    vieja = _senal(OKANI, MEM, "2026-04-10", source="injury_report", ref="2026-04-10_01_00PM")
    vistos = observed_this_season(_hist(), pd.concat([_senales(), vieja]), TARGET)
    assert OKANI not in vistos and MORANT in vistos


def test_ingram_con_solo_senales_sigue_siendo_candidato_de_lac():
    hist = pd.concat([_hist(), _filas(INGRAM, TOR, 10, minutes=33.0, gid0=400)],
                     ignore_index=True)
    assert INGRAM in _ids(rank_team_candidates(hist, LAC, {}, target_date=TARGET,
                                               roster_signals=_senales()))


def test_sin_senales_el_filtro_no_se_aplica():
    """Fallo de carga de roster_signals: degrada a D-PRE-6, no vacía el mensaje."""
    assert OKANI in _ids(rank_team_candidates(_hist(), MEM, {}, target_date=TARGET))


def test_senales_vacias_el_filtro_no_se_aplica():
    vacio = _senales().iloc[0:0]
    assert OKANI in _ids(rank_team_candidates(_hist(), MEM, {}, target_date=TARGET,
                                              roster_signals=vacio))


def test_sin_target_date_el_filtro_no_se_aplica():
    assert OKANI in _ids(rank_team_candidates(_hist(), MEM, {}, roster_signals=_senales()))
