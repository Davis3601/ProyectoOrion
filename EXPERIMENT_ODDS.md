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

## Auditoría de calendario (2026-09-13)

Se contrastó la primera captura real con una descarga del calendario NBA 2026-27.
Resultado: 41 eventos; 8 coincidencias exactas y 33 discrepancias de horario.
En las 33 discrepancias, el candidato con los mismos equipos más cercano figura
exactamente 10 minutos ANTES en el calendario NBA. Esto es una observación de esta
muestra, no una explicación confirmada del proveedor ni autorización para corregirla.
El matcher conserva su regla estricta; los 33 casos quedan sin game_id asignado.

Ejemplo: Orlando local vs Atlanta, 2026-10-21: Odds API 23:10 UTC,
calendario NBA 23:00 UTC. No se cambiaron horarios ni se registraron apuestas.
También se observó inicio del calendario regular el 20 de octubre en las capturas;
las fechas del documento canónico deben contrastarse con el calendario actualizado
antes de operación. No se modificó CLAUDE.md ni el protocolo oficial en esta rama.

Informe local: data/paper/reports/reconciliation-20260913.json. Incluye todos los
candidatos, identificadores y SHA-256 de ambas fuentes. Capturas e informe permanecen
bajo data/ (ignorado por Git). Reproducir sin red, usando un nombre de salida nuevo:

```powershell
python -m nba_predictor.research.reconcile_odds --capture data/paper/captures/nba-20260913T165900546522Z.json --schedule data/paper/captures/schedule-20260913T170057304259Z.json --output data/paper/reports/reconciliation-recheck.json
```

El auditor excluye preseason y exige equipos, UTC exacto y unicidad tanto del ID
NBA como del ID externo. Próximo paso: resolver/documentar el desfase de 10 minutos
antes de ampliar las coincidencias. Una eventual tolerancia deberá conservar ambos
horarios y usar el inicio NBA como límite conservador para registro prepartido.

## Resolución operativa del desfase (2026-09-13)

Sustituye únicamente la restricción de coincidencia exacta cuando se utiliza la
opción explícita --allow-ten-minute-offset. Sin esa opción, sigue el modo estricto.

La documentación del proveedor describe commence_time como inicio del evento y
contiene ejemplos NBA con minutos :10, pero no confirma una regla universal de
+10 minutos. Fuentes revisadas:
https://the-odds-api.com/sports-odds-data/nba-odds.html
https://the-odds-api.com/liveapi/guides/v4/
No se atribuye el desfase a TV, tip-off real u otra causa no verificada.

Regla experimental exact_or_plus_600s_v1: mismos equipos local/visitante y un único
candidato con diferencia (Odds menos NBA) de 0 o +600 segundos, exclusivamente.
No es una tolerancia de +/-10 minutos. Otros desfases y cualquier ambigüedad se
rechazan. Se consideran juntos candidatos exactos y +600: no se favorece uno si
existen dos. No ajusta las fuentes originales.

```powershell
python -m nba_predictor.research.reconcile_odds --capture data/paper/captures/nba-20260913T165900546522Z.json --schedule data/paper/captures/schedule-20260913T170057304259Z.json --output data/paper/reports/reconciliation-offset-recheck.json --allow-ten-minute-offset
```

Resultado sobre la captura: 8 matched + 33 matched_offset; cero ambiguos.
Informe: data/paper/reports/reconciliation-offset-20260913.json. El informe estricto
previo se conserva. Son coincidencias de identidad bajo la regla experimental, no
confirmación de frescura de las cuotas ni autorización para apostar.

Para registrar con la regla opcional, añade a odds_api record:

```text
--schedule RUTA_AL_CALENDARIO_JSON --allow-ten-minute-offset
```

El calendario es obligatorio en este modo. Se recomputa el cruce completo para
verificar unicidad y se compara game_id y tip_off_utc de la predicción contra NBA.
El snapshot guarda nba_tip_off_utc, provider_tip_off_utc, offset_seconds y
matching_rule. tip_off_utc sigue siendo NBA: nunca se amplía la ventana prepartido.
La antigüedad de predicción/cuota sigue validándose, sin alterar timestamps.
Antes de una captura operativa se debe renovar el calendario para detectar cambios.
La opción se registra por snapshot; no está aún congelada en policy.json, por lo
que debe fijarse de antemano para una evaluación formal, como la selección de casa.
No se generaron predicciones ni decisiones simuladas con esta captura antigua.

## Demostración completa sin API

```powershell
python -m nba_predictor.research.demo_paper_trading --output data/paper/synthetic-demo-002
```

