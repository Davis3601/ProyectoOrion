"""D-PROD-2 — línea 50/50, split con/sin, escalera, marcador semanal y activación.

Diseño sellado en CLAUDE.md (2026-10-29T02:30:32Z); la línea .5 verificada en
D-PROD-2a (50.10%, 6/6 folds en banda). Lo que estos tests fijan:
  - antes de DPROD2_START_DATE el mensaje 2 es BYTE A BYTE el de v21 (golden
    generado con el código del tag api-v21);
  - la escalera nueva con nombres REALES de los días de 13, 14 y 15 partidos;
  - las reglas del split (uno solo, Out, equipo actual, llegada, 5 y 5);
  - que ningún valor usa el partido del día (leakage);
  - el marcador: lunes, mínimo 50 por métrica, pasa al siguiente día.
"""
from __future__ import annotations

import inspect
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from nba_predictor.api import daily_predictions as dp
from nba_predictor.api import weekly_scoreboard as ws
from nba_predictor.api.daily_predictions import (
    PLAYERS_MESSAGE_BUDGET,
    AvailabilityFlag,
    DailyResult,
    GamePrediction,
    PlayerHighlight,
    PlayerSplit,
    apply_players_length_budget,
    format_players_message,
)
from nba_predictor.api.player_highlights import (
    MODE_R1,
    MODE_REGIMEN,
    choose_split,
    pts_line,
    rank_team_candidates,
    split_window_stats,
)
from nba_predictor.config import DPROD2_START_DATE

FIX = Path(__file__).parent / "fixtures"
DIAS = json.loads((FIX / "dprod2_budget_days.json").read_text(encoding="utf-8"))["dias"]
GOLDEN_V21 = json.loads((FIX / "dprod2_golden_v21.json").read_text(encoding="utf-8"))["dias"]
D13, D14, D15 = "2026-12-16", "2027-04-09", "2026-12-23"


# ---------------------------------------------------------------------------
# Construcción de un día real del fixture
# ---------------------------------------------------------------------------

def _dia(fecha: str, *, dprod2: bool, split_p: float = 0.0, q_rank1: bool = False,
         scoreboard: str | None = None) -> DailyResult:
    """Día real del fixture. split_p = fracción de los rank 1 (en orden del
    mensaje) que llevan split, con el rank 2 de su equipo como compañero — la
    misma convención del presupuesto sellado en D-PROD-2."""
    games, rank1 = [], []
    for g in DIAS[fecha]:
        players = []
        for p in g["players"]:
            ph = PlayerHighlight(**p)
            if dprod2 and ph.games_in_window == 10 and ph.pts_min is not None:
                ph.pts_line = pts_line(ph.pts_median)
            if q_rank1 and ph.rank == 1:
                ph.status = "Q"
            players.append(ph)
        gp = GamePrediction(home_tricode=g["home"], away_tricode=g["away"], game_date=fecha,
                            probability_home=0.5, home_absences=[], away_absences=[],
                            availability_flag=AvailabilityFlag.OK, model_version="x",
                            game_id=g["game_id"], players=players,
                            players_data_available=bool(players))
        games.append(gp)
        for lado in ("home", "away"):
            del_lado = [p for p in players if p.team == lado]
            rank1 += [(p, del_lado) for p in del_lado if p.rank == 1]
    if dprod2:
        for p, lado in rank1[:round(split_p * len(rank1))]:
            mate = next((x for x in lado if x.rank == 2), p)
            p.split = PlayerSplit(mate_id=1, mate_name=mate.player_name, n_with=14,
                                  pts_with=24.0, n_without=6, pts_without=29.0)
    return DailyResult(target_date=fecha, games=games, feed_down=False, feed_down_reason=None,
                       model_version="x", highlights_mode="r1",
                       highlights_prev_season="2025-26", highlights_dprod2=dprod2,
                       scoreboard=scoreboard)


