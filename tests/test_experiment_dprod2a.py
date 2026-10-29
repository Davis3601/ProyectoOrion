"""D-PROD-2a — funciones puras de la verificación de la línea .5.

Lo que importa: la línea es ceil(mediana) - 0.5 en los dos casos (mediana
entera y x.5), el evento es real >= ceil(mediana), y la puerta es de dos
lados en el agregado y en cada fold.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "_dprod2a", Path(__file__).resolve().parent.parent / "scripts" / "experiment_dprod2a.py")
dp2a = importlib.util.module_from_spec(_SPEC)
sys.modules["_dprod2a"] = dp2a
_SPEC.loader.exec_module(dp2a)


def test_linea_es_entero_publicado_menos_medio():
    # 28 -> 27.5; 27 -> 26.5; 27.5 se queda en 27.5 (publicado hoy: 28).
    assert dp2a.linea_publicada([28.0, 27.0, 27.5, 0.0]).tolist() == [27.5, 26.5, 27.5, -0.5]


def test_evento_es_real_mayor_o_igual_al_entero():
    # mediana 27.5: acierta con 28, no con 27; mediana 28: acierta con 28.
    assert dp2a.acierta([28, 27, 28, 27], [27.5, 27.5, 28.0, 28.0]).tolist() == [
        True, False, True, False]


@pytest.mark.parametrize("agg, fold, cruza", [
    (0.48, 0.50, True), (0.52, 0.50, True), (0.4799, 0.50, False), (0.5201, 0.50, False),
    (0.50, 0.45, True), (0.50, 0.55, True), (0.50, 0.4499, False), (0.50, 0.5501, False),
])
def test_puerta_de_dos_lados(agg, fold, cruza):
    assert dp2a.puerta(agg, [0.50] * 5 + [fold]) is cruza