Exige directorio nuevo. No carga .env, no usa red ni altera el reloj del equipo.
Inyecta un reloj ficticio de 2030 únicamente a las funciones del experimento.
Todos los datos son sintéticos; no son predicciones del modelo entrenado.

La ejecución inicial quedó en data/paper/synthetic-demo-001/. Abre LEEME.txt para
recorrer los archivos, o summary.json para el resumen. Conserva predicción, cuotas,
calendario, cruce, snapshot, reglas, decisión y resultados alternativos.

Con P(local)=0.60, cuota=1.80, importe=100, comisión=0 y umbral=0.02: EV=0.08 y
selección local. Victoria: +80 (ROI 80%); derrota: -100 (ROI -100%); anulación: 0,
ROI null por no existir importe liquidado elegible. Son tres escenarios excluyentes,
no una cartera de tres apuestas. Se verificaron rechazos de duplicados, cuotas
antiguas y registro posterior al inicio. La suite adicional cubre abstenciones.
Resultado: demostración correcta, 40 pruebas aprobadas, Ruff limpio; cero consultas
API. No aporta evidencia sobre rentabilidad real.

## Política v2 congelada (2026-09-13)

Sustituye los pendientes de congelación de casa/regla descritos arriba para el
nuevo ejercicio data/paper/odds-v2. Los experimentos anteriores se conservan sin
migrarlos. Archivo real: data/paper/odds-v2/policy.json (local, no versionado).

Valores iniciales elegidos para el ejercicio, no optimizados por rendimiento:
- Casa: draftkings. Mayor cobertura de la captura inicial: 41 eventos frente a
  betmgm 19, bovada 17, fanduel 10 y betrivers 8. No se compararon precios para elegir.
  Es referencia de simulación, sin afirmar acceso o ejecutabilidad en México.
- Regla: exact_or_plus_600s_v1, con verificación del calendario NBA.
- Momento: objetivo 60 minutos antes de NBA; registro Y descarga entre 65 y 55
  minutos antes, inclusive. Predicción, cuota y descarga con antigüedad máxima 300s.
- Presupuesto: 300 intentos por mes calendario UTC para este directorio. Una región
  (us), un mercado (h2h), una petición por captura; sin reintentos automáticos.
- Importe simulado fijo: 100 unidades; EV neto estrictamente >0.02; comisión 0.
  No modela impuestos, deslizamiento ni aceptación. Parámetros ilustrativos.

El contador SQLite reserva atómicamente ANTES del HTTP; fallos también consumen
intento. La falta de clave y el archivo de salida existente se detectan antes de
reservar. No se reinicia con cada proceso, sí por mes UTC. No representa el saldo
real de Odds API ni el total de otros scripts/cuentas/directorios. Conserva headers
reales de cuota para contrastarlos. No se debe borrar el contador para evadir el tope.

Inicialización reproducible para OTRO directorio nuevo:

```powershell
python -m nba_predictor.research.paper_trading --directory data/paper/odds-v2-new init --min-ev 0.02 --stake 100 --max-age-seconds 300 --commission 0 --bookmaker draftkings --matching-rule exact_or_plus_600s_v1 --monthly-requests 300
```

Captura gobernada, con ODDS_API_KEY disponible en el entorno:

```powershell
python -m nba_predictor.research.odds_api capture --directory data/paper/odds-v2 --region us --output data/paper/captures/governed-001.json
```

El registro exige la marca de presupuesto de esta política; una captura exploratoria
sin --directory no sirve para registrar en v2. Puede capturarse fuera de la ventana
para diagnosticar cobertura, pero esa captura no será elegible para una decisión.
Un único fetch puede servir a varios partidos si todos cumplen su ventana NBA.

```powershell
python -m nba_predictor.research.odds_api record --directory data/paper/odds-v2 --capture data/paper/captures/governed-001.json --prediction prediction.json --event-id ID_EXTERNO --bookmaker draftkings --schedule CALENDARIO_JSON --allow-ten-minute-offset
```

Los archivos de política siguen protegidos contra sobrescritura desde init; su hash
viaja con capturas y registros y el contador detecta ediciones posteriores a su primer
uso. No son almacenamiento inmutable contra edición manual del dueño del disco.
Si hay que cambiar reglas, crea un ejercicio nuevo e identifica la nueva versión.
Los init sin --bookmaker siguen disponibles para demos/compatibilidad v1.

Validación: 51 pruebas aprobadas, incluidas reglas, ventanas, límite y cambio mensual.
No se hicieron peticiones ni apuestas para congelar esta política. No se programó
ninguna tarea automática: el siguiente trabajo es el planificador de capturas.