# ---------------------------------------------------------------------------
# Antes de la activación: v21 byte a byte
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fecha", [D13, D14, D15])
def test_sin_dprod2_el_mensaje_2_es_byte_a_byte_el_de_v21(fecha):
    """El golden lo produjo el código del tag api-v21 sobre el mismo fixture."""
    r = _dia(fecha, dprod2=False)
    apply_players_length_budget(r)
    assert r.highlights_truncated == GOLDEN_V21[fecha]["truncated"]
    assert format_players_message(r) == GOLDEN_V21[fecha]["message"]


def test_activacion_por_fecha_en_attach(monkeypatch):
    """11-nov (R1, antes de la constante): sin línea, sin split, sin marcador."""
    assert DPROD2_START_DATE == date(2026, 11, 12)
    store = _StoreAttach(outs={})
    for target, activo in ((date(2026, 11, 11), False), (date(2026, 11, 12), True)):
        sg, gp = _sg_gp(target)
        extra: dict = {}
        modo, _ = dp._attach_highlights(store=store, target_date=target, scheduled=[sg],
                                        game_preds=[gp], statuses_by_tid={},
                                        player_map=_NOMBRES, extra=extra,
                                        tricode_by_tid={100: "AAA", 200: "BBB"})
        assert modo == MODE_R1
        assert extra["dprod2"] is activo
        assert gp.players
        completos = [p for p in gp.players if p.games_in_window == 10]
        assert completos
        assert all((p.pts_line is not None) is activo for p in completos)
        assert all(p.pts_line is None for p in gp.players if p.games_in_window < 10)


# ---------------------------------------------------------------------------
# Línea .5
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mediana, linea", [(28.0, 27.5), (27.0, 26.5), (27.5, 27.5),
                                            (0.0, -0.5), (9.5, 9.5)])
def test_linea_es_ceil_mediana_menos_medio(mediana, linea):
    assert pts_line(mediana) == linea


def _hist_basico(n=12, pid=1, team=100, mins=30.0, desde="2026-09-01", pts=20):
    fechas = pd.date_range(desde, periods=n, freq="2D")
    return pd.DataFrame({"game_id": [f"g{pid}_{i}" for i in range(n)], "player_id": pid,
                         "team_id": team, "game_date": fechas, "minutes": mins,
                         "pts": pts, "reb": 5, "ast": 3, "fg3m": 1})


def test_linea_solo_con_r1_ventana_completa_y_activada():
    h = pd.concat([_hist_basico(12, 1), _hist_basico(6, 2, mins=25.0)], ignore_index=True)
    T = date(2026, 11, 20)
    con = {c["player_id"]: c for c in rank_team_candidates(h, 100, {}, target_date=T,
                                                           mode=MODE_R1, line=True)}
    assert con[1]["pts_line"] == 19.5                   # mediana 20 -> 19.5
    assert "pts_line" not in con[2]                     # ventana de 6: sin línea
    sin = rank_team_candidates(h, 100, {}, target_date=T, mode=MODE_R1, line=False)
    assert all("pts_line" not in c for c in sin)        # no activada
    reg = rank_team_candidates(h, 100, {}, target_date=T, mode=MODE_REGIMEN, line=True)
    assert all("pts_line" not in c for c in reg)        # régimen: jamás


def test_formato_de_la_linea_y_del_titulo():
    r = _dia(D13, dprod2=True)
    msg = format_players_message(r)
    assert msg.splitlines()[0] == (
        "🏀 Destacados · 16 dic 2026 (últimos 10 · línea 50/50 y rango de puntos al 80%)")
    lineas = [x for x in msg.splitlines() if x.startswith("• ")]
    con_rango = [x for x in lineas if "(últimos" not in x]
    assert con_rango and all(".5 pts" in x for x in con_rango)
    cortas = [x for x in lineas if "(últimos" in x]      # ventana corta: entero, sin línea
    assert all(".5 pts" not in x for x in cortas)


# ---------------------------------------------------------------------------
# Split con/sin: reglas
# ---------------------------------------------------------------------------

def _juego(gid, fecha, filas):
    return [{"game_id": gid, "game_date": pd.Timestamp(fecha), "player_id": p, "team_id": t,
             "minutes": m, "pts": pts, "reb": 4, "ast": 2, "fg3m": 1} for p, t, m, pts in filas]


