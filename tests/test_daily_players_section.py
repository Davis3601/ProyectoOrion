"""Formato congelado del SEGUNDO mensaje: destacados (D-PROD-1b).

Régimen 13e-2.1: el mensaje ES el producto, así que su formato exacto —
redondeos, separadores, tricodes, orden, UTF-8 — se fija aquí y cualquier
cambio tiene que pasar por estos tests.

REEMPLAZO DECLARADO DE LOS TESTS DE PRESUPUESTO DE D-PROD-1: aquel diseño
metía los destacados como sección del mensaje único y no cabía (6 225 chars
contra 4 096 de límite duro). El fallo quedó registrado en
`test_presupuesto_de_longitud_dia_lleno` con xfail(strict=True) y en
`test_cuantos_partidos_caben_con_la_seccion` (9 y 8 partidos). Con el formato
adelgazado el xfail estricto se habría puesto ROJO — esa era exactamente su
función: avisar de que el registro y los tests tocaban actualizarse en vez de
envejecer en silencio. Ambos se sustituyen aquí por el presupuesto del diseño
nuevo, que sí se cumple y se mide.

Los fixtures se construyen a mano a propósito: lo que se congela es la
PLANTILLA, no la procedencia del dato. La lección de los fixtures sintéticos
(13e-1, capa 4) aplica a parsers e índices, donde un fixture inventado puede
codificar un vocabulario falso; aquí el oracle es el texto que queremos ver.
"""
from __future__ import annotations

import pytest

from nba_predictor.api.daily_predictions import (
    TELEGRAM_MAX_CHARS,
    AvailabilityFlag,
    DailyResult,
    GamePrediction,
    PlayerHighlight,
    format_daily_message,
    format_players_message,
)

MODELO = "v1_logistic_bclean_2026-08-22"


def _jugador(nombre: str, team: str = "home", **kw) -> PlayerHighlight:
    base = dict(
        player_name=nombre, team=team,
        pts_median=24.0, pts_min=11.0, pts_max=35.0,
        reb_median=7.0, ast_median=5.0, games_in_window=10,
    )
    base.update(kw)
    return PlayerHighlight(**base)


def _partido(**kw) -> GamePrediction:
    base = dict(
        home_tricode="BOS", away_tricode="LAL", game_date="2026-10-21",
        probability_home=0.67, home_absences=["Jaylen Brown"], away_absences=[],
        availability_flag=AvailabilityFlag.OK, model_version=MODELO,
        tip_off_cdmx="19:30 CDMX",
    )
    base.update(kw)
    return GamePrediction(**base)


def _resultado(games: list[GamePrediction]) -> DailyResult:
    return DailyResult(target_date="2026-10-21", games=games, feed_down=False,
                       feed_down_reason=None, model_version=MODELO)


# ---------------------------------------------------------------------------
# 1. El mensaje 1 queda INTACTO, lleve o no datos de jugador
# ---------------------------------------------------------------------------


GOLDEN_MENSAJE_1 = (
    "🏀 Predicciones NBA · 21 oct 2026\n"
    "\n"
    "LAL @ BOS · 19:30 CDMX\n"
    "BOS 67% — LAL 33%\n"
    "Bajas BOS: Jaylen Brown\n"
    "Bajas LAL: –\n"
    "\n"
    "Predicciones estadísticas — no constituyen consejo de apuestas.\n"
    f"Modelo: {MODELO}"
)


def test_mensaje_1_sin_datos_de_jugador():
    assert format_daily_message(_resultado([_partido()])) == GOLDEN_MENSAJE_1


def test_mensaje_1_con_flag_false_y_lista_cargada():
    gp = _partido(players=[_jugador("Jayson Tatum")], players_data_available=False)
    assert format_daily_message(_resultado([gp])) == GOLDEN_MENSAJE_1


def test_mensaje_1_con_flag_true_y_lista_vacia():
    gp = _partido(players=[], players_data_available=True)
    assert format_daily_message(_resultado([gp])) == GOLDEN_MENSAJE_1


