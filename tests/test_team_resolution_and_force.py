"""D-PRE-4 — resolución de equipos del endpoint e interruptor manual del feed.

BUG (instancia 19): el mapa de equipos del endpoint normalizaba con el
normalizador de PERSONAS: 'Philadelphia76ers' -> 'philadelphiaers' y
'LAClippers' -> 'laclippers', sin coincidir con la tabla teams. Las filas se
descartaban con un WARNING: bajas de PHI y LAC ignoradas y "Bajas: –" falso.
FIX: odds_logic.normalize_team, la única normalización de equipos del repo.

LISTAS REALES: los 30 nombres de la tabla teams (BigQuery, 2026-10-11) y los
30 tokens que el parser extrae del PDF GemBox vigente (corpus archivado). Si la
NBA renombra una franquicia, este test falla y obliga a mirarlo.
"""
from __future__ import annotations

import inspect
from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from nba_predictor.api.daily_predictions import (
    FORCE_FEED_DOWN_ENV,
    FORCE_FEED_DOWN_REASON,
    AvailabilityFlag,
    _build_norm_team_map,
    build_daily_predictions,
    format_daily_message,
    resolve_team_id,
)
from nba_predictor.ingestion.future_schedule import ScheduledGame
from nba_predictor.ingestion.injury_report import InjuryRow, InjuryStatus, NysEntry
from nba_predictor.models.logistic import OFFICIAL_LOGISTIC_COLS

TEAMS_TABLA = [
    "Atlanta Hawks", "Boston Celtics", "Brooklyn Nets", "Charlotte Hornets",
    "Chicago Bulls", "Cleveland Cavaliers", "Dallas Mavericks", "Denver Nuggets",
    "Detroit Pistons", "Golden State Warriors", "Houston Rockets", "Indiana Pacers",
    "Los Angeles Clippers", "Los Angeles Lakers", "Memphis Grizzlies", "Miami Heat",
    "Milwaukee Bucks", "Minnesota Timberwolves", "New Orleans Pelicans",
    "New York Knicks", "Oklahoma City Thunder", "Orlando Magic", "Philadelphia 76ers",
    "Phoenix Suns", "Portland Trail Blazers", "Sacramento Kings", "San Antonio Spurs",
    "Toronto Raptors", "Utah Jazz", "Washington Wizards",
]
TOKENS_PDF = [
    "AtlantaHawks", "BostonCeltics", "BrooklynNets", "CharlotteHornets", "ChicagoBulls",
    "ClevelandCavaliers", "DallasMavericks", "DenverNuggets", "DetroitPistons",
    "GoldenStateWarriors", "HoustonRockets", "IndianaPacers", "LAClippers",
    "LosAngelesLakers", "MemphisGrizzlies", "MiamiHeat", "MilwaukeeBucks",
    "MinnesotaTimberwolves", "NewOrleansPelicans", "NewYorkKnicks",
    "OklahomaCityThunder", "OrlandoMagic", "Philadelphia76ers", "PhoenixSuns",
    "PortlandTrailBlazers", "SacramentoKings", "SanAntonioSpurs", "TorontoRaptors",
    "UtahJazz", "WashingtonWizards",
]
TEAMS_DF = pd.DataFrame(
    [{"team_id": 1610612737 + i, "name": n} for i, n in enumerate(TEAMS_TABLA)]
)
MAPA = _build_norm_team_map(TEAMS_DF)


class TestResolucion:
    def test_30_de_30_tokens_reales_del_pdf(self):
        ids = {tok: resolve_team_id(tok, MAPA) for tok in TOKENS_PDF}
        assert [t for t, i in ids.items() if i is None] == []
        assert len(set(ids.values())) == 30  # biyección: ningún equipo colapsa con otro

    @pytest.mark.parametrize("tok, nombre", [
        ("Philadelphia76ers", "Philadelphia 76ers"),
        ("LAClippers", "Los Angeles Clippers"),
        ("LALakers", "Los Angeles Lakers"),
        ("LosAngelesLakers", "Los Angeles Lakers"),
    ])
    def test_regresion_de_los_casos_rotos(self, tok, nombre):
        esperado = int(TEAMS_DF.loc[TEAMS_DF["name"] == nombre, "team_id"].iloc[0])
        assert resolve_team_id(tok, MAPA) == esperado

    def test_una_sola_verdad_de_normalizacion(self):
        from nba_predictor.api import daily_predictions as dp

        src = inspect.getsource(dp._build_norm_team_map) + inspect.getsource(dp.resolve_team_id)
        assert "from nba_predictor.jobs.odds_logic import normalize_team" in src
        assert "_normalize_name" not in inspect.getsource(dp._fetch_absences)


# ---------------------------------------------------------------------------
# build_daily_predictions con PDF simulado
# ---------------------------------------------------------------------------

_IR = "nba_predictor.ingestion.injury_report"
_CLF = "nba_predictor.features.live_lookup.compute_live_features"
TARGET = date(2026, 10, 20)
MDY = "10/20/2026"
GAME = ScheduledGame(
    game_id="0022600002", game_date=TARGET,
    home_team_id=1610612752, away_team_id=1610612755,  # NYK, PHI (ids reales)
    home_tricode="NYK", away_tricode="PHI", season="2026-27",
)
TEAMS_REALES = pd.DataFrame([
    {"team_id": 1610612752, "name": "New York Knicks"},
    {"team_id": 1610612755, "name": "Philadelphia 76ers"},
])