def _hist_split(n_con=6, n_sin=6, mate=50, mate_min=30.0, team=100, otro=None, pre=0,
                mate_pts_rows=True):
    """Destacado 1 (equipo 100). Compañero `mate`: llega en el partido `pre`, juega
    n_con partidos con él y falta n_sin. `otro`: partidos extra del destacado con
    OTRO equipo (no cuentan)."""
    filas, d = [], pd.Timestamp("2026-10-01")
    for i in range(pre):                                 # antes de que llegue el compañero
        filas += _juego(f"p{i}", d, [(1, team, 32.0, 10)])
        d += timedelta(days=1)
    for i in range(n_con):
        filas += _juego(f"c{i}", d, [(1, team, 32.0, 20), (mate, team, mate_min, 15)])
        d += timedelta(days=1)
    for i in range(n_sin):
        extra = [(mate, team, 0.0, 0)] if (mate_pts_rows and i == 0) else []
        filas += _juego(f"s{i}", d, [(1, team, 32.0, 30)] + extra)
        d += timedelta(days=1)
    for i in range(otro or 0):
        filas += _juego(f"o{i}", d - timedelta(days=60 + i), [(1, 999, 32.0, 50)])
    return pd.DataFrame(filas)


_NOMBRES = {1: "Uno Destacado", 2: "Dos Destacado", 50: "Cincuenta Compañero",
            60: "Sesenta Compañero", 40: "Cuarenta Compañero"}


def test_split_basico_con_y_sin():
    st = split_window_stats(_hist_split(), 1, 100, 50)
    assert st == {"n_with": 6, "pts_with": 20.0, "n_without": 6, "pts_without": 30.0}
    s = choose_split(_hist_split(), 1, 100, {50: "Out"}, _NOMBRES)
    assert s["mate_id"] == 50 and s["mate_name"] == "Cincuenta Compañero"


@pytest.mark.parametrize("estatus", ["Doubtful", "Questionable", "Probable", "Available"])
def test_split_solo_con_out(estatus):
    assert choose_split(_hist_split(), 1, 100, {50: estatus}, _NOMBRES) is None


def test_split_exige_cinco_y_cinco():
    assert choose_split(_hist_split(n_sin=4), 1, 100, {50: "Out"}, _NOMBRES) is None
    assert choose_split(_hist_split(n_con=4), 1, 100, {50: "Out"}, _NOMBRES) is None
    assert choose_split(_hist_split(n_con=5, n_sin=5), 1, 100, {50: "Out"}, _NOMBRES)


def test_split_exige_rolling_de_25():
    assert choose_split(_hist_split(mate_min=24.0), 1, 100, {50: "Out"}, _NOMBRES) is None


def test_split_solo_partidos_con_el_equipo_actual():
    """Partidos del destacado con otro equipo no entran ni al con ni al sin."""
    st = split_window_stats(_hist_split(otro=8), 1, 100, 50)
    assert (st["n_with"], st["n_without"]) == (6, 6)


def test_split_sin_cuenta_desde_la_llegada():
    """Los 5 partidos previos a la llegada del compañero no son 'sin él'."""
    st = split_window_stats(_hist_split(n_sin=3, pre=5), 1, 100, 50)
    assert st["n_without"] == 3
    assert choose_split(_hist_split(n_sin=3, pre=5), 1, 100, {50: "Out"}, _NOMBRES) is None


def test_split_fila_de_cero_minutos_cuenta_como_sin():
    st = split_window_stats(_hist_split(), 1, 100, 50)       # s0 trae fila de 0 min
    assert st["n_without"] == 6


def test_split_ventana_de_20():
    st = split_window_stats(_hist_split(n_con=15, n_sin=10), 1, 100, 50)
    assert st["n_with"] + st["n_without"] == 20 and st["n_without"] == 10