def test_mensaje_1_intacto_incluso_con_destacados_cargados():
    """El invariante del diseño nuevo: los destacados NO entran al mensaje 1."""
    gp = _partido(players=[_jugador("Jayson Tatum"), _jugador("Luka Doncic", "away")],
                  players_data_available=True)
    assert format_daily_message(_resultado([gp])) == GOLDEN_MENSAJE_1


# ---------------------------------------------------------------------------
# 2. Mensaje 2 — formato exacto
# ---------------------------------------------------------------------------


def test_mensaje_2_dos_partidos_formato_exacto():
    gps = [
        _partido(
            tip_off_cdmx="17:30 CDMX",
            players=[_jugador("Jayson Tatum", "home", pts_median=27.0, pts_min=14.0,
                              pts_max=41.0, reb_median=8.0, ast_median=5.0),
                     _jugador("Luka Doncic", "away", pts_median=31.0, pts_min=19.0,
                              pts_max=49.0, reb_median=9.0, ast_median=8.0)],
            players_data_available=True,
        ),
        _partido(
            tip_off_cdmx="19:30 CDMX", home_tricode="MIA", away_tricode="GSW",
            home_absences=[],
            players=[_jugador("Bam Adebayo", "home", pts_median=19.0, pts_min=8.0,
                              pts_max=30.0, reb_median=10.0, ast_median=4.0),
                     _jugador("Stephen Curry", "away", pts_median=29.0, pts_min=12.0,
                              pts_max=46.0, reb_median=5.0, ast_median=6.0)],
            players_data_available=True,
        ),
    ]
    esperado = (
        "🏀 Destacados · 21 oct 2026 (últimos 10 partidos)\n"
        "\n"
        "LAL @ BOS\n"
        "• Jayson Tatum (BOS): 27 pts (14-41) · 8 reb · 5 ast\n"
        "• Luka Doncic (LAL): 31 pts (19-49) · 9 reb · 8 ast\n"
        "\n"
        "GSW @ MIA\n"
        "• Bam Adebayo (MIA): 19 pts (8-30) · 10 reb · 4 ast\n"
        "• Stephen Curry (GSW): 29 pts (12-46) · 5 reb · 6 ast"
    )
    assert format_players_message(_resultado(gps)) == esperado


def test_mensaje_2_ordena_partidos_por_tip_off_como_el_mensaje_1():
    gps = [
        _partido(tip_off_cdmx="21:00 CDMX", home_tricode="MIA", away_tricode="GSW",
                 players=[_jugador("Tarde", "home")], players_data_available=True),
        _partido(tip_off_cdmx="17:30 CDMX",
                 players=[_jugador("Temprano", "home")], players_data_available=True),
    ]
    msg = format_players_message(_resultado(gps))
    assert msg.index("Temprano") < msg.index("Tarde")


def test_mensaje_2_local_antes_que_visitante():
    gp = _partido(players=[_jugador("Visitante", "away"), _jugador("Local", "home")],
                  players_data_available=True)
    msg = format_players_message(_resultado([gp]))
    assert msg.index("Local (BOS)") < msg.index("Visitante (LAL)")


def test_tricode_identifica_al_equipo_de_cada_jugador():
    """La ambigüedad que D-PROD-1 declaró sin resolver: aquí muere."""
    gp = _partido(players=[_jugador("Local", "home"), _jugador("Visita", "away")],
                  players_data_available=True)
    msg = format_players_message(_resultado([gp]))
    assert "• Local (BOS): " in msg
    assert "• Visita (LAL): " in msg


# ---------------------------------------------------------------------------
# 3. Questionable — sufijo tras el tricode, leyenda UNA vez al pie
# ---------------------------------------------------------------------------


def test_questionable_sufijo_tras_el_tricode():
    gp = _partido(players=[_jugador("Jayson Tatum", "home", status="Q")],
                  players_data_available=True)
    assert "• Jayson Tatum (BOS) (Q): 24 pts (11-35) · 7 reb · 5 ast" in \
        format_players_message(_resultado([gp]))


