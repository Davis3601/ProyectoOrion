"""
Tests de Settings — carga de configuración y manejo de secretos.

Contexto (incidente 2026-09-13): `ODDS_API_KEY` aterrizó en .env sin estar
declarada en Settings; con extra="forbid", pydantic no solo falló — volcó el
VALOR COMPLETO de la clave en el mensaje de error de validación, que viajó a
logs y a un contexto LLM. El campo se declaró como SecretStr para que su valor
no pueda aparecer en repr(), logs ni tracebacks.

Aislamiento: todos los tests construyen Settings con `_env_file=None`. Sin eso,
el .env REAL de la máquina entraría en la prueba y el resultado dependería de
qué tenga cada quien en su disco — el test de "sin la variable → None" pasaría
o fallaría según el entorno, que es justo lo contrario de una guarda.
"""
from __future__ import annotations

from pydantic import SecretStr

from nba_predictor.config import Settings

# Valor de prueba, deliberadamente reconocible: si aparece en algún repr o log,
# el test lo caza por substring exacto.
CLAVE_DE_PRUEBA = "clave-de-prueba-no-real-0123456789"

# El env_prefix de Settings es NBA_PREDICTOR_, así que ESTE es el nombre que
# puebla el campo desde el entorno. Verificado: la forma sin prefijo NO lo
# puebla (aunque en un .env sí bastaba para romper la carga con extra="forbid").
ENV_NAME = "NBA_PREDICTOR_ODDS_API_KEY"


class TestOddsApiKey:
    def test_settings_instancia_con_la_variable_presente(self, monkeypatch):
        """(a) La presencia de ODDS_API_KEY ya no rompe la carga."""
        monkeypatch.setenv(ENV_NAME, CLAVE_DE_PRUEBA)
        s = Settings(_env_file=None)
        assert isinstance(s.odds_api_key, SecretStr)

    def test_el_valor_no_aparece_en_repr_ni_en_str(self, monkeypatch):
        """(b) SecretStr cumple su único trabajo: no filtrar el valor."""
        monkeypatch.setenv(ENV_NAME, CLAVE_DE_PRUEBA)
        s = Settings(_env_file=None)
        assert CLAVE_DE_PRUEBA not in repr(s)
        assert CLAVE_DE_PRUEBA not in str(s.odds_api_key)

    def test_get_secret_value_si_devuelve_el_valor(self, monkeypatch):
        """(c) El consumidor legítimo (futuro job de cuotas) sí puede leerlo."""
        monkeypatch.setenv(ENV_NAME, CLAVE_DE_PRUEBA)
        s = Settings(_env_file=None)
        assert s.odds_api_key.get_secret_value() == CLAVE_DE_PRUEBA

    def test_sin_la_variable_el_campo_es_none(self, monkeypatch):
        """(d) Opcional de verdad: el job de cuotas todavía no existe."""
        monkeypatch.delenv(ENV_NAME, raising=False)
        s = Settings(_env_file=None)
        assert s.odds_api_key is None