def test_split_uno_solo_mayor_rolling_y_desempate():
    h = _hist_split(mate=50, mate_min=30.0)
    h2 = _hist_split(mate=60, mate_min=34.0)
    h2 = h2[h2["player_id"] == 60]
    h3 = _hist_split(mate=40, mate_min=30.0)
    h3 = h3[h3["player_id"] == 40]
    h = pd.concat([h, h2, h3], ignore_index=True)
    assert choose_split(h, 1, 100, {50: "Out", 60: "Out", 40: "Out"}, _NOMBRES)["mate_id"] == 60
    # Sin el de mayor rolling: empate 50 vs 40 a 30 min -> gana el player_id menor.
    assert choose_split(h, 1, 100, {50: "Out", 40: "Out"}, _NOMBRES)["mate_id"] == 40


def test_split_sin_nombre_no_se_publica():
    assert choose_split(_hist_split(), 1, 100, {50: "Out"}, {1: "Uno"}) is None


def test_formato_de_la_sublinea():
    s = PlayerSplit(mate_id=50, mate_name="Jamal Murray", n_with=14, pts_with=23.6,
                    n_without=6, pts_without=28.5)
    assert dp._format_split(s) == "  ↳ sin Jamal Murray: 29 pts en 6 · con: 24 en 14"


# ---------------------------------------------------------------------------
# Leakage: ningún valor usa el partido del día
# ---------------------------------------------------------------------------

class _StoreAttach:
    """Store mínimo para _attach_highlights. `del_dia` añade una fila del propio
    target_date con valores extremos: si algo la usara, cambiaría el resultado."""

    def __init__(self, outs, del_dia: date | None = None, log_rows=None):
        self.outs, self.del_dia, self.log_rows = outs, del_dia, log_rows or []

    def _pgs(self):
        h = _hist_split()
        h = pd.concat([h, _hist_basico(12, 2, mins=28.0, desde="2026-10-01")],
                      ignore_index=True)
        if self.del_dia is not None:
            h = pd.concat([h, pd.DataFrame(_juego(
                "HOY", self.del_dia, [(1, 100, 48.0, 99), (2, 100, 48.0, 99)]))],
                ignore_index=True)
        return h

    def load_games(self):
        h = self._pgs()
        g = h[["game_id", "game_date"]].drop_duplicates()
        g = pd.concat([g, pd.DataFrame({"game_id": ["0022600001"],
                                        "game_date": [pd.Timestamp("2026-10-20")]})])
        return g

    def load_player_game_stats(self, season=None):
        h = self._pgs().drop(columns=["game_date", "reb"])
        # derive_box_stats recalcula pts = 2*fgm + fg3m + ftm.
        h = h.assign(fgm=(h["pts"] - h["fg3m"]) / 2, ftm=0, oreb=1, dreb=3)
        return h.drop(columns=["pts"]) if season == "2026-27" else h.iloc[0:0].drop(
            columns=["pts"])

    def load_roster_signals(self, start_date=None, end_date=None):
        raise RuntimeError("sin señales")

    def load_player_predictions_log(self, start_date, end_date):
        return self.log_rows


def _sg_gp(target):
    from nba_predictor.ingestion.future_schedule import ScheduledGame

    sg = ScheduledGame(game_id="0022699999", game_date=target, home_team_id=100,
                       away_team_id=200, home_tricode="AAA", away_tricode="BBB",
                       season="2026-27")
    gp = GamePrediction(home_tricode="AAA", away_tricode="BBB", game_date=target.isoformat(),
                        probability_home=0.5, home_absences=[], away_absences=[],
                        availability_flag=AvailabilityFlag.OK, model_version="v",
                        game_id="0022699999")
    return sg, gp


def _attach(store, target):
    sg, gp = _sg_gp(target)
    extra: dict = {}
    dp._attach_highlights(store=store, target_date=target, scheduled=[sg], game_preds=[gp],
                          statuses_by_tid={100: {50: "Out"}}, player_map=_NOMBRES,
                          extra=extra, tricode_by_tid={100: "AAA", 200: "BBB"})
    return gp, extra