def test_leyenda_una_sola_vez_al_pie_aunque_haya_varios_q_y_partidos():
    """Diferencia con D-PROD-1: la leyenda es del MENSAJE, no de cada partido."""
    gps = [
        _partido(tip_off_cdmx="17:30 CDMX",
                 players=[_jugador("A", "home", status="Q"),
                          _jugador("B", "away", status="Q")],
                 players_data_available=True),
        _partido(tip_off_cdmx="19:30 CDMX", home_tricode="MIA", away_tricode="GSW",
                 players=[_jugador("C", "home", status="Q")],
                 players_data_available=True),
    ]
    msg = format_players_message(_resultado(gps))
    assert msg.count("Q = cuestionable: históricamente ~50% de los Q juegan") == 1
    assert msg.endswith("Q = cuestionable: históricamente ~50% de los Q juegan")


def test_sin_questionable_no_aparece_la_leyenda():
    gp = _partido(players=[_jugador("Jayson Tatum")], players_data_available=True)
    assert "cuestionable" not in format_players_message(_resultado([gp]))


def test_q_de_un_partido_sin_datos_no_saca_la_leyenda():
    """El jugador no se publica, así que su marca no necesita explicación."""
    gps = [
        _partido(tip_off_cdmx="17:30 CDMX",
                 players=[_jugador("Publicado", "home")], players_data_available=True),
        _partido(tip_off_cdmx="19:30 CDMX", home_tricode="MIA", away_tricode="GSW",
                 players=[_jugador("Oculto", "home", status="Q")],
                 players_data_available=False),
    ]
    msg = format_players_message(_resultado(gps))
    assert "Oculto" not in msg
    assert "cuestionable" not in msg


# ---------------------------------------------------------------------------
# 4. Especialista de triples — SOLO mediana, sin rango
# ---------------------------------------------------------------------------


def test_especialista_muestra_triples_solo_mediana():
    """Desviación 2 de D-EXP-4: en soporte corto el rango es poco informativo."""
    gp = _partido(players=[_jugador("Payton Pritchard", "home", fg3m_median=4.0,
                                    is_shooting_specialist=True)],
                  players_data_available=True)
    assert "• Payton Pritchard (BOS): 24 pts (11-35) · 7 reb · 5 ast · 4 3P" in \
        format_players_message(_resultado([gp]))


def test_no_especialista_con_mediana_de_triples_no_la_muestra():
    gp = _partido(players=[_jugador("Jayson Tatum", "home", fg3m_median=4.0,
                                    is_shooting_specialist=False)],
                  players_data_available=True)
    assert "3P" not in format_players_message(_resultado([gp]))


def test_especialista_sin_mediana_de_triples_no_revienta():
    gp = _partido(players=[_jugador("Sin Dato", "home", fg3m_median=None,
                                    is_shooting_specialist=True)],
                  players_data_available=True)
    assert "3P" not in format_players_message(_resultado([gp]))


# ---------------------------------------------------------------------------
# 5. Ventana corta — se declara por jugador, no se hereda del título
# ---------------------------------------------------------------------------


def test_ventana_menor_a_diez_lleva_su_sufijo():
    gp = _partido(players=[_jugador("Recien Llegado", "home", games_in_window=6)],
                  players_data_available=True)
    msg = format_players_message(_resultado([gp]))
    assert "• Recien Llegado (BOS): 24 pts (11-35) · 7 reb · 5 ast (últimos 6)" in msg
    assert "🏀 Destacados · 21 oct 2026 (últimos 10 partidos)" in msg


def test_ventana_de_diez_no_lleva_sufijo():
    gp = _partido(players=[_jugador("Veterano", "home", games_in_window=10)],
                  players_data_available=True)
    linea = next(ln for ln in format_players_message(_resultado([gp])).split("\n")
                 if ln.startswith("• Veterano"))
    assert linea == "• Veterano (BOS): 24 pts (11-35) · 7 reb · 5 ast"


# ---------------------------------------------------------------------------
# 6. Redondeo medio-arriba — aprobado en D-PROD-1, se conserva
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mediana,esperado", [(10.5, "11"), (11.5, "12"), (24.4, "24")])
def test_redondeo_medio_arriba_no_al_par(mediana, esperado):
    """round() de Python daría 10 para 10.5 y 12 para 11.5: incoherente a la vista."""
    gp = _partido(players=[_jugador("X", "home", pts_median=mediana)],
                  players_data_available=True)
    assert f"• X (BOS): {esperado} pts " in format_players_message(_resultado([gp]))