def _store() -> MagicMock:
    store = MagicMock()
    store.load_teams.return_value = TEAMS_REALES.copy()
    pipe = MagicMock()
    pipe.predict_proba.return_value = np.array([[0.45, 0.55]])
    store.load_model.return_value = (pipe, {})
    return store


def _row(team: str, player: str, status=InjuryStatus.OUT, matchup="PHI@NYK") -> InjuryRow:
    return InjuryRow(game_date=MDY, game_time="07:00(ET)", matchup=matchup, team=team,
                     player_name=player, status=status, reason="Injury")


def _build(rows, nys=(), force=False, discover=None):
    disc = discover or MagicMock(return_value=("u", "01_15PM"))
    with (
        patch(f"{_IR}.discover_latest_snapshot", disc),
        patch(f"{_IR}.download_snapshot", return_value=b"%PDF-fake"),
        patch(f"{_IR}.parse_pdf", return_value=(list(rows), list(nys))),
        patch(_CLF, return_value={c: 0.0 for c in OFFICIAL_LOGISTIC_COLS}),
    ):
        return build_daily_predictions(
            TARGET, _store(), season="2026-27", scheduled_games=[GAME], version_name="v1",
            player_map={77: "Joel Embiid", 88: "Jalen Brunson"}, save_injury_raw=False,
            force_feed_down=force,
        )


class TestEnElEndpoint:
    def test_baja_de_phi_ahora_si_se_aplica(self):
        r = _build([_row("Philadelphia76ers", "Embiid,Joel")])
        gp = r.games[0]
        assert gp.away_absences == ["Joel Embiid"]
        assert gp.availability_flag == AvailabilityFlag.OK
        assert r.unresolved_team_count == 0
        assert "Bajas PHI: Joel Embiid" in format_daily_message(r)

    def test_nys_de_phi_se_declara(self):
        r = _build([], nys=[NysEntry(team="Philadelphia76ers", game_date=MDY)])
        assert r.games[0].nys_tricodes == ["PHI"]

    def test_equipo_sin_resolver_degrada_el_partido_declarado(self):
        r = _build([_row("PhiladelphiaSeventySixers", "Embiid,Joel")])
        gp = r.games[0]
        assert r.unresolved_team_count == 1
        assert r.unresolved_team_tokens == ["PhiladelphiaSeventySixers"]
        assert gp.availability_flag == AvailabilityFlag.NYS
        assert set(gp.nys_tricodes) == {"PHI", "NYK"}
        msg = format_daily_message(r)
        assert "⚠️ Disponibilidad PHI sin confirmar" in msg
        assert "Bajas PHI: –" not in msg  # el "sin bajas" falso ya no se publica

    def test_sin_resolver_de_otro_partido_no_toca_este(self):
        r = _build([_row("EquipoFantasma", "X,Y", matchup="LAL@BOS")])
        assert r.unresolved_team_count == 1
        assert r.games[0].availability_flag == AvailabilityFlag.OK

    def test_nys_sin_resolver_se_cuenta(self):
        r = _build([], nys=[NysEntry(team="EquipoFantasma", game_date=MDY)])
        assert r.unresolved_team_tokens == ["EquipoFantasma"]


class TestInterruptor:
    def test_forzado_no_descubre_ni_parsea_y_declara(self):
        disc = MagicMock(return_value=("u", "01_15PM"))
        r = _build([_row("Philadelphia76ers", "Embiid,Joel")], force=True, discover=disc)
        disc.assert_not_called()
        assert r.feed_down is True
        assert r.feed_down_reason == FORCE_FEED_DOWN_REASON
        assert r.feed_down_reason == "feed desactivado manualmente (NBA_PREDICTOR_FORCE_FEED_DOWN)"
        assert r.games[0].availability_flag == AvailabilityFlag.FEED_DOWN
        assert r.games[0].away_absences == []

    def test_sin_forzar_comportamiento_normal(self):
        r = _build([_row("Philadelphia76ers", "Embiid,Joel")], force=False)
        assert r.feed_down is False

    def test_server_lee_la_env_var_en_el_borde(self):
        from nba_predictor.api import server

        assert 'os.getenv(FORCE_FEED_DOWN_ENV) == "1"' in inspect.getsource(
            server.predictions_today
        )
        assert "getenv" not in inspect.getsource(build_daily_predictions)
        assert FORCE_FEED_DOWN_ENV == "NBA_PREDICTOR_FORCE_FEED_DOWN"

    @pytest.mark.parametrize("valor, esperado", [("1", True), ("0", False), (None, False)])
    def test_server_pasa_el_interruptor(self, monkeypatch, valor, esperado):
        from tests.test_server import _make_result, _running_client

        if valor is None:
            monkeypatch.delenv(FORCE_FEED_DOWN_ENV, raising=False)
        else:
            monkeypatch.setenv(FORCE_FEED_DOWN_ENV, valor)
        with _running_client() as client:
            with patch("nba_predictor.api.server.build_daily_predictions",
                       return_value=_make_result()) as bdp:
                assert client.get("/predictions/today?date=2026-10-20").status_code == 200
        assert bdp.call_args.kwargs["force_feed_down"] is esperado