def test_ningun_valor_usa_el_partido_del_dia():
    T = date(2026, 11, 16)
    limpio, _ = _attach(_StoreAttach(outs={}), T)
    sucio, _ = _attach(_StoreAttach(outs={}, del_dia=T), T)
    assert limpio.players and limpio.players[0].split is not None
    assert [vars(p) for p in limpio.players] == [vars(p) for p in sucio.players]


def test_el_endpoint_jamas_fuerza_modo_ni_activacion():
    from nba_predictor.api import server

    fuente = inspect.getsource(server.predictions_today)
    assert "force_mode" not in fuente and "force_dprod2" not in fuente


# ---------------------------------------------------------------------------
# Escalera con nombres reales (días de 13, 14 y 15 partidos)
# ---------------------------------------------------------------------------

def test_escalera_dia_de_13_formato_completo():
    r = _dia(D13, dprod2=True, split_p=0.2)
    assert not apply_players_length_budget(r)
    msg = format_players_message(r)
    assert r.highlights_truncated is None and "↳ sin " in msg
    assert len(msg) <= PLAYERS_MESSAGE_BUDGET


def test_escalera_dia_de_14_sin_splits_con_pie():
    r = _dia(D14, dprod2=True, split_p=0.2)
    assert apply_players_length_budget(r)
    msg = format_players_message(r)
    assert r.highlights_truncated == "sin_splits" and r.splits_dropped
    assert "↳ sin " not in msg and msg.endswith("\n\n(sin splits hoy por longitud)")
    assert not r.players_trimmed and len(msg) <= PLAYERS_MESSAGE_BUDGET


def test_escalera_dia_de_15_top1_con_formato_completo():
    r = _dia(D15, dprod2=True, split_p=0.2)
    assert apply_players_length_budget(r)
    msg = format_players_message(r)
    assert r.highlights_truncated == "top1" and r.players_trimmed and not r.splits_dropped
    assert "↳ sin " in msg                                  # los splits vuelven
    assert "(sin splits hoy por longitud)" not in msg
    assert all(p.rank == 1 for gp in r.games for p in gp.players)
    assert len(msg) <= PLAYERS_MESSAGE_BUDGET


def test_borde_cota_pesimista_dia_de_14_sin_splits():
    """Cota pesimista del presupuesto sellado: 50% de splits y todos los rank 1
    con Q. El día de 14 sin splits queda pegado al umbral: se fija el número.

    DESVIACIÓN DECLARADA: el presupuesto sellado daba 3 799 (a 1 carácter). La
    simulación puso el .5 también en las 5 líneas de ventana corta de ese día,
    que por regla no llevan línea: 5 x 2 = 10 caracteres menos, 3 789."""
    r = _dia(D14, dprod2=True, split_p=0.5, q_rank1=True)
    apply_players_length_budget(r)
    msg = format_players_message(r)
    assert r.highlights_truncated == "sin_splits"
    assert len(msg) == BORDE_14_PESIMISTA
    assert PLAYERS_MESSAGE_BUDGET - len(msg) <= 15


BORDE_14_PESIMISTA = 3_789


def test_el_marcador_no_lo_quita_la_escalera():
    sb = "📊 Semana 9-15 nov: líneas 50/50 acertaron 52 de 101 · rango 80% cubrió 79 de 98"
    for fecha in (D13, D14, D15):
        r = _dia(fecha, dprod2=True, split_p=0.2, scoreboard=sb)
        apply_players_length_budget(r)
        assert format_players_message(r).splitlines()[1 + r.players_trimmed] == sb


def test_omision_lleva_el_marcador():
    sb = "📊 Semana 9-15 nov: muestra insuficiente"
    r = _dia(D15, dprod2=True, scoreboard=sb)
    r.highlights_truncated = "omitted"
    assert format_players_message(r) == (
        "🏀 Destacados · 23 dic 2026: omitidos hoy por longitud del día.\n" + sb)