# ---------------------------------------------------------------------------
# 7. Degradación: partidos omitidos y mensaje vacío
# ---------------------------------------------------------------------------


def test_partido_sin_datos_se_omite_del_mensaje_2():
    gps = [
        _partido(tip_off_cdmx="17:30 CDMX",
                 players=[_jugador("Con Datos", "home")], players_data_available=True),
        _partido(tip_off_cdmx="19:30 CDMX", home_tricode="MIA", away_tricode="GSW",
                 players_data_available=False),
    ]
    msg = format_players_message(_resultado(gps))
    assert "Con Datos" in msg
    assert "GSW @ MIA" not in msg      # el bloque entero desaparece, sin rastro vacío


def test_ningun_partido_con_datos_da_cadena_vacia():
    """n8n no dispara el segundo Send; el mensaje 1 ya cubrió el heartbeat."""
    gps = [_partido(), _partido(home_tricode="MIA", away_tricode="GSW")]
    assert format_players_message(_resultado(gps)) == ""


def test_dia_sin_partidos_da_cadena_vacia():
    assert format_players_message(_resultado([])) == ""


def test_flag_true_con_lista_vacia_da_cadena_vacia():
    gp = _partido(players=[], players_data_available=True)
    assert format_players_message(_resultado([gp])) == ""


def test_feed_down_no_impide_los_destacados():
    """Los destacados no dependen del injury report para existir."""
    gp = _partido(availability_flag=AvailabilityFlag.FEED_DOWN,
                  players=[_jugador("Local", "home")], players_data_available=True)
    assert "• Local (BOS): " in format_players_message(_resultado([gp]))


def test_nys_no_marca_al_jugador_individualmente():
    """La declaración NYS es de equipo y ya viaja en el mensaje 1."""
    gp = _partido(availability_flag=AvailabilityFlag.NYS, nys_tricodes=["LAL"],
                  players=[_jugador("Visita", "away")], players_data_available=True)
    msg = format_players_message(_resultado([gp]))
    assert "• Visita (LAL): 24 pts (11-35) · 7 reb · 5 ast" in msg
    assert "sin confirmar" not in msg


# ---------------------------------------------------------------------------
# 8. PRESUPUESTO DE LONGITUD del diseño nuevo — ahora SÍ cabe
# ---------------------------------------------------------------------------


_NOMBRES_LARGOS = ["Shai Gilgeous-Alexander", "Nickeil Alexander-Walker"]


def _dia_maximo(n_partidos: int = 15) -> str:
    """Peor caso: n partidos, nombres largos reales, Q en todos, especialistas.

    Todo lo que alarga la línea a la vez — es el caso que el presupuesto tiene
    que aguantar, no el promedio.
    """
    gps = []
    for i in range(n_partidos):
        gps.append(_partido(
            tip_off_cdmx=f"{17 + i % 3}:30 CDMX",
            players=[
                _jugador(_NOMBRES_LARGOS[0], "home", status="Q", pts_median=28.5,
                         pts_min=11.0, pts_max=41.0, reb_median=7.5, ast_median=6.5,
                         fg3m_median=3.5, is_shooting_specialist=True),
                _jugador(_NOMBRES_LARGOS[1], "away", status="Q", pts_median=21.5,
                         pts_min=9.0, pts_max=38.0, reb_median=6.5, ast_median=5.5,
                         fg3m_median=4.5, is_shooting_specialist=True),
            ],
            players_data_available=True,
        ))
    return format_players_message(_resultado(gps))


def test_presupuesto_peor_caso_cabe_con_margen():
    """15 partidos x 2 destacados con todo lo que alarga, bajo 4096 con 15% de margen."""
    largo = len(_dia_maximo(15))
    assert largo <= TELEGRAM_MAX_CHARS * 0.85, (
        f"el mensaje 2 mide {largo} y el presupuesto es {TELEGRAM_MAX_CHARS * 0.85:.0f}")


def test_presupuesto_margen_medido_y_fijado():
    """Deja el número en la red de regresión: si el formato engorda, se ve aquí."""
    largo = len(_dia_maximo(15))
    margen = 1 - largo / TELEGRAM_MAX_CHARS
    assert 2400 < largo < 2700
    assert margen > 0.30


