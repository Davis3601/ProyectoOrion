"""D-PRE-1 / bug 3 — --force-features: el paso 2 ejercitado en la nube sin
tocar el parquet canónico.

Dos contratos:
  1. Sin el flag, el camino de siempre no cambia: paso 2 solo con partidos
     nuevos, escribiendo al canónico.
  2. Con el flag, el paso 2 corre aunque no haya partidos, escribe SIEMPRE al
     nombre de verificación y compara contenido contra el canónico (nivel 2 de
     la reconstrucción). Un contenido distinto falla ruidosamente.

Sin GCP: DataStore falso y assemble_features parcheado.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from nba_predictor.jobs.ingest_logic import (
    FEATURES_CANONICAL_VERSION,
    FEATURES_FORCECHECK_VERSION,
    _should_rebuild_features,
    features_target_version,
)


def _features(n: int = 4, bump: float = 0.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_id": [f"00225{i:05d}" for i in range(n)],
            "efg_diff": [0.01 * i + bump for i in range(n)],
            "home_won": [i % 2 for i in range(n)],
        }
    )


class TestDecisionDelPaso2:
    def test_sin_flag_sin_partidos_no_reconstruye(self):
        ok, reason = _should_rebuild_features(0)
        assert ok is False
        assert "omitido" in reason

    def test_sin_flag_con_partidos_reconstruye(self):
        ok, _ = _should_rebuild_features(7)
        assert ok is True

    def test_flag_fuerza_aunque_no_haya_partidos(self):
        ok, reason = _should_rebuild_features(0, force=True)
        assert ok is True
        assert "VERIFICACIÓN" in reason
        assert FEATURES_FORCECHECK_VERSION in reason

    def test_nombres_canonico_y_verificacion(self):
        assert FEATURES_CANONICAL_VERSION == "v1"  # features/features_v1.parquet
        assert FEATURES_FORCECHECK_VERSION == "v1_forcecheck"

    @pytest.mark.parametrize("n_new", [0, 1, 50])
    def test_forzado_jamas_apunta_al_canonico(self, n_new):
        _should_rebuild_features(n_new, force=True)
        assert features_target_version(True) != FEATURES_CANONICAL_VERSION

    def test_normal_apunta_al_canonico(self):
        assert features_target_version(False) == FEATURES_CANONICAL_VERSION


@pytest.fixture
def job():
    import scripts.ingest_job as mod

    return mod


def _run_step2(job, ds, rebuilt: pd.DataFrame, force: bool):
    with patch("nba_predictor.features.assemble.assemble_features", return_value=rebuilt):
        return job._step2_features(ds, force=force)


class TestStep2:
    def test_normal_escribe_al_canonico_y_no_compara(self, job):
        ds = MagicMock()
        _run_step2(job, ds, _features(), force=False)
        ds.save_features.assert_called_once()
        assert ds.save_features.call_args.kwargs["version"] == FEATURES_CANONICAL_VERSION
        ds.load_features.assert_not_called()

    def test_forzado_escribe_solo_a_verificacion(self, job):
        ds = MagicMock()
        ds.load_features.return_value = _features()
        _run_step2(job, ds, _features(), force=True)
        versiones = [c.kwargs.get("version") for c in ds.save_features.call_args_list]
        assert versiones == [FEATURES_FORCECHECK_VERSION]
        assert FEATURES_CANONICAL_VERSION not in versiones
        ds.load_features.assert_called_once_with(FEATURES_CANONICAL_VERSION)

    def test_forzado_contenido_identico_pasa(self, job):
        ds = MagicMock()
        canon = _features()
        # Mismo contenido con dtype distinto (artefacto de motor): nivel 2 pasa.
        canon["home_won"] = canon["home_won"].astype("Int64")
        ds.load_features.return_value = canon
        sha = _run_step2(job, ds, _features(), force=True)
        assert isinstance(sha, str) and len(sha) == 64

    def test_forzado_contenido_distinto_falla_ruidosamente(self, job):
        ds = MagicMock()
        ds.load_features.return_value = _features()
        with pytest.raises(RuntimeError, match="difiere del canónico"):
            _run_step2(job, ds, _features(bump=0.5), force=True)

    def test_forzado_conteo_distinto_falla(self, job):
        ds = MagicMock()
        ds.load_features.return_value = _features(n=5)
        with pytest.raises(RuntimeError, match="conteo diferente"):
            _run_step2(job, ds, _features(n=4), force=True)


class TestCableado:
    def test_cli_pasa_el_flag_a_main(self, job):
        src = inspect.getsource(job)
        assert '"--force-features"' in src
        assert "force_features=args.force_features" in src

    def test_main_no_fuerza_el_paso_3(self, job):
        """La cadencia del reentrenamiento queda intacta: el flag no llega al paso 3."""
        src = inspect.getsource(job.main)
        paso3 = src[src.index("_step3_retrain("):]
        assert "force_features" not in paso3
        assert "force=force_retrain" in paso3

    def test_comparacion_es_la_de_la_reconstruccion(self, job):
        """Una sola implementación de 'idéntico': la de rebuild_cloud."""
        src = inspect.getsource(job._step2_features)
        assert "from scripts.rebuild_cloud import _compare_features_content" in src