def test_log_no_registra_splits_no_publicados():
    from nba_predictor.api.player_predictions_log import build_player_log_rows

    r = _dia(D14, dprod2=True, split_p=0.2)
    apply_players_length_budget(r)
    filas = build_player_log_rows(r, served_by="r", model_version="v",
                                  predicted_at_utc=datetime(2027, 4, 9, 19, tzinfo=timezone.utc))
    assert all(f["split_mate_id"] is None for f in filas)
    assert any(f["pts_line"] is not None for f in filas)
    r15 = _dia(D15, dprod2=True, split_p=0.2)
    apply_players_length_budget(r15)
    filas15 = build_player_log_rows(r15, served_by="r", model_version="v",
                                    predicted_at_utc=datetime(2026, 12, 23, tzinfo=timezone.utc))
    assert any(f["split_mate_name"] for f in filas15)


# ---------------------------------------------------------------------------
# Marcador semanal
# ---------------------------------------------------------------------------

def test_semana_cerrada():
    assert ws.closed_week(date(2026, 11, 16)) == (date(2026, 11, 9), date(2026, 11, 15))
    assert ws.closed_week(date(2026, 12, 8)) == (date(2026, 11, 30), date(2026, 12, 6))


def test_lunes_siempre_y_lunes_sin_partidos_pasa_al_siguiente():
    lunes = date(2026, 12, 7)                                # lunes sin partidos 002
    jugadas = {date(2026, 12, 6), date(2026, 12, 8)}
    assert ws.should_publish(lunes, jugadas)
    assert ws.should_publish(date(2026, 12, 8), jugadas)     # el martes lo lleva
    assert not ws.should_publish(date(2026, 12, 9), jugadas | {date(2026, 12, 8)})
    assert not ws.should_publish(date(2026, 11, 17), {date(2026, 11, 16)})


def _fila_log(gid, pid, hora_utc, game_date="2026-11-10", **kw):
    base = dict(game_id=gid, game_date=game_date, player_id=pid, team_tricode="AAA",
                pts_median=20.0, pts_line=19.5, pts_min=12.0, pts_max=28.0, games_in_window=10,
                predicted_at_utc=datetime.fromisoformat(f"{game_date}T{hora_utc}+00:00"), rank=1)
    base.update(kw)
    return base


def test_servida_de_record():
    filas = [_fila_log("g", 1, "18:40:00", pts_line=1.5),       # 12:40 CDMX: fuera de franja
             _fila_log("g", 1, "19:00:30", pts_line=2.5),       # 13:00:30: la de record
             _fila_log("g", 1, "19:20:00", pts_line=3.5),       # 13:20: en franja, más lejos
             _fila_log("h", 2, "21:00:00"), _fila_log("h", 2, "20:00:00", pts_line=9.5),
             _fila_log("x", 3, "19:00:00", game_date="2026-11-11",   # servida el día antes
                       predicted_at_utc=datetime(2026, 11, 10, 19, tzinfo=timezone.utc))]
    rec = {(r["game_id"], r["player_id"]): r["pts_line"] for r in ws.record_rows(filas)}
    assert rec[("g", 1)] == 2.5
    assert rec[("h", 2)] == 9.5                                  # sin franja: la más temprana
    assert ("x", 3) not in rec                                   # sin fila del día: sin record


def test_evaluar_solo_evaluados_y_r1():
    inicio = date(2026, 10, 20)
    rec = [_fila_log("g1", 1, "19:00:00"),                       # jugó, acierta línea y rango
           _fila_log("g2", 2, "19:00:00"),                       # no jugó: fuera
           _fila_log("g3", 3, "19:00:00"),                       # otro equipo: fuera
           _fila_log("g4", 4, "19:00:00", pts_line=None, pts_min=None, pts_max=None,
                     games_in_window=6),                         # ventana corta: nada
           _fila_log("g5", 5, "19:00:00", game_date="2026-11-05")]  # régimen: solo línea
    played = {("g1", 1): (100, 20.0), ("g3", 3): (200, 40.0), ("g4", 4): (100, 9.0),
              ("g5", 5): (100, 19.0)}
    c = ws.evaluate(rec, played, {100: "AAA", 200: "BBB"}, inicio)
    assert c == {"line_hits": 1, "line_n": 2, "range_hits": 1, "range_n": 1}