def test_mensaje_1_mas_mensaje_2_caben_en_sus_propios_limites():
    """Son dos Send distintos: cada uno tiene su propio límite de 4096."""
    gps = []
    for i in range(15):
        gps.append(_partido(
            tip_off_cdmx=f"{17 + i % 3}:30 CDMX", away_absences=["LeBron James"],
            players=[_jugador(_NOMBRES_LARGOS[0], "home"),
                     _jugador(_NOMBRES_LARGOS[1], "away")],
            players_data_available=True))
    r = _resultado(gps)
    assert len(format_daily_message(r)) <= TELEGRAM_MAX_CHARS * 0.85
    assert len(format_players_message(r)) <= TELEGRAM_MAX_CHARS * 0.85


# ---------------------------------------------------------------------------
# 9. GUARDA DE TRANSPORTE — parse_mode=HTML en el nodo Telegram (D-PROD-1c)
# ---------------------------------------------------------------------------


def test_mensajes_seguros_bajo_parse_mode_html():
    """Ningún mensaje puede contener '<', '>' ni '&'.

    POR QUÉ EXISTE: el nodo Telegram de n8n aplica parse_mode Markdown POR
    DEFECTO cuando Additional Fields no trae Parse Mode, y los guiones bajos de
    "v1_logistic_bclean_2026-09-19" reventaban el envío ("can't parse
    entities... byte offset 712"). El fix adoptado es Parse Mode = HTML
    explícito, donde SOLO esos tres caracteres son especiales y los guiones
    bajos viajan literales.

    Esta guarda fija el supuesto del que depende ese fix. Si algún día un
    nombre de jugador o de equipo trae un '&', el test se pone rojo ANTES de
    que Telegram rechace la publicación — y entonces la decisión será escapar
    en la función de formato, no descubrirlo en el canal.

    Los 27 días de heartbeats sanos no probaban nada: no contienen caracteres
    especiales. Hace falta el peor caso realista.
    """
    especiales = ("<", ">", "&")

    gps = [
        _partido(
            tip_off_cdmx="17:30 CDMX",
            home_absences=["Jaylen Brown", "Kristaps Porziņģis"],
            away_absences=["LeBron James"],
            players=[
                _jugador("Shai Gilgeous-Alexander", "home", status="Q",
                         fg3m_median=3.0, is_shooting_specialist=True),
                _jugador("Nikola Vučević", "away", games_in_window=7),
            ],
            players_data_available=True,
        ),
        _partido(
            tip_off_cdmx="19:30 CDMX", home_tricode="MIA", away_tricode="GSW",
            home_absences=[], away_absences=[],
            availability_flag=AvailabilityFlag.NYS, nys_tricodes=["GSW"],
            players=[_jugador("Bam Adebayo", "home")],
            players_data_available=True,
        ),
        _partido(
            tip_off_cdmx="21:00 CDMX", home_tricode="LAC", away_tricode="SAC",
            availability_flag=AvailabilityFlag.FEED_DOWN,
            players_data_available=False,
        ),
    ]
    dia_lleno = DailyResult(target_date="2026-10-21", games=gps, feed_down=True,
                            feed_down_reason="feed caído", model_version=MODELO)

    textos = {
        "mensaje 1 (día lleno)": format_daily_message(dia_lleno),
        "mensaje 2 (día lleno)": format_players_message(dia_lleno),
        "mensaje 1 (heartbeat)": format_daily_message(_resultado([])),
        "mensaje 2 (heartbeat)": format_players_message(_resultado([])),
    }

    for etiqueta, texto in textos.items():
        for ch in especiales:
            assert ch not in texto, (
                f"{etiqueta} contiene {ch!r}: bajo parse_mode=HTML Telegram lo "
                f"interpretaría como marcado y rechazaría o deformaría el envío. "
                f"El fix es escapar en la función de formato."
            )

    # El caso que motivó todo: los guiones bajos del model_version SÍ viajan,
    # y son precisamente los que Markdown rompía.
    assert "v1_logistic_bclean_2026-08-22" in textos["mensaje 1 (día lleno)"]
