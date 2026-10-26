"""Instrumento de seguimiento de destacados (D-EXP-7, entregable paralelo).

Fija la clasificación con los casos reales del cotejo de Antonio (20-24 oct):
Maxey dentro del rango, Brown publicado en BOS jugando con PHI, Kyrie sin jugar.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "_seg", Path(__file__).resolve().parent.parent / "scripts" / "seguimiento_destacados.py")
seg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(seg)


def _fila(**kw):
    base = {"game_date": "2026-10-24", "game_id": "0022600050", "player_name": "Tyrese Maxey",
            "team_tricode": "PHI", "status_flag": None, "rank": 2,
            "pts_median": 24.0, "pts_min": 15.0, "pts_max": 32.0,
            "reb_median": 3.0, "ast_median": 6.0, "fg3m_median": 2.0,
            "minutes": 36.0, "fgm": 10, "fg3m": 4, "ftm": 2, "oreb": 0, "dreb": 4, "ast": 4,
            "real_tricode": "PHI", "tricode_ese_dia": "PHI"}
    base.update(kw)
    return base


def test_maxey_26_puntos_dentro_del_rango():
    e = seg.classify_row(_fila())
    assert e["veredicto"] == "evaluado"
    assert e["real"] == {"pts": 26, "reb": 4, "ast": 4, "fg3m": 4}
    assert e["pts_en_rango"] is True
    assert e["err_pts"] == 2.0 and e["err_ast"] == 2.0 and e["err_fg3m"] == 2.0


def test_limites_del_rango_incluidos():
    e = seg.classify_row(_fila(fgm=6, fg3m=1, ftm=2))   # 15 puntos = pts_min
    assert e["real"]["pts"] == 15 and e["pts_en_rango"] is True


def test_brown_publicado_en_bos_jugando_con_phi():
    e = seg.classify_row(_fila(player_name="Jaylen Brown", team_tricode="BOS",
                               minutes=None, real_tricode=None, tricode_ese_dia="PHI"))
    assert e["veredicto"] == "equipo_distinto" and e["real_team"] == "PHI"


def test_kyrie_sin_fila_ni_partido_ese_dia_no_jugo():
    e = seg.classify_row(_fila(player_name="Kyrie Irving", team_tricode="DAL",
                               minutes=None, real_tricode=None, tricode_ese_dia=None))
    assert e["veredicto"] == "no_jugo"


def test_activado_con_cero_minutos_no_jugo():
    assert seg.classify_row(_fila(minutes=0.0))["veredicto"] == "no_jugo"


def test_resumen_cuenta_solo_evaluados_en_la_cobertura():
    evals = [seg.classify_row(_fila()),
             seg.classify_row(_fila(fgm=2, fg3m=0, ftm=0)),          # 4 pts, fuera
             seg.classify_row(_fila(minutes=None, real_tricode=None, tricode_ese_dia=None))]
    s = seg.summarize(evals)
    assert (s["destacados"], s["evaluados"], s["no_jugo"]) == (3, 2, 1)
    assert s["pts_en_rango"] == 1 and s["cobertura_pts"] == 0.5


def _ev_r1(d, dentro, n=10):
    return {"veredicto": "evaluado", "game_date": d, "game_id": d, "games_in_window": n,
            "pts_en_rango": dentro}


def test_monitor_r1_ignora_el_regimen_y_ventanas_cortas():
    from datetime import date

    ini = date(2026, 10, 20)
    evals = [_ev_r1("2026-11-09", False)] * 50 + [_ev_r1("2026-11-12", False, n=7)] * 50
    m = seg.monitor_r1(evals, ini)
    assert m["filas_r1"] == 0 and m["veredicto"] == "insuficiente"


def test_monitor_r1_alerta_bajo_72():
    from datetime import date

    ini = date(2026, 10, 20)
    ok = [_ev_r1(f"2026-11-{10 + i // 10:02d}", i % 4 != 0) for i in range(100)]   # 75%
    mal = [_ev_r1(f"2026-11-{10 + i // 10:02d}", i % 10 >= 3) for i in range(100)]  # 70%
    assert seg.monitor_r1(ok, ini)["veredicto"] == "ok"
    m = seg.monitor_r1(mal, ini)
    assert m["veredicto"] == "ALERTA" and m["cobertura_ultimos_100"] == 0.70