@pytest.mark.parametrize("counts, texto", [
    ({"line_hits": 52, "line_n": 101, "range_hits": 79, "range_n": 98},
     "📊 Semana 9-15 nov: líneas 50/50 acertaron 52 de 101 · rango 80% cubrió 79 de 98"),
    ({"line_hits": 20, "line_n": 49, "range_hits": 40, "range_n": 50},
     "📊 Semana 9-15 nov: líneas 50/50: muestra insuficiente · rango 80% cubrió 40 de 50"),
    ({"line_hits": 30, "line_n": 50, "range_hits": 9, "range_n": 10},
     "📊 Semana 9-15 nov: líneas 50/50 acertaron 30 de 50 · rango 80%: muestra insuficiente"),
    ({"line_hits": 1, "line_n": 2, "range_hits": 1, "range_n": 2},
     "📊 Semana 9-15 nov: muestra insuficiente"),
    (None, "📊 Semana 9-15 nov: marcador no disponible hoy"),
])
def test_render_minimo_50_por_metrica(counts, texto):
    assert ws.render(date(2026, 11, 9), date(2026, 11, 15), counts) == texto


def test_etiqueta_de_semana_entre_meses():
    assert ws.render(date(2026, 11, 30), date(2026, 12, 6), None).startswith(
        "📊 Semana 30 nov-6 dic:")


def test_attach_publica_marcador_el_lunes_y_declara_fallo():
    lunes = date(2026, 11, 16)
    _, extra = _attach(_StoreAttach(outs={}), lunes)
    assert extra["scoreboard"] == "📊 Semana 9-15 nov: muestra insuficiente"

    class _Roto(_StoreAttach):
        def load_player_predictions_log(self, start_date, end_date):
            raise RuntimeError("sin permiso")

    _, extra = _attach(_Roto(outs={}), lunes)
    assert extra["scoreboard"] == "📊 Semana 9-15 nov: marcador no disponible hoy"
    # Antes de la activación no hay marcador, aunque sea lunes.
    _, extra = _attach(_StoreAttach(outs={}), date(2026, 11, 9))
    assert extra["scoreboard"] is None


def test_log_local_ida_y_vuelta_con_columnas_dprod2(tmp_path):
    """El espejo SQLite guarda las 7 columnas nuevas y el lector del marcador
    las devuelve; una tabla vieja (sin ellas) se migra al abrir."""
    import sqlite3

    from nba_predictor.api.player_predictions_log import build_player_log_rows
    from nba_predictor.storage.local import LocalDataStore

    db = tmp_path / "nba.sqlite"
    with sqlite3.connect(db) as conn:                        # tabla de antes de D-PROD-2
        conn.execute("CREATE TABLE player_predictions_log (game_id TEXT, game_date DATE, "
                     "player_id INTEGER, player_name TEXT, team_tricode TEXT, status_flag "
                     "TEXT, pts_median REAL, pts_min REAL, pts_max REAL, reb_median REAL, "
                     "ast_median REAL, fg3m_median REAL, games_in_window INTEGER, "
                     "model_version TEXT, predicted_at_utc TEXT, served_by TEXT, rank INTEGER)")
    store = LocalDataStore(db, tmp_path / "raw")
    r = _dia(D13, dprod2=True, split_p=0.2)
    apply_players_length_budget(r)
    filas = build_player_log_rows(r, served_by="local", model_version="v",
                                  predicted_at_utc=datetime(2026, 12, 16, 19, tzinfo=timezone.utc))
    store.save_player_predictions_log(filas)
    leidas = store.load_player_predictions_log(date(2026, 12, 14), date(2026, 12, 20))
    assert len(leidas) == len(filas)
    assert {f["pts_line"] for f in leidas} == {f["pts_line"] for f in filas}
    with sqlite3.connect(db) as conn:
        n_split = conn.execute("SELECT COUNT(*) FROM player_predictions_log "
                               "WHERE split_mate_name IS NOT NULL").fetchone()[0]
    assert n_split == sum(1 for f in filas if f["split_mate_name"])
    assert store.load_player_predictions_log(date(2026, 12, 21), date(2026, 12, 27)) == []
