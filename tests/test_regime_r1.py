"""D-PRE-11 + R1 de puntos (adjudicación de D-EXP-7, 2026-10-25).

- Régimen de arranque: los primeros 21 días desde el primer partido 002 de la
  temporada (día 0 = ese partido), centro C3; después B0. 2026-27 empezó el
  2026-10-20: régimen del 20-oct al 9-nov, R1 desde el 10-nov.
- Fuera del régimen, el rango de PUNTOS es R1 al 80% con la constante calibrada
  en config; se comprueba contra el cálculo del experimento en 5 filas REALES de
  la muestra de auditoría (tests/fixtures/d7_r1_audit_rows.json).
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nba_predictor.api.daily_predictions import (
    PLAYERS_MESSAGE_BUDGET,
    AvailabilityFlag,
    DailyResult,
    GamePrediction,
    PlayerHighlight,
    _attach_highlights,
    format_players_message,
)
from nba_predictor.api.player_highlights import (
    MODE_R1,
    MODE_REGIMEN,
    highlight_mode,
    r1_pts_range,
    rank_team_candidates,
    regime_center,
)
from nba_predictor.config import PTS_R1_Q_SCALED, PTS_R2_Q_ABS

FIXTURE = Path(__file__).parent / "fixtures" / "d7_r1_audit_rows.json"
INICIO_2026 = date(2026, 10, 20)


# ── Régimen: fecha dentro, fuera y tope exacto ──

@pytest.mark.parametrize("d, esperado", [
    (date(2026, 10, 20), MODE_REGIMEN),   # día 0
    (date(2026, 10, 26), MODE_REGIMEN),   # día 6 (hoy, al escribir esto)
    (date(2026, 11, 9), MODE_REGIMEN),    # día 20: el 21.er día, último del régimen
    (date(2026, 11, 10), MODE_R1),        # día 21: primero fuera
    (date(2027, 1, 15), MODE_R1),
])
def test_modo_por_fecha(d, esperado):
    assert highlight_mode(d, INICIO_2026) == esperado


# ── Centro C3 ──

def _jugados(vals_prev, vals_act, *, desde_prev="2026-03-01", desde_act="2026-10-20"):
    filas = []
    for vals, desde in ((vals_prev, desde_prev), (vals_act, desde_act)):
        for i, v in enumerate(vals):
            filas.append({"game_date": pd.Timestamp(desde) + pd.Timedelta(days=i),
                          "pts": float(v), "reb": 5.0, "ast": 4.0, "fg3m": 2.0,
                          "minutes": 30.0})
    return pd.DataFrame(filas)


def test_c3_shrinkage_con_dos_de_la_temporada():
    c = regime_center(_jugados([10.0] * 12, [20.0, 20.0]), date(2026, 10, 25))
    assert c["pts_median"] == pytest.approx((2 * 20 + 5 * 10) / 7)


def test_c3_sin_partidos_de_la_temporada_es_la_media_anterior():
    c = regime_center(_jugados([10.0, 30.0] * 6, []), date(2026, 10, 20))
    assert c["pts_median"] == pytest.approx(20.0)


def test_c3_con_10_de_la_temporada_es_la_media_de_los_ultimos_10():
    act = [float(i) for i in range(12)]
    c = regime_center(_jugados([50.0] * 10, act), date(2026, 11, 8))
    assert c["pts_median"] == pytest.approx(np.mean(act[-10:]))


def test_c3_sin_temporada_anterior_usa_la_media_de_los_ultimos():
    c = regime_center(_jugados([], [10.0, 20.0, 30.0]), date(2026, 10, 30))
    assert c["pts_median"] == pytest.approx(20.0)


# ── R1 de puntos ──

def test_r1_contra_el_experimento_en_5_filas_reales():
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fx["q1"] == pytest.approx(PTS_R1_Q_SCALED, abs=1e-6)
    assert fx["q2"] == pytest.approx(PTS_R2_Q_ABS)
    for f in fx["filas"]:
        x = np.array(f["prev10_pts"], dtype=float)
        assert float(np.median(x)) == pytest.approx(f["B0_pts"])
        assert float(np.mean(np.abs(x - x.mean()))) == pytest.approx(f["disp_pts"])
        played = pd.DataFrame({"game_date": pd.date_range("2026-01-01", periods=10),
                               "pts": x})
        r = r1_pts_range(played)
        assert r["pts_min"] == max(0, math.ceil(f["r1_lo"])), f["jugador"]
        assert r["pts_max"] == math.floor(f["r1_hi"]), f["jugador"]


def test_r1_enteros_contienen_exactamente_los_mismos_puntos():
    """[ceil(lo), floor(hi)] y [lo, hi] contienen los mismos enteros."""
    played = pd.DataFrame({"game_date": pd.date_range("2026-01-01", periods=10),
                           "pts": [12, 30, 25, 18, 22, 15, 28, 20, 24, 19]})
    x = played["pts"].to_numpy(float)
    c, d = np.median(x), np.mean(np.abs(x - x.mean()))
    lo, hi = c - PTS_R1_Q_SCALED * d, c + PTS_R1_Q_SCALED * d
    r = r1_pts_range(played)
    for y in range(0, 60):
        assert (lo <= y <= hi) == (r["pts_min"] <= y <= r["pts_max"])


def test_r1_dispersion_cero_cae_a_r2():
    played = pd.DataFrame({"game_date": pd.date_range("2026-01-01", periods=10),
                           "pts": [20.0] * 10})
    assert r1_pts_range(played) == {"pts_min": math.ceil(20 - PTS_R2_Q_ABS),
                                    "pts_max": math.floor(20 + PTS_R2_Q_ABS)}


def test_r1_se_acota_en_cero():
    played = pd.DataFrame({"game_date": pd.date_range("2026-01-01", periods=10),
                           "pts": [0, 0, 0, 0, 0, 1, 2, 9, 12, 15]})
    assert r1_pts_range(played)["pts_min"] == 0


def test_r1_no_aplica_con_menos_de_10():
    played = pd.DataFrame({"game_date": pd.date_range("2026-01-01", periods=7),
                           "pts": [10.0] * 7})
    assert r1_pts_range(played) is None


# ── Integración en la selección: el modo cambia lo publicado, no el orden ──

def _hist():
    filas = []
    for pid, minutos, base in ((1, 34.0, 25.0), (2, 30.0, 12.0)):
        for i in range(12):
            filas.append({"player_id": pid, "team_id": 100, "game_id": f"0022500{pid}{i:02d}",
                          "game_date": pd.Timestamp("2026-03-20") + pd.Timedelta(days=i),
                          "minutes": minutos, "pts": base + 5 * (i % 5), "reb": 5.0,
                          "ast": 3.0, "fg3m": 1.0})
    return pd.DataFrame(filas)


def test_modo_no_cambia_el_orden_y_si_lo_publicado():
    T = date(2026, 10, 25)
    base = rank_team_candidates(_hist(), 100, {}, target_date=T)
    reg = rank_team_candidates(_hist(), 100, {}, target_date=T, mode=MODE_REGIMEN)
    r1 = rank_team_candidates(_hist(), 100, {}, target_date=T, mode=MODE_R1)
    assert [c["player_id"] for c in base] == [c["player_id"] for c in reg] \
        == [c["player_id"] for c in r1] == [1, 2]
    # régimen: centro C3 (sin partidos de 2026-27 = media de la temporada previa)
    assert reg[0]["pts_median"] != base[0]["pts_median"]
    assert (reg[0]["pts_min"], reg[0]["pts_max"]) == (base[0]["pts_min"], base[0]["pts_max"])
    # r1: centro B0 intacto, rango de puntos distinto de [mín, máx]
    assert r1[0]["pts_median"] == base[0]["pts_median"]
    assert (r1[0]["pts_min"], r1[0]["pts_max"]) != (base[0]["pts_min"], base[0]["pts_max"])
    assert r1[0]["reb_median"] == base[0]["reb_median"]


# ── Modo del día desde el calendario jugado ──

class _Store:
    def __init__(self, fechas_2026):
        self.fechas = fechas_2026

    def load_games(self):
        h = _hist()
        g = pd.DataFrame({"game_id": h["game_id"], "game_date": h["game_date"]}).drop_duplicates()
        nuevos = pd.DataFrame({"game_id": [f"00226{i:05d}" for i in range(len(self.fechas))],
                               "game_date": pd.to_datetime(self.fechas)})
        return pd.concat([g, nuevos], ignore_index=True)

    def load_player_game_stats(self, season=None):
        h = _hist()
        out = h.drop(columns=["game_date", "pts", "reb"]).assign(fgm=10, ftm=3, oreb=1, dreb=4)
        return out if season == "2025-26" else out.iloc[0:0]

    def load_roster_signals(self, start_date=None, end_date=None):
        raise RuntimeError("sin señales en este test")


def _gp():
    from nba_predictor.ingestion.future_schedule import ScheduledGame

    sg = ScheduledGame(game_id="0022600099", game_date=date(2026, 11, 10), home_team_id=100,
                       away_team_id=200, home_tricode="AAA", away_tricode="BBB",
                       season="2026-27")
    gp = GamePrediction(home_tricode="AAA", away_tricode="BBB", game_date="2026-11-10",
                        probability_home=0.5, home_absences=[], away_absences=[],
                        availability_flag=AvailabilityFlag.OK, model_version="v",
                        game_id="0022600099")
    return sg, gp


@pytest.mark.parametrize("target, esperado", [
    (date(2026, 11, 9), MODE_REGIMEN), (date(2026, 11, 10), MODE_R1)])
def test_attach_devuelve_el_modo_desde_el_primer_partido_002(target, esperado):
    sg, gp = _gp()
    modo, previa = _attach_highlights(store=_Store(["2026-10-20", "2026-10-21"]),
                                      target_date=target, scheduled=[sg], game_preds=[gp],
                                      statuses_by_tid={}, player_map={1: "A", 2: "B"})
    assert (modo, previa) == (esperado, "2025-26")


def test_sin_partidos_de_la_temporada_el_dia_es_el_dia_0():
    sg, gp = _gp()
    modo, _ = _attach_highlights(store=_Store([]), target_date=date(2026, 10, 20),
                                 scheduled=[sg], game_preds=[gp], statuses_by_tid={},
                                 player_map={1: "A", 2: "B"})
    assert modo == MODE_REGIMEN


# ── Títulos y presupuesto de longitud ──

def _ph(nombre, team, rank, **kw):
    base = dict(pts_median=24.0, pts_min=11.0, pts_max=35.0, reb_median=7.0,
                ast_median=5.0, games_in_window=10, player_id=rank)
    base.update(kw)
    return PlayerHighlight(player_name=nombre, team=team, rank=rank, **base)


def _res(games, modo, previa="2025-26"):
    return DailyResult(target_date="2026-11-02", games=games, feed_down=False,
                       feed_down_reason=None, model_version="v1",
                       highlights_mode=modo, highlights_prev_season=previa)


def _gp_fmt(i, players):
    return GamePrediction(home_tricode="PHI", away_tricode="POR", game_date="2026-11-02",
                          probability_home=0.6, home_absences=[], away_absences=[],
                          availability_flag=AvailabilityFlag.OK, model_version="v1",
                          tip_off_cdmx=f"{17 + i // 4}:{(i % 4) * 15:02d} CDMX",
                          game_id=f"00226{i:05d}", players=players,
                          players_data_available=True)


@pytest.mark.parametrize("modo, titulo", [
    (MODE_REGIMEN, "🏀 Destacados · 2 nov 2026 (inicio de temporada: mezcla con 2025-26)"),
    (MODE_R1, "🏀 Destacados · 2 nov 2026 (últimos 10 partidos · rango de puntos al 80%)"),
    (None, "🏀 Destacados · 2 nov 2026 (últimos 10 partidos)"),
])
def test_titulo_por_modo(modo, titulo):
    msg = format_players_message(_res([_gp_fmt(0, [_ph("A Uno", "home", 1)])], modo))
    assert msg.splitlines()[0] == titulo


def test_presupuesto_dia_lleno_con_titulos_nuevos():
    """15 partidos, 2 por equipo, nombres de largo típico: medido con cada título."""
    games = [_gp_fmt(i, [_ph(f"Jugador Nom{i:02d}{t}{r}", t, r)
                         for t in ("home", "away") for r in (1, 2)]) for i in range(15)]
    largos = {m: len(format_players_message(_res(games, m))) for m in (None, MODE_REGIMEN, MODE_R1)}
    assert largos[None] == 3_752
    assert largos[MODE_REGIMEN] == 3_752 + 20
    assert largos[MODE_R1] == 3_752 + 25
    assert largos[MODE_R1] <= PLAYERS_MESSAGE_BUDGET          # cabe, con 23 de margen
