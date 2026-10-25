"""D-PRE-8 — señales de plantilla (pretemporada + injury report) para destacados.

Los cuatro casos que el cotejo de Antonio encontró en el dry-run del 21-oct
(D-PRE-6b), con ids REALES de la tabla players y los equipos que el spike vio
en los boxscores 001 de 2026-27:

  jugador          id        última fila regular   señal 001 (spike)
  Julius Randle    203944    MIN                   BKN, 2026-10-14
  Brandon Ingram   1627742   TOR                   LAC, 2026-10-14, 0 min
  Kawhi Leonard    202695    LAC                   TOR, 2026-10-15
  Cody Williams    1642262   UTA                   MIN, 2026-10-16

Regla: equipo = fila MÁS RECIENTE entre player_game_stats y roster_signals;
estadísticas = últimos 10 de temporada regular, de cualquier equipo. La
pretemporada da EQUIPO, jamás números — y el test de no contaminación prueba
que el paso 1b no toca ninguna tabla STRUCTURED ni el parquet de features.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from nba_predictor.api.player_highlights import (
    build_game_highlights,
    latest_team_rows,
    rank_team_candidates,
)
from nba_predictor.ingestion.cdn_client import (
    _normalize_cdn_player_stats,
    _normalize_cdn_team_stats,
)
from nba_predictor.jobs.roster_signals import (
    SOURCE_INJURY_REPORT,
    SOURCE_PRESEASON,
    preseason_played_games,
    roster_signals_step,
    season_window,
    signals_from_preseason_box,
)
from nba_predictor.storage.base import ROSTER_SIGNAL_COLUMNS
from nba_predictor.storage.local import LocalDataStore

FIXTURES = Path(__file__).parent / "fixtures"
BOX_001 = FIXTURES / "boxscore_preseason_0012600064.json"   # DEN @ LAC, 2026-10-14
PDF_GEMBOX = FIXTURES / "injury_report_2026-03-13_01_15PM.pdf"

MIN, BKN, LAC, TOR, UTA = 1610612750, 1610612751, 1610612746, 1610612761, 1610612762
RANDLE, INGRAM, KAWHI, CODY = 203944, 1627742, 202695, 1642262
NOMBRES = {RANDLE: "Julius Randle", INGRAM: "Brandon Ingram",
           KAWHI: "Kawhi Leonard", CODY: "Cody Williams"}
TARGET = date(2026, 10, 22)
T_OBS = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def _filas(pid, tid, n, *, minutes=32.0, pts=20.0, desde="2026-03-30", gid0=0):
    """n partidos de temporada regular 2025-26 (prefijo 00225) con pts fijos."""
    return pd.DataFrame({
        "player_id": [pid] * n,
        "team_id": [tid] * n,
        "game_id": [f"00225{gid0 + i:05d}" for i in range(n)],
        "game_date": pd.date_range(desde, periods=n, freq="D"),
        "minutes": [minutes] * n,
        "pts": [pts + i for i in range(n)],
        "reb": [6.0] * n,
        "ast": [3.0] * n,
        "fg3m": [1.0] * n,
    })


def _senal(pid, tid, d, *, source=SOURCE_PRESEASON, ref="0012600064"):
    return pd.DataFrame([{
        "player_id": pid, "team_id": tid, "source": source, "ref_id": ref,
        "game_date": date.fromisoformat(d), "observed_at": T_OBS,
    }])


def _hist_verano():
    """Fin de 2025-26: los cuatro con sus equipos VIEJOS, 10 partidos cada uno."""
    return pd.concat([
        _filas(RANDLE, MIN, 10, minutes=34.0, pts=21.0, gid0=0),
        _filas(INGRAM, TOR, 10, minutes=33.0, pts=22.0, gid0=100),
        _filas(KAWHI, LAC, 10, minutes=32.0, pts=25.0, gid0=200),
        _filas(CODY, UTA, 10, minutes=31.0, pts=12.0, gid0=300),
    ], ignore_index=True)


def _senales_pretemporada():
    """Las señales que el spike vio, con sus fechas reales."""
    return pd.concat([
        _senal(RANDLE, BKN, "2026-10-14", ref="0012600003"),
        _senal(INGRAM, LAC, "2026-10-14", ref="0012600064"),
        _senal(KAWHI, TOR, "2026-10-15", ref="0012600055"),
        _senal(CODY, MIN, "2026-10-16", ref="0012600058"),
    ], ignore_index=True)


def _ids(cands):
    return [c["player_id"] for c in cands]


# ---------------------------------------------------------------------------
# Los cuatro casos con ids reales
# ---------------------------------------------------------------------------


CASOS = [
    # (jugador, equipo viejo, equipo nuevo, pts del último partido con el viejo)
    (RANDLE, MIN, BKN, 30.0),
    (INGRAM, TOR, LAC, 31.0),
    (KAWHI, LAC, TOR, 34.0),
    (CODY, UTA, MIN, 21.0),
]


@pytest.mark.parametrize("pid, viejo, nuevo, pts_max", CASOS,
                         ids=["Randle->BKN", "Ingram->LAC", "Kawhi->TOR", "Cody->MIN"])
class TestCuatroCasos:
    def test_con_la_senal_es_candidato_del_equipo_nuevo(self, pid, viejo, nuevo, pts_max):
        cands = rank_team_candidates(_hist_verano(), nuevo, {}, target_date=TARGET,
                                     roster_signals=_senales_pretemporada())
        assert pid in _ids(cands)

    def test_con_la_senal_ya_no_es_del_equipo_viejo(self, pid, viejo, nuevo, pts_max):
        cands = rank_team_candidates(_hist_verano(), viejo, {}, target_date=TARGET,
                                     roster_signals=_senales_pretemporada())
        assert pid not in _ids(cands)

    def test_sus_numeros_son_los_ultimos_10_regulares_con_el_viejo(
        self, pid, viejo, nuevo, pts_max
    ):
        c = next(c for c in rank_team_candidates(
            _hist_verano(), nuevo, {}, target_date=TARGET,
            roster_signals=_senales_pretemporada()) if c["player_id"] == pid)
        assert c["games_in_window"] == 10
        assert c["pts_max"] == pts_max            # el último partido con el equipo viejo

    def test_sin_senal_sigue_el_limite_de_d_pre_6(self, pid, viejo, nuevo, pts_max):
        """Control: sin roster_signals el comportamiento es el de producción v16."""
        assert pid in _ids(rank_team_candidates(_hist_verano(), viejo, {}, target_date=TARGET))


def test_ingram_cuenta_con_su_fila_001_de_cero_minutos():
    """La señal es 'activado con ese equipo': 0 minutos cuenta (spike, 2026-10-14)."""
    box = json.loads(BOX_001.read_text(encoding="utf-8"))
    pgs = _normalize_cdn_player_stats(box["game"], "0012600064")
    fila = pgs[pgs["player_id"] == INGRAM]
    assert len(fila) == 1 and fila["team_id"].iloc[0] == LAC
    assert pd.isna(fila["minutes"].iloc[0])     # PT00M00.00S → None (Decisión 9)
    senales = signals_from_preseason_box(pgs, game_id="0012600064",
                                         game_date="2026-10-14", observed_at=T_OBS)
    assert INGRAM in set(senales["player_id"])
    cands = rank_team_candidates(_hist_verano(), LAC, {}, target_date=TARGET,
                                 roster_signals=senales)
    assert INGRAM in _ids(cands)


def test_mensaje_2_publica_a_randle_en_brooklyn():
    hs = build_game_highlights(
        _hist_verano(), home_team_id=BKN, away_team_id=MIN, statuses_by_tid={},
        player_map=NOMBRES, target_date=TARGET, roster_signals=_senales_pretemporada(),
    )
    por_lado = {(h.player_name, h.team) for h in hs}
    assert ("Julius Randle", "home") in por_lado
    assert ("Cody Williams", "away") in por_lado
    assert ("Julius Randle", "away") not in por_lado


# ---------------------------------------------------------------------------
# Regla de equipo actual
# ---------------------------------------------------------------------------


def test_senal_del_injury_report_mas_reciente_cambia_el_equipo():
    hist = _filas(RANDLE, MIN, 10)                         # último regular: 2026-04-08
    pdf = _senal(RANDLE, BKN, "2026-10-19", source=SOURCE_INJURY_REPORT,
                 ref="2026-10-19_03_00PM")
    assert RANDLE in _ids(rank_team_candidates(hist, BKN, {}, target_date=TARGET,
                                               roster_signals=pdf))
    assert RANDLE not in _ids(rank_team_candidates(hist, MIN, {}, target_date=TARGET,
                                                   roster_signals=pdf))


def test_senal_mas_vieja_que_la_ultima_fila_regular_no_cambia_nada():
    """Una señal de antes de su último partido oficial está superada por él."""
    hist = _filas(RANDLE, MIN, 10, desde="2026-03-30")
    vieja = _senal(RANDLE, BKN, "2026-03-01", source=SOURCE_INJURY_REPORT,
                   ref="2026-03-01_01_00PM")
    ultima = latest_team_rows(hist, vieja)
    assert ultima.loc[ultima["player_id"] == RANDLE, "team_id"].item() == MIN


def test_empate_de_fecha_se_resuelve_por_game_id_ref_id():
    """Mismo día: gana el mayor identificador ('2026-...' > '00226...')."""
    hist = _filas(RANDLE, MIN, 1, desde="2026-10-20")      # game_id 0022500000
    pdf = _senal(RANDLE, BKN, "2026-10-20", source=SOURCE_INJURY_REPORT,
                 ref="2026-10-20_03_00PM")
    ultima = latest_team_rows(hist, pdf)
    assert ultima.loc[ultima["player_id"] == RANDLE, "team_id"].item() == BKN


def test_la_recencia_se_mide_sobre_la_fila_mas_reciente_incluida_la_senal():
    """Último regular hace >250 días, pero visto en pretemporada: candidato."""
    hist = _filas(KAWHI, LAC, 10, desde="2025-12-01")
    sin = rank_team_candidates(hist, TOR, {}, target_date=TARGET)
    con = rank_team_candidates(hist, TOR, {}, target_date=TARGET,
                               roster_signals=_senal(KAWHI, TOR, "2026-10-15"))
    assert sin == [] and _ids(con) == [KAWHI]


# ---------------------------------------------------------------------------
# La pretemporada NO aporta números
# ---------------------------------------------------------------------------


def test_la_pretemporada_no_aporta_filas_a_las_estadisticas():
    """Mismos números con y sin señales: las señales solo deciden el equipo."""
    hist = _hist_verano()
    sin = {c["player_id"]: c for c in rank_team_candidates(hist, MIN, {}, target_date=TARGET)}
    con = {c["player_id"]: c for c in rank_team_candidates(
        hist, BKN, {}, target_date=TARGET, roster_signals=_senales_pretemporada())}
    for k in ("games_in_window", "minutes_rolling", "pts_median", "pts_min", "pts_max",
              "reb_median", "ast_median", "fg3m_median"):
        assert con[RANDLE][k] == sin[RANDLE][k], k


def test_una_senal_sola_sin_partidos_regulares_no_hace_candidato():
    """Un novato visto solo en pretemporada no tiene números: no se publica."""
    cands = rank_team_candidates(_hist_verano(), BKN, {}, target_date=TARGET,
                                 roster_signals=_senal(9999999, BKN, "2026-10-14"))
    assert 9999999 not in _ids(cands)


def test_las_senales_de_pretemporada_no_llevan_columnas_de_estadistica():
    box = json.loads(BOX_001.read_text(encoding="utf-8"))
    pgs = _normalize_cdn_player_stats(box["game"], "0012600064")
    senales = signals_from_preseason_box(pgs, game_id="0012600064",
                                         game_date="2026-10-14", observed_at=T_OBS)
    assert tuple(senales.columns) == ROSTER_SIGNAL_COLUMNS
    assert len(senales) == len(pgs) and set(senales["source"]) == {SOURCE_PRESEASON}


# ---------------------------------------------------------------------------
# Lectura del schedule y ventana de temporada
# ---------------------------------------------------------------------------


def _schedule(*juegos):
    return {"leagueSchedule": {"seasonYear": "2026-27", "gameDates": [{"games": [
        {"gameId": gid, "gameDateEst": f"{d}T00:00:00Z", "gameStatus": st}
        for gid, d, st in juegos]}]}}


def test_preseason_played_games_solo_001_terminados():
    raw = _schedule(("0012600064", "2026-10-14", 3), ("0012600099", "2026-10-16", 1),
                    ("0022600001", "2026-10-20", 3))
    assert preseason_played_games(raw) == [{"game_id": "0012600064", "game_date": "2026-10-14"}]


def test_season_window():
    assert season_window("2026-27") == (date(2026, 7, 1), date(2027, 6, 30))


# ---------------------------------------------------------------------------
# Paso 1b sobre un LocalDataStore: NO CONTAMINACIÓN, idempotencia, best-effort
# ---------------------------------------------------------------------------


class _FakeCDN:
    """Sirve el boxscore 001 real del fixture con los normalizadores de la Decisión 9."""

    def __init__(self, falla=False):
        self.llamadas: list[str] = []
        self.falla = falla

    def fetch_boxscore(self, game_id):
        self.llamadas.append(game_id)
        if self.falla:
            raise RuntimeError("S3 caído")
        raw = json.loads(BOX_001.read_text(encoding="utf-8"))
        return (_normalize_cdn_team_stats(raw["game"], game_id),
                _normalize_cdn_player_stats(raw["game"], game_id), raw)


def _hash_tabla(db: Path, tabla: str) -> str:
    with sqlite3.connect(db) as conn:
        filas = conn.execute(f"SELECT * FROM {tabla} ORDER BY 1, 2").fetchall()
    return hashlib.sha256(repr(filas).encode()).hexdigest()


def _hashes(store: LocalDataStore) -> dict[str, str]:
    h = {t: _hash_tabla(store.db_path, t)
         for t in ("games", "team_game_stats", "player_game_stats")}
    h["features"] = hashlib.sha256(
        (store.processed_dir / "features_v1.parquet").read_bytes()).hexdigest()
    return h


def _player_map_del_pdf() -> dict[int, str]:
    """Nombres 'Nombre Apellido' para los jugadores del PDF fixture (ids sintéticos)."""
    from nba_predictor.ingestion.injury_report import parse_pdf

    rows, _ = parse_pdf(PDF_GEMBOX.read_bytes())
    mapa = {}
    for i, r in enumerate(rows):
        apellido, _, nombre = r.player_name.partition(",")
        mapa[5_000_000 + i] = f"{nombre} {apellido}"
    return mapa


@pytest.fixture
def store(tmp_path):
    from tests.test_team_resolution_and_force import TEAMS_DF

    st = LocalDataStore(db_path=tmp_path / "nba.sqlite", raw_dir=tmp_path / "raw",
                        processed_dir=tmp_path / "processed")
    teams = TEAMS_DF.rename(columns={"name": "name"}).assign(abbreviation="XXX")
    st.save_teams(teams[["team_id", "abbreviation", "name"]])
    st.save_games(pd.DataFrame([{
        "game_id": "0022500001", "season": "2025-26", "season_type": "Regular Season",
        "game_date": "2026-04-01", "home_team_id": LAC, "away_team_id": TOR,
        "home_pts": 110, "away_pts": 100, "home_won": 1, "neutral_site": 0}]))
    raw = json.loads(BOX_001.read_text(encoding="utf-8"))
    tgs = _normalize_cdn_team_stats(raw["game"], "0022500001")
    pgs = _normalize_cdn_player_stats(raw["game"], "0022500001")
    st.save_team_game_stats(tgs)
    st.save_player_game_stats(pgs)
    st.save_features(pd.DataFrame({"game_id": ["0022500001"], "home_won": [1]}))
    return st


def _correr(store, cliente, pdfs=None):
    raw = _schedule(("0012600064", "2026-10-14", 3), ("0012600099", "2026-10-16", 1),
                    ("0022600001", "2026-10-20", 3))
    pdfs = [("2026-03-13", "01_15PM", PDF_GEMBOX.read_bytes())] if pdfs is None else pdfs
    return roster_signals_step(store, cliente, raw, pdfs, observed_at=T_OBS,
                               player_map=_player_map_del_pdf())


def test_no_contaminacion_hashes_identicos_antes_y_despues(store):
    """El paso 1b NO toca games, team_game_stats, player_game_stats ni features."""
    antes = _hashes(store)
    resumen = _correr(store, _FakeCDN())
    assert _hashes(store) == antes
    # ... y sí hizo su trabajo: hay señales de las DOS fuentes.
    senales = store.load_roster_signals()
    assert resumen["senales_preseason"] > 0 and resumen["senales_injury_report"] > 0
    assert set(senales["source"]) == {SOURCE_PRESEASON, SOURCE_INJURY_REPORT}


def test_no_contaminacion_ningun_save_structured_es_llamado(store, monkeypatch):
    def prohibido(*a, **k):
        raise AssertionError("el paso 1b escribió en una tabla STRUCTURED")

    for m in ("save_games", "save_team_game_stats", "save_player_game_stats",
              "save_features", "save_raw_boxscore"):
        monkeypatch.setattr(store, m, prohibido)
    resumen = _correr(store, _FakeCDN())
    assert resumen["preseason_fallidos"] == 0 and resumen["senales_preseason"] > 0


def test_raw_de_pretemporada_en_su_directorio_propio(store):
    _correr(store, _FakeCDN())
    assert (store.raw_dir / "boxscores_preseason" / "0012600064.json").exists()
    assert not (store.raw_dir / "0012600064.json").exists()
    assert not (store.raw_dir / "boxscores_live").exists()


def test_solo_baja_los_001_terminados(store):
    cdn = _FakeCDN()
    _correr(store, cdn)
    assert cdn.llamadas == ["0012600064"]


def test_idempotente_la_segunda_corrida_no_baja_nada(store):
    _correr(store, _FakeCDN())
    n = len(store.load_roster_signals())
    cdn = _FakeCDN()
    resumen = _correr(store, cdn)
    assert cdn.llamadas == [] and resumen["preseason_nuevos"] == 0
    assert len(store.load_roster_signals()) == n


def test_best_effort_s3_caido_no_lanza_y_el_pdf_sigue(store):
    resumen = _correr(store, _FakeCDN(falla=True))
    assert resumen["preseason_fallidos"] == 1 and resumen["senales_preseason"] == 0
    assert resumen["senales_injury_report"] > 0


def test_best_effort_sin_schedule_no_lanza(store):
    resumen = roster_signals_step(store, _FakeCDN(), None, [], observed_at=T_OBS)
    assert resumen["senales_preseason"] == 0


def test_pdf_ilegible_no_emite_senales_y_no_lanza(store):
    resumen = _correr(store, _FakeCDN(), pdfs=[("2026-10-20", "03_00PM", b"%PDF-roto")])
    assert resumen["senales_injury_report"] == 0
    assert resumen["senales_preseason"] > 0


def test_senales_del_pdf_llevan_ref_id_del_snapshot_y_fecha_del_corte(store):
    _correr(store, _FakeCDN())
    ir = store.load_roster_signals().query("source == @SOURCE_INJURY_REPORT")
    assert set(ir["ref_id"]) == {"2026-03-13_01_15PM"}
    assert set(ir["game_date"]) == {date(2026, 3, 13)}
    assert ir["player_id"].is_unique


# ---------------------------------------------------------------------------
# Almacén local: tabla, filtro de fechas, idempotencia
# ---------------------------------------------------------------------------


def test_local_roster_signals_idempotente_por_clave(tmp_path):
    st = LocalDataStore(db_path=tmp_path / "x.sqlite", raw_dir=tmp_path / "raw")
    st.save_roster_signals(_senales_pretemporada())
    st.save_roster_signals(_senales_pretemporada())
    df = st.load_roster_signals()
    assert len(df) == 4 and tuple(df.columns) == ROSTER_SIGNAL_COLUMNS
    # Misma clave, equipo distinto: actualiza, no duplica.
    st.save_roster_signals(_senal(RANDLE, MIN, "2026-10-14", ref="0012600003"))
    df = st.load_roster_signals()
    assert len(df) == 4
    assert df.loc[df["player_id"] == RANDLE, "team_id"].item() == MIN


def test_local_roster_signals_filtro_por_fecha(tmp_path):
    st = LocalDataStore(db_path=tmp_path / "x.sqlite", raw_dir=tmp_path / "raw")
    st.save_roster_signals(_senales_pretemporada())
    df = st.load_roster_signals(start_date=date(2026, 10, 15), end_date=date(2026, 10, 15))
    assert list(df["player_id"]) == [KAWHI]
    assert st.load_roster_signals(start_date=date(2027, 7, 1)).empty


# ---------------------------------------------------------------------------
# Cableado en daily_predictions
# ---------------------------------------------------------------------------


class _StoreDestacados:
    def __init__(self, senales=None, falla_senales=False):
        self.senales = senales
        self.falla_senales = falla_senales
        self.pedidas: list[tuple] = []

    def load_games(self):
        h = _hist_verano()
        return pd.DataFrame({"game_id": h["game_id"], "game_date": h["game_date"]}
                            ).drop_duplicates()

    def load_player_game_stats(self, season=None):
        h = _hist_verano()
        out = h.drop(columns=["game_date", "pts", "reb"]).assign(
            fgm=8, ftm=2, oreb=1, dreb=5)
        return out if season == "2025-26" else out.iloc[0:0]

    def load_roster_signals(self, start_date=None, end_date=None):
        self.pedidas.append((start_date, end_date))
        if self.falla_senales:
            raise RuntimeError("tabla roster_signals inexistente")
        return self.senales


def _gp_bkn_min():
    from nba_predictor.api.daily_predictions import AvailabilityFlag, GamePrediction
    from nba_predictor.ingestion.future_schedule import ScheduledGame

    sg = ScheduledGame(game_id="0022600031", game_date=TARGET, home_team_id=BKN,
                       away_team_id=MIN, home_tricode="BKN", away_tricode="MIN",
                       season="2026-27")
    gp = GamePrediction(home_tricode="BKN", away_tricode="MIN", game_date=TARGET.isoformat(),
                        probability_home=0.5, home_absences=[], away_absences=[],
                        availability_flag=AvailabilityFlag.OK, model_version="v",
                        game_id="0022600031")
    return sg, gp


def test_attach_highlights_carga_las_senales_de_la_temporada_efectiva():
    from nba_predictor.api.daily_predictions import _attach_highlights

    store = _StoreDestacados(senales=_senales_pretemporada())
    sg, gp = _gp_bkn_min()
    _attach_highlights(store=store, target_date=TARGET, scheduled=[sg], game_preds=[gp],
                       statuses_by_tid={}, player_map=NOMBRES, season="2026-27")
    assert store.pedidas == [(date(2026, 7, 1), date(2027, 6, 30))]
    assert ("Julius Randle", "home") in {(p.player_name, p.team) for p in gp.players}


def test_attach_highlights_ignora_senales_del_mismo_dia_o_posteriores():
    """Anti-leakage: una señal del día a publicar o posterior no cuenta."""
    from nba_predictor.api.daily_predictions import _attach_highlights

    store = _StoreDestacados(senales=_senal(RANDLE, BKN, TARGET.isoformat(),
                                            source=SOURCE_INJURY_REPORT,
                                            ref=f"{TARGET}_03_00PM"))
    sg, gp = _gp_bkn_min()
    _attach_highlights(store=store, target_date=TARGET, scheduled=[sg], game_preds=[gp],
                       statuses_by_tid={}, player_map=NOMBRES, season="2026-27")
    assert ("Julius Randle", "away") in {(p.player_name, p.team) for p in gp.players}


def test_attach_highlights_sin_senales_degrada_a_d_pre_6_sin_lanzar():
    from nba_predictor.api.daily_predictions import _attach_highlights

    store = _StoreDestacados(falla_senales=True)
    sg, gp = _gp_bkn_min()
    _attach_highlights(store=store, target_date=TARGET, scheduled=[sg], game_preds=[gp],
                       statuses_by_tid={}, player_map=NOMBRES, season="2026-27")
    assert gp.players_data_available is True
    assert ("Julius Randle", "away") in {(p.player_name, p.team) for p in gp.players}


# ---------------------------------------------------------------------------
# Adapter cloud: contrato (sin GCP)
# ---------------------------------------------------------------------------


def test_cloud_merge_key_y_lectura_por_filas():
    from unittest.mock import MagicMock

    from nba_predictor.storage import cloud

    assert cloud._MERGE_KEYS["roster_signals"] == ["player_id", "source", "ref_id"]
    fila = MagicMock(player_id=RANDLE, team_id=BKN, source="preseason",
                     ref_id="0012600003", game_date=date(2026, 10, 14), observed_at=T_OBS)
    bq = MagicMock()
    bq.query.return_value.result.return_value = [fila]
    st = cloud.CloudDataStore("p", "d", "b", _bq_client=bq, _gcs_client=MagicMock())
    df = st.load_roster_signals(start_date=date(2026, 7, 1), end_date=date(2027, 6, 30))
    assert df.to_dict("records")[0]["team_id"] == BKN
    sql = bq.query.call_args[0][0]
    assert "game_date >= '2026-07-01'" in sql and "game_date <= '2027-06-30'" in sql
    bq.query.return_value.to_dataframe.assert_not_called()   # capa 5


def test_cloud_raw_de_pretemporada_va_a_su_prefijo():
    from unittest.mock import MagicMock

    from nba_predictor.storage import cloud

    gcs = MagicMock()
    st = cloud.CloudDataStore("p", "d", "b", _bq_client=MagicMock(), _gcs_client=gcs)
    st.save_raw_preseason_boxscore("0012600064", {"game": {}})
    gcs.bucket.return_value.blob.assert_called_with("raw/boxscores_preseason/0012600064.json")
