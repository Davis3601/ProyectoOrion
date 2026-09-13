# Experimento local de cuotas y apuestas simuladas

Esta rama añade una herramienta independiente del modelo y del gate oficial.
No coloca apuestas, no accede a GCP, no lee .env y no ejecuta predicciones.
Usa únicamente la biblioteca estándar. Admite captura manual y The Odds API;
la integración se ha validado con HTTP simulado, sin evidencia de ejecución real.

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

## The Odds API (ejercicio separado)

Proveedor: https://the-odds-api.com/ (dominio con guiones).
Contrato consultado: https://the-odds-api.com/liveapi/guides/v4/
Cliente: nba_predictor/research/odds_api.py. Solo NBA, h2h y cuotas decimales.
No modifica main, el modelo, la API oficial ni la configuración de GCP.

1. Crea una cuenta en el proveedor. Configura ODDS_API_KEY en el entorno de tu
   terminal local; no pegues la clave en el chat, en archivos versionados ni en
   argumentos del comando. El cliente no carga .env automáticamente.
2. Inicializa el experimento con el comando init descrito arriba.
3. Captura cuotas (una petición, sin reintentos automáticos):

```powershell
python -m nba_predictor.research.odds_api capture --region us --output data/paper/captures/nba-001.json
```

La respuesta completa queda archivada con fetched_at_utc y contadores de créditos,
sin la clave ni la URL autenticada. El nombre debe ser nuevo en cada ejecución.
Una lista vacía de eventos se conserva como observación válida; no se inventan cuotas.
El comando muestra número de eventos y cuota restante. El crédito se consume incluso
si no consigues registrar después una decisión (por ejemplo, por cuota antigua).

4. Prepara prediction.json como una fila exportada de predictions_log:
   game_id, home_team y away_team (abreviaturas NBA), p_home_win, model_version,
   predicted_at_utc. Añade tip_off_utc desde el calendario del partido. No cambies
   el timestamp de la predicción: si es antigua, se necesita una nueva predicción.
5. Identifica id del evento y key de la casa en la captura y registra:

```powershell
python -m nba_predictor.research.odds_api record --capture data/paper/captures/nba-001.json --prediction prediction.json --event-id ID_DEL_PROVEEDOR --bookmaker CLAVE_DE_LA_CASA --directory data/paper/demo
```

No se escoge una casa automáticamente. Para una evaluación formal, fija la casa
antes de observar las cuotas; el CLI actual exige seleccionarla pero no congela
esa selección en policy.json. No uses esta flexibilidad para elegir retrospectivamente.

El cruce exige ID único del proveedor, equipos exactos y hora de inicio idéntica.
Si hay un alias no reconocido o un partido reprogramado, falla para revisión; nunca
usa coincidencia aproximada ni confunde el ID externo con game_id. Las reglas de
regular season, antigüedad y registro prepartido las aplica el ledger existente.
quoted_at_utc sale de last_update del mercado o de la casa, nunca de la descarga.
El mercado h2h NBA se trata como moneyline de dos resultados incluyendo prórroga;
verifica las reglas de liquidación de la casa elegida antes de una evaluación real.

El report existente sigue recibiendo resultados explícitos. Pendientes fuera de
esta integración: captura programada, exportación automática de predicciones,
selección congelada de casa, resultados automáticos, históricos y CLV.
Para validar cobertura real hace falta una clave y una respuesta real del proveedor.

```powershell
python -m pytest tests/test_odds_api.py tests/test_paper_trading.py -q
```
