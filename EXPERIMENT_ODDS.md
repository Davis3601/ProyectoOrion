# Experimento local de cuotas y apuestas simuladas

Esta rama añade una herramienta independiente del modelo y del gate oficial.
No coloca apuestas, no consulta la nube, no lee .env y no ejecuta predicciones.
Usa únicamente la biblioteca estándar. Las cuotas se capturan manualmente:
no existe todavía un proveedor conectado ni evidencia de ejecución real.

## Uso (desde la raíz del repositorio)

Inicializa un experimento ANTES de observar oportunidades. Los valores del ejemplo
son ilustrativos, no parámetros optimizados ni recomendación de apuesta:

```powershell
python -m nba_predictor.research.paper_trading --directory data/paper/demo init --min-ev 0.02 --stake 100 --commission 0 --max-age-seconds 300
```

Crea un JSON con una fila exportada de predictions_log y estos campos adicionales.
Conserva game_id como string. Las fechas siguientes son ilustrativas: para una
captura real deben corresponder al instante actual y a un partido aún no iniciado.
No regeneres timestamps de predicciones antiguas para superar la validación.

```json
{
  "game_id": "0022600001",
  "home_team": "BOS",
  "away_team": "LAL",
  "p_home_win": 0.60,
  "model_version": "version-del-registry@hash-del-parquet",
  "predicted_at_utc": "2026-10-21T18:00:00Z",
  "quoted_at_utc": "2026-10-21T18:00:00Z",
  "tip_off_utc": "2026-10-21T23:30:00Z",
  "bookmaker": "nombre-de-la-casa",
  "source": "referencia-de-la-captura",
  "market": "moneyline_including_overtime",
  "home_decimal_odds": 1.80,
  "away_decimal_odds": 2.10
}
```

```powershell
python -m nba_predictor.research.paper_trading --directory data/paper/demo record snapshot.json
```

La primera captura válida de cada partido queda congelada, incluso si no hay apuesta.
Se elige el lado con mayor EV neto, solo cuando supera estrictamente el umbral.
La comisión se aplica a ganancias, no al capital retornado. Una apuesta por partido,
importe fijo, sin reinversión ni límite de bankroll modelado. No hay búsqueda posterior
entre casas ni selección retrospectiva de la mejor cuota.

Tras finalizar los partidos, prepara resultados.json con resultados explícitos:

```json
{"0022600001": "home"}
```

Valores admitidos: home, away, void; omitir un partido significa pendiente.
El usuario verifica que el resultado sea final; la herramienta solo puede comprobar
que el partido ya inició. Conserva el archivo fuente de resultados para reproducirlo.

```powershell
python -m nba_predictor.research.paper_trading --directory data/paper/demo report resultados.json
```

El informe devuelve beneficio neto de comisión, ROI sobre importe liquidado (excluye
anulados), abstenciones, pendientes, curva y máxima caída en orden de registro.
Esta caída no representa flujo de caja por hora de liquidación ni riesgo de ruina.
Registra hashes de reglas y resultados. Los archivos no se sobrescriben desde la
herramienta, pero el dueño del disco puede editarlos: no son prueba criptográfica
externa ni garantía de autenticidad de cuotas/timestamps.

## Alcance y próximos experimentos

Sin impuestos, slippage, límites de aceptación, CLV, intervalos estadísticos ni
Kelly. El EV utiliza la probabilidad estimada, no la verdadera. Un ROI positivo en
una muestra pequeña no demuestra ventaja. Capturar cuotas no prueba ejecutabilidad.
La cobertura depende de registrar todas las oportunidades, incluidas abstenciones;
esta herramienta no detecta partidos omitidos por el operador.

No usar el comando report para mirar métricas acumuladas del período congelado del
protocolo oficial antes de su adjudicación. Esta herramienta no cambia ese protocolo.
Una evaluación formal necesitará fijar por adelantado universo, ventana, proveedor,
reglas de selección y método estadístico. No se ejecutó ningún análisis real aquí.

Verificación local sin red ni datos de producción:

```powershell
python -m pytest tests/test_paper_trading.py -q
```
