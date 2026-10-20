"""D-PRE-6 — destacados por EQUIPO ACTUAL, con los últimos 10 de cualquier equipo.

Caso real que lo destapó (día cero, 2026-10-20): Jaylen Brown, traspasado de
BOS a PHI en el verano, salió como destacado de Boston. La regla vieja ("su
último partido CON ESTE equipo dentro de 250 días") lo habría mantenido como
destacado de BOS hasta el 2026-12-16: sus 10 partidos con Boston nunca se
renuevan porque ya no juega ahí.

Regla nueva (adjudicada por Antonio):
  - equipo actual = equipo de su fila MÁS RECIENTE en la liga (cualquier fila,
    también de 0 minutos: fila = activado, Decisión 9);
  - estadísticas = sus ÚLTIMOS 10 partidos jugados con CUALQUIER equipo.
Así un traspasado aparece con su equipo nuevo y sus números reales recientes.

LÍMITE CONOCIDO, fijado aquí para que no se olvide: antes de su primera fila
con el equipo nuevo, el sistema no puede saber que cambió de equipo.

Ids reales: BOS 1610612738, PHI 1610612755; Brown 1627759, Maxey 1630178.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from nba_predictor.api.player_highlights import build_game_highlights, rank_team_candidates

BOS, PHI, NYK = 1610612738, 1610612755, 1610612752
BROWN, MAXEY, PRITCHARD = 1627759, 1630178, 1630202
NOMBRES = {BROWN: "Jaylen Brown", MAXEY: "Tyrese Maxey", PRITCHARD: "Payton Pritchard"}


def _filas(pid, tid, n, *, minutes=30.0, pts=20.0, desde="2026-03-30", gid0=0):
    fechas = pd.date_range(desde, periods=n, freq="D")
    return pd.DataFrame({
        "player_id": [pid] * n,
        "team_id": [tid] * n,
        "game_id": [f"00225{gid0 + i:05d}" for i in range(n)],
        "game_date": fechas,
        "minutes": [minutes] * n,
        "pts": [pts] * n,
        "reb": [5.0] * n,
        "ast": [4.0] * n,
        "fg3m": [2.0] * n,
    })


def _verano():
    """Fin de 2025-26: Brown y Pritchard en BOS, Maxey en PHI."""
    return pd.concat([
        _filas(BROWN, BOS, 10, minutes=35.0, pts=29.0),
        _filas(PRITCHARD, BOS, 10, minutes=30.0, pts=15.0, gid0=100),
        _filas(MAXEY, PHI, 10, minutes=34.0, pts=24.0, gid0=200),
    ], ignore_index=True)


def _debut_brown_en_phi(minutes=36.0):
    return _filas(BROWN, PHI, 1, minutes=minutes, pts=31.0, desde="2026-10-20", gid0=600)


def _ids(cands):
    return [c["player_id"] for c in cands]


class TestTraspaso:
    def test_tras_su_debut_brown_ya_no_es_de_boston(self):
        hist = pd.concat([_verano(), _debut_brown_en_phi()], ignore_index=True)
        assert _ids(rank_team_candidates(hist, BOS, {}, target_date=date(2026, 10, 21))) == [
            PRITCHARD]

    def test_tras_su_debut_brown_es_candidato_de_phi_con_sus_ultimos_10(self):
        hist = pd.concat([_verano(), _debut_brown_en_phi()], ignore_index=True)
        cands = rank_team_candidates(hist, PHI, {}, target_date=date(2026, 10, 21))
        brown = next(c for c in cands if c["player_id"] == BROWN)
        # Ventana = 9 partidos con BOS + el debut con PHI: los ÚLTIMOS 10, de cualquier equipo.
        assert brown["games_in_window"] == 10
        assert brown["pts_max"] == 31.0          # el partido con PHI está dentro
        assert brown["minutes_rolling"] > 34.0   # le gana a Maxey por minutos
        assert _ids(cands) == [BROWN, MAXEY]

    def test_activado_sin_minutos_ya_cuenta_para_el_equipo_nuevo(self):
        """Fila de 0 minutos = activado con PHI: ya es de PHI; sus stats salen de BOS."""
        hist = pd.concat([_verano(), _debut_brown_en_phi(minutes=0.0)], ignore_index=True)
        d = date(2026, 10, 21)
        assert BROWN not in _ids(rank_team_candidates(hist, BOS, {}, target_date=d))
        brown = next(c for c in rank_team_candidates(hist, PHI, {}, target_date=d)
                     if c["player_id"] == BROWN)
        assert brown["games_in_window"] == 10 and brown["pts_max"] == 29.0

    def test_mensaje_de_phi_lo_publica_con_su_equipo_nuevo(self):
        hist = pd.concat([_verano(), _debut_brown_en_phi()], ignore_index=True)
        hs = build_game_highlights(hist, home_team_id=NYK, away_team_id=PHI,
                                   statuses_by_tid={}, player_map=NOMBRES,
                                   target_date=date(2026, 10, 22))
        assert [(h.player_name, h.team) for h in hs] == [("Jaylen Brown", "away")]

    def test_limite_conocido_antes_del_debut_sigue_en_su_equipo_viejo(self):
        """Sin fila con PHI no hay señal de traspaso: este es el hueco declarado."""
        assert _ids(rank_team_candidates(_verano(), BOS, {}, target_date=date(2026, 10, 20)))[
            0] == BROWN


class TestReglaGeneral:
    def test_la_recencia_se_mide_sobre_la_fila_mas_reciente(self):
        viejo = _filas(7, BOS, 10, desde="2016-01-01")
        assert rank_team_candidates(viejo, BOS, {}, target_date=date(2026, 10, 21)) == []

    def test_desempate_del_mismo_dia_por_game_id(self):
        """Dos filas el mismo día: manda la de game_id mayor, no el orden de entrada."""
        a = _filas(9, BOS, 5, desde="2026-04-01", gid0=10)
        b = _filas(9, PHI, 1, desde="2026-04-05", gid0=999)   # mismo día que la 5a de BOS
        for hist in (pd.concat([a, b]), pd.concat([b, a])):
            assert _ids(rank_team_candidates(hist, PHI, {}, target_date=date(2026, 10, 21))) == [9]
            assert rank_team_candidates(hist, BOS, {}, target_date=date(2026, 10, 21)) == []

    def test_excluido_por_estatus_en_su_equipo_nuevo(self):
        hist = pd.concat([_verano(), _debut_brown_en_phi()], ignore_index=True)
        cands = rank_team_candidates(hist, PHI, {BROWN: "Out"}, target_date=date(2026, 10, 21))
        assert _ids(cands) == [MAXEY]
