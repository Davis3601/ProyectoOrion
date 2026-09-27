"""Clasificacion de filas de injury report que debe ser IGUAL en los dos corpus.

POR QUE EXISTE: D-EXP-5 midio que la inferencia "G League"/"Two-Way" sobre el
texto de la RAZON no es portable entre layouts. En GemBox la razon lleva el
texto completo ("G League - On Assignment"); en ITEXT_V1 ese texto vive en la
columna CATEGORY y la razon suele ser "-", asi que buscar solo en la razon
pierde las filas. Medido: de las 199 filas Available del corpus legacy con
Category explicita, 93 (47%) DISCREPAN de la inferencia por razon, y la
poblacion G-League pasa de 16.8% a 24.7% al mirar donde hay que mirar.

Consecuencia sobre la medicion: la brecha de P(juega | Available) entre corpus
caia de -9.18pp a -4.7pp con solo cambiar de donde se lee la etiqueta. Era un
artefacto del instrumento, no un cambio de la NBA.

REGLA: una sola funcion, importada por todos los scripts de experimento. Dos
implementaciones de la misma particion son dos verdades, y la comparabilidad de
las tablas entre corpus es justo lo que se mide.

ALCANCE: soporte de MEDICION. Ningun camino de produccion la llama hoy; los
pesos oficiales de D-EXP-1 no cambian — cambia la PARTICION sobre la que se
aplican.
"""
from __future__ import annotations

import re

# "G League", "GLeague", "Two-Way", "Two Way", "TwoWay" en cualquier caja. El
# corpus escribe la etiqueta de varias formas segun el layout y el año.
GLEAGUE_RE = re.compile(r"g\s*league|two\s*-?\s*way", re.IGNORECASE)


def is_gleague_or_twoway(category: str | None, reason: str | None) -> bool:
    """True si la fila pertenece a la poblacion G-League / two-way.

    Lee la CATEGORY cuando el layout la trae y cae a la RAZON cuando no. No se
    concatenan ambos campos a ciegas: el orden importa para poder explicar de
    donde salio cada clasificacion, y la category es la etiqueta EXPLICITA del
    documento — cuando existe, manda sobre cualquier inferencia de texto libre.

    Args:
        category: columna Category (solo ITEXT_V1; None en ITEXT_V2 y GemBox).
        reason: columna Reason.
    """
    if category:
        return bool(GLEAGUE_RE.search(category))
    return bool(GLEAGUE_RE.search(reason or ""))


def available_bucket(category: str | None, reason: str | None) -> str:
    """Etiqueta de la particion de Available: "gleague_o_twoway" o "resto".

    D-EXP-1 adjudico que "Available" mezcla poblaciones y no admite un peso
    unico; esta es la particion con la que se descompone.
    """
    return "gleague_o_twoway" if is_gleague_or_twoway(category, reason) else "resto"
