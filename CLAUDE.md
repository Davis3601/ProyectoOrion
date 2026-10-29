# CLAUDE.md — Contexto del proyecto NBA Predictor

Este archivo da contexto a Claude Code sobre el proyecto. Léelo al iniciar.

**REGLA DE MANTENIMIENTO DE ESTE DOCUMENTO:** las secciones de decisiones
cerradas y el "Roadmap post-Fase 3" NUNCA se eliminan ni se reescriben al
actualizar — solo se les añaden bloques de RESULTADO al final o marcas ✅.
Este roadmap ya se perdió dos veces en reestructuraciones; no repetir.

**FUENTE ÚNICA DE VERDAD:** este archivo es el único documento canónico del
proyecto. Cualquier archivo de memoria automática de Claude Code es cache
derivado: si contradice a este documento, este documento gana, y Code
corrige el derivado — jamás al revés.

## PUNTO DE ENTRADA — dónde estamos ahora (2026-08-25)

**FASES 3, 4 Y 5a CERRADAS ✅. Modelo oficial: Logística B-limpia (0.63138
LL / 64.5% acc / Brier 0.22064). Fase 5b: Decisiones 1-11 + decisiones del
feed CERRADAS. 13d DESPLEGADA Y VERIFICADA — el sistema corre autónomo (7/7
corridas, `v1_logistic_bclean_2026-08-22` por cadencia). e-0 ✅. 13e-1 ✅
CERRADA (2026-08-24) — parser por coordenadas (`extract_words()`), 91 tests
(incl. cruce PDF↔JSON sin espacios), conteos auditados (73/17, 160/3),
regla de parada activada y honrada. **13e-2: núcleo DESPLEGADO ✅
(2026-08-25)** — endpoint v5 en producción (Cloud Run Service
`predictions-api`), primer mensaje real verificado con 11 partidos reales
del oracle, 5 capas de supuestos del entorno local cobradas. Siguiente:
integración feed/job (Decisión 4), predictions_log, n8n, canal Telegram.**

**RESTRICCIÓN DE CALENDARIO (offseason):** la temporada 2026-27 arranca en
octubre. Hito de la primera predicción real: primera semana de octubre. El
job diario corre en vacío = ensayo general gratuito. n8n debe estar
desplegado y rodado ANTES de octubre (ver Fase 6, hosting).

Arco de resultados: Trivial 0.68917 → ELO 0.63819 (vara) → **Logística
B-limpia 0.63138 ✅** → XGBoost 0.63642. Conclusión central: **la señal es
lineal en las features-diferencia; mejorar = mejores features, no mejores
modelos.**

En disco: features_v1.parquet ✅, models/ ✅, future_schedule ✅,
live_lookup ✅, predict_game ✅, predict_tonight ✅, test_live_equivalence ✅
(100/100), CloudDataStore ✅ (3/3 sellos), rebuild_cloud.py ✅ (A-D),
cdn_client.py ✅ (dual-URL), injury_report.py ✅ (13e-1 CERRADA). En la
nube: RAW completo + schedules diarios en GCS, 4 tablas BigQuery
(equivalencia exacta), features en GCS (idénticas), registry con
`v1_logistic_bclean_2026-08-22` (modelo generado por el job en cadencia),
imagen v2 del job en Artifact Registry, `nba-ingest-job` + Scheduler
ENABLED, **`predictions-api` v5 (Cloud Run Service, auth IAM)**.
NO existe aún: canal de Telegram, n8n, predictions_log, archivo del PDF en
el job. `injury_report.py` integrado al endpoint (feed en vivo) ✅; archivo
en el job pendiente (Decisión 4 del feed).

## Objetivo del proyecto

Flujo agéntico que predice la **probabilidad de victoria del equipo local**
en temporada regular NBA. Proyecto de aprendizaje: la justificación de cada
decisión importa tanto como el resultado.

## Contrato del proyecto (decisiones cerradas)

- **Target:** probabilidad de victoria del local (binaria calibrada).
- **Universo:** temporada regular; exclusión primeros 15 por equipo (regla
  de AMBOS). **Ventana:** 2016-17 a 2025-26; warmup 2014-15/2015-16
  (alimentan rolling y ELO; nunca filas ni folds).
- **Métrica primaria:** log loss. Secundarias: Brier, accuracy, calibración.
- **Umbrales:** batir trivial (0.68917) ✅ y batir ELO (0.63819) ✅ — AMBOS
  CUMPLIDOS por la logística B-limpia.
- **Línea de mercado:** NO como feature; referencia (vs Vegas pendiente —
  Camino 5). **Horizonte:** solo info pre-partido.
- Ponderación temporal: ninguna (experimento futuro). `neutral_site=1` para
  la burbuja 2020.

## Features (cerrado, ✅ implementado)

Una fila por partido; diferencias LOCAL − VISITANTE. G1: `efg_diff`,
`tov_rate_diff`, `oreb_rate_diff`, `ft_rate_diff`. G2:
`off/def/net_rating_diff`. G3: `off/def/net_rating_adj_diff`. G4:
`rest_diff` (cap 7), `home_b2b`, `away_b2b`, `neutral_site`. G5:
`availability_diff`. Target `home_won`. features_v1.parquet: 9 643 × 19,
cero NaN, home_won 0.5582.

Decisiones clave Fase 2 (implementadas): rolling ventana 10 con shift(1)
cruzando temporadas; ratios sobre promedios; ajuste primer orden (a) con
league_avg expanding; disponibilidad interpretación B (la A es LEAKAGE —
lección clave) con minutos rolling; ensamblado con regla de AMBOS ≥15 e
invariante cero-NaN. Test reina de no-leakage por grupo.

## Fase 3 — Baselines (✅)

- **Trivial:** constante = tasa local del TRAIN de cada fold.
- **ELO:** K=20, +100 local (0 si neutral), divisor 400, 75/25 → 1505 entre
  temporadas, sin margen, init 1500 en 2014-15. Predice antes de actualizar.
  Procesa todo internamente; se evalúa solo sobre filas de features_v1.
- Conexión conceptual: ELO ES una logística online de una feature con
  coeficiente por convención.

## Fase 3 — Resultados (walk-forward, 5 823 partidos) — CERRADA ✅

| Modelo | LL | Accuracy | Brier | vs ELO |
|---|---|---|---|---|
| Trivial | 0.68917 | 54.8% | 0.24801 | — |
| ELO (vara) | 0.63819 | 63.8% | 0.22333 | — |
| Logística A (adj) | 0.63142 | 64.2% | — | +0.00677 |
| Logística B (raw) | 0.63138 | 64.5% | — | +0.00681 |
| **Logística B-limpia** | **0.63138** | **64.5%** | **0.22064** | **+0.00681 ✅ OFICIAL** |
| XGBoost (depth=3) | 0.63642 | 63.7% | 0.22286 | +0.00177 |

Brier cerrado en Fase 4. La B-limpia gana en TODAS las métricas.

**Hallazgos registrados:**
- **Calibración:** el intercepto por fold elimina el sesgo del ELO
  (+8-10 pp → 0.8 pp; bin [0.5-0.6]: ELO real 46.6%, logística 54.1%).
- **Duelo A/B: empate técnico.** B por parsimonia.
- **B-limpia: degradación cero exacto.** Coeficientes legibles.
- **availability_diff: +0.21 logística / 8.5% gain XGBoost.** G5 validado.
- **Coeficientes B-limpia:** off +0.5935, def −0.3600, avail +0.2081,
  away_b2b +0.12, home_b2b −0.09. efg/oreb/ft con signo volteado por
  colinealidad ENTRE grupos (supresión; inofensivo). efg 3º por gain en XGB.
- **XGBoost NO mejora (−0.00504):** no hay no-linealidad aprovechable.
  Árboles [56, 67, 44, 113, 100, 113]. Sin bandera de auditoría.
- **Escala:** trivial→ELO 0.051; ELO→logística 0.007; log→XGB −0.005.
- **CONCLUSIÓN CENTRAL (README):** mejorar pasa por mejores features, no
  mejores modelos.

## Fase 3 — Decisión de limpieza de B (CERRADA ✅ EJECUTADA)

B-limpia = B sin `net_rating_diff` (combinación lineal exacta de off−def).
Criterio degradación < 0.001: cumplido con cero exacto → **B-limpia OFICIAL
(`OFFICIAL_LOGISTIC_COLS`, 11 features).**

## Fase 3 — Decisión del XGBoost (CERRADA ✅ EJECUTADA)

max_depth 3, LR 0.05, techo 1000, subsample/colsample 0.8. **Early stopping
con la ÚLTIMA temporada del TRAIN del fold** (verificado con
TestEvalSetAntiLeakage). Expectativa pre-registrada +0.002-0.008; >0.02 =
auditar. **RESULTADO: 0.63642 — bate ELO, no a la logística. No hay
no-linealidad.** Sin tuning ni SHAP.

## Fase 4 — Decisión del registry (CERRADA ✅ EJECUTADA)

1. **Modelo de producción:** reentrenar B-limpia con TODO features_v1.
   Métricas oficiales = walk-forward (0.63138); el modelo final no tiene
   validación propia y NO se la inventa.
2. **Registry:** directorio por versión con `model.joblib` + `metadata.json`
   (SHA-256 del parquet, features, hiperparámetros, métricas walk-forward,
   fecha, commit). `save_model`/`load_model` en DataStore.
3. **Cadencia: SEMANAL** (`RETRAIN_CADENCE_DAYS = 7`).

**RESULTADO CONFIRMADO (2026-08-12):**
- `data/models/v1_logistic_bclean_2026-08-12/` (SHA-256 `13358021f558f62d...`,
  commit `86e35ee`).
- Brier consolidado: **B-limpia 0.22064** | XGB 0.22286 | ELO 0.22333 |
  Trivial 0.24801.
- LL in-sample 0.63047 (IN-SAMPLE / NO COMPARABLE; brecha mínima = no
  memoriza).
- 167/167 tests. Paso 0: re-ingesta idempotente sin novedades; 2025-26 =
  1 225 partidos, total 14 429.

## Fase 5a — Decisiones (CERRADAS — no reabrir)

### Decisión 1 — Calendario futuro: bajo demanda, SIN persistir en `games`

`games` es la capa STRUCTURED de partidos JUGADOS. El pipeline en vivo
consulta el calendario del día directamente del endpoint y trabaja en
memoria. Si hiciera falta persistir programados, será tabla SEPARADA
`scheduled_games` — nunca `games`.

### Decisión 2 — Disponibilidad pre-partido: MANUAL v0, automatización v1

`predict_game` acepta ausencias manuales (`--out "Jugador A, B"`); el
pipeline calcula `availability_diff` con la lógica de siempre (rotación
reciente menos ausentes declarados). **Deuda PARCIALMENTE ABIERTA** →
**la automatización (v1) fue promovida a prerequisito del canal (13e-1)
por decisión de 2026-08-19** — ver Roadmap.

### Decisión 3 — Lookup con test de equivalencia EXACTA (criterio de cierre)

`predict_game` calcula las 11 features con lógica puntual; test de
equivalencia obligatorio contra la vectorizada (~100 partidos, rtol=1e-9).
**Cumplido 100/100 ✅.**

### Hito (primera semana de temporada 2026-27)

Un partido real predicho antes del tip-off, registrado en `predictions_log`
(fecha, partido, ausencias asumidas, probabilidad, versión del modelo).

## Fase 5a — RESULTADO CONFIRMADO (2026-08-13)

- `future_schedule.py` (ScheduleLeagueV2, jamás escribe a `games`),
  `live_lookup.py` (dummy-row + dos fórmulas de rolling played/DNP —
  insight: DNP en G necesita `shift(1).rolling(N)` porque el training
  propaga vía ffill el rolling del último jugado K, cubriendo [K-N-1, K-2]),
  `predict_game.py` + `predict_tonight.py` (CLI + log JSONL).
- `test_live_equivalence.py`: **100/100 exactos** (rtol=1e-9).
- Demo DAL vs CLE (2025-01-03): P(DAL)=30.5% → CLE ganó ✓.
- Tests: 171/171.

## Fase 5b — Auditoría de preparación (2026-08-13, COMPLETADA ✅)

- **RAW completo:** 14 429 JSON planos `{game_id}.json`, cruce bilateral vs
  `games` con cero huérfanos. 89.96 MB.
- **Acoplamiento cero:** ingestion/features/models/live solo hablan con
  DataStore.
- **Pre-cableado:** stub cloud.py, factory, campos GCP en Settings, deps
  `[cloud]` opcionales.
- **Contrato DataStore: 14 métodos.**
- Residuo: `notebooks/data/nba.sqlite` (56 KB) — limpiar algún día.

## Fase 5b — Decisiones (CERRADAS — no reabrir)

### Decisión 1 — Reconstruir desde RAW, no migrar el SQLite

Subir los JSON a GCS y poblar BigQuery con el pipeline existente vía
CloudDataStore. Ejerce la promesa de la capa RAW; migrar no validaría nada;
es el camino de producción. **Criterio: equivalencia exacta
STRUCTURED-cloud vs local (SQLite = oracle) + features-check en dos niveles
pre-registrados (SHA-256 idéntico ideal / contenido idéntico suficiente).**
→ **CUMPLIDA AL COMPLETO ✅** (ver RESULTADO PARCIAL).

### Decisión 2 — Mapeo de artefactos (Opción A para features)

| Artefacto | Servicio |
|---|---|
| JSON crudos | GCS (`raw/boxscores/`, `raw/boxscores_live/`) |
| teams, games, team_game_stats, player_game_stats | BigQuery |
| features_v1.parquet | **GCS parquet canónico** |
| model.joblib + metadata.json | GCS (espejo del registry) |

Features en GCS, no BigQuery: patrón de acceso = archivo; preserva cadena
SHA-256 → metadata → modelo. Reversible con `bq load` si hiciera falta SQL.
Opción C (ambos) descartada: desincronización silenciosa posible.

### Decisión 3 — Idempotencia en BigQuery: MERGE con staging

Staging temporal (expiración 1h) + MERGE + delete post-MERGE. Claves:
`games`(game_id); `team_game_stats`(game_id,team_id);
`player_game_stats`(game_id,player_id); `teams`(team_id). Garantía EN LA
ESCRITURA — la tabla física ES el estado limpio. Descartados
delete-and-insert y append+dedup-en-lectura.

### Decisión 4 — Layout GCS y nombres
gs://{bucket}/
├── raw/boxscores/{game_id}.json # legacy stats.nba.com (histórico)
├── raw/boxscores_live/{game_id}.json # CDN (2026-27+)
├── raw/schedules/scheduleLeagueV2_{fecha}.json
├── raw/injury_reports/ # PDFs oficiales (método 15; 13e-1)
├── features/features_{version}.parquet
└── models/{version_name}/

**Entorno real:** proyecto `predictorsnonprod` (UN SOLO proyecto — decisión
explícita: dev/prod lo da la arquitectura, mode=local vs mode=cloud;
`nba_predictor_test` es scratch de integración), bucket
`predictorsnonprod-nba-predictors`, región `us-south1` (inmutable).
**Vertex AI DESCARTADO** (no escala a cero, ~$50/mes vs <$5; registry
propio cumple; reevaluable con múltiples modelos — modelos-en-GCS es
prerequisito de esa migración).

### Decisión 5 — Testing del CloudDataStore: híbrido unit + integración

Unit (mocks) siempre; integración (`@pytest.mark.integration`, GCP real,
dataset `nba_predictor_test` + prefijo `integration_test/`) manual.
Emuladores descartados (BigQuery sin emulador oficial). **Definición de
"hecho": unit ✅ + integración ✅ + equivalencia de reconstrucción ✅ —
LOS 3 SELLOS CUMPLIDOS.**

### Decisión 6 — Cloud Run Job ÚNICO diario con lógica condicional

`ingest_job` con 3 pasos: (1) ingesta incremental siempre; (2) rebuild de
features solo si hubo nuevos; (3) reentrenamiento solo si cadencia cumplida
(fecha del metadata del registry; config = única fuente de la cadencia) o
`--force-retrain`. Decisiones loggeadas ruidosamente. Scheduler diario
12:00 UTC. En offseason corre en vacío = ensayo. Descartados: 3 jobs
(orquestación innecesaria) y retrain separado (doble origen de la cadencia).

### Decisión 7 — Dockerfile del ingest_job

- Base **`python:3.12-slim`** — **3.12 promovido a versión canónica
  (2026-08-14):** el lockfile generado en el venv 3.12 (scipy 1.18.0
  requiere ≥3.12) chocó con la base 3.11 original en build. La evidencia
  validada del proyecto (todos los tests, reconstrucción y equivalencias)
  corrió en 3.12 → la versión validada gana a la declarada. **Lección:
  lockfile y runtime deben compartir versión de Python.**
- `requirements.lock` congelado con `==` (pyproject = intención, lock =
  reproducción; gemelo del SHA-256 del parquet). 50 paquetes runtime.
- Capas lock → install → código. SIN data/, tests/, notebooks/, .env
  (.dockerignore). Config vía env vars del despliegue; sin credenciales en
  imagen (identidad = SA). Entrypoint `scripts/ingest_job.py`; exit codes
  como señal. Un Dockerfile por artefacto.

### Decisión 8 — Service account dedicada `ingest-job-sa`

Jamás la default. `bigquery.dataEditor` sobre el DATASET,
`bigquery.jobUser` a nivel proyecto, `storage.objectAdmin` sobre el BUCKET.
Sin acceso a `nba_predictor_test`. **+ `roles/run.invoker` sobre el job
(añadido 2026-08-15):** el Scheduler falló con PERMISSION_DENIED (code=7)
en su primer disparo — invocar un job es permiso distinto de ejecutarlo.
Lección: la SA lleva dos sombreros (identidad DEL job / identidad que LO
invoca). Secret Manager diferido → **saldrá del diferimiento con el token
del bot de Telegram (primer secreto real; 13e-2).**
**+ `roles/bigquery.readSessionUser` a nivel proyecto (añadido 2026-08-25,
blindaje preventivo):** ver capa 5 de la cebolla en el RESULTADO 13e-2.

### Decisión 9 — Migrar ingesta a CDN/S3 (stats.nba.com bloqueado en cloud)

**Causa:** Akamai WAF silencioso desde IPs datacenter, confirmado 3/3 desde
Cloud Run (ReadTimeout). **Además:** BoxScoreTraditionalV2 ya no publica
datos para 2025-26+ (deprecado oficialmente; V3 lo reemplaza) — la
migración era necesaria independientemente del bloqueo.

**Diseño dual-URL con fallback:** `CDNClient` con lista ordenada de bases:
(1) `cdn.nba.com/static/json`, (2)
`nba-prod-us-east-1-mediaops-stats.s3.amazonaws.com/NBA` (backend S3
público). Tenacity por base; HTTPError no se reintenta (cae a la
siguiente); loggea qué base sirvió; ambas fallan = RuntimeError.

**Transformaciones aprobadas:** minutes ISO 8601 → decimal (campo
`minutes`, JAMÁS `minutesCalculated`; PT00M → None); plus_minus equipo =
points − pointsAgainst; filtrar `status != ACTIVE` (fila = activado);
neutral_site = 0 para 2026-27+; gameType==2 = Regular Season; gameStatus==3
= finalizado.

**RESULTADO (2026-08-15) — CERRADA ✅:**
(a) Equivalencia de parsers ~100 partidos: exacta en TODO lo consumido
(team stats, minutes, started, estructura). 18/32 500 divergencias en
contables individuales = **correcciones oficiales post-partido de la NBA**
(9 pares suma-cero; verificación V3 fresco 3/3 == SQLite). Test refinado:
tier estricto (falla) / tier suave (pares suma-cero → warning informativo;
delta neto ≠ 0 → falla).
(b) Limitación documentada: partidos vía CDN no reciben correcciones
post-hoc. Impacto en el modelo: NULO (ninguna feature consume contables
individuales). Reevaluable si Camino 5 las usara.
(c) **Diagnóstico desde Cloud Run (2026-08-15):** CDN frontal 403 en AMBOS
orígenes (local y datacenter); S3 200 completo (schedule 661ms, boxscore
61ms). Producción opera vía fallback S3; el WARNING diario del frontal es
monitor gratuito de si cambia de política.

### Decisión 10 — Publicación del canal vía n8n (2026-08-19, decisión de Antonio)

El canal de Telegram con las predicciones diarias se publica desde n8n
(plano único de orquestación), NO desde un Cloud Run Job dedicado
(propuesta alternativa evaluada y descartada por Antonio: prefiere
consolidar todo el flujo en n8n). **Frontera extendida del principio #10
de Fase 6:** n8n dispara el cron y transporta el mensaje; TODO contenido
predictivo lo produce el servicio Python — n8n invoca un endpoint
"predicciones del día" (Cloud Run Service, la API de 13e-2) que
internamente hace schedule → ausencias → predict_game por partido → JSON.
JAMÁS lógica del modelo dentro de workflows. **Consecuencias aceptadas:**
n8n es infraestructura crítica del hito de octubre (desplegar y rodar
ANTES); error handling del workflow de publicación = pendiente de diseño
explícito (¿qué hace el canal si el job falla?).

### Decisión 11 — Hosting de n8n: arranque barato con trigger pre-registrado

Fase inicial (canal, sin pagos): opción de bajo costo — VM con Docker
Compose (~$13-15/mes e2-small; SQLite viable en VM porque el descarte de
SQLite aplicaba al filesystem efímero de Cloud Run) o Cloud Run
`min-instances=0` + Cloud SQL (~$10-12/mes; el supuesto "min=1 obligatorio"
corregido: scale-to-zero SÍ recibe webhooks vía cold start y
Stripe/Telegram reintentan — el costo real es latencia, no pérdida).
**Trigger de upgrade pre-registrado: cuando existan webhooks de pago
reales → reevaluar min-instances=1/recursos dedicados, contrastando contra
ingresos, no contra el presupuesto <$5 del sistema predictivo.** Variante
concreta: diferida al despliegue (pre-octubre).
→ **RESUELTA (2026-08-24): ver Decisión 13e-2.3.**

### Decisiones del feed de injury report (CERRADAS 2026-08-22)

Completan el pendiente "3 decisiones de diseño" del spike; son 4 tras la
resolución de la contradicción del e-2:

1. **Fuente primaria: PDF oficial** (`ak-static.cms.nba.com`). balldontlie
   = fallback DOCUMENTADO, no implementado (YAGNI; se implementa si la
   primaria muere).
2. **Solo status==Out cuenta como ausencia en v1.** Doubtful/Questionable/
   Probable se registran pero no restan disponibilidad. Experimento
   pre-registrado para Camino 5: medir P(juega | Doubtful/Questionable)
   con el archivo histórico de PDFs para decidir si futuros modelos los
   incorporan. NYS = disponibilidad desconocida (lista vacía + flag),
   jamás "sin ausencias" silencioso.
3. **El feed EN VIVO vive en el ENDPOINT (13e-2), no en ingest_job.**
   Razón temporal: el snapshot madura durante el día (17/30 equipos NYS
   a la 1:15PM en el fixture). El job corre 12:00 UTC (madrugada US);
   convertir ese snapshot en ausencias sería predecir con la peor versión
   del dato. El endpoint descubre el snapshot más fresco al momento de
   la invocación.
4. **Reconciliación job/endpoint (resuelve la ambigüedad del texto del
   e-2 "integrar a ingest_job y al endpoint"):** DOS usos del mismo PDF
   con requisitos temporales opuestos:
   - **ingest_job: ARCHIVO histórico.** Paso nuevo barato: descubrir
     snapshot del día → GET → `save_raw_injury_report` (método 15). SIN
     parsear, sin ausencias, sin tocar features. Best-effort: PDF ausente
     o red caída → WARNING, jamás error (el archivo no puede tumbar la
     ingesta de boxscores, misión crítica del job). Razón: el experimento
     de Camino 5 exige un archivo COMPLETO; los PDFs no son recuperables
     retroactivamente con garantías (retención no prometida; el formato
     de URL ya mutó 2024→2026). Misma lógica que `raw/schedules/`:
     persistir hoy porque reconstruir mañana puede ser imposible. La
     completitud la garantiza el sistema autónomo (Scheduler probado),
     no n8n.
   - **endpoint: FEED en vivo** (descubrir → parsear → matching →
     ausencias) + persiste también su propio snapshot (idempotente por
     nombre; dos snapshots/día enriquecen el experimento con cortes
     temprano/tarde).
   "El feed vive en el endpoint" sigue siendo verdad: el job no alimenta
   ninguna predicción con el PDF — solo archiva RAW, como todo lo demás.
- [CERRADO 2026-08-25] Fallo de persistencia del snapshot en el endpoint: best-effort idéntico al ingest job — save_raw_injury_report falla → logging.warning, la respuesta se sirve completa e intacta (200). Razón: la misión del endpoint es la predicción; el snapshot es evidencia secundaria. Matiz que descarta el 500: la degradación declarada de 13e-2.5 aplica a la CALIDAD DEL DATO SERVIDO (feed de lesiones no disponible → se declara); aquí el feed ya operó y la predicción es íntegra — fallo de archivo ≠ degradación del dato. Costo aceptado: absences_applied podría referenciar un snapshot no persistido; mitigación: WARNING visible en logs de Cloud Run + el archivo diario independiente del ingest job (dos escritores, almacén idempotente — la pérdida total exige doble fallo).

### Decisiones de diseño 13e-2 (CERRADAS 2026-08-24)

**13e-2.1 — Contrato del endpoint: B-con-data.** El endpoint devuelve
`{message, data}`: `message` = texto FINAL listo para Telegram, construido
por función pura en Python (`format_daily_message`) bajo unit tests que
fijan formato exacto (redondeos, orden, banderas, disclaimer); `data` =
JSON estructurado por partido (equipos, probabilidad, ausencias asumidas,
flags NYS/feed, versión del modelo). Razón: EL MENSAJE ES EL PRODUCTO —
lo único que el suscriptor ve no puede ser lo único fuera del régimen de
tests. n8n transporta `message` sin tocarlo (extensión natural de la
Decisión 10). `data` no es para que n8n formatee: es observabilidad,
predictions_log, y la interfaz futura del agente editorial de Fase 6
(capa ADITIVA que consume data y produce comentario — jamás toca números).
Determinista ≠ estático: la función tiene toda la lógica condicional
necesaria (secciones por nº de partidos, líneas de bajas, banderas NYS),
cada rama con su test.

**13e-2.2 — Auth n8n→endpoint: OIDC contra IAM de Cloud Run.** El servicio
se despliega SIN --allow-unauthenticated; SA nueva `n8n-invoker-sa` con
`roles/run.invoker` SOLO sobre el servicio de predicciones. n8n obtiene el
token OIDC del metadata server (disponible en ambas variantes de hosting)
y llama con Authorization: Bearer. Cero código de auth en el endpoint
(verifica Google antes de tocar nuestro código), cero secretos estáticos
que rotar. API key artesanal DESCARTADA (sistema de auth casero + endpoint
público a nivel de red). Spike pre-registrado en el despliegue: 2 nodos en
n8n (token + llamada); expectativa 200 con token / 403 sin él. Fallback
documentado si el spike fallara: API key (no construir preventivamente).
NOTA DE ALCANCE: esto protege el ENDPOINT; n8n mismo es públicamente
alcanzable (UI + webhooks futuros) con su propia auth de aplicación
(login n8n + encryption key en Secret Manager).

**13e-2.3 — Decisión 11 RESUELTA: n8n en Cloud Run + Cloud SQL, cron
invertido.** Evidencia nueva (2026-08-24): guía oficial de Google (blog
nov-2025) + codelab oficial + guía espejo de n8n para desplegar la imagen
oficial de n8n en Cloud Run con Cloud SQL (PostgreSQL) y Secret Manager.
TRAMPA DETECTADA en la Decisión 11: min=0 recibe webhooks vía cold start,
pero el Schedule Trigger de n8n corre DENTRO del proceso — con cero
instancias no hay cron. FIX: invertir el disparador — Cloud Scheduler
(pieza ya probada) → POST al webhook del workflow → cold start → publica →
duerme. n8n sigue orquestando (Decisión 10 intacta); el despertador es de
GCP. min-instances=0 se conserva. Detalle de despliegue: n8n usa /healthz
por defecto y Cloud Run lo reserva → setear N8N_ENDPOINT_HEALTH. VM
e2-small DEGRADADA a fallback documentado (fricción inesperada del
serverless). Trigger de upgrade de la Decisión 11 intacto (webhooks de
pago reales → reevaluar min=1 contra ingresos).
- [CERRADO 2026-08-25] Cloud SQL para n8n: instancia n8n-db (POSTGRES_16, Enterprise, db-f1-micro, us-south1-b, HDD 10GB, zonal, backups ON, PITR OFF, deletion-protection ON). IP pública 34.174.135.115 sin redes autorizadas — acceso solo vía conector Cloud SQL/IAM. Password de postgres fijado por prompt interactivo (nunca en historial ni en contexto LLM); destino: Secret Manager (paso 3). Costo estimado: ~$9-10 USD/mes, partida dominante de los $10-13 previstos.
- [CERRADO 2026-08-25] Secret Manager: primeros secretos reales del proyecto. n8n-db-password (password de postgres de n8n-db) y n8n-encryption-key (32 bytes hex generados con RNG criptográfico de .NET, nunca vista en pantalla), ambos versión 1, replicación automática. Carga vía archivo temporal ASCII con -NoNewline (evita trampa UTF-16 del > en PowerShell 5) + borrado inmediato + Clear-History. Regla operativa: los valores jamás se imprimen ni entran en contexto LLM; verificación solo estructural (versions list). La encryption-key se fija ANTES del primer arranque de n8n para evitar el bug clásico de key autogenerada + min-instances=0 que corrompe credenciales cifradas.
- [CERRADO 2026-08-25] Prerequisitos deploy n8n (4a): imagen oficial espejada y pineada en Artifact Registry us-south1-docker.pkg.dev/predictorsnonprod/n8n/n8n:2.36.7 (digest sha256:770da605a7dfdda55838fb2b66b701435690ffcce5d3067585fc7e3cb17b168f, single-platform linux/amd64, versión verificada con n8n --version contra la imagen local). Base de datos n8n creada en la instancia. Usuario dedicado n8n-user (mínimo privilegio; el superusuario postgres queda como credencial de administración fuera de n8n). Hallazgos: (a) en Cloud SQL/Postgres el usuario nace con password obligatorio — gcloud sql users create sin password da HTTPError 400, y --prompt-for-password no existe en users create; vía limpia: Read-Host -AsSecureString → --password=$plain → Remove-Variable. (b) Secreto n8n-db-password rotado a versión 2 (password de n8n-user); versión 1 (postgres) deshabilitada para que la SA de n8n no pueda leerla. SA n8n-invoker-sa creada con roles/cloudsql.client (proyecto) + secretmanager.secretAccessor (solo sobre los dos secretos); será también la identidad de runtime del servicio n8n y emisora del token OIDC del spike.
- [CERRADO 2026-08-25] Deploy n8n (4b): Cloud Run Service n8n, revisión n8n-00001-kdp, URL https://n8n-1095892399320.us-south1.run.app (determinística, predicha y confirmada). Imagen pineada 2.36.7 del espejo propio. Config: min=0/max=1 (max=1 OBLIGATORIO: n8n modo regular asume proceso único; 2 instancias = ejecuciones duplicadas), 2Gi, --no-cpu-throttling, puerto 5678, sleep 5 antes de n8n start (workaround oficial: espera al socket del conector Cloud SQL), N8N_ENDPOINT_HEALTH=health (Cloud Run reserva /healthz — verificado: /health responde 200), GENERIC_TIMEZONE=America/Mexico_City, URLs (N8N_HOST/WEBHOOK_URL/N8N_EDITOR_BASE_URL) fijadas desde el primer deploy. DB por socket del conector (/cloudsql/predictorsnonprod:us-south1:n8n-db) como n8n-user; secretos montados con :latest. Decisión de seguridad: --allow-unauthenticated + login propio de n8n (patrón de la guía oficial; el cliente principal es un navegador humano, sin identidad IAM; webhooks con path UUID). predictions-api sigue --no-allow-unauthenticated. Upgrade futuro pre-registrado si esto escala: IAP delante de Cloud Run — no construir ahora. Verificación end-to-end: dashboard cargado en navegador (migraciones OK sobre la base n8n) — cierra definitivamente los sospechosos de password n8n-user↔secreto v2.
- [CERRADO 2026-08-25] Auth n8n→predictions-api (pasos 5 y 6): binding roles/run.invoker para n8n-invoker-sa SOLO sobre el servicio predictions-api (política verificada: único invoker, sin allUsers). Spike OIDC adjudicado en verde: workflow spike-oidc de 3 nodos (Manual Trigger → GET al metadata server con header Metadata-Flavor: Google y audience=URL determinística de predictions-api, Response Format Text → GET a /predictions/today con Authorization: Bearer {{ $json.data }}). Resultado: 200 con token (contrato {message, data}); sin header Authorization, rechazo en la puerta de Cloud Run (nodo en error por respuesta no-2xx, código verificado). Fallback de API key: MUERTO sin construirse, como se pre-registró. Reglas operativas derivadas: (a) la URL determinística (predictions-api-1095892399320.us-south1.run.app) es la canónica del proyecto — la legada (-6q3pf7wkua-vp.a.run.app) no se usa en configuración nueva, para que audience y llamada nunca se mezclen; (b) el metadata server no lleva autenticación (la red interna es la credencial; Metadata-Flavor es anti-SSRF, no auth); (c) cero secretos almacenados en n8n para esta integración.
- [CERRADO 2026-08-25] Cron invertido (paso 7) y CIERRE DEL CAPÍTULO n8n: Cloud Scheduler job nba-publish-daily, 0 13 * * * America/Mexico_City (absorbe cambios de horario), POST a la URL de producción del webhook de daily-predictions, attempt-deadline 300s (holgura para peor caso térmico: cold start n8n + sleep 5 + cold start encadenado de predictions-api), sin reintentos (maxRetryDuration 0s — se decidirán con datos reales de la validación). Scheduler SÍ disponible en us-south1: proyecto sigue mono-región sin excepciones. Workflow daily-predictions publicado (n8n 2.x usa modelo borrador/publicación con versiones — la ejecución registra la versión publicada, p.ej. e032afe2; patrón registry aplicado a workflows). Webhook con Respond: When Last Node Finishes — el 200 del Scheduler certifica cadena completa. Verificación: disparo manual (jobs run) → ejecución Succeeded 5.63s, 3 nodos verdes, contenedor CALIENTE (editor abierto). PRE-REGISTRO PENDIENTE: primera ejecución autónoma en frío (13:00 CDMX) — expectativa éxito < 300s, duración esperada 30-90s; auditar en Executions + status del job; un timeout sería hallazgo a adjudicar, no probado hoy. Estado del capítulo: budget ✅, Cloud SQL ✅, secretos ✅, deploy n8n ✅, run.invoker ✅, spike OIDC ✅, cron invertido ✅. Decisión 10 intacta: Scheduler = despertador; n8n = orquestación.
- [HALLAZGO+FIX 2026-08-26] Fallo silencioso en frío del cron invertido, cazado por la auditoría fría pre-registrada. Síntoma: Scheduler recibió 200 a las 19:00:02Z pero Executions vacío y predictions-api sin requests (verificado con gcloud logging read por franja temporal). Causa raíz (cronología de logs): el POST despierta el contenedor; el STARTUP TCP probe de Cloud Run pasa al abrirse el puerto 5678 (19:00:32); los workflows se activan 18s DESPUÉS (Activated daily-predictions 19:00:50); el POST aterrizó en el limbo intermedio, donde la página de arranque de n8n responde 200 sin ejecutar nada. El 200 del limbo derrota reintentos del Scheduler (no reintenta éxitos) y haría pasar un probe HTTP sobre /health. FIX (doble toque — desacoplar despertar de disparar): segundo job nba-warmup-daily, 58 12 * * * America/Mexico_City, GET a /health de n8n, deadline 180s — despierta el contenedor 2 min antes del POST real; su propia respuesta es irrelevante. Decisión 10 intacta (el despertador toca dos veces, sigue sin orquestar). Alternativa min-instances=1 DESCARTADA por costo (~$15+/mes contra decisión cerrada). PRE-REGISTRO verificación 2026-08-27: no tocar n8n antes de las 13:05; expectativa: ejecución de daily-predictions REGISTRADA en Executions ~13:00 con duración de segundos + status {} en ambos jobs; si Executions vuelve a quedar vacío, escalar (startup probe sobre readiness real + reevaluar min=1 contra costo). Registro para el gate operativo: 2026-08-26 = primer fallo de autonomía con silencio, en validación pre-ventana. Cosecha secundaria (backlog, no urgente): (a) Postgres 16 en soporte de compatibilidad según n8n — upgrade eventual a 17+ (la versión SÍ se puede subir in-place; adjudicación honesta: la guía usaba 17, se eligió 16 como "irrelevante", error menor); (b) WEBHOOK_URL deprecada → renombrar a N8N_WEBHOOK_URL en redeploy futuro; (c) warning de Python task runner irrelevante (no se usan nodos Python).
- [CERRADO 2026-08-27] Verificación del doble toque: FALLO SILENCIOSO MUERTO. Pre-registro cumplido punto por punto: warmup 18:58:00Z status {}, publish 19:00:02Z status {}, ejecución de daily-predictions REGISTRADA en Executions (ID#5, 13:00:02, Succeeded 15.447s, 3 nodos verdes, versión e032afe2), y testigo independiente: GET /predictions/today 200 a las 19:00:03Z en logs de predictions-api — servido por v7 (revisión 00007-94v), estreno del código nuevo en cadena real sin novedad (rest day: rama heartbeat, cero filas en predictions_log por diseño). Matiz de los 15.447s vs 1.071s del día tibio: el warmup despierta n8n, NO predictions-api — el cold start del endpoint (~12-14s: uvicorn + modelo desde GCS) vive dentro de la ejecución del workflow; esperado, absorbido por el deadline de 300s, no es hallazgo. Mismo día, primera ejecución del cron de ingest con v5 (nba-ingest-job-kltcm, 12:00:02, exit=0): Decisión 4 estrenada con datos reales en su rama de fallo benigno — injury report inexistente en offseason → WARNING con 20 sufijos probados y diagnóstico, ingesta continúa sin interrupción. Best-effort verificado en producción. Estado: el cron invertido queda VERIFICADO EN FRÍO; la infraestructura de publicación está completa y probada — solo falta el nodo de Telegram para que el mensaje llegue a un humano.
- [CERRADO 2026-08-27] Capítulo Telegram y FASE DE VALIDACIÓN OPERATIVAMENTE COMPLETA. Bot creado vía BotFather; token en Secret Manager (telegram-bot-token v2 enabled, v1 disabled — el primer token llegó corrupto en tránsito y dio 401, causa no determinada, rotado tras validar con getUpdates ok:true; SIN binding IAM: es bóveda de recuperación, nadie lo lee programáticamente). Canal privado con bot como admin (solo permiso de publicar). Credencial telegram-nba-predictor en n8n (cifrada en Postgres con la encryption key pre-generada — cumpliendo su propósito original). Nodo Telegram Send Text Message al final de la cadena: Chat ID -100..., Text = expression {{ $json.message }}, parse mode texto plano (deliberado: cero superficie de parseo Markdown; lo que Python emite es lo que el canal muestra), Append n8n Attribution DESACTIVADO. Con Respond When Last Node Finishes, el 200 del Scheduler certifica desde ahora PUBLICACIÓN ENTREGADA, no solo predicción calculada. Hallazgos del capítulo: (a) expression pegada en modo Fixed viaja como string literal — verificar siempre el toggle Expression y el preview resuelto; (b) HERENCIA DEL SPIKE: call-predictions-api arrastró Response Format Text del nodo get-token → $json.message undefined; fix: JSON en el nodo 3, Text se queda SOLO en get-token (el JWT es string plano); alternativa JSON.parse en expression DESCARTADA (lógica de parseo no testeada en n8n); (c) regla operativa derivada: toda republicación del workflow lleva disparo de prueba (jobs run) antes de dejarla al cron — el sistema PUEDE publicar contenido malformado si un nodo se configura mal, y esa categoría es la que el gate operativo castiga. Decisión de diseño: Send Text Message sobre rich message — el message del contrato ya está formateado y congelado bajo tests; n8n transporta, no compone (Decisión 10). Verificación final: mensaje limpio en canal (🏀 Predicciones NBA · 27 ago 2026 / Sin partidos hoy.) — cadena UTF-8 íntegra FastAPI→n8n→Telegram→pantalla. ESTADO DE LA FASE: desde 2026-08-28, ciclo autónomo diario 12:58 warmup → 13:00 disparo → OIDC → predicción v7 → predictions_log → Telegram, sin intervención humana. Pendientes hacia octubre: verificación diferida al primer día con partidos (model_version poblado, campos nuevos de GamePrediction, primera fila real del log), y la adjudicación del criterio pre-registrado en enero 2027.

**13e-2.4 — Validación antes de comercialización (dos fases, UN sistema).**
Fase de VALIDACIÓN (octubre →): se construye TODO el pipeline de
producción (endpoint + n8n + Scheduler + bot) publicando al canal privado
de UNA persona (Antonio). Cada mensaje diario = test de integración
end-to-end + heartbeat + expediente (predictions_log con timestamp
pre-tip-off verificable — futuro material de marketing honesto). Fase
COMERCIAL: NO se enciende nada nuevo — se añaden suscriptores a un sistema
ya rodado; el switch es de AUDIENCIA, no de sistema. Rechazado el diseño
"n8n apagado + vía de consulta paralela": estrenaría modelo e
infraestructura a la vez ante clientes, y validaría un camino distinto al
que se vende. El HITO de octubre se redefine: primera predicción real
PUBLICADA POR EL PIPELINE COMPLETO al canal de validación. Fase 6 queda
gateada por la decisión de comercializar, no por calendario.
**PENDIENTE NOMBRADO: criterio de comercialización pre-registrado.** Lo
que octubre valida es la OPERACIÓN (pipeline vivo sin fallos, features
sin leakage), NO el modelo (ya validado: walk-forward 5 823 partidos);
30-50 partidos son varianza pura. Borrador del criterio: (a) N semanas
sin fallos operativos + (b) log loss en vivo CONSISTENTE con walk-forward
(margen por definir con el tamaño de muestra en mano). Redactar antes de
octubre.
**PRESUPUESTO AUTORIZADO:** ~$10-13 USD/mes incrementales (dominado por
Cloud SQL db-f1-micro, único costo fijo que no escala a cero); escenario
negativo del experimento oct-dic ~$40; techo con colchón $60. Salida
limpia: apagar Cloud SQL + n8n → <$5/mes en minutos. TAREA DE DESPLIEGUE:
budget alert GCP a $25/mes (avisos 50/90/100%).
- [CERRADO 2026-08-25] Budget alert GCP: 470 MXN/mes (≈ $25 USD, ago-2026) sobre predictorsnonprod, umbrales 50/90/100%, avisos por email a admins de facturación. Recurso: billingAccounts/01A1EE-508485-380FE5/budgets/ecdf594a-fa3c-4533-828e-a7f4a6647032. Hallazgo: la API de budgets exige la moneda de la cuenta (MXN); 25USD produjo INVALID_ARGUMENT sin detalle de campo. Referencia mental sigue en USD ($10-13/mes esperado, techo $60 experimento).
- [CERRADO 2026-08-25] Diseño de predictions_log: el log es EVIDENCIA, no telemetría — cada predicción congelada con timestamp anterior al partido, en almacén append-only; contra él se medirá el criterio pre-registrado de comercialización. Schema (una fila por partido por servida): game_id, game_date, home_team, away_team, p_home_win, model_version (id del registry + hash), predicted_at_utc, served_by (revisión de Cloud Run), absences_applied (player_ids Out aplicados o referencia al snapshot persistido del injury report). Deliberadamente SIN resultado del partido: el grading se computa después como JOIN contra resultados en BigQuery — jamás como update de la fila (grading = query; log = intocable). Almacén: BigQuery, tabla predictions_log en dataset nba_predictor; espejo SQLite local vía patrón Repository. Escritor: el endpoint de predictions-api (Decisión 10: lógica crítica en Python, n8n solo transporta). Semántica: se loggea CADA servida sin deduplicar (predicted_at_utc las distingue); la ejecución "de record" del día se identifica en el análisis (primera del día o la más cercana a las 13:00), nunca en la escritura — log tonto y completo, interpretación en el query.
- [CERRADO 2026-08-26] Fallo de escritura a predictions_log en el endpoint: best-effort — logging.warning, la respuesta se sirve completa (200), misma lógica que el fallo de persistencia del snapshot (la misión del endpoint es la predicción). Matiz reconocido: predictions_log es LA evidencia del criterio, un fallo aquí pesa más que el del snapshot; mitigación estructural: el gate operativo audita diariamente "hay filas en predictions_log" durante la ventana (operación permitida por el no-peeking), por lo que un fallo de log no puede pasar silencioso más de un día. Best-effort + auditoría operativa diaria = mismo patrón, con red.
- [CERRADO 2026-08-25] CRITERIO PRE-REGISTRADO DE COMERCIALIZACIÓN (13e-2.4) — congelado 10 semanas antes del primer partido; se adjudica en enero 2027 SIN renegociación. (1) MÉTRICA: Brier score decide (acotado, penalización no dominable por colas); log loss se reporta como diagnóstico (divergencia fuerte entre ambas = hallazgo de descalibración de colas). Ambas se computan sobre las mismas predicciones del único modelo publicado (B-limpia); shadow models futuros permitidos vía model_version en predictions_log, jamás publicados ni decisores. (2) BASELINE: B0 = predicción constante igual a la tasa de victoria local de la temporada 2025-26 (Brier esperado ~0.2475) — el listón "¿sabe algo?", inatacable por construcción. El ELO propio se reporta como referencia, no decide (calibra a 8.7pp, sería listón blando con superficie de disputa). El mercado (líneas de cierre) queda EXPLÍCITAMENTE fuera de v1; consecuencia vinculante: PROHIBIDO todo claim o insinuación de rentabilidad apostando, "vence a las casas" o ROI — el producto se posiciona como probabilidades calibradas, transparentes y auditables (predictions_log con timestamps pre-partido). Puerta futura: criterio v2 con odds ingestadas, pre-registrado antes de mirar retrospectivas contra mercado. Pendiente no-epistémico: verificar aristas regulatorias en México antes de cobrar. (3) VENTANA: 2026-10-21 → 2026-12-31 (~470 partidos, n mínimo para detectar mejora ≥0.02 con potencia cómoda a α=0.05); adjudicación primera semana de enero 2027; se evalúa sobre la servida "de record" de cada día (definición de predictions_log). NO-PEEKING: durante la ventana se audita operación (publicó, hay filas, Executions verde) pero NO se computan métricas predictivas acumuladas hasta la adjudicación; excepción única: evidencia de bug (predicciones fuera de [0,1], NaN, valores idénticos anómalos) se investiga de inmediato. Análisis secundario pre-registrado: mismas métricas excluyendo oct-21→nov-3 (debilidad documentada de rosters de inicio de temporada; los 14 días son convención fijada HOY, no hallazgo); informa, no decide — el gate se decide sobre la ventana completa. (4) REGLA DE DECISIÓN, tres zonas. VERDE (comercializa) = conjunción de tres: (a) test pareado unilateral sobre diferencias de Brier por partido vs B0, α=0.05; (b) mejora práctica ≥0.010 de Brier (juicio anclado: ~1/3 de la mejora esperada 0.025-0.035 de un buen Four Factors); (c) calibración: Spiegelhalter |z| < 1.96 (dos colas). Implicación aceptada: modelo rentable pero descalibrado NO comercializa en v1 — el claim es calibración. ROJO (no comercializa, volver a features) = Brier(modelo) ≥ Brier(B0) puntual, sin test (carga de prueba asimétrica protege al suscriptor); rojo = "el sistema en vivo no reprodujo el backtest", hallazgo de ingeniería, no condena del enfoque. GRIS (todo lo demás) = no se cobra; extensión ÚNICA hasta el All-Star break (~15-feb-2027, +~200 partidos); re-adjudicación con el MISMO criterio sin retoques; segunda gris = rojo. Canal gratuito durante extensión: decisión de negocio en su momento, no pre-registrada. CONGELAMIENTO: criterio y modelo congelados durante la ventana — reentrenamiento programado (mismo pipeline, mismas 11 features, mismos hiperparámetros) INCLUIDO como parte del sistema; cambios de modelo/features PROHIBIDOS (van al backlog de v2 con fecha); bugs se corrigen SIEMPRE de inmediato con post-mortem (discriminante: bug = contradice lo escrito en CLAUDE.md; mejora = quiere que diga otra cosa). (5) GATE OPERATIVO (paralelo al predictivo): (a) ≥95% de publicaciones diarias autónomas en la ventana — rescate manual cuenta como FALLO de autonomía (el rescate se hace para el canal, se registra como fallo); días degradados DECLARADOS según 13e-2.5 (feed caído con aviso, heartbeat de descanso) cuentan como ÉXITO — el fallo es el silencio, no la degradación honesta; (b) CERO publicaciones corruptas por bug: una, con post-mortem y fix desplegado → gris global; dos → rojo operativo. Auditoría: Executions n8n + status del Scheduler + predictions_log; condición (b) retrospectiva en la adjudicación (sin monitor automático — YAGNI), pero detectarla no la perdona. RESULTADO GLOBAL = mínimo de ambos gates (predictivo verde + operativo gris = gris global, la extensión aplica a ambos; operativo rojo veta aunque el predictivo sea verde — el producto no es el modelo, es el modelo ENTREGADO).

**13e-2.5 — Días degradados: degradación DECLARADA, jamás silenciosa;
umbral bajo el cual no se predice.**
1. SIN PARTIDOS: mensaje breve de descanso (no silencio — el silencio es
   ambiguo entre "no hay partidos" y "el sistema murió"; el mensaje es
   heartbeat gratuito). Endpoint responde normal con data vacío; n8n sin
   lógica condicional.
2. FEED CAÍDO (PDF ausente/ak-static muerto/budget agotado): SE PREDICE
   declarándolo — availability_diff (+0.21) es importante, no dominante;
   el modelo sigue batiendo a ELO. Línea obligatoria en el mensaje
   ("reporte de lesiones no disponible; sin ajuste de bajas de hoy") +
   flag en data. Publicar esos números como completos = la mentira
   silenciosa prohibida.
3. NYS AL PUBLICAR (caso real verificado: 3 equipos jugando ESE día
   seguían NYS a la 1:15PM): versión granular del 2 — se predice, y ESE
   partido lleva su marca ("disponibilidad sin confirmar"). El
   NysEntry-con-fecha del parser existe para esto.
4. FALLO DURO (endpoint caído/modelo no carga/schedule inaccesible): NO
   se publica contenido predictivo — no hay predicción confiable que
   degradar. Endpoint falla ruidosamente (500 + log); la rama de error
   del workflow publica aviso honesto de problema técnico.

## Fase 6 — Monetización y bot de Telegram (DOCUMENTADA 2026-08-19 — NO IMPLEMENTAR)

Capa NUEVA sobre el sistema predictivo; no modifica ingesta, features,
modelo ni pipeline. Única interfaz entre subsistemas: el endpoint de
predicciones diarias (Decisión 10). Prerequisitos originales de esta
sección (fix ModuleNotFoundError, equivalencia CDN, Docker v2 + Cloud Run):
**TODOS CUMPLIDOS** — ver RESULTADOS de Fase 5b. Reutiliza el proyecto GCP
existente.

### Producto y modelo de negocio (CERRADO)
Suscripción de pago a canal privado de Telegram con las predicciones
diarias. Flujo: pago → confirmación → acceso → publicación diaria → al
vencer, revocación. El negocio NO recibe apuestas, NO administra fondos,
NO entrega premios: el producto es información/análisis.

### Privacidad comercial (CERRADO)
Clientes no ven identidad personal (nombre legal, RFC, CLABE); marca
comercial separada. NO anonimato ante SAT/bancos/exchanges/procesadores —
cumplimiento fiscal completo. Titular en Sueldos y Salarios; probablemente
añadirá RESICO o Actividad Empresarial (pendiente con contador). Sin
sociedad mercantil para lanzar. CFDI: Stripe no emite el de la venta final;
CFDI global a público en general; factura individual expondría nombre
fiscal (limitación conocida y aceptada).

### Arquitectura de cobro híbrida (CERRADO)
Stripe = principal (Payment Links, suscripciones, webhooks, descriptor con
marca). USDC = alternativa cripto; red preliminar Base (pendiente
confirmar). Ambos convergen en el mismo backend de suscripciones.
**Reglas USDC (CERRADAS):** dirección única POR INTENTO DE COBRO (jamás
dirección pública compartida; renovaciones = dirección nueva); todas bajo
la misma infraestructura de wallets; separación estricta
recepción/tesorería/personal/exchange; no acumular fondos en wallets
operativas. Candidatos de proveedor: CDP, Alchemy, Privy, Fireblocks
(pendiente).

### Orquestación con n8n (frontera CERRADA)
n8n recibe webhooks de Stripe, updates de Telegram, cron de publicación
(Decisión 10) y llamadas al monitoreo on-chain. **FRONTERA (análoga a
adapter/lógica del DataStore): n8n = transporte y orquestación; la lógica
crítica del Subscription Engine (¿existe el pago?, ¿monto?,
¿confirmaciones?, ¿usuario?, grant/revoke) vive en servicio Python en
Cloud Run que n8n invoca vía HTTP. NUNCA lógica financiera (ni del modelo)
en workflows.** El nodo AI Agent de n8n NO recibe herramientas de escritura
ni acceso a pagos/permisos — solo lectura de estado y conversación.
Secretos en n8n solo para APIs no críticas; llaves privadas JAMÁS en n8n.
Hosting: Decisión 11 → resuelta en 13e-2.3.

### Seguridad y aislamiento del LLM (CERRADO)
El LLM nunca tiene autoridad sobre acciones sensibles: interpreta
intenciones, el backend valida TODO. Nunca: mover fondos, leer llaves,
activar suscripciones sin validación, consultar datos sensibles sin
autorización, modificar permisos. Prompt injection = amenaza permanente;
el usuario del bot es SIEMPRE fuente no confiable: separación system/user,
cero secretos en contexto, permisos en backend no en prompts, whitelist de
acciones, contenido externo = datos jamás instrucciones. Llaves: nunca en
código, texto plano ni contexto del LLM; preferir MPC/HSM o custodia
especializada. HTTPS, auth fuerte, logging de operaciones sensibles.

### Modelo de datos mínimo (BASE ACORDADA)
`users`(id, telegram_user_id, status, created_at);
`subscriptions`(id, user_id, provider stripe|usdc, start_date, expires_at,
status); `payment_requests`(id, user_id, subscription_id, provider, amount,
currency, payment_address, transaction_hash, status, expires_at). Cada
payment_request USDC genera dirección nueva.

### Flujo objetivo
Telegram Bot → n8n → Backend Python Cloud Run (users/subscriptions/
validación) → Stripe webhook | monitoreo on-chain → Subscription Engine →
grant/revoke → Canal privado (predicciones diarias vía endpoint, Dec. 10).

### Decisiones pendientes de Fase 6 (orden)
**P1:** red USDC definitiva (Base/Polygon/Solana); proveedor de wallets;
custodia vs self-custody. [Hosting n8n: resuelto — 13e-2.3.]
**P2:** HD wallets/derivación; consolidación a tesorería; detección
on-chain y nº de confirmaciones; expiración de payment_requests.
**P3:** framework Python y BD del backend; renovaciones/revocación
automática; rate limiting/anti-abuse; fiscal final con contador; ToS y
disclaimer sobre predicciones.

### Capacidad del agente editorial (anotación 2026-08-22)

El agente podrá enriquecer las publicaciones con contexto narrativo de
transferencias/movimientos desde fuentes no estructuradas (idea de
Antonio: free-agent tracker, redirigida de dato-de-entrada a capa
editorial). FRONTERA REAFIRMADA: el LLM es REDACTOR, jamás fuente de
datos del pipeline — nada de lo que el LLM lea o escriba entra a
features, ausencias ni predicciones.

### Principios inviolables de Fase 6
1. No revelar información personal innecesaria a clientes. 2. Cumplir SAT.
3. El LLM nunca decide sobre finanzas/autorización. 4. Nunca reutilizar
una dirección crypto. 5. Una dirección por intento de cobro. 6. Separar
wallets operativas/tesorería/personales. 7. Stripe = menor fricción;
USDC = alternativa. 8. Acceso y revocación automatizados desde backend.
9. Cero secretos/private keys en contexto del LLM. 10. n8n orquesta; el
servicio Python valida — nunca lógica financiera (ni del modelo) en
workflows.

## Roadmap post-Fase 3 (DECIDIDO — orden 1→2→3→4, el 5 siempre-después)

Lógica: cada camino habilita al siguiente. Hay un MODELO; falta un SISTEMA.

### Fase 4 — Ciclo de vida del modelo ✅ CERRADA

### Fase 5a — Pipeline de predicción en vivo ✅ CERRADA
HITO pendiente de calendario: primera predicción real (octubre 2026).

### Fase 5b — Despliegue GCP ← FASE ACTIVA
Decisiones 1-11 CERRADAS. **13a-13d ✅ DESPLEGADAS Y AUTÓNOMAS** (7/7
corridas, `v1_logistic_bclean_2026-08-22`). **e-0 ✅ verificado en
producción.** Restante: 13e-1 (spike ✅ → verificación datacenter →
diseño → implementación) → 13e-2 (endpoint + canal).

**RESULTADO PARCIAL (2026-08-13/14) — CloudDataStore + reconstrucción:**
- cloud.py: 14 métodos, MERGE+staging(exp. 1h)+delete, gcs_prefix,
  fallback ruidoso. Integración 5/5 ✅.
- Bug 1 (integración): MERGE 404 destino inexistente → CREATE TABLE IF NOT
  EXISTS AS SELECT WHERE FALSE. Lección: la 1ª implementación DEFINE el
  contrato de facto; la 2ª lo REVELA.
- Bug 2 (integración): JOIN incondicional a games → JOIN condicional solo
  con filtro season. Ambos invisibles para 47 unit en verde.
- rebuild_cloud.py fases A-D, 28 unit tests.
- **Reconstrucción ejecutada — EQUIVALENCIA EXACTA:** teams 30 | games
  14 429 | team_game_stats 28 858 | player_game_stats 371 253. ~9 min.
- **Features-check — NIVEL 2 PASS:** 9 643×19 contenido IDÉNTICO (SHA-256
  difiere = serialización, pre-registrado; oracle `13358021...` = hash del
  metadata del modelo — cadena de integridad verificada punta a punta).
  **Decisión 1 CUMPLIDA. CloudDataStore 3/3 sellos.**
- Deuda RAW-no-autosuficiente para games/teams: histórico depende del
  SQLite (boxscores sin metadata de calendario); 2026-27+ pagada hacia
  adelante vía `raw/schedules/` (ingest_job persiste cada corrida).
- Fix test_live_equivalence robusto a settings.mode.

**RESULTADO PARCIAL (2026-08-14) — Cloud Run Job (código):**
- ingest_job.py 3 pasos; funciones puras en
  `nba_predictor/jobs/ingest_logic.py` (fix estructural: pytest CLI no
  añade CWD a sys.path; tests importan del paquete, script = CLI delgado).
- Dockerfile (3.12-slim tras la promoción), .dockerignore,
  requirements.lock (50 paquetes).

**RESULTADO PARCIAL (2026-08-14/15) — CDN/S3 (Decisión 9):**
- cdn_client.py (funciones puras + CDNClient dual-URL + run_diagnostics),
  47 unit; test_cdn_equivalence 8 tests integración (tier estricto/suave).
- ingest_job paso 1 vía CDNClient; RAW a boxscores_live/ + schedules/;
  flag `--check-endpoints`.
- Suite: **302/302 passed, 13 deselected** (antes del fix de temporada).

**RESULTADO (2026-08-15) — 13d DESPLEGADA Y VERIFICADA ✅:**
- SA `ingest-job-sa` + 3 roles (Decisión 8) + `run.invoker` (post
  PERMISSION_DENIED del Scheduler — ver Decisión 8).
- Artifact Registry `nba-predictor` (us-south1); imagen v2 vía
  `gcloud builds submit` (v1 falló por scipy 3.12-vs-3.11 → promoción de
  3.12 a canónico, Decisión 7).
- `nba-ingest-job` creado (imagen v2, SA, env vars, timeout 30m).
- `--check-endpoints` desde Cloud Run: frontal 403 / S3 200 (61-661ms).
- **Primera ejecución real exit 0:** 0 nuevos (offseason) → features
  omitido → primer retrain → **`v1_logistic_bclean_2026-08-15` en el
  registry GCS** (el job pobló su propio registry). Schedule persistido.
- **Scheduler `nba-ingest-daily` ENABLED (12:00 UTC diario):** verificado
  con ejecución autónoma COMPLETE, RUN BY = la SA. **El sistema corre solo
  desde 2026-08-15.**

**RESULTADO (2026-08-15) — Fix desajuste de temporada CDN ✅:**
- **Problema:** `_step1_ingest` usaba `TRAINING_SEASONS[-1]` = "2025-26"
  (config estática) como filtro del schedule CDN. El CDN sirve "2026-27".
  En offseason inofensivo; en octubre 2026 habría descartado toda la
  temporada silenciosamente con exit 0. Detectado en el log de la 1ª
  ejecución real.
- **Dos conceptos distintos** (documentados en `ingest_logic.py`):
  `TRAINING_SEASONS` = ventana estática del modelo (no se toca).
  `effective_season` = `leagueSchedule.seasonYear` del CDN = fuente
  canónica de ingesta.
- **Fix (dos capas):** (1) `_season_from_raw_schedule(raw)` extrae
  `seasonYear` del payload CDN; (2) `_check_season_guard(filter, cdn,
  has_played)` — mismatch sin jugados → WARNING, mismatch CON jugados →
  RuntimeError. Defensa en profundidad: con derivación correcta el guard
  NUNCA se activa en operación normal. `_step1_ingest` renombra `season` →
  `config_season`; primer fetch obtiene raw_payload; extrae cdn_season;
  re-fetch si difieren; guard + log. Dos llamadas HTTP solo en la
  transición anual.
- **9 unit tests nuevos** en `tests/test_ingest_job.py` (5 derivación + 4
  guard). Total: 18 tests en el archivo.
- **Suite total: 311/311 passed, 13 deselected.** ✅

**RESULTADO (2026-08-21) — e-0 verificado en producción ✅:**
- `effective_season` = "2026-27" derivado del CDN desde el primer run post-fix.
- Guard = WARNING (sin partidos jugados — offseason). Comportamiento correcto.
- **7/7 corridas autónomas exitosas** desde el deploy del fix.
- `v1_logistic_bclean_2026-08-22` generado por el job al alcanzar la cadencia
  de 7 días desde `2026-08-15` (primer modelo). El pipeline de reentrenamiento
  automático queda validado end-to-end. Fix cerrado.

**RESULTADO PARCIAL (2026-08-21/22) — Spike + Implementación 13e-1:**
- **A. Fuente y descubrimiento:** PDFs oficiales en
  `ak-static.cms.nba.com/referee/injury/Injury-Report_{YYYY-MM-DD}_{HH}_{MM}{AM|PM}.pdf`
  (formato de hora mutó: `_06AM` viejo pre-2025; `_01_15PM` en 2026+).
  Servidor AmazonS3 (Server header), sin WAF desde IP local. URLs inexistentes
  → 403 (no 404) con cuerpo XML; condición de existencia = `status==200 AND
  primeros 4 bytes == b'%PDF'`. HEAD 200 + Content-Length es el check barato.
- **B. Parser:** `pdfplumber.extract_tables()` falla — el PDF usa texto
  posicionado sin comandos de dibujo de bordes. `extract_words()` también
  falló (bug en detección de headers por coordenada Y). Solución definitiva:
  `extract_text()` + regex + state machine. 88 filas parseadas (PDF 2026) /
  142 (PDF 2024) — layout estable entre años. 16 equipos "NOT YET SUBMITTED"
  en el PDF de 2026 — se modelan como estado de disponibilidad desconocido.
- **C. Matching de nombres:** PDF en formato "Apellido, Nombre" → invertir →
  normalizar ASCII → comparar contra `PLAYER_NAME` de los JSON crudos.
  ~80% con muestra de 50 ficheros (~90-93% estimado con corpus completo).
  Fallos: sufijos (Jr., II, III) confunden el parser de "Apellido, Nombre";
  fallback por apellido único + fuzzy como mejora.
- **D. Accesibilidad desde datacenter:** PENDIENTE — es el objetivo del
  tercer diagnóstico de `--check-endpoints`. `INJURY_REPORT_DIAG_URL`
  constante en `cdn_client.py`; `diagnose_injury_report()` añadido a
  `CDNClient` (HEAD + GET, sin pdfplumber, verifica `%PDF` en primeros bytes).
  Antonio corre `python scripts/ingest_job.py --check-endpoints` desde Cloud
  Run para obtener la respuesta.
- **E. Veredicto:** viable como fuente primaria. Diseño del feed:
  pendiente (3 decisiones de diseño del chat de diseño — añadir aquí cuando
  se documenten formalmente). → RESUELTO: ver "Decisiones del feed de
  injury report" (sección de Decisiones de Fase 5b).

**RESULTADO (2026-08-22) — Implementación 13e-1 ✅
[SUPERSEDIDO por RESULTADO FINAL 2026-08-23: los conteos de este bloque
(96/17, 199/3) resultaron INCORRECTOS — fantasmas del parser; los oficiales
son 73/17 y 160/3. Se conserva como registro histórico.]:**
- `nba_predictor/ingestion/injury_report.py` (módulo autónomo, NO integrado
  todavía a ingest_job ni endpoint — integración es 13e-2):
  - `discover_latest_snapshot()`: HEAD probing, presupuesto configurable
    (20 default), caché de sufijo hint, formatos nuevo (`HH_MMAM|PM`) y
    viejo (`HHAM|PM`), RuntimeError si budget agotado.
  - `download_snapshot()`: GET + verificación `%PDF`.
  - `parse_pdf()`: extract_text+regex+state machine, 3 fixes completos:
    (a) sufijo romano comprimido "ButlerIII" → "Butler III"; (b) reason
    multilínea genera nueva InjuryRow (no append); (c) NOT YET SUBMITTED
    → `nys_teams` con date-strip completo (extrae "Brooklyn Nets" de
    "03/14/2026 01:00(ET) BKN@PHI Brooklyn Nets NOT YET SUBMITTED").
  - `NameIndex.from_player_map()`: cascada norm-sin-sufijo → norm-con-sufijo
    para desempate; conservador (None en ambiguo, WARNING al log).
  - `load_player_names_from_raw_json()` / `load_player_names_from_cdn_json()`.
  - `get_absences()`: orchestration completa → AbsenceResult.
  - Conteos verificados con parser de producción:
    2026-03-13: **96 player rows, 17 NYS** {Out:54, Q:22, D:14, P:3, A:3}
    2024-03-13: **199 player rows, 3 NYS** {Out:160, A:26, Q:9, P:4}
- Método 15 DataStore (`save_raw_injury_report(date_str, suffix, pdf_bytes)`):
  añadido a base.py (abstractmethod), local.py (→ raw/injury_reports/),
  cloud.py (→ GCS raw/injury_reports/, helper `_gcs_injury_report_path`).
- `tests/test_injury_report.py`: 71 unit tests (73 colectados, 2 deselect
  @integration). Fixture PDFs en tests/fixtures/. Suite total: **382 passed**.
- `pyproject.toml`: pdfplumber>=0.11.0 añadido a main deps.
- `requirements.lock`: pdfplumber==0.11.10, pdfminer.six==20260107,
  Pillow==12.3.0, pypdfium2==5.13.0 añadidos.
- Integración a ingest_job + endpoint → 13e-2.

**HALLAZGO (2026-08-22) — El PDF es MULTI-FECHA:** un snapshot cubre los
partidos de hoy Y de mañana (LA Clippers apareció con filas de jugadores
del 03/13 Y en NYS del 03/14: entregó el reporte de hoy, no el de mañana).
Consecuencias: `InjuryRow` lleva campo `game_date` extraído del encabezado
de partido; el parser reporta TODO sin filtrar; el filtrado por fecha
objetivo se decide en `get_absences()`/endpoint (decisión abierta de
13e-2, posición preliminar: `target_date` explícito).

**LECCIÓN (2026-08-22) — Tests de fixtures son guardas de REGRESIÓN, no
de corrección:** los 71 tests en verde codificaron un bug real del parser.
Los conteos "verificados con el parser de producción" eran circulares: el
parser verificando su propia salida. El pre-registro del spike (88/16, 142)
cazó la desviación (96/17, 199) y la auditoría manual del listado contra el
PDF la adjudicó como bug, no como subconteo del spike. Protocolo: ningún
conteo de fixture se adopta como oficial sin auditoría humana del listado
contra el documento fuente.

**RESULTADO FINAL (2026-08-23) — 13e-1 CERRADA ✅ tras auditoría en 6 rondas:**

**Historia del parser (registrada como lección de arquitectura):** el enfoque
`extract_text()`+state machine produjo CUATRO capas del mismo bug de
linealización geométrica: (1) fragmentos de razón multilínea → filas
fantasma (96 filas aparentes vs 73 reales); (2) fila embebida → jugador
PERDIDO (caso McConnell, T.J.); (3) atribución de equipo corrida en
fronteras de bloque (caso Trae Young→"Portland"); (4) fronteras en
transiciones de FECHA (casos Okogie→"Boston", Walsh→"Clippers"). La regla
de parada pre-registrada (una iteración más; otra capa = migrar) se ACTIVÓ
en la capa 4: `parse_pdf()` migró a `extract_words()` con reconstrucción
por coordenadas X/Y. La migración resolvió las 4 capas Y los interleavings
de reason dados por perdidos (verificación cruzada: la lesión de Trae Young
inferida manualmente en la ronda 3 coincidió exactamente con la
reconstrucción geométrica). Se conservó todo lo demás: descubrimiento,
matching, guarda anti-fila-embebida (con unit test), método 15. [Nota: el
`extract_words()` que el spike descartó falló por un bug de implementación
en detección de headers, no por inviabilidad — la migración lo resolvió
con bandas por fila ancladas al X de columnas.]

**Conteos OFICIALES (auditados a mano contra los PDF):**
- 2026-03-13 (1:15PM): 73 filas / 17 NYS (3 del 03/13 — Dallas, Memphis,
  Chicago juegan HOY y no habían entregado; 14 del 03/14) /
  {Out:40, D:10, Q:17, P:4, A:2}.
- 2024-03-13 (11PM): 160 filas (118 del 03/13 + 42 del 03/14) / 3 NYS
  (todos 03/14) / {Out:129, Q:7, P:3, A:21}.
- Ni el spike (88/16, 142) ni la 1ª implementación (96/17, 199) contaban
  bien: subconteo y fantasmas respectivamente.
- NYS lleva FECHA (`NysEntry`): requisito funcional, no cosmético — un
  equipo puede tener reporte entregado para hoy y NYS para mañana (caso
  Clippers 2026 y los 3 NYS de 2024). Flag NYS sin fecha daría
  "desconocido" para días cuyo reporte SÍ existe.

**Representación interna (decisión adjudicada 2026-08-23):** el PDF NO
emite espacios en su capa de texto (precedente "ButlerIII" del spike);
`InjuryRow.team/player` almacenan los tokens CRUDOS (`"ChicagoBulls"`,
`"YanicKonan"`) — filosofía RAW. El matching funciona por TRANSFORMACIÓN
SIMÉTRICA en `_normalize_name` (split de CamelCase en ambos lados de la
comparación). **Pendiente nombrado para 13e-2:** frontera de traducción
token-PDF → equipo canónico del sistema al cruzar ausencias contra el
schedule CDN (el endpoint NO debe asumir que "ChicagoBulls" == equipo del
schedule sin pasar por la normalización). **✅ Verificación añadida
(2026-08-24):** `test_camelcase_pdf_token_matches_json_player_name`
cruza lado-JSON "Yanic Konan Niederhauser" contra lado-PDF
"Niederhauser,YanicKonan" por la cascada completa `NameIndex.match()`. Pass.

**Decisión de fuente (2026-08-23, ratificada con regla de parada):** se
evaluó pivotar a terceros (balldontlie) por la dificultad del parsing.
RECHAZADO: los terceros parsean el MISMO PDF (pivote = tercerizar el
parsing a un parser inauditable); el oracle de validación seguiría siendo
el PDF; la auditabilidad fue lo que cazó las 4 capas. balldontlie solo se
promueve si la fuente oficial MUERE, jamás por fricción.

**PROTOCOLO DE AUDITORÍA (cobrado 5 veces en esta fase — elevado a regla):**
1. Tests de fixtures = guardas de REGRESIÓN. La corrección solo la
   establece auditoría humana del listado completo contra el documento
   fuente + conocimiento externo (la capa 3 pasó TODOS los invariantes
   automáticos; solo "Trae Young no juega en Portland" la cazó).
2. Los resúmenes narrados de Code NO sustituyen al output literal
   (pytest tail, listados). Tres veces la narración afirmó corrección que
   el listado desmintió; una vez reportó "todas las atribuciones
   correctas" verificando solo los nombres pre-registrados.
3. Desviación de pre-registro = adjudicar, jamás aceptar en silencio.
   Requisito irrealizable = reportar y proponer, jamás sustituir en
   silencio (caso espacios: la solución de Code era correcta; el proceso no).

**Tests:** 91 unit de injury_report (guardas de regresión de la auditoría:
Young→Atlanta, Green→GSW, Okogie→PHX, Walsh→BOS, McConnell Probable,
Clippers multi-fecha, 118/42 fechas 2024; + 3 tests de la guarda de fila
embebida; + 1 cruce PDF↔JSON sin espacios). Suite total: **402 passed,
15 deselected** (382+20; live equivalence corrido aparte — split @slow de
facto, formalizar algún día).
Script temporal de verificación borrado.

**RESULTADO (2026-08-25) — 13e-2 NÚCLEO DESPLEGADO Y VERIFICADO ✅ — endpoint
en producción, primer mensaje real del sistema:**

**Desplegado:** Cloud Run Service `predictions-api` (us-south1, imagen v5,
`--no-allow-unauthenticated`, 1Gi, NBA_PREDICTOR_MODE=cloud), SA
`predictions-api-sa` de LECTURA (asimetría deliberada con ingest-job-sa:
storage.objectUser en bucket, bigquery.jobUser, READER del dataset vía ACL
legacy, bigquery.readSessionUser a nivel proyecto). `cloudbuild.api.yaml`
(el --tag default no ve Dockerfile.api; substitution _VERSION) +
`.gcloudignore` explícito (antes: fallback a .gitignore que dejaba pasar
.git/ y dependía de él para .env). Verificado: /health desde la nube con
model_version del registry GCS (método 16 en producción); 401 de IAM ante
token expirado (la muralla verifica ANTES de tocar el código — decisión
13e-2.2 comprobada); dual-URL en vivo (frontal 403 → S3, el monitor diario
operando).

**LA CEBOLLA DE 5 CAPAS — cada supuesto del entorno local cobrado en una
tarde (2026-08-25). Regla que las une: el camino en vivo JAMÁS había
corrido fuera de la laptop; cada capa era invisible hasta la primera
ejecución real desde datacenter:**
1. **Filesystem local:** `_discover_latest_version()` leía `data/models/`
   → FileNotFoundError en Cloud Run. FIX: método 16 del DataStore
   (`get_latest_model_version`, ambos adapters) + guarda de acoplamiento
   cero (test que falla si "data/models" reaparece en server.py).
   LIMITACIÓN NOMBRADA: en cloud ordena por nombre de blob (lexicográfico
   ≡ cronológico solo mientras el prefijo sea v1_...); corregir a
   fecha-del-metadata ANTES de cualquier segundo modelo.
   `NBA_PREDICTOR_MODEL_VERSION` como pin manual opcional.
2. **Fuente muerta (Decisión 9 incompleta):** `future_schedule.py` usaba
   ScheduleLeagueV2 → stats.nba.com (bloqueado en datacenter — la causa
   raíz de la Decisión 9, que migró ingesta pero NO el camino en vivo).
   FIX: migrado a CDNClient. Fecha fuera de ventana = escenario 1, no
   excepción. nba_api queda solo en nba_client.py (legacy reconstrucción)
   y predict_game.py (CLI local) — deuda de limpieza nombrada.
3. **Reloj vs target_date (gemelo e-0):** `_current_season()` derivaba de
   `date.today()` — agosto→"2025-26" para un request de octubre 2026.
   Dormido el 95% del año; despierta EXACTAMENTE en la frontera de
   temporada (la semana del hito). FIX: derivar de target_date + el
   payload CDN gana (patrón e-0). Guarda:
   test_october_target_date_not_today_bug. REGLA: la temporada se deriva
   del target_date y se corrige contra el payload — jamás del reloj,
   jamás de config.
4. **Vocabulario del documento equivocado:** el filtro usaba `gameType`,
   campo que scheduleLeagueV2 NO TIENE (es vocabulario de boxscores); los
   fixtures sintéticos codificaron el error → 22 tests en verde sobre un
   filtro que descartaba el 100% de los partidos en producción. FIX:
   filtrar regular season por PREFIJO de gameId ("002"; 001=preseason);
   incluir gameStatus 1 (programado); fixtures REALES recortados del
   scheduleLeagueV2 archivado (misma regla que los PDF de 13e-1: el
   fixture desciende del documento verdadero). Guarda numérica:
   21-oct-2026 → exactamente 11 partidos. HALLAZGO del payload real:
   `gameDateTimeEst` trae sufijo Z pero la hora es ET (gameDateTimeUTC
   difiere 4h; gameStatusText lo confirma) — el Z es DECORATIVO; jamás
   tratar ese campo como UTC o los tip-offs CDMX se corren en silencio.
   `gameDateEst` trae hora 00:00 siempre — solo sirve para la fecha.
5. **Tercer permiso de BigQuery:** `to_dataframe()` con
   bigquery-storage instalado usa la Storage Read API →
   `bigquery.readsessions.create`, que NO viene con jobUser ni con READER
   del dataset. FIX: `roles/bigquery.readSessionUser` a nivel proyecto
   (solo habilita el transporte; el ACL del dataset sigue gobernando qué
   se lee). **BLINDAJE PREVENTIVO: ingest-job-sa recibió el mismo rol —
   su paso 2 (rebuild de features, que LEE con este camino) se ha saltado
   las 7/7 corridas por offseason; habría fallado la primera mañana de
   octubre con partidos. El endpoint le encontró el bug al job dos meses
   antes.**

**TRAMPAS WINDOWS/GCP DEL DESPLIEGUE (recetas pagadas):**
- `bq add-iam-policy-binding` a dataset requiere allowlist → camino
  operativo: ACL legacy vía `bq show/update` (READER ≡ dataViewer).
- El `>` de PS5 escribe UTF-16; bq exige UTF-8 sin BOM → `WriteAllText`
  con `UTF8Encoding($false)`.
- `Get-Content f | Set-Content f` se autobloquea (pipeline streaming) →
  leer con -Raw primero.
- PS5 decodifica respuestas HTTP como Latin-1 → mojibake en consola NO
  implica bug del servidor; auditar con RawContentStream + UTF-8. (El
  servidor envía UTF-8 correcto — verificado byte a byte.)

**PRIMER MENSAJE REAL (2026-08-25, auditado contra el formato congelado):**
`?date=2026-10-21` → 200 con los 11 partidos exactos del oracle
(scheduleLeagueV2 archivado), tip-offs CDMX verificados (7:30 pm ET →
17:30 CDMX), probabilidades plausibles del rolling de abril (caso "roster
change v0: aceptar lag"), feed_down declarado con razón ejemplar (20
intentos + sufijos probados — el injury report de una fecha futura no
existe aún, comportamiento correcto), model_version poblado, disclaimer y
línea de modelo en su sitio. Ajuste cosmético aplicado: partidos ordenados
por tip-off. PENDIENTE del alcance 13e-2 (nada toca el pipeline
predictivo): archivo del PDF en ingest_job (Decisión 4 del feed),
persistencia del snapshot desde el endpoint, predictions_log, n8n, canal.

**MORALEJA (elevada a principio):** cinco capas, un patrón — supuestos del
entorno local (filesystem, red residencial, reloj, fixtures sintéticos,
permisos implícitos) invisibles para 490 tests en verde. Desplegar en
agosto los cobró todos en una tarde con calma; octubre los habría cobrado
como cinco incidentes con público. El despliegue temprano ES una
herramienta de testing.

- [CERRADO 2026-08-26/27] Deploys v7 (predictions-api) y v5 (ingest-job) + RECETAS CANÓNICAS (deuda documental saldada; hallazgo: CLAUDE.md registraba decisiones de deploy pero no comandos — reconstruidos contra evidencia viva, no memoria). RECETA predictions-api: build con gcloud builds submit --config=cloudbuild.api.yaml --substitutions=_VERSION=vN --project=predictorsnonprod (usa Dockerfile.api: python:3.12-slim, requirements.lock, uvicorn server:app en 8080); deploy con gcloud run deploy predictions-api --image=us-south1-docker.pkg.dev/predictorsnonprod/nba-predictor/predictions-api:vN --region=us-south1 --project=predictorsnonprod (la config no especificada SE HEREDA de la revisión previa: SA, env vars, 1Gi, port 8080, maxScale 20, startup-cpu-boost, probe TCP 240s). RECETA ingest-job: build con gcloud builds submit --tag us-south1-docker.pkg.dev/predictorsnonprod/nba-predictor/ingest-job:vN --project=predictorsnonprod (usa Dockerfile raíz: ENTRYPOINT scripts/ingest_job.py); update con gcloud run jobs update nba-ingest-job --image=...:vN --region=us-south1 --project=predictorsnonprod (hereda SA, env vars, timeout 1800s). CONVENCIÓN DE TAGS: incremental vN por servicio, JAMÁS latest. Estado: predictions-api v7 (revisión 00007-94v, digest sha256:2138dde42240df9ccc116dc1d227ee036e2a34929599d3a4aba1fcf3a5ef7c74) con snapshot-persist + predictions_log; ingest-job v5 (digest sha256:c4ca1b7b184ba705880c8f777ca73104e049378a75f30f670d713e5b91d476b6) con archivo best-effort del PDF — verificación del ingest: cron 12:00 UTC del 27, expectativa SUCCESS + primer PDF en raw/injury_reports/ (WARNING sin fila también sería contrato OK). Verificación v7: 200 heartbeat UTF-8 íntegro contra bytes crudos; model_version null CORRECTO en rest day (adjudicado con código: None reservado a games vacía, fallo real de resolución = 500, jamás null; regla de lectura: games!=[] && model_version==null = estado imposible = bug); verificación plena de model_version/campos nuevos/primera fila del log DIFERIDA al primer día con partidos. Tabla predictions_log creada (PARTITION BY game_date, 9 campos idénticos al schema del código) + predictions-api-sa dataEditor A NIVEL TABLA (mínimo privilegio, sin ACL legacy). TRAMPAS NUEVAS PowerShell/entorno: (a) curl es alias de Invoke-WebRequest — sintaxis nativa con -Headers @{}; (b) usar -UseBasicParsing para evitar prompt interactivo; (c) mojibake de PANTALLA: Invoke-RestMethod decodifica Latin-1 si el Content-Type no declara charset — juzgar encoding SOLO contra bytes crudos con UTF8.GetString(RawContentStream); FastAPI responde UTF-8 correcto (backlog cosmético: declarar charset=utf-8); (d) ADC puede fallar con RefreshError internal_failure retryable — ante rojos masivos de TestSanityRealData, primer sospechoso credenciales (gcloud auth application-default login), no datos; (e) gcloud logging read en PowerShell exige comillas internas escapadas con backslash. BACKLOG: except ancho en load_features (cloud.py) convierte fallos de auth en FileNotFoundError falso — afinar a NotFound de GCS. Base de tests oficial: 531 passed, 15 deselected.
- [HALLAZGO+FIX+CERRADO 2026-08-28] EL GEMELO DE LA CAPA 4 — el bug más caro del proyecto, muerto 54 días antes de cobrar. RETRACTACIÓN previa: el pendiente "migrar future_schedule.py a CDN" estaba MUERTO (superado por la capa 2 del 2026-08-25; el renglón de Fase 5a en CLAUDE.md:188 es registro histórico) — lección: la lista de pendientes se deriva del registro completo, no de la memoria del chat. El diagnóstico de solo-lectura que lo confirmó encontró al culpable real al lado: _normalize_cdn_schedule (cdn_client.py) filtraba por gameType, campo AUSENTE en scheduleLeagueV2 → games_df SIEMPRE vacío → el ingest job jamás habría visto partidos en octubre (exit 0 silencioso, features congeladas en abril, el endpoint publicando toda la temporada con rolling rancio — degradación silenciosa de la evidencia del criterio). Evidencia de producción: log del cron "0 partidos de temporada regular para '2026-27'" con calendario ya publicado. El endpoint nunca lo sufrió porque future_schedule descarta el DataFrame y parsea el payload crudo (por eso la capa 4 se corrigió ahí y el gemelo sobrevivió). FIX (commit f2ac143, test-first): test de regresión con fixture REAL en rojo ANTES (Empty DataFrame reproducido) → filtro por prefijo de gameId ("002", espejo de capa 4) → verde; sintéticos migrados 4-por-4; _GAME_TYPE_MAP conservado con nota (boxscores sí traen el campo). Suite: 531 limpia / 539 mixta — ARTEFACTO DEL 535 RESUELTO de rebote: live_equivalence aporta exactamente 4 tests en modo mixto (535=531+4 antes, 539=535+4 ahora; el "+13" supuesto era erróneo). DEPLOY: ingest-job:v6, jobs update, ejecución manual verificada: "Schedule CDN: 1206 partidos de temporada regular para '2026-27'" (0→1206 con el mismo payload; los ~24 faltantes = huecos TBD de NBA Cup que payloads futuros rellenarán — si en diciembre no crece, ESO es hallazgo), "0 partidos jugados" intacto, guard OK, exit=0. BACKLOG derivado del diagnóstico: (a) DOBLE PARSER de scheduleLeagueV2 (future_schedule por prefijo/seasonYear vs normalizador por prefijo/_season_from_year) — unificar para que el gemelo no pueda renacer; (b) fetch_todays_schedule sin call sites — adjudicar API-futura vs residuo; (c) deuda nba_api (nba_client.py, predict_game.py, scripts de exploración). Verificación de octubre pre-registrada: primer día de PRESEASON en el calendario (partidos "001") → el heartbeat debe seguir diciendo "sin partidos" (ambos filtros de prefijo excluyen 001) y el job debe ingestarlos como... NO: los 001 quedan fuera de regular season por diseño — expectativa: ni el endpoint ni games_df los reportan; el primer número >0 de "partidos jugados" llega con los 002 del 21-oct.
- [CORRECCIÓN 2026-09-13] Base oficial de tests: 535 limpia / 539 mixta (4 = live_equivalence). El 531 del bloque 2026-08-26/27 quedó obsoleto con f2ac143.

### Fase 6 — Monetización + agente (documentada; NO implementar)
Especificación completa en la sección "Fase 6" de decisiones (arriba).
El agente LLM original queda dentro: capa de explicación/interacción sobre
el canal, con el aislamiento de seguridad ya especificado.

### Camino 5 — Mejora del modelo (siempre-después)
Ponderación temporal, ventanas 5/15/20, SRS, calibración explícita, injury
reports históricos para G5 (archivo diario vía método 15 + experimento
P(juega|Doubtful/Questionable) pre-registrado), comparación vs Vegas
(benchmark final).

**D-RES-2 — Spike backfill injury reports (PRE-REGISTRO 2026-09-13).**
Medición por muestreo estratificado de cuánta cobertura de PDFs historicos
sigue viva en ak-static.cms.nba.com. NO es el backfill completo: es la
medicion que decide si el backfill completo se hace.

HIPOTESIS: la NBA retiene PDFs de injury report al menos dos temporadas
atras (evidencia: Injury-Report_2024-03-13_11PM.pdf descargado en agosto de
2026). EXPECTATIVA DE COBERTURA (fraccion de fechas muestreadas con al menos
un PDF): 2025-26 alta, >80%; 2024-25 media-alta, 50-80%; 2023-24 incierta,
>=50% seria sorpresa positiva. CRITERIO DE PROMOCION pre-registrado: si al
menos DOS temporadas tienen cobertura >=70% con al menos un corte por dia, el
backfill completo se convierte en la siguiente tarea y el experimento
P(juega | Questionable) entra a la cola de 2026. Si no, A1/A2 se difieren a
2027-28 y el archivo diario sigue acumulando. Desviacion de esta expectativa
= hallazgo a adjudicar, no a aceptar en silencio.

DISEÑO. Fase 0, calibracion de sufijos (3 fechas conocidas): 2026-03-13
(formato nuevo, hit conocido _01_15PM), 2024-03-13 (formato viejo, hit
conocido _11PM) y una fecha de 2024-25 elegida y registrada (2025-01-15).
Por fecha, barrido amplio: formato viejo {01..12}{AM,PM} (24 probes) y
formato nuevo {01..12}_{00,15,30,45}{AM,PM} (96 probes), cap 150 HEAD por
fecha; se registran TODOS los hits, no solo el primero, para aprender horas y
minutos reales de publicacion por era y reducir el set en fase 1. Fase 1,
muestreo estratificado: temporadas 2023-24, 2024-25 y 2025-26, solo fechas de
temporada regular tomadas del calendario real ya ingestado (tabla games), 30
fechas por temporada estratificadas por mes (~5 por mes), seed fijo 42; la
lista se escribe a data/spike_backfill/dates.json ANTES de sondear y es
INMUTABLE una vez escrita. Set de sufijos por fecha: el reducido aprendido en
fase 0 para la era correspondiente, cap 30 HEAD por fecha, registrando todos
los hits. Cada hit: GET, verificacion de b'%PDF' en los primeros 4 bytes y
persistencia con save_raw_injury_report (metodo 15, modo local) — los PDFs
recuperados son archivo real, no descartables. CONDUCTA DE RED (no
negociable): se reutiliza el cliente HTTP y los headers de injury_report.py
(sin inventar otro), sleep 0.5s entre requests, timeout 10s; regla de parada
= 429, o 403 en una URL que antes dio 200, o 10 errores de red consecutivos →
ABORTAR, guardar lo acumulado y reportar, sin reintentar en bucle y sin
cambiar User-Agent para evadir; todo corre desde la laptop (IP local), NO
desde Cloud Run. SALIDA: data/spike_backfill/results.json (por fecha: sufijos
probados, hits, tamaño de cada PDF, status codes) + tabla resumen por
temporada. Pertenece al Camino 5 y no toca nada del pipeline de produccion.

**D-RES-2 — RESULTADO FASE 0 (2026-09-12) + DETENTE ANTES DE FASE 1.**
Fase 0 corrio completa (360 HEAD, 3 fechas x 120 sufijos, regla de parada NO
activada). Fase 1 NO se ejecuto: la clausula DETENTE del encargo se activo con
dos hallazgos que invalidan parte del diseño pre-registrado. dates.json NO se
escribio (la lista inmutable no quedo congelada bajo un diseño a revisar) y
CERO PDFs se archivaron.

SUFIJOS OBSERVADOS POR ERA (fase 0, literal): 2026-03-13 → 96/96 hits del
formato nuevo, 0/24 del viejo (status {200:96, 403:24}); 2024-03-13 → 24/24
del viejo, 0/96 del nuevo; 2025-01-15 → 24/24 del viejo, 0/96 del nuevo. Los
dos sets quedan CLAROS y disjuntos: era vieja = las 24 horas {01..12}{AM,PM};
era nueva = los 96 cuartos {01..12}_{00,15,30,45}{AM,PM}.

HALLAZGO 1 — EL FEED PUBLICA UN CORTE CADA 15 MINUTOS, TODO EL DIA, no uno o
dos. Adjudicado con evidencia interna, no por inferencia del status: el PDF de
2026-03-13_12_00AM existe, pesa 72 922 bytes y su /CreationDate interno es
D:20260313000004-04'00 (creado a las 00:00:04 de ese dia); el de
2024-03-13_04AM pesa 78 981 y declara D:20240313043002 (la hora del sufijo
viejo mapea a un corte generado a :30). Control negativo sano: 1999-01-01_11PM
responde 403 con cuerpo XML AccessDenied — el servidor SI discrimina
existencia, no devuelve 200 a todo. CONSECUENCIA SOBRE EL DISEÑO: (a) la
metrica "distribucion de cortes por dia (1/2/3+)" queda SIN SENTIDO — la
respuesta es 24 (era vieja) o 96 (era nueva) para toda fecha viva; (b) la
cobertura ("¿existe al menos un PDF?") se contesta con 2-4 probes por fecha,
no con 30; (c) archivar TODOS los hits, como pedia el encargo, significaria
~24-30 PDFs por fecha x 90 fechas = ~2 700 PDFs y ~5 400 requests, no el
archivo modesto que el diseño suponia. Nada de esto se resolvio por cuenta
propia: es decision de Antonio.

HALLAZGO 2 — LA FRONTERA DE FORMATO CAE DENTRO DE LA TEMPORADA 2025-26, no
entre temporadas. Calibracion complementaria (4 probes/fecha): 2025-04-01
viejo, 2025-10-22 viejo, 2025-11-15 viejo, 2025-12-15 viejo, 2026-01-15 NUEVO.
El mapeo temporada→era del script (2025-26 = "new") era un SUPUESTO no
validado por las 3 fechas de fase 0, y de haber corrido fase 1 con el habria
sondeado solo sufijos nuevos en oct-dic 2025 → COBERTURA CERO FALSA para media
temporada, con exit 0 y tabla de aspecto sano. Misma familia que el gemelo de
la capa 4: un vocabulario equivocado que ningun invariante automatico delata.
La era debe derivarse POR FECHA (probe de un sufijo de cada familia), jamas por
temporada.

SEÑAL LATERAL SOBRE LA HIPOTESIS (no es el veredicto): toda fecha sondeada
hasta ahora esta viva — 2024-03, 2025-01, 2025-04, 2025-10, 2025-11, 2025-12,
2026-01, 2026-03. La retencion aparenta ser excelente y apunta hacia promocion,
pero el criterio pre-registrado exige las 30 fechas por temporada del muestreo
estratificado: NO se declara veredicto con evidencia de calibracion. El
criterio de promocion sigue intacto y sin adjudicar.

ESTADO: fase 1 BLOQUEADA a la espera de decision sobre (1) que significa
"cobertura" y "cortes por dia" ahora que hay 24-96 cortes diarios, (2) cuantos
y cuales cortes archivar por fecha, (3) derivacion de era por fecha. El script
vive en scripts/spike_backfill_injury_reports.py (funciones puras separadas del
CLI, borrable sin residuo); phase0.json en data/spike_backfill/. Cero cambios
al pipeline de produccion: suite 531 passed, 15 deselected, sin cambios.

**D-RES-2 — ENMIENDA de metodo (2026-09-13).**
MOTIVO: fase 0 mostro (a) un corte cada 15 min en era nueva y cada hora en era
vieja, y (b) frontera de formato dentro de 2025-26. La medicion cambia; el
CRITERIO DE PROMOCION NO CAMBIA (>=70% de cobertura en >=2 temporadas).

DEFINICIONES ENMENDADAS:
- Era por FECHA, detectada con 2 probes (07PM y 07_30PM), nunca por temporada.
- Cobertura = existe el corte canonico o uno de sus dos vecinos inmediatos.
- Corte canonico = ultimo PDF con hora de creacion ET <= 13:00 CDMX de esa
  fecha convertido a ET. Se calcula con zoneinfo (America/Mexico_City y
  America/New_York), nunca con offset fijo: CDMX no tiene DST desde 2022, ET
  si. El sufijo viejo HH mapea a creacion HH:30 (evidencia de fase 0: 04AM ->
  /CreationDate 04:30:02); el sufijo nuevo HH_MM mapea a creacion HH:MM. La
  regla de mapeo queda documentada en el script.
- Densidad de cortes: se mide solo en una submuestra de 3 fechas nuevas (una
  por temporada, la de 2025-26 dentro de oct-dic 2025, era vieja), con barrido
  completo de su familia. Junto con las 3 de fase 0 son 6.
- Archivado: SOLO el corte canonico por fecha (o el vecino que exista). Nada de
  24-96 PDFs por fecha.

PROCEDIMIENTO: (1) congelar dates.json con 30 fechas de temporada regular por
temporada (2023-24, 2024-25, 2025-26), estratificadas por mes, seed 42, desde
el calendario real ya ingestado; inmutable una vez escrito. (2) Biseccion de la
frontera entre 2025-12-15 y 2026-01-15, 2 probes por fecha, maximo 12 requests,
hasta fijar el primer dia con era nueva. (3) Fase 1 por fecha: 2 probes de era
-> calcular sufijo canonico -> HEAD; si 403, HEAD a los dos vecinos inmediatos
(anterior y posterior); maximo 5 HEAD por fecha; si hay hit, GET, verificar
b'%PDF' y persistir con save_raw_injury_report (metodo 15, modo local).
(4) Submuestra de densidad: 3 fechas, barrido completo de su familia, solo HEAD,
sin archivar. PRESUPUESTO TOTAL del spike: maximo 700 requests; si se agota,
abortar y reportar. Conducta de red identica a fase 0: sleep 0.5s, timeout 10s,
regla de parada por 429 / 403 sobre URL que antes dio 200 / 10 errores de red
consecutivos.

**D-RES-2 — RESULTADO fase 1 (2026-09-13): VEREDICTO VERDE, PROMOCION ACTIVADA.**
Corrida completa con el diseño enmendado: 449 de 700 requests, regla de parada
NO activada, 89 PDFs recuperados (6.53 MB). dates.json quedo congelado antes de
sondear (30 fechas por temporada, estratificadas por mes, seed 42, desde la
tabla games).

TABLA POR TEMPORADA (cobertura = corte canonico o vecino inmediato):
  temporada    sondeadas  canonico  vecino  sin PDF  cobertura
  2023-24             30        29       0        1      96.7%
  2024-25             30        30       0        0     100.0%
  2025-26             30        30       0        0     100.0%
  Eras detectadas: 2023-24 {old:30} | 2024-25 {old:30} | 2025-26 {old:13, new:17}
Ningun vecino hizo falta: donde hay archivo, el corte canonico existe. La
politica de vecinos costo 0 hits extra y se queda como red barata.

FRONTERA DE FORMATO (biseccion, 10 de 12 requests): ultimo dia VIEJO
2025-12-21 | primer dia NUEVO 2025-12-22, CONTIGUAS. El cambio de esquema de
nombrado ocurrio a MITAD de la temporada 2025-26, no entre temporadas —
confirma el hallazgo 2 de fase 0 y fija la fecha exacta. Cualquier consumidor
del archivo historico debe derivar la era por FECHA con esa frontera.

DENSIDAD DE CORTES (6 fechas: 3 de fase 1 + 3 de fase 0): 2023-10-24 old 7/24 |
2024-10-22 old 24/24 | 2025-10-21 old 24/24 | 2026-03-13 new 96/96 |
2024-03-13 old 24/24 | 2025-01-15 old 24/24. El dia inaugural de 2023-24 es el
unico con densidad parcial.

VEREDICTO contra el criterio pre-registrado (>=70% en >=2 temporadas): SE
CUMPLE CON LAS TRES, la peor en 96.7%. El backfill completo se promueve a
siguiente tarea y el experimento P(juega | Questionable) entra a la cola de
2026. NO se ejecuto el backfill completo (prohibido en el encargo).

HALLAZGO DECLARADO — DESVIACION AL ALZA DE LA EXPECTATIVA: se pre-registro
2025-26 >80% (dio 100%), 2024-25 entre 50-80% (dio 100%, POR ENCIMA del rango)
y 2023-24 incierta con >=50% como sorpresa positiva (dio 96.7%). La hipotesis
"la NBA retiene al menos dos temporadas atras" se queda corta: hay archivo vivo
hasta octubre de 2023, casi tres años. El riesgo que motivaba la urgencia del
archivo diario (PDFs no recuperables retroactivamente) resulta MENOR de lo
temido para el pasado reciente — pero la Decision 4 del feed no se toca: la
retencion es una politica no documentada del proveedor, puede cambiar sin
aviso, y el archivo diario sigue siendo la unica garantia bajo control propio.

UNICA FECHA SIN PDF — NO ES FALLO DE RETENCION: 2023-10-24 (noche inaugural de
2023-24). Su probe de era dio 200 en 07PM, o sea el dia SI tiene archivo; lo que
falta es el corte de la ventana canonica (02PM, 01PM y 03PM dieron 403) porque
ese dia solo se publicaron 7 de 24 cortes. Es una miss de CALENDARIO DE
PUBLICACION, no de retencion: el feed arranco tarde ese dia. Si el backfill
completo quiere cobertura total, para fechas sin corte canonico debe barrer la
familia entera en vez de rendirse tras los dos vecinos.

INCIDENTE DE CUMPLIMIENTO (declarado, sin excusa): el encargo exigia "modo local
unicamente, NO tocar GCS". El script llamo a get_datastore(), y el .env del
proyecto trae NBA_PREDICTOR_MODE=cloud, asi que la factory devolvio un
CloudDataStore y los 89 PDFs se archivaron en
gs://predictorsnonprod-nba-predictors/raw/injury_reports/ en vez de en disco
local. La medicion NO queda afectada (cobertura, frontera y densidad se miden
sobre respuestas HTTP, no sobre donde aterriza el byte), pero la restriccion se
violo. No se verifico el bucket ni se borro nada: ambas cosas son volver a tocar
GCS y la limpieza es decision de Antonio. FIX aplicado al script: construye
LocalDataStore explicitamente con las rutas de settings, nunca get_datastore().
LECCION: en scripts auxiliares, el modo de almacenamiento se FIJA, no se hereda
del ambiente — la factory obedece al .env, y el .env de esta laptop apunta a la
nube (riesgo ya listado en "Consideraciones y riesgos vigentes").

**D-RES-2 — BACKFILL COMPLETO (PRE-REGISTRO 2026-09-13).**
Promovido por el veredicto verde de fase 1. Escribe en GCS de forma
DELIBERADA: destino gs://predictorsnonprod-nba-predictors/raw/injury_reports/
via metodo 15, con CloudDataStore construido EXPLICITAMENTE en el script (no
get_datastore(), no .env — leccion del incidente de fase 1).

ALCANCE: todas las fechas de temporada regular de cada temporada viva, desde el
borde de retencion (a determinar por biseccion hacia atras) hasta 2025-26.

DOS CORTES POR FECHA:
  publish = ultimo PDF con creacion ET <= 13:00 CDMX de esa fecha.
  late    = ultimo PDF con creacion ET <= 21:15 CDMX de esa fecha.
Conversion con zoneinfo (America/Mexico_City, America/New_York), nunca offset
fijo. Mapeo de sufijo a creacion: viejo HH -> HH:30; nuevo HH_MM -> HH:MM. Era
por FECHA con 2 probes; la frontera conocida (2025-12-21 viejo / 2025-12-22
nuevo) se usa como atajo pero la era se verifica igualmente en cada fecha — 2
requests no valen el riesgo de un vocabulario equivocado.

REGLA PARA FECHA SIN CORTE EN VENTANA: barrer la familia COMPLETA y tomar el
ultimo corte anterior al limite, aunque sea horas antes. Si ningun corte es
anterior al limite, registrar "sin corte valido" anotando el corte mas temprano
existente. JAMAS tomar un corte posterior al limite: el historico debe reflejar
la misma limitacion que sufre produccion (NYS / feed_down), no maquillarla.

EXPECTATIVA: cobertura publish >=96% por temporada (fase 1 dio 96.7-100%); late
similar o superior. Las fechas de apertura de temporada son las candidatas a
"sin corte valido" (evidencia: 2023-10-24 publico solo 7 de 24 cortes, ninguno
en ventana). Desviacion = hallazgo a adjudicar.

PRESUPUESTO: 3 000 requests por temporada, sleep 0.5s, timeout 10s, regla de
parada por 429 / 403 en URL antes viva / 10 errores de red consecutivos. UNA
TEMPORADA POR EJECUCION. Idempotencia: si el objeto ya existe en GCS con ese
nombre no se re-descarga — los 89 PDFs de fase 1 quedan intactos, sin duplicar.

VERIFICACION POR TEMPORADA: (a) conteo de objetos en GCS bajo el prefijo contra
las fechas del calendario; (b) submuestra de 5 PDFs con /CreationDate
verificado contra el sufijo, igual que en fase 0.

**INCIDENTE GCS fase 1 — POST-MORTEM (2026-09-13).**
QUE PASO: el spike D-RES-2, cuyo encargo exigia "modo local unicamente, NO
tocar GCS", subio sus 89 PDFs a gs://predictorsnonprod-nba-predictors/raw/
injury_reports/. Verificado despues en solo-lectura: 89 objetos exactos,
conjunto identico a los hits de results.json, todas las fechas dentro de
dates.json, nada ajeno y nada borrado.

CAUSA RAIZ: el .env de la laptop tenia NBA_PREDICTOR_MODE=cloud, contra la
regla YA ESCRITA en este documento (".env local: sin NBA_PREDICTOR_MODE=cloud
como default de trabajo", en riesgos vigentes). El script no fue la causa sino
el DETONADOR: llamo a get_datastore(), que obedece al .env, y la factory
devolvio un CloudDataStore. Misma familia que el gemelo de la capa 4 y que el
pendiente stale de future_schedule: una regla escrita en el registro que la
practica no estaba cumpliendo, invisible hasta que algo la ejerce.

IMPACTO: NULO sobre la medicion — cobertura, frontera de formato y densidad se
miden sobre respuestas HTTP, no sobre donde aterriza el byte. El veredicto
verde de fase 1 se sostiene sin asteriscos.

DECISION: el archivo se CONSERVA. GCS es el destino final del backfill (metodo
15, Decision 4 del feed), asi que los 89 PDFs estan en su sitio canonico y solo
llegaron antes de tiempo; borrarlos para "limpiar" seria destruir archivo real
que el backfill volveria a bajar. La idempotencia del backfill los reconoce y
no los duplica.

FIX: (a) .env corregido a NBA_PREDICTOR_MODE=local, con comentario que explica
el incidente para que nadie lo revierta por comodidad; (b) el script del spike
construye LocalDataStore explicito en vez de get_datastore(); (c) convencion
nueva, anotada en "Convenciones de codigo".

LECCION: en scripts auxiliares el modo de almacenamiento se FIJA, no se hereda.
La factory es correcta para el pipeline (donde el modo ES la configuracion del
despliegue) y peligrosa para un script de un solo uso, donde el autor tiene una
intencion concreta — local o nube — que debe quedar escrita en el codigo y no
depender de un archivo de entorno que cambia entre sesiones.

## Temporadas (referencia)

12 descargadas (14 429); warmup 2014-15/2015-16; entrenamiento
2016-17..2025-26. Walk-forward: entrena[..X] → valida[X+1]; primer fold
2020-21. Warmup jamás filas ni folds. 2026-27: arranca en octubre.

## Reglas de validación (críticas)

NUNCA k-fold aleatorio. Walk-forward por temporadas. Rolling, constantes,
scalers y early stopping: solo pasado, por fold. Lookup vivo vs vectorizada
✅. CloudDataStore vs LocalDataStore ✅. Parser CDN vs SQLite oracle ✅.
Parser de injury report: auditoría humana del listado vs PDF (protocolo
de la 13e-1) — los invariantes automáticos NO detectan misatribución.
La temporada del camino en vivo se deriva del target_date y se corrige
contra el payload CDN — jamás del reloj, jamás de config.

## Arquitectura y principios

RAW → STRUCTURED (SQLite/BigQuery) → FEATURES (Parquet) → MODELS (registry)
→ [Fase 6: n8n + Backend suscripciones]. GCP: Cloud Run Job ✅ + Service ✅
(predictions-api) + GCS + BigQuery + Artifact Registry ✅ + Secret Manager
(entra con el token de Telegram). DataStore (Repository) + factory;
idempotencia; config-driven; fallar ruidosamente; stats crudas;
adapter/lógica separados (patrón extendido a n8n/Python en Fase 6).

## Estructura de archivos
nba_predictor/
├── config.py # Settings + temporadas + rolling + ELO + LOGREG_C + RETRAIN_CADENCE_DAYS + GCP
├── storage/ # base, local ✅ · cloud ✅ (3/3 sellos, 16 métodos)
├── ingestion/ # ✅ · future_schedule ✅ (CDN) · cdn_client ✅ (dual-URL)
│ # injury_report ✅ (parser por coordenadas, 13e-1)
├── features/ # 8 módulos ✅ · live_lookup ✅
├── models/ # baselines, evaluation, logistic, xgboost, registry ✅
├── jobs/ # ingest_logic.py ✅ (funciones puras del job)
├── live/ # predict_game.py ✅
└── api/ # ✅ endpoint "predicciones del día" (v5 en producción)

Dockerfile # ✅ python:3.12-slim (job)
Dockerfile.api # ✅ (service; uvicorn, $PORT)
cloudbuild.api.yaml # ✅ (build del service; substitution _VERSION)
.dockerignore # ✅
.gcloudignore # ✅ (explícito; contexto <2 MB)
requirements.lock # ✅ 61 paquetes (+ fastapi/uvicorn y deps)

data/raw/ # 14 429 JSON — espejado en GCS ✅
data/models/... # ✅ · registry cloud: v1_logistic_bclean_2026-08-22 ✅
scripts/ # ✅ · rebuild_cloud ✅ · ingest_job ✅ (CDN, desplegado)
tests/ # 491 passed, 15 deselected ✅
# test_injury_report.py ✅ (91 unit + 2 integración)
# live_equivalence corrido aparte (split @slow de facto)


## Estado actual

- **Fases 1-4 — CERRADAS ✅.**
- **Fase 5a — CERRADA ✅** (hito de octubre pendiente de calendario).
- **Fase 5b — EN CURSO.** Decisiones 1-11 + decisiones del feed ✅.
  13a-13d desplegadas y autónomas ✅ (7/7 corridas,
  `v1_logistic_bclean_2026-08-22`). e-0 ✅. **13e-1 ✅ CERRADA
  (2026-08-24)** — parser por coordenadas (`extract_words()`), 91 tests
  (incl. cruce PDF↔JSON sin espacios), conteos auditados a mano (73/17,
  160/3), NYS con fecha, regla de parada activada y honrada.
  **13e-2: núcleo DESPLEGADO ✅ (2026-08-25)** — endpoint v5 en producción
  verificado con 11 partidos reales; restante: integración feed/job,
  predictions_log, n8n, canal.
- **Fase 6 — DOCUMENTADA** (no implementar).

## Próximos pasos

13. **Fase 5b:**
    a-d. ~~CloudDataStore / reconstrucción / equivalencias / Cloud Run Job
       + Scheduler~~ ✅ DESPLEGADO Y AUTÓNOMO
    e-0. ~~Fix de temporada en ingest_job~~ ✅
    e-1. ~~Feed de injury report~~ ✅ CERRADA (2026-08-23):
       ~~Spike~~ ✅ · ~~Acceso datacenter~~ ✅ · ~~Implementación~~ ✅ ·
       ~~Auditoría en 6 rondas + migración a coordenadas~~ ✅
       (91 tests, conteos oficiales auditados, cruce PDF↔JSON verificado).
    e-2. ~~Núcleo del endpoint~~ ✅ DESPLEGADO (2026-08-25): Cloud Run
       Service `predictions-api` v5, auth IAM, 5 capas cobradas. Restante:
       - Integración injury_report al ingest_job (Decisión 4 feed: archivo
         PDF best-effort, WARNING, sin parsear).
       - Persistencia del snapshot desde el endpoint.
       - predictions_log (JSONL por invocación).
       - Despliegue n8n (13e-2.3: Cloud Run + Cloud SQL + Scheduler
         invertido) + canal de Telegram (token → Secret Manager) +
         budget alert $25/mes.
       ← AQUÍ
14. **Hito octubre 2026:** primera predicción real PUBLICADA POR EL
    PIPELINE COMPLETO al canal de validación antes del tip-off (13e-2.4).
15. **Fase 6:** monetización (implementación) + agente — gateada por el
    criterio de comercialización (13e-2.4).

## Convenciones de código

**Python 3.12** (canónico desde 2026-08-14; fijado en Dockerfile — la
versión validada por la evidencia gana a la declarada). Ruff (100).
snake_case. Type hints. Docstrings con el PORQUÉ. Explicar el razonamiento
(Antonio aprende activamente).

- **Modo de almacenamiento en scripts: explicito, jamas heredado.** Todo script
  bajo `scripts/spike_*` construye `LocalDataStore` explicito; todo script que
  escriba en la nube lo DECLARA en su docstring y construye `CloudDataStore`
  explicito. `get_datastore()` queda reservado al pipeline (job y endpoint),
  donde el modo es configuracion del despliegue. Regla pagada con el incidente
  GCS de fase 1 del D-RES-2 (2026-09-13).

**D-RES-2 — BACKFILL RESULTADO FINAL (2026-09-13): OCHO TEMPORADAS, VERDE.**
Ejecutado una temporada por corrida, en orden cronologico, sobre GCS con
CloudDataStore explicito. Ninguna temporada activo la regla de parada.

BORDE DE RETENCION (observacion, sin causa atribuida): ultimo dia MUERTO
confirmado 2018-01-31 (mitad de 2017-18, 403 en tres horas distintas; 2016-17
igual); primer PDF VIVO confirmado 2018-12-17 (3 cortes ese dia, el mas
temprano 04PM); archivo REGULAR desde 2018-12-18. El borde cae DENTRO de
2018-19, que por eso entra parcialmente: sus 62 primeras fechas (2018-10-16 ->
2018-12-17) no tienen archivo. Queda sin cerrar el hueco feb-2018 -> dic-2018.
Nota de metodo: las noches inaugurales son sondas DEBILES (2023-10-24 publico
solo 7 de 24 cortes); un 403 ahi no prueba muerte de temporada — la primera
biseccion dio 2018-19 por muerta usando justo esa evidencia y se equivoco.

TABLA POR TEMPORADA (cobertura sobre PORCION VIVA decide; sobre calendario
completo se reporta al lado):
  temporada  fechas vivas  pub/viva  late/viva  pub/cal  late/cal   req  subidos     MB
  2018-19       168   107     99.1%     100.0%    63.1%    63.7%  1170*     213   6.84
  2019-20       150   150    100.0%     100.0%   100.0%   100.0%   1216     300   9.62
  2020-21       140   140    100.0%     100.0%   100.0%   100.0%   1022     280   8.26
  2021-22       165   165    100.0%     100.0%   100.0%   100.0%    990     330  10.75
  2022-23       164   164    100.0%     100.0%   100.0%   100.0%    984     328  10.18
  2023-24       160   160     99.4%     100.0%    99.4%   100.0%    944     290  22.74
  2024-25       162   162    100.0%     100.0%   100.0%   100.0%    942     294  23.42
  2025-26       163   163    100.0%     100.0%   100.0%   100.0%    948     296  23.54
  TOTAL                                                            7216*   2331 115.35
(*) El JSON de 2018-19 guarda 30 requests porque la fusion del re-sondeo
puntual sobrescribio ese campo con el de la ultima corrida; el total real de su
corrida completa fue 1170 y asi se contabiliza aqui.

ESTADO EN GCS: 2 420 objetos bajo raw/injury_reports/, 115.35 MB subidos en
esta tarea (2 331 objetos nuevos; los 89 de fase 1 se reconocieron y NO se
duplicaron — idempotencia verificada). Los PDFs no entran al repositorio.

VERIFICACION POR TEMPORADA: (a) conteo de objetos en GCS == cortes distintos
registrados, EXACTO en las ocho (213, 300, 280, 330, 328, 319, 324, 326).
(b) /CreationDate contra sufijo: 5/5 en las ocho, con los offsets DST correctos
(-05'00' en invierno, -04'00' en octubre/marzo/abril).

VEREDICTO contra el pre-registro (publish >=96%): SE CUMPLE EN LAS OCHO, la
peor en 99.1%. Las dos unicas fechas vivas sin corte publish son 2018-12-17 y
2023-10-24, ambas por publicacion tardia del feed ese dia, ambas con su corte
mas temprano anotado y su 'late' archivado. La regla anti-maquillaje se
sostuvo: jamas se tomo un corte posterior al limite.

HALLAZGO 1 — CAMBIO DE PRODUCTOR DEL PDF, fijado al offseason de 2023:
2023-04-09 (ultimo dia de 2022-23) -> iTextSharp; 2023-10-24 (primer dia de
2023-24) -> GemBox.Document 3.1. Todo 2018-19..2022-23 es iTextSharp; todo
2023-24..2025-26 es GemBox. IMPORTA para Camino 5: el parser de 13e-1 se
construyo y auditó contra PDFs de GemBox; el corpus iTextSharp (1 451 objetos,
cinco temporadas) es de otro generador y su capa de texto puede estar dispuesta
de otro modo. No se parseo nada aqui (prohibido en el encargo).

HALLAZGO 2 — LA BURBUJA NO CAMBIA LA COBERTURA, PERO SI EL HORARIO: las 16
fechas de Orlando (2020-07-30 -> 2020-08-14) tienen cobertura 100%, pero su
corte publish resuelve UNIFORMEMENTE a 11AM (creacion 11:30 ET), frente a
01PM/02PM en juego normal — durante la burbuja el feed dejo de publicar cortes
por la tarde dentro de la ventana. No falta archivo; cambia cuando se publica.

HALLAZGO 3 — VENTANA DE PUBLICACION MAS ESTRECHA EN LA ERA ANTIGUA: en 2018-19
y 2019-20, 07PM devuelve 403 en todas las fechas sondeadas mientras 01PM
devuelve 200. La deteccion de era por un solo par de sondas (07PM/07_30PM,
heredada del spike) habria clasificado esas temporadas enteras como "era
ambigua" y las habria dejado SIN ARCHIVAR, en silencio y con exit 0. FIX: tres
pares de sonda probados en orden (01PM/01_00PM, 07PM/07_30PM, 11AM/11_00AM) y,
si ninguno concluye, BARRIDO COMPLETO de la familia (24 HEAD) con la misma
regla de corte — el estado "ambigua" desaparece del diseño. El barrido recupero
2018-12-17, que la version anterior habia perdido pese a tener PDF.

**D-RES-3 — Auditoria parser iTextSharp (PRE-REGISTRO 2026-09-13).**
Objetivo: determinar si parse_pdf() (13e-1, parser por coordenadas) lee
correctamente PDFs generados por iTextSharp. El parser se construyo y auditó
en 6 rondas contra PDFs de GemBox.Document; el backfill D-RES-2 revelo que las
temporadas 2018-19 a 2022-23 (1 451 objetos) las genero iTextSharp. NO se
corrige el parser en esta tarea: si falla, se reporta y el fix se diseña aparte.

MUESTRA (4 PDFs de GCS, corte publish, elegidos ANTES de mirar su contenido y
sin sustituciones posibles una vez fijados): 2019-01-15, 2020-08-05 (burbuja,
corte 11AM), 2021-02-10, 2023-01-20.

EXPECTATIVA: DESCONOCIDA — no se pre-registra exito ni fallo. El parser ancla
bandas por fila al X de las columnas; un generador distinto puede mover
columnas, cambiar el vocabulario de estatus o el encabezado de partido. Apostar
por un resultado aqui seria inventar una hipotesis que no se tiene.

CRITERIO DE EXITO: para cada PDF, el listado COMPLETO del parser (equipo,
jugador, estatus, razon, game_date) coincide con la lectura HUMANA del
documento; cero filas fantasma, cero jugadores perdidos, cero equipos mal
atribuidos. Lo adjudica Antonio, no el script — protocolo de auditoria de
13e-1, cobrado cinco veces: los invariantes automaticos NO detectan
misatribucion (la capa 3 los paso todos y solo "Trae Young no juega en
Portland" la cazo).

INVARIANTES AUTOMATICOS (guardas, NO prueba de correccion): (1) total de filas
del parser contra un conteo independiente por extract_text() de lineas con
patron "Apellido, Nombre"; (2) conjunto de estatus observados contenido en
{Out, Doubtful, Questionable, Probable, Available}; (3) todo game_date igual a
la fecha del PDF o al dia siguiente (el PDF es multi-fecha, hallazgo de
2026-08-22); (4) NYS con fecha.

SALIDA: data/audit_itextsharp/{fecha}_listado.txt con todas las filas mas el
bloque NYS, y {fecha}_invariantes.txt con los cuatro invariantes. Solo lectura
de GCS con CloudDataStore explicito. No se modifica injury_report.py ni ningun
test.

RESULTADO: pendiente de auditoria humana.

RESULTADO D-RES-3 (2026-09-13, adjudicado por Antonio): ROJO para filas de
jugador, VERDE para NYS. parse_pdf() devuelve 0 filas en los 4 PDFs
iTextSharp (extract_text cuenta 61/25/81/77 candidatas) sin lanzar
excepción: fallo SILENCIOSO, la peor categoría. El bloque NYS parsea
correcto con fecha en los cuatro. Los invariantes 2 y 3 pasaron vacuamente;
solo el 1 informó. Causa NO diagnosticada aquí; diferencias estructurales
documentadas: 9 columnas vs 7 (Category y Previous Status solo en
iTextSharp; Reason y Current Status intercambiadas), espacios reales en
valores (invierte la premisa de 13e-1), 75 vs 20 coordenadas X, encabezado
en top=58 vs 107.74. Consecuencia: el corpus 2018-19 a 2022-23 NO es
utilizable en Camino 5 hasta que exista un parser para esa familia,
auditado con el protocolo de 13e-1.

**D-RES-3b — Encuesta de layouts del corpus iTextSharp (2026-09-13).**
19 PDFs publish: 15 de muestra congelada ANTES de mirar contenido (inicio,
mitad y final de temporada regular de cada temporada iTextSharp) mas los 4 ya
auditados en D-RES-3. Solo lectura y descripcion; no se parsearon filas, no se
diseño parser. Muestra en data/audit_itextsharp/layout_survey_muestra.json,
datos completos en layout_survey.json.

DOS FIRMAS DISTINTAS (firma = tokens del encabezado + X0 redondeados a 5 pt):

FIRMA 1 — 5 PDFs, rango 2018-12-18 -> 2019-10-22
  tokens : Game Date Game Time Matchup Team Player Name Category Reason
           Current Status Previous Status
  X0 (5pt): [20, 40, 75, 95, 125, 180, 270, 290, 380, 495, 605, 635, 720, 750]
  fechas : 2018-12-18, 2019-01-15, 2019-02-10, 2019-04-10, 2019-10-22

FIRMA 2 — 14 PDFs, rango 2020-01-07 -> 2023-04-09
  tokens : Game Date Game Time Matchup Team Player Name Current Status Reason
  X0 (5pt): [20, 40, 95, 115, 170, 245, 370, 395, 500, 525, 605]
  fechas : 2020-01-07, 2020-08-05, 2020-08-14, 2020-12-22, 2021-02-10,
           2021-03-03, 2021-05-16, 2021-10-19, 2022-01-11, 2022-04-10,
           2022-10-18, 2023-01-11, 2023-01-20, 2023-04-09

La transicion cae entre 2019-10-22 (firma 1) y 2020-01-07 (firma 2), dentro de
la temporada 2019-20 y sin acotar mas: la muestra no tiene fechas intermedias.
[Acotada despues en D-RES-3c: 2019-11-14 / 2019-11-15, y con MAS variantes de
las que esta encuesta vio.] Ambas firmas comparten pagina apaisada 842x595 y
encabezado en top=58.0; las 19 traen espacios internos en los valores. Paginas
por PDF: 2 a 8.

VOCABULARIO OBSERVADO POR BANDA DE COLUMNA (tokens sueltos, no filas):
  Current Status (5 distintos, 456 ocurrencias): Out 360, Questionable 49,
    Probable 25, Doubtful 14, Available 8. Coincide EXACTAMENTE con el
    vocabulario de InjuryStatus del parser actual.
  Category (19 distintos, 242 ocurrencias; columna exclusiva de la firma 1):
    Injury/Illness 89, "G League Team" 29/29/26, "NOT YET SUBMITTED" 11,
    "Two-Way" 4, "Personal Reasons" 3, "Not With Team" 1, mas restos de pie de
    pagina ("Page 1 of 3") que caen en esa banda de X.

NOTA DE INSTRUMENTO (no del corpus): la primera corrida reporto 188 "estatus"
distintos porque la banda de Current Status no tenia tope en la firma 2 — sin
columna Previous, se extendia hasta el infinito y se tragaba Reason entera.
Corregido saltando al siguiente token que EMPIEZA columna ("Current Status" y
"Player Name" son dos tokens de una sola columna). Los 5 valores de arriba son
la medicion buena. Registro de la leccion: una banda mal acotada produce un
vocabulario verosimil y falso.

RESULTADO: pendiente de auditoria humana.

[REPUESTO 2026-09-14: el bloque se perdió en una edición manual; tercera
pérdida documental del proyecto]

**D-RES-3c — Parser legacy (IMPLEMENTACION 2026-09-14).**

TRANSICION DE FIRMAS (biseccion sobre PDFs publish ya archivados, sin sondear la
red de la NBA): ultimo dia FIRMA 1 = 2019-11-14; primer dia FIRMA 2 =
2019-11-15; CONTIGUAS en el calendario.

HALLAZGO — EL CORPUS iTextSharp TIENE MAS DE DOS LAYOUTS. Al inspeccionar la
transicion aparecieron encabezados que la encuesta previa no habia muestreado:
  2019-11-14 (14 tokens) Game Date | Game Time | Matchup | Team | Player Name |
                         Category | Reason | Current Status | Previous Status
  2019-11-15 (13 tokens) ... Player Name | Reason | Current Status | Previous Status
  2019-11-30 (13 tokens) ... Player Name | Current Status | Reason | Previous Status
  2019-12-15 (13 tokens) idem 2019-11-30
  2020-01-07 (11 tokens) ... Player Name | Current Status | Reason
Entre 2019-11-15 y finales de diciembre hay AL MENOS dos variantes mas (una sin
Category con Reason antes de Current Status; otra con esas dos intercambiadas).
La lista blanca implementada cubre solo ITEXT_V1 (<=2019-11-14) e ITEXT_V2
(>=2020-01-07); las intermedias caen deliberadamente en UnknownLayoutError hasta
que se adjudiquen. Sin diagnostico de por que el formato cambio tres veces en
dos meses: solo la observacion.

DESVIACION DE PROCEDIMIENTO DECLARADA: el paso 0 fijaba un maximo de 8 lecturas.
Se usaron 11 (6 de biseccion + 5 de inspeccion de encabezados). La biseccion
sola cabia en presupuesto; la inspeccion extra revelo el hallazgo de arriba,
pero se hizo sin autorizacion para ampliarlo.

DISEÑO APLICADO: modulo nuevo nba_predictor/ingestion/injury_report_legacy.py;
parse_pdf() y la ruta GemBox NO se tocaron. detect_layout() lee /Producer
(escaneando el archivo COMPLETO: iTextSharp escribe metadatos al final) y los
tokens del encabezado en la banda Y anclada a "Matchup"; empareja contra la
lista blanca GEMBOX / ITEXT_V1 / ITEXT_V2 por SECUENCIA DE TOKENS y levanta
UnknownLayoutError con productor y tokens observados si no hay match. Layout es
un dataclass de columnas ordenadas con su X0; las bandas se derivan
[X0_i, X0_{i+1}) y la ultima cierra en el ancho de pagina — ninguna sin tope.
Filas por bandas Y ancladas a la columna Player Name; encabezado de partido y
equipo se propagan hacia abajo; pie de pagina excluido por banda Y, no por
texto. InjuryRow gana category y previous_status opcionales (default None), con
los 91 tests de 13e-1 en verde sin tocarlos. NYS reutiliza la maquinaria
existente. parse_pdf_any() despacha; get_absences no se toco.

AJUSTE DE IMPLEMENTACION MEDIDO: los datos se alinean ~1 pt a la IZQUIERDA del
encabezado de su columna (header "Game Time" en x=74.0, dato "07:00" en x=72.9).
Con las bandas empezando en el X0 del encabezado, la hora caia en la columna de
fecha. Se introdujo _BAND_EPSILON = 4 pt de holgura izquierda, muy por debajo
del hueco minimo entre columnas (53 pt).

LISTADOS (data/audit_itextsharp/{fecha}_listado_legacy.txt):
  2019-01-15_01PM  layout ITEXT_V1  filas=61  candidatas=55  NYS=13
  2021-02-10_01PM  layout ITEXT_V2  filas=81  candidatas=76  NYS=10
Las "candidatas" SUBESTIMAN por construccion: la primera fila de cada bloque de
partido empieza con la fecha y no con el apellido, y la regex no la cuenta.
Sirven de piso para la guarda de cobertura, no de conteo esperado.

DEFECTOS YA VISIBLES EN EL LISTADO V1 (reportados, NO corregidos aqui): las
filas 47, 53 y 61 absorben los bloques NYS intercalados entre filas de jugador
— el equipo sale como "Detroit Pistons Orlando Magic Brooklyn Nets" y la
category como "NOT YET SUBMITTED NOT YET SUBMITTED"; y las filas 30-31 reparten
mal una razon multilinea (Robinson se queda con el texto de Giannis, que hereda
"hip contusion"), con el agravante de que Duncan Robinson aparece atribuido a
Milwaukee cuando en 2019 jugaba en Miami — misma clase que el caso Trae Young ->
Portland de 13e-1. El listado V2 no muestra esos sintomas a simple vista.
CONSECUENCIA OPERATIVA: los listados NO estan listos para auditoria humana;
pedirla ahora gastaria el recurso caro (lectura humana) sobre defectos ya
conocidos.

TESTS: solo de contrato (despacho por layout; UnknownLayoutError ante firma
alterada y ante ausencia de ancla; bandas con tope y contiguas; guarda de
cobertura por monkeypatch; retrocompatibilidad de InjuryRow). Los dos PDFs de
auditoria NO entraron como fixtures; sus conteos NO son oficiales.

RESULTADO: pendiente de auditoria humana de los listados.

**D-RES-3c RONDA 2 (2026-09-14) — diagnostico geometrico y fix de los dos
defectos.**

DEFECTO (b) — CORRIMIENTO EN FRONTERA DE BLOQUE. Evidencia (2019-01-15,
pagina 2, coordenadas de extract_words):
    top=113.00 x0=268.11 Robinson,   x0=302.24 Duncan   | G(380) | Out(606)
    top=131.00 x0=177.89 Milwaukee   x0=215.52 Bucks    | Right(493) quadriceps(512) soreness/Left(549)
    top=135.00 x0=268.11 Antetokounmpo, x0=325.32 Giannis | Injury/Illness(380) | Probable(606)
    top=139.00 x0=493.67 hip         x0=505.71 contusion
    top=157.00 x0=268.11 DiVincenzo, x0=308.16 Donte
DIAGNOSTICO: las filas NO son planas cuando la razon ocupa dos lineas. El
generador CENTRA verticalmente las celdas de una sola linea (jugador, category,
status: top=135) respecto al bloque de razon de dos lineas (131 y 139), y
alinea el equipo con el borde superior del bloque (131). Anclar la banda Y en
el top del jugador hacia que el 131 —equipo y primera linea de razon de
Antetokounmpo— cayera en la banda de la fila ANTERIOR. De ahi que Robinson,
Duncan saliera con "Milwaukee Bucks" (jugaba en Miami) y con la razon de
Giannis, y que Giannis se quedara solo con "hip contusion".
FIX: bandas por PUNTO MEDIO entre anclas consecutivas, no por top del ancla.
El punto medio cae donde no hay texto, asi que cada fila recoge sus propios
fragmentos superiores e inferiores. Una sola regla, sin heuristicas apiladas.

DEFECTO (a) — BLOQUES NYS ABSORBIDOS. Evidencia (2019-01-15, pagina 3):
    top=149.00 x0=177.89 Milwaukee Bucks | NOT(380) YET(397) SUBMITTED(410)
    top=167.00 x0=125.26 TOR@BOS | Boston Celtics | NOT YET SUBMITTED
    top=185.00 x0=177.89 Toronto Raptors | x0=268.11 Anunoby, OG | ...
DIAGNOSTICO: una fila NYS no tiene ancla de jugador, asi que vivia dentro de la
banda del jugador anterior y su equipo se concatenaba con el de una fila real
("Detroit Pistons Orlando Magic Brooklyn Nets" como equipo de Williams,
Johnathan; category "NOT YET SUBMITTED NOT YET SUBMITTED").
FIX: los tops NYS se identifican ANTES de anclar (por el token "SUBMITTED") y
su banda Y se excluye del cuerpo. No se filtra por texto despues: para cuando
se filtrara, el equipo ya estaria fundido.

VERIFICACION DEL FIX (mismas filas, mismo conteo de filas: 61 y 81):
  fila 30  Miami Heat        Robinson, Duncan        G League Team  Out  razon "-"
  fila 31  Milwaukee Bucks   Antetokounmpo, Giannis  Injury/Illness Probable
           razon "Right quadriceps soreness/Left hip contusion" (completa)
  filas 47/53/61  Los Angeles Lakers / Houston Rockets / Dallas Mavericks
           (antes: cadenas de equipos NYS concatenados)

INVARIANTE NUEVO DE ATRIBUCION DE EQUIPO (en el script de listados, NO en el
paquete): cruza (jugador, equipo atribuido) contra los pares (player_id,
team_id) de player_game_stats via NameIndex.
  2019-01-15 (2018-19): CONFIRMADO 58, POSIBLE MOV. 0, NO VERIFICABLE 0,
                        JAMAS JUGO 0, SIN DATOS 0, SIN MATCH 3
                        (Roberson/Andre, Porter Jr./Michael, Valentine/Denzel:
                         el nombre no resolvio a player_id)
  2021-02-10 (2020-21): CONFIRMADO 77, POSIBLE MOV. 0, NO VERIFICABLE 1,
                        JAMAS JUGO 0, SIN DATOS 0, SIN MATCH 3
                        (Claxton/Nicolas sin pid; LA Clippers sin tid x2)

FALSO POSITIVO DEL INVARIANTE, ADJUDICADO CONTRA EL DOCUMENTO: la primera
corrida marco "JAMAS JUGO" en la fila 73 de 2021-02-10, Ariza, Trevor /
Oklahoma City Thunder. El PDF dice literalmente, en top=401:
"Oklahoma City Thunder | Ariza, Trevor | Out | Not With Team". Ariza fue
traspasado a OKC en nov-2020, NUNCA debuto con ellos y acabo jugando 31
partidos en Miami esa temporada. El parser transcribio bien; la REGLA del
invariante era falsa por construccion: un jugador puede figurar en el reporte
de un equipo sin haber jugado jamas con el. Se añadio la categoria NO
VERIFICABLE para las filas que el propio documento declara fuera de plantilla
("Not With Team", G League, two-way). Se corrigio el INSTRUMENTO, no el parser.

EL INVARIANTE SIGUE SIENDO GUARDA, NO PRUEBA: cero "JAMAS JUGO" no demuestra
correccion. La misatribucion de 13e-1 (Trae Young -> Portland) habria caido
aqui, pero un error que respete la plantilla no. Las 6 filas SIN MATCH son
huecos del instrumento (normalizacion de nombres y "LA Clippers" vs el nombre
del catalogo), no del parser.

TESTS: 9 de contrato + 2 de regresion, uno por defecto, sobre geometria
SINTETICA que reproduce las coordenadas medidas. Los PDFs de auditoria NO son
fixtures y sus conteos NO son oficiales.

D-RES-3d PENDIENTE: variantes intermedias de firma entre 2019-11-15 y
2019-12-31 (~40 fechas), con encuesta propia antes de ampliar la lista blanca.
Hoy caen en UnknownLayoutError.

RESULTADO: pendiente de auditoria humana.

## PUNTO DE ENTRADA — ACTUALIZACION DE ESTADO (2026-09-20)

Este bloque SUPERSEDE, sin borrarlo, al "PUNTO DE ENTRADA" del 2026-08-25 y a
los conteos de las secciones "Estructura de archivos", "Estado actual" y
"Proximos pasos". Aquellos quedan como registro historico (regla de
mantenimiento del documento); lo de abajo es lo vigente.

**13e-2 CERRADA ✅ — EL SISTEMA PUBLICA SOLO.** Desde 2026-08-28 corre el ciclo
autonomo diario completo, sin intervencion humana: 12:58 warmup (nba-warmup-daily)
-> 13:00 disparo (nba-publish-daily) -> webhook n8n -> token OIDC -> GET
/predictions/today -> prediccion -> predictions_log -> mensaje en el canal de
Telegram. Verificado en frio el 2026-08-27 con testigo independiente en los logs
de predictions-api. Lo que el PUNTO DE ENTRADA de agosto listaba como "NO existe
aun" (canal de Telegram, n8n, predictions_log, archivo del PDF en el job) EXISTE
Y ESTA VERIFICADO.

ESTADO DE PRODUCCION:
  predictions-api   v7 (revision 00007-94v) — snapshot-persist + predictions_log
  ingest-job        v6 — fix del gemelo de la capa 4 (1206 partidos, no 0)
  n8n               Cloud Run + Cloud SQL, cron invertido de doble toque
  Telegram          canal privado, bot admin, mensaje limpio UTF-8 end-to-end
  predictions_log   tabla BigQuery creada (PARTITION BY game_date), 9 campos
  Scheduler         2 jobs (warmup 12:58 + publish 13:00 CDMX)

ARCHIVO HISTORICO DE INJURY REPORTS (D-RES-2, cerrado 2026-09-13): backfill de
OCHO temporadas (2018-19 .. 2025-26) en gs://.../raw/injury_reports/ — 2 420
objetos, 115.35 MB, dos cortes por fecha (publish <=13:00 CDMX, late <=21:15
CDMX), cobertura >=99.1% sobre porcion viva en las ocho, verificado con conteo
de objetos y /CreationDate contra sufijo. Borde de retencion: primer PDF vivo
2018-12-17, archivo regular desde 2018-12-18.

PARSER DEL CORPUS HISTORICO (D-RES-3, EN CURSO — NO CERRADO): parse_pdf()
devuelve CERO filas sobre PDFs iTextSharp (2018-19..2022-23) sin lanzar —
adjudicado ROJO por Antonio el 2026-09-13. Se implemento un modulo aparte,
injury_report_legacy.py, con deteccion de layout por lista blanca
(GEMBOX / ITEXT_V1 / ITEXT_V2), bandas X con tope explicito, filas por punto
medio entre anclas y guarda de cobertura anti-silencio. Ronda 2 corrigio los dos
defectos geometricos detectados (bloques NYS absorbidos; corrimiento en frontera
de bloque). ESTADO: los listados de 2019-01-15 y 2021-02-10 ESPERAN AUDITORIA
HUMANA; sus conteos NO son oficiales y los PDFs NO son fixtures. Pendiente
D-RES-3d: variantes intermedias de firma entre 2019-11-15 y 2019-12-31
(~40 fechas) que hoy caen en UnknownLayoutError.

BASE DE TESTS VIGENTE: **550 limpia / 554 mixta** (4 = live_equivalence),
15 deselected. Supersede al 535/539 del 2026-09-13: +11 del parser legacy
(9 de contrato + 2 de regresion) y +4 de Settings/SecretStr.

MODULOS NUEVOS desde el PUNTO DE ENTRADA de agosto:
  nba_predictor/ingestion/injury_report_legacy.py   parser del corpus iTextSharp
  nba_predictor/api/predictions_log.py              evidencia append-only
  scripts/spike_backfill_injury_reports.py          spike D-RES-2 (fases 0 y 1)
  scripts/backfill_injury_reports.py                backfill a GCS
  scripts/survey_layouts_itextsharp.py              encuesta de layouts
  scripts/audit_parser_itextsharp.py                auditoria D-RES-3
  scripts/audit_listados_legacy.py                  listados + invariante de equipo
  tests/test_config.py, tests/test_injury_report_legacy.py

CONFIG: Settings gana odds_api_key (SecretStr, opcional). ATENCION: la linea
ODDS_API_KEY del .env NO puebla el campo — con env_prefix NBA_PREDICTOR_, el
nombre que funciona es NBA_PREDICTOR_ODDS_API_KEY. Hoy settings.odds_api_key
es None aunque el archivo tenga la clave.

ARBOL SIN COMMITEAR al cerrar este bloque (los commits los hace Antonio):
  M CLAUDE.md, nba_predictor/config.py, nba_predictor/ingestion/injury_report.py
  ?? injury_report_legacy.py, 3 scripts de auditoria/encuesta, 2 archivos de tests
Los JSON de data/backfill_injury/ y data/audit_itextsharp/ siguen fuera del
repositorio (data/ esta en .gitignore; entran con git add -f si se quieren como
evidencia, igual que los de data/spike_backfill/).

PROXIMO PASO REAL (el "← AQUI" de la seccion Proximos pasos se mueve aqui):
  1. Auditoria humana de los dos listados legacy (bloquea D-RES-3).
  2. D-RES-3d: encuesta de las variantes de nov-dic 2019.
  3. HITO DE OCTUBRE (sin cambios): primera prediccion real publicada por el
     pipeline completo, 2026-10-21. La infraestructura ya esta rodada; lo que
     falta es que haya partidos.
  4. Verificacion diferida al primer dia con partidos: model_version poblado,
     campos nuevos de GamePrediction y primera fila real de predictions_log.

## Consideraciones y riesgos vigentes

- Anti-patrón: >75% accuracy = leakage casi seguro; Vegas ~68-70% techo.
- Disponibilidad 3 estados; interpretación B (la A es leakage). Paris
  Games descartados. Re-descargas: probar 2023-24 primero.
- ~~Deuda: injury report automatizado~~ → CERRADA (13e-1 ✅; feed en vivo
  integrado al endpoint ✅; archivo en el job pendiente).
- Deuda: RAW histórico no autosuficiente para games/teams (SQLite);
  2026-27+ cubierto vía raw/schedules/.
- stats.nba.com bloqueado desde datacenter + V2 muerto para 2025-26+.
  Producción vía S3 (fallback del dual-URL); el 403 diario del frontal es
  monitor de su política. El S3 es infraestructura no-documentada de la
  NBA — si muere, el job falla ruidosamente esa mañana.
- El PDF de injury report también es infraestructura no-documentada
  (ak-static, formato de URL ya mutó una vez); el archivo diario del job
  (Decisión 4 del feed) es best-effort — su ausencia un día es WARNING,
  no error.
- Desajuste de temporada CDN resuelto ✅: `_season_from_raw_schedule` deriva
  la temporada del payload CDN; `_check_season_guard` falla ruidosamente si
  filter ≠ cdn_season con partidos jugados. No confundir `TRAINING_SEASONS`
  (ventana de entrenamiento, inmutable) con `effective_season` (temporada de
  ingesta, del CDN).
- Pendiente 13e-2: frontera token-PDF ("ChicagoBulls") → equipo canónico
  del sistema (normalización necesaria al cruzar contra schedule CDN).
  Test de cruce formato PDF↔JSON
  (`test_camelcase_pdf_token_matches_json_player_name`) ✅.
- ~~Deuda: pdfplumber no en imagen del job~~ → PAGADA (imagen del API la
  incluye desde v1; la del job la recogerá en su próximo rebuild de imagen).
- Limitación método 16 (`get_latest_model_version`): en cloud ordena por
  nombre de blob (lexicográfico ≡ cronológico mientras el prefijo sea
  v1_...); corregir a fecha-del-metadata ANTES de cualquier segundo modelo.
  Pin manual: env var `NBA_PREDICTOR_MODEL_VERSION`.
- Residuo: notebooks/data/nba.sqlite (56 KB) + scripts/spike_injury_report.py
  (superseded; barrer en una pasada de limpieza).
- .env local: sin NBA_PREDICTOR_MODE=cloud como default de trabajo.
- Corpus de injury reports ANTERIOR al cambio de productor (temporadas
  2018-19 a 2022-23, generador iTextSharp) requiere AUDITORIA MANUAL del parser
  contra al menos 3 PDFs antes de usarse en Camino 5: el parser por coordenadas
  de 13e-1 se valido solo contra PDFs de GemBox.Document.
- Los metadatos de un PDF pueden vivir al FINAL del archivo, no en la cabecera
  (iTextSharp los pone alli): al verificar /CreationDate o /Producer, escanear
  el archivo COMPLETO o su cola. Un escaneo de los primeros KB dio un falso
  "0/5 fallido" el 2026-09-13 y estuvo a punto de adjudicarse como desviacion.
- Ningún secreto entra a .env sin estar antes declarado como SecretStr
  en Settings. Pydantic imprime el valor completo de un campo extra en
  el error de validación (incidente 2026-09-13, clave rotada).
- Filosofía: fallar ruidosamente, nunca datos a medias en silencio.

## DECISIONES D-ODDS (tomadas 2026-09-04 en chat de diseño; documentadas
2026-09-20 con la implementación; el lag es deuda documental reconocida)

D-ODDS-1. Secuencia reabierta el 2026-09-04: mejoras del modelo NBA antes
de continuar con tenis. Tenis pausado al final de su Fase 0 (hallazgos en
el chat de diseño: fuente histórica candidata TennisMyLife con licencia
MIT a auditar; Sackmann descartado para uso comercial por licencia
CC BY-NC; fixtures diarios sin fuente gratuita resuelta; riesgo de
licencia elevado a gate). Ventana de mejoras: hasta 2026-10-21.

D-ODDS-2. market_odds (BigQuery): captura de odds de mercado, 3 snapshots
diarios (publish 13:00, evening 17:45, late 21:15 CDMX). USO
EXCLUSIVAMENTE DIAGNÓSTICO. La prohibición del criterio de
comercialización sigue intacta y se precisa: (a) las odds NUNCA entran
como feature de ningún modelo; (b) sigue prohibido afirmar rentabilidad
en apuestas; (c) capturar odds para medición interna no viola (a) ni (b).
Fundamento cuantitativo registrado: proyección honesta de EV apostando
con B-limpia contra el mercado = NEGATIVO (~-5% a -10% por apuesta;
mercado ~0.59-0.61 LL vs B-limpia 0.63138 + vig ~4.5%). La prohibición
no es solo prudencia: es aritmética.

D-ODDS-3. Métrica CLV adoptada como diagnóstico: predicción publicada
(snapshot publish) vs proxy de cierre (último snapshot anterior al
tipoff). Limitación aceptada: snapshots fijos subestiman movimiento de
última hora. Los cortes publish/late replican deliberadamente los del
backfill de injury reports (comparabilidad histórica).

D-ODDS-4. Pre-registros:
- Expectativa: CLV agregado de B-limpia indistinguible de cero.
- Única hipótesis estructural de CLV positivo: partidos con injury
  report ambiguo (experimento P(juega|Questionable), en cola).
- Toda decisión de producto basada en CLV se toma tras el cierre de la
  ventana de validación, no antes. Puerta de producto con dos salidas:
  CLV demostrado -> producto para apostadores con afirmación verificable
  ("batimos la línea de cierre", jamás "ganarás dinero"); CLV cero ->
  el producto de picks no existe y el canal vive de las otras propuestas
  (honestidad auditable, contexto, educación).
- The Odds API tier gratuito asumido suficiente (>=93 llamadas/mes);
  verificación en Paso 0.

D-ODDS-5. Los candidatos de mejora (P(juega|Questionable), continuidad
de roster, bottom-up) serán MODELOS NUEVOS en modo sombra vía
model_version en predictions_log. B-limpia intacta como artefacto y único
modelo publicado durante la ventana. Test de equivalencia (rtol=1e-9)
sigue siendo guardián de cualquier cambio en pipeline compartido.

D-ODDS-6. Alcance de partidos (decisión del 2026-09-04, chat de diseño):
B-limpia predice y publica SOLO temporada regular. Pretemporada excluida
permanentemente de predicción (proceso generador distinto: rotaciones
experimentales, sin intención de ganar); su único uso futuro es como
insumo de features de continuidad. Playoffs 2027: modo sombra
(predicciones a predictions_log con flag, sin publicar), con expectativa
pre-registrada de Brier peor que temporada regular; si se confirma, la
ruta es recalibración posterior (Platt/isotónica), jamás tocar B-limpia.
market_odds SÍ captura pretemporada (la tabla archiva; el análisis
filtra por tipo de partido).

## D-ODDS — RESULTADO DE IMPLEMENTACION (2026-09-20/21)

BUG FATAL CAZADO EN REVISION DE DISEÑO, PRE-DESPLIEGUE: el matching de
game_id leia la tabla `games`, que solo contiene partidos JUGADOS
(Decision 1 de Fase 5a; verificado: cero filas con fecha >= hoy y 2026-27
inexistente). El indice habria salido vacio -> todos los eventos sin
match -> market_odds vacia para siempre con exit 0 y logs de aspecto
sano. QUINTA instancia del patron "logica jamas ejercida en offseason que
revienta en silencio el primer dia real" (capa 4, gemelo de la capa 4,
era-por-temporada del spike, sondas de era del backfill, y esta). La cazo
la revision de diseño en el chat, NO un test: los 23 unit tests pasaban
en verde sobre el bug, porque el test del partido nocturno construia su
indice a mano y probaba la aritmetica de fechas, no la procedencia del
dato (misma leccion que los fixtures sinteticos de 13e-1 y de la capa 4).
FIX: el indice sale de fetch_future_schedule (CDN dual-URL, el mismo
camino del endpoint); game_id canonico por construccion. Guarda
anti-silencio con dos casos distinguidos: calendario CDN vacio con API
con partidos = excepcion ruidosa (fuente rota); ventana vacia con
calendario lleno = log informativo (no hay partidos, legitimo).

ENMIENDA DE ESQUEMA (adjudicada por Antonio): las filas sin match se
ESCRIBEN, no se descartan (filosofia RAW: la odd capturada es evidencia
irrecuperable; la interpretacion se arregla despues). Columnas nuevas
api_event_id (NOT NULL, identidad nativa del dato) y matched (BOOL);
game_id anulable (canonico si matched). CLAVE DE IDEMPOTENCIA ENMENDADA:
(game_date, api_event_id, bookmaker, snapshot_label) — game_id anulable
no puede ser clave (dos sin-match del mismo dia colisionarian en NULL).
game_date de filas sin match se deriva de commence_time con la aritmetica
ET del matching, jamas de la fecha de captura. CONSECUENCIA DELIBERADA:
la pretemporada (prefijo 001) entra como matched=false — D-ODDS-6 se
cumple sin tercer lector de scheduleLeagueV2 (opcion b descartada por el
backlog del doble parser; opcion c descartada por perdida de dato).

DESVIACIONES DE LA SPEC ORIGINAL, DECLARADAS Y ACEPTADAS:
(1) MERGE sin tabla staging: bigquery.tables.create es permiso de
DATASET, incompatible con dataEditor a nivel TABLA. Se conservo el
privilegio minimo; el MERGE toma su fuente de ARRAY<STRUCT> como
parametro de consulta (misma garantia de idempotencia en la escritura,
sin interpolacion SQL). La Decision 3 de Fase 5b queda ENMENDADA para
SAs de tabla unica, no violada.
(2) Permiso adicional: dataViewer a nivel tabla sobre teams para
normalizacion de nombres (games ya no se lee: el indice viene del CDN).

THE ODDS API (Paso 0 verificado): tier Starter 500 creditos/mes; coste
= markets x regions; h2h x 1 region = 1 credito/llamada; bookmakers
explicitos (draftkings,fanduel,betmgm) sin coste extra (grupos de 10 =
1 region). Consumo proyectado 93/mes, margen 5.4x. RESTRICCION CONOCIDA:
odds HISTORICAS x10 creditos — backfill de odds seria de pago; razon
adicional para capturar en vivo desde ya. Formato americano pedido a la
API; decimal derivado por conversion exacta (no segunda llamada).

PRE-REGISTRO DE VERIFICACION DIFERIDA: primer dia en que The Odds API
liste partidos NBA (pretemporada ~1-oct): filas en market_odds con
matched=false y game_date correcto; tres snapshots del dia presentes.
Primer dia de temporada regular (21-oct): filas matched=true con game_id
del CDN; un mar de WARNINGs de matching ESE dia seria hallazgo a
adjudicar, no ruido. Consumo real de creditos contra el proyectado.

## D-ODDS — ADDENDUM POST-CIERRE (2026-09-21): dos bugs destapados por la
ejecucion real, ya corregidos

(1) CAPA 5 REINCIDENTE: .to_dataframe() exige
bigquery.readsessions.create (documentado en CLAUDE.md desde 2026-08-25)
y aun asi se reintrodujo. FIX por privilegio minimo: iterar filas del
query result (jobUser lo cubre) en vez de conceder el rol de proyecto
para leer 30 equipos. LECCION: una regla documentada no se auto-aplica;
readsessions entra al checklist mental de todo codigo nuevo que lea
BigQuery.

(2) VENTANA DE MATCHING ESTRECHA, FALLO SILENCIOSO REAL: el indice
limitaba a +-2 dias de hoy; The Odds API lista partidos con semanas de
antelacion. Primera corrida real: 41/41 eventos SIN match, archivados
como matched=false con exit 0, corrompiendo la columna de la que
depende el analisis de CLV. SEXTA instancia del patron de silencio, y
la primera que llego a escribir datos mal etiquetados en produccion.
La cazo la inspeccion de la corrida real, no los tests. FIX: sin tope
superior, el indice lleva los 1206 futuros del CDN. Las filas mal
etiquetadas se ACTUALIZARON in situ en la corrida siguiente:
idempotencia por clave (game_date, api_event_id, bookmaker,
snapshot_label) demostrada sobre datos reales. Verificacion posterior:
70/70 matched en los tres labels.

OPERATIVO: --max-retries=1 implica 2 creditos por ejecucion fallida;
fallo sistematico duplicaria el consumo (93 -> 186 de 500, aun con
margen). Los 6 partidos con team_id 0 son los huecos TBD de NBA Cup ya
anticipados, no fallo. Consumo verificado: 1 credito/llamada
(x-requests-last: 1), 494 restantes.

ESTADO: market_odds OPERATIVA Y AUTONOMA desde 2026-09-21, tres
snapshots diarios, 70 filas iniciales matched=true con odds reales de
la primera semana de temporada. El sistema acumula evidencia CLV desde
31 dias antes de la ventana de validacion.

## D-EXP-1: EXPERIMENTO P(juega | estatus) SOBRE CORPUS GEMBOX
(PRE-REGISTRO 2026-09-21)

Entra en ejecucion el experimento pre-registrado en las Decisiones del
feed (2026-08-22). Corpus: SOLO GemBox (2023-24..2025-26, parser
auditado de 13e-1); iTextSharp excluido hasta cerrar D-RES-3. Es
MEDICION pura: cero cambios a modelos, features o pipeline.

Definiciones congeladas: instancia = (fecha, jugador, estatus) con
game_date igual a la fecha objetivo (PDF multi-fecha: filas del dia
siguiente se capturan en su propio dia). "Jugo" primaria = minutes > 0
en player_game_stats; secundaria = fila presente (activado). Corte
primario = publish (condicion de produccion); late = secundario
(maduracion intradia). Exclusiones contadas aparte: partido fuera de
games, jugador sin match del NameIndex (tasa reportada por temporada).

Expectativas: P(juega|Out) < 2% (sanity del instrumento);
Probable 80-95%; Questionable 45-65%; Doubtful 5-20%; ordenamiento
estricto Out < D < Q < P en las tres temporadas por separado; corte
late con menos masa en Questionable y probabilidades mas extremas.
Desviacion = hallazgo a adjudicar, jamas aceptar en silencio.

Los agregados NO son oficiales hasta que Antonio adjudique la muestra
de auditoria (2 fechas, seed 42, listado instancia por instancia
cotejado contra PDF y boxscore; protocolo 13e-1).

USO POSTERIOR (fuera de esta tarea): si las expectativas se sostienen,
los valores medidos alimentan el diseño del candidato sombra
"disponibilidad v2" (ponderar ausencias esperadas en vez de binarias),
como MODELO NUEVO via model_version en predictions_log (D-ODDS-5).
B-limpia intacta.

RESULTADO: pendiente de ejecucion y auditoria.

## D-EXP-1 RESULTADO (2026-09-21): agregados PROVISIONALES + adjudicaciones

INSTRUMENTO SANO: 78 121 instancias, 0 PDFs ilegibles, 0 equipos sin
match, inclusion 95.7-96.4% en las seis celdas temporada x corte.
Exclusiones: 13-15 nombres distintos sin resolver por temporada
(repetidos a lo largo del calendario) y sin_partido 0/47/70. Ajuste
declarado: _normalize_name del parser es para personas ("Philadelphia
76ers" -> "philadelphia ers"); el experimento usa normalizador
alfanumerico propio con alias LAClippers/LALakers, local al script.

TABLA publish (corte de produccion), P(juega) primaria (minutes > 0):
  estatus       2023-24        2024-25        2025-26       pre-registro
  Out           0.003 (7750)   0.004 (8620)   0.001 (8625)  <2%      OK
  Doubtful      0.021 (244)    0.035 (283)    0.013 (390)   5-20%    DESVIACION
  Questionable  0.467 (2130)   0.477 (1996)   0.494 (1788)  45-65%   OK
  Probable      0.909 (619)    0.898 (746)    0.919 (628)   80-95%   OK
  Available     0.722 (485)    0.800 (491)    0.817 (812)   no pre-reg
Ordenamiento estricto Out < D < Q < P: SE CUMPLE en las tres temporadas
por separado. El dato central del experimento: ~50% de los Questionable
juegan, y v1 los cuenta como disponibles al 100%.

ADJUDICACION 1 (Doubtful bajo el rango, las tres temporadas): HALLAZGO,
no fallo. El rango pre-registrado era estimacion de conocimiento publico
y los datos lo contradicen de forma consistente con n sano; el
pre-registro funciono cazando la expectativa mala. Sustancia: Doubtful
opera como eufemismo de Out (~98% no juega). Peso de Doubtful ~ peso de
Out para el candidato sombra.

ADJUDICACION 2 (Available < Probable): la etiqueta mezcla poblaciones,
no es escalon de gravedad. Descompuesto 2023-24: G-League/Two-Way
P(juega)=0.448 (n=67) vs resto 0.766 (n=418). Consecuencia v2: Available
jamas se trata como "mas disponible que Probable"; la descomposicion por
razon entra al diseño del candidato.

ADJUDICACION 3 (corte late INVALIDADO como medicion de maduracion para
partidos del mismo dia): los sufijos late del backfill caen en
09PM-11:15PM ET, con partidos en curso o terminados; el reporte ya dice
quien jugo (P(juega|Out)=0.000 exacto; masa D+Q+P colapsa de 24.8% a
0.2%). Es RETROSPECTIVA, no pronostico. Los numeros late NO se usan como
medicion predictiva. El archivo late del backfill queda intacto (es
registro de lo publicado, no esta corrupto). Variante que SI mediria
maduracion: filas del dia siguiente del mismo PDF; queda nombrada como
D-EXP-1b, con pre-registro propio SI algun dia se necesita. NO ejecutada.

GEMELO EN market_odds (nombrado aqui, verificacion pre-registrada):
CDMX sin DST, ET con DST. En octubre (EDT) los snapshots caen en 15:00 /
19:45 / 23:15 ET: late es post-tipoff para casi todos los partidos y
evening para los tempranos. Consecuencias: (a) el proxy de cierre de
D-ODDS-3 para partidos tempranos sera publish (4 horas antes del
tipoff), mas debil de lo estimado; (b) VERIFICAR en octubre que devuelve
The Odds API para eventos ya iniciados (odds en vivo vs evento ausente);
filas en vivo mezcladas con pre-partido corromperian el CLV. El analisis
de CLV DEBE comparar capture_ts contra el tipoff real del CDN
(gameDateTimeUTC; jamas gameDateTimeEst, cuyo Z es decorativo). Desde
noviembre (EST) evening pasa a 18:45 ET y vuelve a preceder la mayoria
de tipoffs: la severidad varia con el DST ajeno.

USO POSTERIOR confirmado: los pesos medidos (Out ~0, Doubtful ~0.02,
Questionable ~0.48, Probable ~0.91, Available descompuesto por razon)
alimentan el diseño del candidato sombra disponibilidad-v2 (D-ODDS-5).
B-limpia intacta.

ESTADO: agregados PROVISIONALES. Oficiales solo cuando Antonio adjudique
los dos listados de auditoria (auditoria_2023-11-24.txt, 194 instancias;
auditoria_2025-10-27.txt, 174), protocolo 13e-1.

CORRECCION DE BASE DE TESTS: la base vigente es 605 limpia / 609 mixta,
15 deselected (584 post-D-ODDS + 21 del experimento). El 550/554 del
bloque 2026-09-20 quedo obsoleto con los 30 tests de odds_logic + 4 de
Settings ya contados en 584; se conserva como registro historico.

## D-EXP-1 CERRADO (2026-09-21): auditoria humana adjudicada, agregados
OFICIALES

Antonio adjudico los dos listados de auditoria (auditoria_2023-11-24.txt,
194 instancias; auditoria_2025-10-27.txt, 174 instancias) contra PDF y
boxscore: SIN misatribuciones, sin fantasmas, sin perdidos, sin
veredictos contradichos. Los agregados del corte publish pasan de
provisionales a OFICIALES tal como estan tabulados en el bloque
D-EXP-1 RESULTADO, con sus tres adjudicaciones (Doubtful como hallazgo,
Available como mezcla de poblaciones, late invalidado como pronostico
del mismo dia).

PESOS OFICIALES PARA EL CANDIDATO disponibilidad-v2 (promedio simple de
las tres temporadas, redondeo a 2 decimales; la estabilidad entre
temporadas justifica el promedio):
  Out          0.00
  Doubtful     0.02
  Questionable 0.48
  Probable     0.91
  Available    sin peso unico: requiere descomposicion por razon
               (G-League/Two-Way vs resto) en el diseño del candidato
NOTA DE VIGENCIA: pesos medidos sobre 2023-24..2025-26 (corpus GemBox).
Si el corpus iTextSharp se incorpora tras cerrar D-RES-3, los pesos se
RE-MIDEN con el corpus ampliado y ambas versiones quedan registradas;
jamas se mezclan mediciones de parsers no auditados.

DESBLOQUEADO: diseño del candidato sombra disponibilidad-v2 como MODELO
NUEVO (D-ODDS-5): availability_diff ponderada por P(juega|estatus) en
lugar de binaria por Out. B-limpia intacta como unico modelo publicado.
El diseño del candidato lleva su propio pre-registro antes de escribir
codigo.

Los archivos de data/experiment_pjuega/ entran al repositorio como
evidencia con git add -f (mismo criterio que los JSON del spike
D-RES-2), a discrecion de Antonio en el proximo commit.

## D-EXP-2: CANDIDATO disponibilidad-v2 OFFLINE (PRE-REGISTRO 2026-09-21)

Mide si availability_diff ponderada por P(juega|estatus) (pesos
oficiales D-EXP-1: Out 0.00, D 0.02, Q 0.48, P 0.91, Available
descompuesto 0.45/0.77) mejora el log loss frente a la binaria v1.
Experimento OFFLINE: nada se despliega, B-limpia intacta,
features_v1.parquet intacto.

Diseño congelado: feature v2 computada desde el PDF publish REAL de
cada fecha (backfill GemBox 2023-24..2025-26, parser auditado; corte
anterior a todo tip-off = cero leakage por construcción; iTextSharp
prohibido hasta D-RES-3). Folds restringidos (A: entrena 23-24, valida
24-25; B: entrena 23-25, valida 25-26). CONTROL = B-limpia RE-ENTRENADA
en los mismos folds; el 0.63138 oficial no es vara comparable. Candidato
idéntico salvo la feature. NYS a fecha objetivo = v1 sin ajuste para ese
equipo, con contador (réplica de 13e-2.5). Sin match = 1.0 con contador.

Expectativas: ganancia 0.000-0.008 LL; escenario nulo plausible y
legítimo (el rolling ya absorbe disponibilidad); ganancia > 0.015 =
auditar leakage antes que celebrar; coeficiente v2 con signo + como el
de v1, inversión = hallazgo. Desviación = adjudicar, jamás aceptar en
silencio.

Puerta de decisión pre-registrada: ganancia positiva en AMBOS folds y
fuera del ruido -> disponibilidad-v2 se promueve a candidato sombra
(modelo nuevo vía model_version en predictions_log, D-ODDS-5), con
tarea propia de diseño de despliegue. Ganancia nula o negativa -> el
hallazgo se registra, v1 sigue, y los pesos quedan disponibles para la
capa editorial (el mensaje puede DECIR "Questionable: juega ~50% de las
veces" sin que el modelo lo use: honestidad gratis).

RESULTADO: pendiente de ejecución.

## D-EXP-2 RESULTADO (2026-09-21): NULO DE RESOLUCION, puerta NO cruzada,
v1 sigue

TABLA (log loss, diferencia pareada candidato vs control):
  fold  n     control(v1)  candidato(v2)  ganancia   t      IC95
  A     989   0.60554      0.60426        +0.00127   0.37   [-0.0054,+0.0080]
  B     993   0.61625      0.61547        +0.00079   0.18   [-0.0077,+0.0092]
Candidato gana 514/989 y 521/993 partidos: indistinguible de una moneda.
Coeficiente con signo + en los tres brazos, sin inversion. Ganancia muy
por debajo de 0.015: auditoria de leakage no activada. Instrumento sano:
paridad de universo por assert (2980 filas, 0 NaN), 3.3% sin match de
nombre, 0 fechas sin PDF, 421 equipo-partido NYS.

ADJUDICACION DE LA PUERTA: NO se cruza (positiva en ambos folds, pero
t=0.37 y t=0.18, dentro del ruido con holgura). Por la regla
pre-registrada: el hallazgo se registra, v1 sigue como feature de
produccion, disponibilidad-v2 NO se promueve a candidato sombra. La
carga de la prueba esta en el candidato, sin importar la razon por la
que no pudo demostrarse (misma asimetria que protege al suscriptor en
el criterio de comercializacion).

HALLAZGO DE DISEÑO, ERROR PROPIO REGISTRADO: el MDE de los folds es
0.0096 (A) y 0.0121 (B) a 80% de potencia; el rango pre-registrado
(0.000-0.008) cae ENTERO por debajo. El experimento no podia distinguir
su propia expectativa de cero: el null es de RESOLUCION, no de
existencia ("no se distingue con este diseño", jamas "no existe"). El
error es del pre-registro (redactado sin computar potencia), no de la
ejecucion, que lo detecto y declaro sin resolverlo por cuenta propia.
REGLA NUEVA: todo pre-registro futuro con metrica continua incluye su
MDE junto a la expectativa; si el MDE no cubre el rango esperado, el
diseño se corrige o la limitacion se declara de antemano. Detectar
0.001-0.008 aqui exigiria ~90k-145k partidos de validacion, que el
corpus GemBox no tiene ni tendra.

CONSECUENCIA SOBRE LA COLA: cerrar D-RES-3 (parser iTextSharp) adquiere
valor CUANTIFICADO: el corpus 2018-19..2022-23 añadiria hasta cinco
folds y bajaria el MDE a la vecindad del rango esperado. La re-medicion
con corpus ampliado (ya prevista en D-EXP-1) es la unica via realista
de resolver esta pregunta. Si ocurre, entra con pre-registro nuevo que
incluya: MDE computado, la hipotesis del brazo diagnostico formalizada,
y el contraste limpio como primario (ver abajo).

BRAZO DIAGNOSTICO (no pre-registrado: GENERA hipotesis, no evidencia):
binario Out=0 de misma construccion que v2 gana al control en A
(+0.00154) y pierde en B (-0.00602); v2 empata con el binario en A
(t=-0.13) y le gana en B (t=2.78, IC95 [+0.002,+0.012]). Hipotesis
superviviente, con nombre exacto: "ponderar ESTABILIZA frente al
binario de misma construccion", no "ponderar mejora". Un fold de dos:
señal, no conclusion.

CONFOUND DE CONSTRUCCION (declarado antes de ver numeros): control =
numerador de activados del boxscore; candidato y brazo binario =
numerador de rotacion pronosticada desde el PDF. La comparacion limpia
del efecto de ponderar es v2 vs binario (misma construccion); v2 vs
control lleva el confound dentro. Restriccion de interpretacion de todo
el experimento.

DESTINO DEL NULL (por la puerta pre-registrada): los pesos oficiales de
D-EXP-1 pasan a la CAPA EDITORIAL como candidatos de contenido: el
mensaje puede decir "X figura como Questionable; historicamente ~50% de
los Questionable juegan" sin que el modelo los use. Honestidad gratis,
cero cambios al pipeline. Diseño del mensaje v2 = tarea separada, sin
urgencia, no bloqueante.

Base de tests: 630 limpia / 634 mixta, 15 deselected (605 + 25 del
experimento).

## D-EXP-3: FUNDACIÓN DE JUGADOR, EXPERIMENTO minutos x tasa
(PRE-REGISTRO 2026-09-21)

Pregunta fundacional del sistema de stats por jugador (y del bottom-up
de victoria del Camino 5): ¿la descomposición minutos x tasa le gana al
rolling directo del stat? OFFLINE, cero despliegue, B-limpia intacta.

Diseño: targets PTS/REB/AST/3PM; universo minutes > 0 con >= 10
partidos previos, 2016-17..2025-26, walk-forward oficial; B0 = rolling
10 del stat (shift 1); C1 = rolling minutos x rolling tasa (misma
información, distinta estructura); C2 (solo GemBox) = C1 + señal de
disponibilidad del rival desde PDFs publish con pesos D-EXP-1. MAE
primaria, diferencias pareadas.

REGLA DE D-EXP-2 APLICADA COMO MECANISMO: fase 0 de potencia computa el
MDE por stat ANTES de comparar; stat con MDE relativo > 3% del MAE
baseline = SIN RESOLUCIÓN, descriptivo pero no inferencial; cuatro sin
resolución = detenerse y reportar. El MDE deja de ser promesa y pasa a
ser gate ejecutable.

Expectativas: mejora relativa 0-3% por stat, mayor en PTS que en 3PM;
nulo plausible y legítimo (rolling directo ya mezcla ambos); C2 con
efecto concentrado en ausencias importantes del rival; sanity MAE(PTS)
de B0 en 4.5-6.5. Desviación = adjudicar.

Puerta: C1 fuera del ruido en >= 3 de 4 stats -> se diseña la fundación
de jugador (pre-registro propio); menos -> producto de stats sobre
rolling transparente y bottom-up archivado salvo evidencia nueva. AMBAS
salidas producen producto publicable con honestidad.

RESULTADO: pendiente de fase 0 y ejecución.

## D-EXP-3 RESULTADO (2026-09-22): NEGATIVO CON RESOLUCIÓN, puerta NO
cruzada, rolling directo gana

FASE 0 (gate de potencia, estreno como mecanismo): MDE relativo ~0.1%
en los cuatro stats, treinta veces bajo el umbral del 3%; n=148 484
jugador-partidos de validación (desviación menor declarada: por debajo
del ~150k-200k proyectado). Sanity MAE(PTS) B0 = 4.674, dentro del
rango 4.5-6.5. Orden fase 0 -> resultados verificado por timestamps.

TABLA C1 (minutos x tasa) vs B0 (rolling directo), agregado:
  stat  MAE B0   MAE C1   mejora rel   t       folds ganados (de 6)
  PTS   4.7300   4.7370   -0.15%       -4.48   0
  REB   1.9613   1.9745   -0.67%      -12.74   0
  AST   1.3585   1.3634   -0.36%       -9.59   0
  3PM   0.8930   0.8923   +0.08%       +2.33   6
Los efectos negativos son ~10x el MDE: NEGATIVO CON RESOLUCIÓN, no null
ambiguo. El instrumento podía ver y vio que la descomposición PIERDE.

ADJUDICACIÓN DE LA PUERTA (regla pre-registrada, gana en 1 de 4 contra
el umbral de >=3): el producto de stats por jugador, si se construye,
se diseña sobre ROLLING DIRECTO transparente; el bottom-up de victoria
por agregación de jugadores queda ARCHIVADO salvo evidencia nueva. La
fundación compartida "un modelo paralelo para victoria + stats"
(pregunta que originó D-EXP-3) NO se construye: su motor perdió contra
el baseline con potencia sobrada y en igualdad de información.

DESVIACIONES ADJUDICADAS COMO HALLAZGOS (segundo error consecutivo de
expectativa propia, registrado): (a) rango pre-registrado 0%-3% no
contiene el resultado (tres stats negativos con t entre -4.5 y -12.7);
(b) expectativa direccional INVERTIDA: se esperó mayor ganancia en PTS
que en 3PM y PTS es de los peores mientras 3PM es el único positivo
(económicamente irrelevante: +0.08%). Patrón de tres experimentos: las
expectativas a priori han fallado en dirección optimista hacia la
sofisticación (Doubtful intermedio, ponderar, descomponer). La moraleja
empírica del proyecto se refuerza: los baselines simples de este
dominio son brutalmente fuertes.

INTERPRETACIÓN POST-HOC (no pre-registrada, es hipótesis y no
conclusión): el producto de dos rollings compone el error de estimación
de ambos; el rolling directo ya integra la covarianza minutos-producción
en una sola estimación. La descomposición solo pagaría con información
exógena mejor que el rolling en alguno de sus componentes (rotación
anunciada, cambio de rol), que C1 no tenía.

BRAZO C2 (disponibilidad del rival, con resolución en los 8 contrastes):
nulo o levemente negativo en todo; en 3PM estorba (-0.0011, t=-6.04).
Consistente con lo pre-registrado. Con esto, la señal de los PDFs queda
medida en sus tres usos: no mueve el modelo de equipo (D-EXP-2), no
mueve stats de jugador (C2), y su valor confirmado vive en la capa
editorial (pesos D-EXP-1) y en el gating de quién juega.

CONSECUENCIA DE PRODUCTO: un producto de proyecciones por jugador sigue
siendo viable y publicable con honestidad ejemplar: "promedios móviles
transparentes, con la disponibilidad declarada del injury report". Es
un rolling B0 con capa editorial, barato de construir y auditable
línea por línea. Queda como opción de contenido del canal, sin
pre-registro pendiente (no hay hipótesis que probar: B0 ES el modelo),
gateado solo por decisión de producto tras la ventana.

Base de tests: 649 limpia / 653 mixta, 15 deselected.

## D-EXP-4: COBERTURA DE INTERVALOS EMPÍRICOS POR JUGADOR
(PRE-REGISTRO 2026-09-22)

Insumo del producto "distribución de stats en el mensaje diario"
(adjudicado en D-EXP-3: rolling directo es el modelo; aquí no hay
hipótesis de predicción, hay calibración de intervalos a publicar).
Universo idéntico a D-EXP-3 (148 484, paridad por conteo). Intervalos
candidatos de los últimos 10 jugados (shift 1): [min,max], [p10,p90],
[p25,p75]; centro mediana. Métricas: cobertura empírica, ancho, por
subgrupos titulares/banca y por temporada. Potencia: proporciones a
n~150k, IC < ±0.3pp, sin gate formal (se reporta IC).

Expectativas: [p10,p90] BAJO el 80% nominal (70-80%; cuantiles de
muestra 10 subestiman colas); [min,max] 82-92%; [p25,p75] 45-60%;
banca peor que titulares; estabilidad entre temporadas < 3pp. La
elección del intervalo publicable la adjudica Antonio con la tabla;
las PALABRAS del mensaje se calibran a la cobertura MEDIDA, no a la
etiqueta nominal.

Contexto de calendario registrado: si el producto completo (formato
congelado bajo tests, player_predictions_log, endpoint, deploy,
dry-run vía ?date) no está verificado para el 2026-10-14, la sección
se DIFIERE a post-adjudicación: jamás estrenar secciones del mensaje
a mitad de ventana (superficie del gate operativo).

RESULTADO: pendiente de ejecución.

## D-EXP-4 RESULTADO (2026-09-22): cobertura medida, intervalo
adjudicado, redaccion DESCRIPTIVA como regla

TABLA (n=148 484, IC95 < ±0.26pp por celda; paridad exacta con D-EXP-3
por assert):
  stat  [min,max]  [p10,p90](nom.80%)  [p25,p75](nom.50%)
  PTS   85.6%      70.2%               43.7%
  REB   88.1%      74.4%               46.7%
  AST   90.7%      79.7%               54.0%
  3PM   93.3%      85.0%               64.3%
Titulares vs banca ([p10,p90]): PTS 67.0/71.9, REB 72.6/75.3,
AST 74.7/82.3, 3PM 80.9/87.1. Estabilidad entre temporadas < 3pp en
las 12 celdas (max 2.07pp). Centro: MAE mediana vs media +0.051 PTS,
+0.013 REB, -0.011 AST, -0.027 3PM.

ADJUDICACION CENTRAL (Antonio): el mensaje publica HECHOS DESCRIPTIVOS,
jamas afirmaciones probabilisticas. Intervalo publicable: [min,max] de
los ultimos 10 jugados, redactado como "ultimos 10: mediana X,
rango A-B". Es factual y auditable linea por linea; no promete
cobertura; su cobertura medida (85.6% en PTS global) es PROPIEDAD
CONOCIDA del producto, reportable en metodologia, no claim del
mensaje. REGLA DERIVADA para todo formato futuro: ninguna palabra del
mensaje implica probabilidad de cobertura ("rango del 80%", "rango
habitual") salvo que la cobertura MEDIDA del subgrupo relevante la
respalde. [p10,p90] queda como metrica interna; [p25,p75] descartado.
Centro: MEDIANA (costo despreciable, +0.051 sobre MAE 4.7 en el peor
stat; compra robustez y legibilidad).

DESVIACION 1 ADJUDICADA COMO HALLAZGO: banca cubre MAS que titulares
en los 12 contrastes (expectativa pre-registrada invertida; cuarta
expectativa propia consecutiva fallida, esta vez por medir volatilidad
cuando la pregunta era cobertura). Hipotesis post-hoc registrada como
hipotesis, no conclusion: discretizacion (soporte corto de la banca
hace que casi cualquier intervalo lo contenga). CONSECUENCIA DE
PRODUCTO: los destacados del mensaje son titulares, el subgrupo con
PEOR cobertura (PTS 67.0%); la redaccion descriptiva no es preferencia
estetica sino la unica opcion que no exagera.

DESVIACION 2 ADJUDICADA COMO HALLAZGO: 3PM fuera de los tres rangos
por ARRIBA (93.3/85.0/64.3), unico stat cuyo [p10,p90] supera su
nominal. Mismo mecanismo sospechado. Consecuencia: en stats de soporte
corto el rango es poco informativo; el diseño del formato (Tarea B)
decide si 3PM muestra rango, solo mediana, u se omite.

CUMPLIDO DEL PRE-REGISTRO: [p10,p90] bajo el nominal en PTS/REB/AST
(mecanica de interpolacion de cuantiles con n=10, fijada en test);
estabilidad entre temporadas.

DESBLOQUEADO: Tarea B (diseño del formato de la seccion de jugadores
en format_daily_message, bajo tests que congelan formato exacto,
13e-2.1) con estas reglas como contrato: descriptivo, mediana +
[min,max], top por minutos rolling, integracion con injury report
(Out no aparece; Questionable con su marca editorial de D-EXP-1; NYS
hereda 13e-2.5). Fecha de corte VIGENTE: producto completo verificado
con dry-run al 2026-10-14 o se difiere a post-adjudicacion.

Base de tests: 667 limpia / 671 mixta, 15 deselected (649 + 18 de
D-EXP-4).

## D-PROD-1 (Tarea B) CERRADA: sección de jugadores en el mensaje,
formato congelado (2026-09-22)

Contrato implementado bajo 13e-2.1: GamePrediction gana players
(list[PlayerHighlight], default []) y players_data_available; con
defaults, el mensaje vigente se reproduce byte a byte (verificado
contra golden). Formato por jugador: "• {nombre}: {pts} pts
({min}-{max}) · {reb} reb · {ast} ast"; Q con sufijo y UNA línea
editorial por partido con el peso de D-EXP-1 (~50%); especialistas
con 3P solo mediana (Desviación 2 de D-EXP-4); ventana < 10
declarada con "(últimos {n})". Selección contratada: top 2 por
equipo por minutos rolling; Out y Doubtful excluidos (D-EXP-1:
Doubtful juega ~2%), Probable sin marca. Degradación declarada
heredada de 13e-2.5: sin datos => sección ausente + flag, jamás
vacía silenciosa. Presupuesto de longitud bajo test: 15 partidos x 4
destacados < 4096 UTF-8 con margen >= 15%. Cableado de datos, log de
evidencia (player_predictions_log) y despliegue: Tarea C, bajo la
fecha de corte vigente (2026-10-14 o se difiere).

RESULTADO (2026-09-22): implementado y congelado. Suite 690 passed,
15 deselected, 1 xfailed (667 de base + 23 nuevos en verde; el xfail
ES el presupuesto, ver abajo). Los 42 tests de formato preexistentes
pasan SIN una sola edición — la regla de detenerse no se activó.
Verificado byte a byte contra golden en las tres variantes que deben
reproducir el mensaje vigente: players=[], players_data_available
False con lista cargada, y flag True con lista vacía.

HALLAZGO QUE CONTRADICE EL PRESUPUESTO DEL PÁRRAFO ANTERIOR — EL
FORMATO ADJUDICADO NO CABE: medido, no estimado. 15 partidos x 4
destacados = 6 225 caracteres, contra un límite DURO de Telegram de
4 096 y un presupuesto con margen 15% de 3 481. Exceso: 2 129 sobre
el límite duro, 2 744 sobre el presupuesto. La sección cuesta 316
caracteres por partido (mensaje vigente de día lleno: 1 485). Con
este formato caben 9 partidos bajo el límite duro y 8 con margen,
y un día típico de temporada regular trae 10-13. NO se resolvió
recortando (prohibición explícita del encargo): queda registrado en
test_presupuesto_de_longitud_dia_lleno con xfail(strict=True) —
si alguien adelgaza el formato y el test empieza a pasar, se pone
ROJO y obliga a actualizar este registro, así que el fallo no puede
pudrirse. Los números 9 y 8 quedan fijados en
test_cuantos_partidos_caben_con_la_seccion para que la cifra de este
documento no envejezca en silencio. ADJUDICACIÓN PENDIENTE de
Antonio; opciones sin recomendar: menos destacados por partido,
línea más corta, sección solo en partidos seleccionados, o varios
mensajes de Telegram. Tarea C queda BLOQUEADA por esta decisión: no
tiene sentido cablear datos a un formato que no se puede publicar.

DECISIONES DE FORMATO DECLARADAS (el encargo no las fijaba):
(a) REDONDEO MEDIO-ARRIBA, no round() de Python. La mediana de 10
partidos es el promedio de los dos centrales, así que los .5 exactos
son el caso COMÚN, no el raro; round() redondea al par y produciría
10.5 -> 10 junto a 11.5 -> 12 en el mismo mensaje, sin explicación
posible para el lector. Helper _round_half_up con test parametrizado.
(b) ORDEN locales primero, visitantes después, estable dentro de cada
equipo — el mismo orden que ya siguen las líneas de bajas.
(c) AMBIGÜEDAD NO RESUELTA, reportada sin inventar: la línea del
jugador no lleva tricode, así que con 4 destacados el lector no sabe
de qué equipo es cada uno. El campo team existe en PlayerHighlight;
hacerlo visible es cambio de formato y por tanto adjudicación de
Antonio, no sustitución silenciosa.

## D-PROD-1b: ADJUDICACIÓN DEL PRESUPUESTO Y FORMATO REVISADO
(2026-09-22)

ERROR PROPIO REGISTRADO (quinto de la serie, el más evitable): el
diseño de D-PROD-1 afirmó "el peor caso cabe con margen" sin
computarlo; 316 chars/partido x 15 ya rebasaba el límite duro antes
de escribir código. El test numérico obligatorio del propio encargo
cazó la afirmación. REGLA NUEVA: todo presupuesto afirmado en un
diseño se COMPUTA en el diseño (servilleta basta), jamás se descubre
en el test.

ADJUDICACIÓN (Antonio): la sección de destacados se muda a un
SEGUNDO mensaje de Telegram. Aritmética que fuerza la decisión: a
~72-80 chars por línea de jugador, 4096 admite ~50 líneas; 4
destacados/partido no caben en NINGÚN mensaje único (dedicado
incluido: 15x316 ~ 4700); 2/partido en el mensaje actual dejaría 61
chars de margen (1.5%, inaceptable). Diseño adjudicado: mensaje 2
dedicado, top 1 POR EQUIPO (2/partido), tricode visible en cada
línea (resuelve la ambigüedad declarada en D-PROD-1), leyenda Q una
vez al pie, partido sin datos omitido del mensaje 2 (el flag viaja
en data), cero datos => players_message vacío y el segundo Send no
dispara. Peor caso computado ~2700 chars, margen ~37%. El mensaje 1
(predicciones) queda INTACTO byte a byte: lo validado no se toca.
Redondeo medio-arriba y orden local-visitante: aprobados.

FRONTERA n8n (Decisión 10 intacta): players_message es contrato del
endpoint; n8n gana IF-vacío + segundo nodo Send Text Message
(enrutamiento de transporte, no lógica). Toda republicación del
workflow lleva disparo de prueba (regla operativa vigente). Cableado
de datos, player_predictions_log, n8n y deploy: Tarea C, fecha de
corte 2026-10-14 vigente.

Los tests del presupuesto viejo (xfail estricto) se actualizaron al
diseño nuevo como estaba previsto por su propio mecanismo: el rojo
del strict al adelgazar el formato ES la señal diseñada.

RESULTADO: suite 696 passed, 15 deselected, CERO xfail (667 de base
+ 29 del mensaje 2; el xfail estricto del presupuesto viejo
desaparecio al sustituirse, como exigia la verificacion de cierre);
presupuesto peor caso medido 2518 chars (38.5% de margen sobre el
limite duro de 4096, 27.7% sobre el presupuesto del 85%), con 15
partidos, nombres largos reales, Q en TODOS y especialista en TODOS
— todo lo que alarga la linea a la vez; goldens del mensaje 1
intactos sin edición (42 tests de formato preexistentes en verde sin
tocarlos, y un test nuevo que fija el invariante del diseño: el
mensaje 1 no cambia NI SIQUIERA con destacados cargados).

DETALLE DE IMPLEMENTACION: format_players_message() es funcion pura
hermana de format_daily_message() en api/daily_predictions.py;
format_daily_message dejo de llamar a la seccion de jugadores, de
modo que el mensaje 1 es independiente de los datos de jugador por
construccion y no por configuracion. _tip_sort_key se promovio a
nivel de modulo: los dos mensajes ordenan los partidos igual y el
lector los ve en el mismo orden en ambos. server.py devuelve
{message, players_message, data}; message y data sin cambios.

## D-PROD-1c (2026-09-24): destacados CABLEADOS y DESPLEGADOS, NO cerrada
— dos bugs latentes destapados por el dry-run

DESVIACION DEL ENCARGO, DECLARADA: el bloque previsto decia "CERRADA" y
daba por verificado el paso de n8n. Ninguna de las dos cosas es cierta,
asi que no se escribieron: n8n es trabajo de UI que Code no puede
ejecutar, y ademas el cableado esta BLOQUEADO por el bug 2 de abajo.
Rellenar el placeholder de "uno o dos mensajes verificados" habria sido
inventar una verificacion que no ocurrio.

CABLEADO (hecho y verificado): seleccion top 1 por equipo por minutos
rolling (ventana 10 sobre partidos JUGADOS, historico cargado UNA vez
por dia con los mismos metodos del DataStore que usa live_lookup — cero
lectores nuevos); elegibilidad desde el MISMO snapshot publish que
sustenta el mensaje 1 (_fetch_absences devuelve ahora tambien los
estatus: un segundo descubrimiento podria discrepar y el canal
publicaria dos verdades del mismo PDF); Out y Doubtful excluidos
(D-EXP-1: P(juega|Doubtful) ~ 0.02), Q marcado, Probable sin marca;
minimo 3 jugados, el puesto pasa al siguiente por minutos; sufijo
"(ultimos n)" para 3..9. Best-effort en DOS niveles: fallo global deja
el dia sin destacados, fallo de un partido no contamina a los demas; el
mensaje 1 se sirve completo en ambos casos.

DECISIONES DECLARADAS que el encargo no fijaba: (a) umbral de
especialista de triples = mediana >= 2.0 (D-PROD-1b congelo que el
especialista muestra 3P y solo la mediana, no el umbral); (b) el log
guarda el FLOAT observado, no el entero publicado — el entero se
recupera aplicando el redondeo documentado, al reves se pierde
informacion para siempre.

EVIDENCIA: tabla player_predictions_log creada (PARTITION BY game_date,
16 campos verificados con bq show) + predictions-api-sa dataEditor A
NIVEL TABLA (unica binding de la politica). Escritor: el endpoint,
best-effort con WARNING, espejo exacto de predictions_log. Los dos logs
comparten UN SOLO sello de tiempo por servida — es lo que permite
cruzarlos como el mismo hecho. Sin resultado del partido: grading por
JOIN, jamas update.

DEPLOY: v8 (revision 00008-2bw) y, tras el fix del bug 1, v9 (revision
00009-4k2, imagen sha256:bc8ee515... para v8; v9 construida sobre el
mismo cloudbuild.api.yaml con _VERSION=v9). Suite: 742 passed, 15
deselected (696 de base + 26 de seleccion + 19 del log + 1 guarda de
CREATE_NEVER). Goldens del mensaje 1 intactos sin edicion.

DRY-RUN ?date=2026-10-21 (v9): 200, 11 partidos, 11 con destacados, 22
destacados en total, players_message de 1 319 caracteres bien formado
(bloques por partido, tricode por jugador, especialistas con 3P,
"(ultimos 6)" en un jugador con ventana corta, leyenda Q ausente porque
feed_down=True ese dia y no hay estatus). Filas escritas: 22 en
player_predictions_log y 11 en predictions_log, ambas con
served_by=predictions-api-00009-4k2 y model_version con su SHA.

BUG 1 — predictions_log NUNCA HABIA ESCRITO NADA EN PRODUCCION
(encontrado por el dry-run, ya corregido). Sintoma: 403 "Permission
bigquery.tables.create denied on dataset". Causa:
create_disposition=CREATE_IF_NEEDED obliga al load job a pedir
bigquery.tables.create, que es permiso de DATASET e incompatible con el
dataEditor A NIVEL TABLA con el que corre predictions-api-sa por
decision deliberada de minimo privilegio (2026-08-26). OCTAVA instancia
del patron de silencio, y la mas cara de todas por lo que tocaba: desde
el 2026-08-28 todas las ejecuciones cayeron en dia de descanso, donde
CERO FILAS ES EL RESULTADO CORRECTO — el 403 no podia ocurrir porque
nunca habia filas que escribir, y la verificacion del 2026-08-27
("cero filas en predictions_log por diseño") lo confirmo como sano. El
21 de octubre habria sido el primer dia con filas y LA EVIDENCIA DEL
CRITERIO DE COMERCIALIZACION se habria perdido en silencio ese dia; el
gate operativo lo habria cazado al dia siguiente, pero el dia uno ya no
se recupera. FIX: CREATE_NEVER en los dos escritores de evidencia — no
solo por permisos, tambien por semantica: la tabla la provisiona el
operador, y un escritor que puede crear su propia tabla puede inventarse
un schema distinto en silencio. Guarda de regresion ACOTADA a los dos
metodos por inspect.getsource (_save_tabular conserva CREATE_IF_NEEDED
legitimamente: el ingest job si tiene permiso de dataset y crea sus
tablas staging). Verificado tras el fix: 11 filas reales en
predictions_log, las primeras de su historia.

BUG 2 — player_map NUNCA SE PASA: NINGUNA AUSENCIA HACE MATCH JAMAS
(encontrado por el dry-run, NO corregido — requiere adjudicacion).
server.py llama a build_daily_predictions sin player_map, asi que queda
{}. Verificado: NameIndex.from_player_map({}) no resuelve ningun nombre,
luego absences_by_tid sale SIEMPRE vacio. Consecuencias en el primer dia
real: el modelo predice sin ausencias (availability_diff como si todos
estuvieran sanos), el mensaje dice "Bajas X: –" todos los dias, y
absences_applied va vacio en la evidencia. Lo hizo visible el mensaje 2,
donde los destacados salen como "#203506" en vez del nombre. POR QUE NO
SE ARREGLO AQUI: el unico origen de nombres del sistema son los JSON de
raw/ — player_game_stats no tiene columna de nombre y el DataStore no
expone ninguno; en Cloud Run data/raw/ no existe (.dockerignore). Las
salidas (leer miles de JSON de GCS por request, cachear un artefacto que
escriba el ingest job, o añadir una tabla players) tocan ingesta o
arquitectura, prohibidas en este encargo. ADJUDICACION PENDIENTE.

ESTADO Y BLOQUEO: n8n NO cableado, y conviene que siga asi hasta
resolver el bug 2 — con los nombres sin resolver el mensaje 2
publicaria "#203506", que es exactamente una PUBLICACION CORRUPTA de las
que el gate operativo castiga (13e-2.4, condicion b). El mensaje 1 sigue
publicandose solo, intacto, como desde agosto: players_message viaja en
la respuesta pero nadie lo transporta todavia.

PENDIENTES para cerrar D-PROD-1c: (1) adjudicar el origen de nombres de
jugador (bug 2); (2) cablear n8n (IF sobre players_message != "" +
segundo Send Text Message, mismas convenciones: toggle Expression
verificado con preview resuelto, parse texto plano, attribution OFF,
Response Format JSON — la herencia del spike ya mordio una vez); (3)
republicar con disparo de prueba ANTES de dejarlo al cron y verificar
DOS mensajes en el canal. Fecha de corte 2026-10-14 VIGENTE.

PRE-REGISTRO pretemporada: los dias 001 NO generan destacados (el
mensaje 1 ya los excluye por prefijo; players_message vacio, un solo
mensaje en el canal) — el primer dia con partidos 001 lo verifica
gratis. Primer dia real (2026-10-21): DOS mensajes esperados, filas en
ambos logs, model_version poblado, y nombres de jugador REALES — si
siguen saliendo "#id", el bug 2 no se resolvio.

MORALEJA REFORZADA: el dry-run de un dia futuro, hecho 27 dias antes,
encontro dos bugs que ninguna de las 742 pruebas podia ver porque
ninguno vive en el codigo — uno vive en los PERMISOS y el otro en un
argumento que nadie pasa. El despliegue temprano vuelve a pagar, y esta
vez salvo la evidencia del criterio.

## D-PROD-1d (2026-09-24): tabla players, bug 2 CERRADO, v10 desplegada

Adjudicación del bug 2 (Antonio): tabla players en BigQuery, simétrica
con teams, MERGE por player_id, mantenida por el ingest job (paso 1,
best-effort) y consumida por el endpoint vía método nuevo del DataStore
(load_player_names, ambos adapters). Backfill único desde los JSON
crudos de GCS: 1 660 jugadores distintos, 0 blobs ilegibles; muestra
cotejada (708 Kevin Garnett, 977 Kobe Bryant, 1495 Tim Duncan, 1713
Vince Carter, 1717 Dirk Nowitzki). ACLs a nivel tabla verificadas
(predictions-api-sa dataViewer sobre players; ingest-job-sa ya tenía
dataEditor de dataset). REGLA ADJUDICADA de degradación: player_map
vacío = feed caído declarado (13e-2.5 caso 2), con test; el estado
"nadie lesionado" de aspecto sano que el bug demostró posible queda
PROHIBIDO por guarda. Deploy v10 (revisión 00010-pwp). Dry-run
2026-10-21: nombres reales, cero "#", 22 filas jugador + 11 partido.
Suite: 761 passed, 15 deselected (742 + 19 del catálogo). PENDIENTE
ÚNICO para cerrar D-PROD-1c: n8n (UI de Antonio, checklist entregado)
+ disparo de prueba con DOS mensajes.
PRE-REGISTRO primer PDF de pretemporada: ausencias con match real,
líneas de Bajas pobladas cuando haya Outs; "Bajas: -" perpetuo con
PDFs vivos sería el bug 2 renacido.

DETALLE DE IMPLEMENTACION Y HALLAZGOS:

FUENTE UNICA DE NOMBRES: se extrajeron player_names_from_legacy_payload
y player_names_from_cdn_payload de los loaders de directorio de
injury_report, y ahora los loaders las usan por dentro. Backfill, ingest
job y adapter local consumen LAS MISMAS funciones por import. Una
segunda implementacion habria sido una segunda verdad del nombre, que
es justo lo que hizo caro el bug 2.

MANTENIMIENTO SIN COSTE: el job engancha el catalogo al bucle de
boxscores que YA descarga los payloads — cero peticiones extra.
Best-effort con WARNING: la mision critica del job es la ingesta
(misma regla que el archivo del injury report, Decision 4 del feed).

CACHE DEL ENDPOINT: player_map se lee una vez por proceso y por FECHA
(no por TTL: la clave es el dia para que la recarga caiga siempre del
lado correcto de la corrida del job de las 12:00 UTC). Solo se cachea
el mapa POBLADO — cachear un {} condenaria al proceso a publicar
degradado el resto del dia aunque la tabla se recuperase.

CON CATALOGO VACIO NI SIQUIERA SE DESCARGA EL PDF: gastar 20 peticiones
HEAD para no poder resolver un solo nombre no tiene sentido. La rama
comparte el camino EXACTO del feed caido via una excepcion centinela
interna, en vez de duplicar su logica y arriesgarse a que diverjan.

GUARDA DEL BUG, EN EL CALL SITE: el bug no vivia en ninguna funcion —
vivia en un argumento que server.py nunca pasaba, invisible para los
tests y para el type checker. La guarda inspecciona
server.predictions_today y falla si "player_map=" desaparece.

HALLAZGO DE IMPLEMENTACION (MERGE con ARRAY<STRUCT>): el primer intento
del backfill leyo los 14 400 blobs correctamente y murio en el MERGE
final con 400 "STRUCT<player_id INT64, player_name STRING> is not a
valid value". El tipo de un ARRAY<STRUCT> parametrizado NO se pasa como
cadena: exige StructQueryParameterType + StructQueryParameter. D-ODDS ya
lo habia resuelto en el MERGE de market_odds; se copio su patron en vez
de improvisar otro. Leccion repetida del proyecto: el precedente
resuelto se busca ANTES de inventar.

EVIDENCIA DEL BUG Y SU FIX, LADO A LADO EN LA PROPIA TABLA:
player_predictions_log guarda 22 filas de la revision 00009-4k2 con
22/22 nombres sin resolver y 22 filas de 00010-pwp con 0/22 — el
expediente del arreglo quedo en el mismo sitio donde vivia el sintoma.

MATIZ DE LECTURA DEL DRY-RUN (no es bug): los destacados del 2026-10-21
muestran jugadores en equipos donde hoy no estan (Oladipo en ORL,
Westbrook en WAS). Es el lag de roster ya documentado ("roster change
v0: aceptar lag"): el rolling sale de los ultimos partidos JUGADOS, que
son de 2025-26. Se corrige solo con partidos reales de 2026-27.

## D-PROD-1e + 1f (2026-09-24): OOM y seleccion historica, un solo v11

Dos sintomas del mismo dry-run del dia mas pesado (2026-10-21, 11
partidos), cerrados juntos porque el fix de uno es el fix del otro.

NOTA DE REGISTRO: D-PROD-1e no llego a existir como tarea con bloque
propio. Lo que hubo fue la subida MANUAL de memoria a 2Gi (revision
00011-25f, 04:47) para desbloquear, sin fix de codigo. Este bloque la
recoge y la cierra.

SINTOMA 1 — OOM A 1 GiB. Evidencia literal de Cloud Run: "Memory limit
of 1024 MiB exceeded with 1029 MiB used" (2026-09-24 04:41, revision
00010-pwp), precedido de dos "container instance was found to be using
too much memory and was terminated". Solo 5 MiB de exceso. CAUSA:
_attach_highlights cargaba player_game_stats ENTERA (371 253 filas) para
usar el 17% — el historial reciente — encima del modelo y del camino de
features.

SINTOMA 2 — LA SELECCION ELEGIA JUGADORES DE HACE UNA DECADA. El
universo de candidatos por equipo era TODA su historia, sin cota de
recencia, y ganaba quien mas minutos promedio en CUALQUIER tramo de 10
partidos desde 2014. Evidencia del diagnostico de solo lectura, antes
de tocar nada:
  MIA  universo 103 candidatos (2014-10-29 -> 2026-04-12)
       elegido Luol Deng, 34.9 min, ultimo partido 2016-04-13 (3 843 dias)
       vigente mas alto: Bam Adebayo, 34.2 min, 192 dias
  ORL  universo 108 candidatos
       elegido Victor Oladipo, 36.6 min, ultimo 2016-04-08 (3 848 dias)
       vigente mas alto: Paolo Banchero, 33.2 min, 192 dias
  WAS  universo 150 candidatos
       elegido Russell Westbrook, 39.8 min, ultimo 2021-05-16 (1 984 dias)
       vigente mas alto: Will Riley, 32.6 min, 192 dias

RETRACTACION EXPLICITA: al reportar el dry-run de v10 se escribio que
esos nombres eran "el lag de roster ya documentado (roster change v0:
aceptar lag), no un bug". ERA FALSO. El lag documentado es de un verano;
esto era un universo sin cota que llegaba a 2014. El diagnostico de
Antonio fue el correcto y la evidencia de arriba lo confirma punto por
punto. Se registra el error de lectura, no solo el bug.

FIX UNICO, DOS CONSTANTES CON PREGUNTAS DISTINTAS (config.py):
  HIGHLIGHT_RECENCY_DAYS = 250 decide QUIEN es candidato: su ultimo
  partido CON ESE EQUIPO debe caer dentro de la ventana. 250 dias cubren
  el offseason (~190), asi que el primer dia de temporada los candidatos
  siguen siendo los del cierre anterior — ese si es el lag aceptado.
  HIGHLIGHT_HISTORY_SEASONS = 3 decide CUANTO PASADO SE CARGA para que
  los "ultimos 10 jugados" de un candidato vigente esten completos. Se
  lee POR TEMPORADA con load_player_game_stats(season=...): el filtro
  viaja a BigQuery en vez de traerse 371k filas para descartar el 83%.
La recencia es POR EQUIPO, no por liga: un traspasado sigue jugando pero
ya no representa a su equipo anterior (test propio). Equipo sin
candidatos vigentes: se OMITE con WARNING, jamas un jugador rancio con
aspecto sano.

PERFIL ANTES / DESPUES (medido local, mismo dry-run):
  filas leidas       371 253  ->  64 421   (-83%)
  DataFrame final      70.1 MB ->  12.2 MB (-83%)
  pico tracemalloc     79.4 MB ->  37.3 MB (-53%)

VEREDICTO SOBRE 1 GiB: EL FIX BASTA. v11 se desplego DELIBERADAMENTE con
--memory=1Gi (revision 00012-znr) y el dry-run del dia mas pesado
devolvio 200 sin un solo error de memoria en los logs de esa revision.
Los 2Gi de la 00011 quedan como parche superado; leer lo necesario era
el fix, subir el limite era el parche.

DRY-RUN v11 (2026-10-21, a 1 GiB): 200, cero "#", 22 destacados,
CERO RANCIOS — todos con ultimo partido entre 2026-03-10 y 2026-04-12
(192-225 dias). Filas nuevas: 44 en player_predictions_log y 22 en
predictions_log para 00012-znr (dos servidas, consistentes entre ambos
logs). Listado completo de los 22 emitido para ADJUDICACION HUMANA de
Antonio contra su conocimiento del roster (protocolo 13e-1) —
PENDIENTE.

OBSERVACION QUE NO ES BUG Y NECESITA ADJUDICACION: con la recencia
arreglada aparecen junto a estrellas claras (Doncic, Booker, Kawhi,
Banchero, Adebayo) varios nombres de rotacion corta o novatos (Toby
Okani MEM, Cody Williams UTA, Will Riley WAS, Nique Clifford SAC, AJ
Green MIL). Causa probable: los ultimos partidos de abril son de equipos
ya eliminados, donde los titulares descansan y los suplentes juegan 35
minutos, y el criterio "top 1 por minutos de los ultimos 10 jugados" lo
premia. El dato es correcto y reciente; la pregunta es si es el jugador
que el lector espera. Cambiar el criterio (minutos de temporada
completa, puntos, excluir el tramo final) seria cambio de CONTRATO y por
tanto decision de Antonio, no sustitucion silenciosa.

GUARDA CON CONOCIMIENTO EXTERNO: test_luol_deng_no_juega_en_2026 replica
el universo real de MIA (Deng 34.9 min con datos de 2016 contra Adebayo
34.2 de 2026) y exige que gane el reciente. Ningun invariante automatico
podia cazar esto: los numeros de Deng son validos, solo que de hace una
decada. Lo que lo caza es SABER que Deng no juega en Miami — y ese saber
queda codificado. Acompaña test_sin_target_date_el_filtro_no_se_aplica,
que demuestra que el bug era exactamente la ausencia del filtro.

Suite: 768 passed, 15 deselected (761 + 7 de recencia). Goldens del
mensaje 1 intactos. B-limpia y su test de equivalencia sin cambios.

LECCION DOBLE (instancias 11 y 12 del patron de silencio del proyecto):
(1) LOS IDS ESCONDEN LO QUE LOS NOMBRES DELATAN. v9 publico exactamente
la misma seleccion equivocada como "#2736" y nadie la vio; bastaron los
nombres de v10 para que saltara a la vista. Corolario operativo: toda
evidencia destinada a lectura humana se emite en forma legible por
humanos, no en ids, aunque el id sea la clave canonica del JOIN.
(2) EL DRY-RUN DEL DIA MAS PESADO ES OBLIGATORIO PARA TODO CAMINO NUEVO.
El OOM no aparecia en 768 pruebas ni en un dia de descanso: hacia falta
el dia de 11 partidos con el camino completo cargado. Se suma a la
moraleja ya escrita ("el despliegue temprano ES una herramienta de
testing") una precision: el despliegue temprano del PEOR CASO.

## D-PROD-1c CERRADA (2026-09-24): destacados EN PRODUCCION, cadena
completa verificada, criterio de seleccion congelado

ADJUDICACIONES DE ANTONIO QUE CIERRAN EL CAPITULO:
(1) Listado de 22 destacados de v11: ADJUDICADO SANO contra
conocimiento de rosters (protocolo 13e-1, version producto). Cero
rancios, cero misatribuciones.
(2) Criterio de seleccion: OPCION A — se mantiene "top 1 por equipo
por minutos de los ultimos 10 jugados" sin cambios. Razones: es el
unico criterio internamente coherente con las medianas publicadas
(misma ventana elige y describe); la anomalia de suplentes de abril
(Riley, Okani, et al.) es transitoria y se disuelve a las 2-3 semanas
de temporada cuando la ventana rueda; y es la MISMA debilidad de
inicio de temporada que el criterio de comercializacion ya reconoce
(analisis secundario excluye oct-21 a nov-3). El producto y el modelo
comparten la limitacion, declarada en ambos. Umbral de candidato
(>= 3 jugados) sin cambios.

TRANSPORTE n8n VERIFICADO END-TO-END (2026-09-24):
- Workflow publicado con IF sobre players_message ("is not empty",
  Expression verificada con output real) + segundo Send Text Message.
  Rama false DESNUDA por decision: el heartbeat del mensaje 1 ya
  resuelve la ambiguedad del silencio (13e-2.5) y cualquier nodo ahi
  seria contenido en n8n (Decision 10).
- Disparo de prueba post-publicacion via Scheduler: verde, heartbeat
  entregado (rama false), status {}, Executions limpio.
- Rama true verificada en editor con ?date=2026-10-21: DOS mensajes
  en el canal, el segundo con rosters vigentes. Parametro revertido
  antes de publicar.

HALLAZGO DE TRANSPORTE (dos correcciones documentales, la evidencia
mando sobre el registro):
(1) El nodo Telegram de n8n aplica parse_mode Markdown POR DEFECTO
cuando Additional Fields no trae Parse Mode: el error "can't parse
entities... byte offset 712" ocurrio DOS veces con el campo ausente,
y el offset 712 es el primer guion bajo de
"v1_logistic_bclean_2026-09-19". La configuracion registrada en
agosto ("parse mode texto plano, deliberado") describia una opcion
QUE ESTE NODO NO TIENE (el dropdown solo ofrece Markdown Legacy /
MarkdownV2 / HTML). Los heartbeats de un mes entero pasaron porque no
contienen caracteres Markdown; LA PRIMERA PUBLICACION REAL DE LA
TEMPORADA HABRIA FALLADO EL 21 DE OCTUBRE. Lo cazo el disparo de
prueba con contenido realista — la regla operativa de republicacion
pago su deuda mas cara.
(2) FIX ADOPTADO: Parse Mode = HTML explicito en AMBOS nodos Send.
En HTML solo '<', '>' y '&' son especiales y ningun contenido actual
los emite; los guiones bajos del model_version viajan literales
(verificado: mensaje integro en canal, message_id 36). GUARDA
codificada: test_mensajes_seguros_bajo_parse_mode_html afirma que las
funciones de formato jamas emiten esos tres caracteres; si un nombre
futuro los trae, el fix sera escaparlos en formato, decision de ese
momento.

INSTANCIA 13 DEL PATRON DE SILENCIO: fallo latente de transporte,
invisible en 768 tests (viven del lado Python; el default del nodo
vive en n8n) e invisible en 27 dias de heartbeats sanos. La familia
crece: el peor caso realista debe ejercitar TODAS las capas,
transporte incluido, no solo el codigo propio.

ESTADO FINAL DEL SISTEMA (rumbo al 2026-10-01 y a la ventana):
- Ciclo diario autonomo: warmup 12:58 -> publish 13:00 -> OIDC ->
  predictions-api v11 (1 GiB, revision 00012-znr) -> mensaje 1
  (predicciones) + mensaje 2 (destacados, si hay) -> predictions_log
  + player_predictions_log -> canal.
- market_odds: 3 snapshots diarios autonomos desde 2026-09-21.
- Fecha de corte 2026-10-14: CUMPLIDA con 20 dias de holgura.
- Suite: 769 passed, 15 deselected.

PRE-REGISTROS VIGENTES PARA OCTUBRE (consolidados):
- Primer dia de pretemporada (~10-01): mensaje 1 heartbeat (los 001
  quedan fuera por prefijo), players_message vacio, UN mensaje;
  market_odds con filas matched=false de pretemporada; primer PDF de
  la temporada en el archivo diario.
- Primer dia real (10-21): DOS mensajes, nombres reales y vigentes,
  filas en ambos logs con model_version poblado, ausencias con match
  real cuando haya Outs, market_odds matched=true. "Bajas: -"
  perpetuo con PDFs vivos = bug 2 renacido; suplentes de abril en
  destacados las primeras 2 semanas = transitorio ADJUDICADO, no
  hallazgo.

## D-RES-3 CERRADO (2026-09-24): parser legacy ADJUDICADO VERDE por
auditoria humana plena

Antonio cotejo LINEA POR LINEA los dos listados legacy contra sus PDF
fuente, con los documentos abiertos lado a lado — jugadores, equipos,
estatus, razones y bloques NYS incluidos (protocolo 13e-1, la forma
plena):
  2019-01-15_01PM (layout ITEXT_V1, 61 filas + NYS): VERDE, sin
  misatribuciones, sin fantasmas, sin perdidos.
  2021-02-10_01PM (layout ITEXT_V2, 81 filas + NYS): VERDE, idem.
Los defectos de la ronda 1 (bloques NYS absorbidos; corrimiento en
frontera de bloque, caso Robinson->Milwaukee) quedan confirmados como
CORREGIDOS por la ronda 2: la auditoria paso por esas fronteras
especificamente y no encontro reincidencia.

CONSECUENCIAS:
- Los conteos 61/81 pasan de provisionales a OFICIALES.
- injury_report_legacy.py queda AUDITADO para los layouts ITEXT_V1 y
  ITEXT_V2 de su lista blanca. Los PDFs de esos layouts son utilizables
  como fuente de datos.
- SIGUE FUERA: el tramo de variantes intermedias 2019-11-15 ->
  2019-12-31 (~40 fechas), que cae deliberadamente en
  UnknownLayoutError hasta D-RES-3d (encuesta propia + ampliacion de
  lista blanca + su propia auditoria). Ningun PDF de ese tramo entra a
  ningun corpus hasta entonces.
- DESBLOQUEADO: la re-medicion de D-EXP-2 con corpus ampliado
  (2018-19..2022-23 menos el tramo excluido), la unica via
  cuantificada de resolver la pregunta de disponibilidad ponderada
  (MDE al rango necesario). Cuando se emprenda, entra con pre-registro
  nuevo que incluya: MDE computado en fase 0 (regla D-EXP-2), la
  hipotesis del brazo diagnostico formalizada ("ponderar estabiliza
  frente al binario de misma construccion") como contraste primario de
  misma construccion, y el confound de construccion declarado desde el
  diseño.

ORDEN SUGERIDO REGISTRADO (no vinculante): D-RES-3d primero solo si el
tramo excluido importa al corpus (son ~40 fechas de ~740; la
re-medicion puede correr sin ellas y D-RES-3d añadirlas despues).

## D-EXP-5: P(juega | estatus) SOBRE CORPUS iTextSharp (PRE-REGISTRO +
RESULTADO 2026-09-25)

Extension de D-EXP-1 a las cinco temporadas legacy (2018-19..2022-23),
habilitada por el cierre de D-RES-3. Paso PREVIO de la re-medicion de D-EXP-2
con corpus ampliado: antes de re-medir hay que saber si los pesos oficiales de
D-EXP-1 son los MISMOS en la era legacy — usar pesos de un corpus sobre otro
sin comprobarlo seria el vocabulario equivocado que este proyecto ya pago
cinco veces. MEDICION pura: cero cambios a modelos, features, pipeline o
produccion; solo lectura de GCS y BigQuery con CloudDataStore explicito.

DEFINICIONES: IMPORTADAS de D-EXP-1, no copiadas — el script carga
experiment_pjuega.py y reutiliza filter_rows_for_date, classify_instance,
normalize_team, summarize y load_truth tal cual (una reimplementacion seria
una segunda verdad y la comparabilidad de las dos tablas ES el producto).
Diferencias declaradas: parser parse_pdf_any; corte SOLO publish (el late
quedo invalidado como pronostico en D-EXP-1, adjudicacion 3); nombres desde
la tabla players (catalogo canonico de D-PROD-1d) en un indice unico;
medicion NUEVA de P(juega | Available, categoria) con la columna Category,
que solo trae el layout ITEXT_V1.

EXPECTATIVAS PRE-REGISTRADAS: P(juega|Out) < 2% en las cinco temporadas
(sanity del instrumento); ordenamiento estricto Out < D < Q < P por temporada;
estabilidad dentro de +-5pp respecto a GemBox (Out ~0.3%, D ~2%, Q ~48%,
P ~91%) CON DIAGNOSTICO DE INSTRUMENTO OBLIGATORIO antes de atribuir cualquier
divergencia a la NBA; tasa de sin-match de nombre <= 5%. La muestra de
auditoria (2 fechas, una por layout, seed 42) se congelo ANTES de computar:
audit_dates.json sellado 2026-09-25T22:30:55Z (16:30:55 local) contra
results.json 16:45:17 — orden verificado por timestamps.

COBERTURA: 693 de 725 fechas publish parseadas (2018-19: 106, 2019-20: 118,
2020-21: 140, 2021-22: 165, 2022-23: 164), 43 016 instancias, 40 986 incluidas
(95.3%, en linea con el 95.7-96.4% de D-EXP-1). 32 fechas excluidas por
UnknownLayoutError — jamas se forzo un layout sobre ellas. Layouts leidos:
ITEXT_V1 130, ITEXT_V2 563, cero discrepancias entre la era esperada por fecha
y la leida del documento.

TABLA publish, P(juega) primaria (minutes > 0), n entre parentesis:
  estatus       2018-19      2019-20      2020-21      2021-22      2022-23
  Out          0.008(4435)  0.009(5085)  0.004(5315)  0.008(8592)  0.007(7442)
  Doubtful     0.076(144)   0.047(150)   0.049(163)   0.058(173)   0.046(216)
  Questionable 0.483(619)   0.495(731)   0.484(1080)  0.516(1609)  0.479(1723)
  Probable     0.854(316)   0.874(467)   0.865(497)   0.911(427)   0.893(620)
  Available    0.601(173)   0.751(213)   0.838(173)   0.692(260)   0.639(363)

COMPARATIVA legacy (pooleada) vs GemBox, con IC95 de la diferencia:
  estatus       n_leg  P_leg   IC95 legacy     n_gem  P_gem  dif(pp)  IC95 dif
  Out           30869  0.007  [0.006,0.008]    24995  0.003   +0.42  [+0.30,+0.53]
  Doubtful        846  0.054  [0.041,0.072]      917  0.022   +3.26  [+1.46,+5.05]
  Questionable   5762  0.493  [0.480,0.506]     5914  0.479   +1.44  [-0.38,+3.25]
  Probable       2327  0.881  [0.868,0.894]     1993  0.908   -2.68  [-4.50,-0.85]
  Available      1182  0.695  [0.668,0.720]     1788  0.786   -9.18  [-12.42,-5.94]
IC de Wilson para cada proporcion (Wald daria anchura cero en Out, que es
mentira aritmetica) y Wald para la diferencia. Se poolea, no se promedia.

VEREDICTO CONTRA EL PRE-REGISTRO:
(1) Out < 2% en las cinco: SE CUMPLE (0.4%-0.9%).
(2) Ordenamiento estricto Out < D < Q < P: SE CUMPLE en las cinco temporadas
    por separado, sin una sola inversion.
(3) Estabilidad +-5pp en los cuatro estatus pre-registrados: SE CUMPLE
    (Out +0.42, Doubtful +3.26, Questionable +1.44, Probable -2.68). Las
    diferencias de Doubtful y Probable son estadisticamente reales (IC excluye
    cero) pero pequenas y dentro de la tolerancia. Available quedo fuera con
    -9.18pp: ver diagnostico abajo. Available NO tenia valor pre-registrado —
    D-EXP-1 ya lo habia adjudicado como mezcla de poblaciones sin peso unico.
(4) sin-match de nombre <= 5%: SE CUMPLE con holgura (2018-19 0.24%, 2019-20
    2.22%, 2020-21 0.58%, 2021-22 0.31%, 2022-23 0.21%).
CONCLUSION SUSTANTIVA: los pesos oficiales de D-EXP-1 son PORTABLES a la era
legacy. Doubtful sigue operando como eufemismo de Out (5.4% legacy contra 2.2%
GemBox — un poco menos extremo, y justo en el borde inferior del rango 5-20%
que GemBox habia violado); Questionable sigue cerca del 50%, que es el dato
central de todo este hilo; Probable ~0.88-0.91.

DIAGNOSTICO DE LA DIVERGENCIA DE Available (la regla pre-registrada se aplico
antes de contar cualquier historia sobre la liga): NO es cambio de la NBA, es
un CLASIFICADOR QUE NO ES PORTABLE ENTRE LAYOUTS. El _GLEAGUE_RE de
D-EXP-1/D-EXP-2 busca "G League"/"Two-Way" en el texto de la RAZON; en
ITEXT_V1 ese texto vive en la columna CATEGORY y la razon suele ser "-", asi
que la inferencia los pierde. Medido: de las 199 filas Available con Category
explicita, 106 coinciden con la inferencia por razon y 93 DISCREPAN (47%). Con
el mejor clasificador disponible (category si existe, razon si no), la
poblacion G-League de legacy pasa de 16.8% a 24.7% y las tasas quedan
gleague 0.349 (n=292) / resto 0.808 (n=890) contra GemBox gleague 0.448
(n=270) / resto 0.847 (n=1518). Estandarizando legacy a la MEZCLA de GemBox
con ese clasificador, la brecha cae de -9.18pp a -4.7pp: DENTRO de la
tolerancia pre-registrada. La composicion sola explicaba apenas 0.8pp; el
clasificador explica el resto.
CORROBORACION INDEPENDIENTE de los pesos de D-EXP-1: las etiquetas EXPLICITAS
del documento legacy dan G League Team 0.4125 (n=80), G League - On Assignment
0.4286 (n=7), G League - Two-Way 0.50 (n=6) — pooleado 0.419 — e
Injury/Illness 0.80 (n=100), contra los 0.45 / 0.77 que D-EXP-1 habia
inferido por texto en GemBox. La inferencia era buena; lo que no es portable
es DONDE buscar el texto.
HALLAZGO LATERAL (solo visible en ITEXT_V1, que trae Previous Status):
Available con previous_status=Out juega 0.391 (n=110), contra 0.872 si venia
de Probable y 0.925 si venia de Questionable. "Available" no es un estado, es
un ANUNCIO DE CAMBIO, y su valor depende de desde donde se cambio.

HALLAZGO PRINCIPAL, NO BUSCADO — DEFECTO DE PROPAGACION EN EL PARSER LEGACY.
Lo destapo el diagnostico de instrumento, no un test. Las columnas que
aparecen UNA VEZ por bloque (Game Date, Game Time, Matchup, Team) se propagan
hacia abajo, y esa propagacion se queda RANCIA cuando el valor nuevo aterriza
en una fila que el parser excluye (banda NYS) o al cruzar de pagina.
Evidencia literal, PDF 2018-12-18_01PM: contiene 16 matchups — los 4 partidos
del 12/18 (CLE@IND, WAS@ATL, LAL@BKN, DAL@DEN) y los 12 del 12/19 — y el
parser estampa 12/18/2018 en sus 52 filas, porque la transicion de fecha cae
sobre una fila NOT YET SUBMITTED cuya banda se excluye y se lleva la fecha
nueva con ella. No es universal: el PDF de 2019-01-16 SI lee las dos fechas
(68 filas del 16, 10 del 17), porque alli la transicion cae sobre filas con
jugadores. Segunda cara del mismo defecto, con conocimiento externo:
VanVleet, Fred — ultima fila de jugador de la pagina 3, dentro del bloque de
Toronto que cruza desde la pagina 2 — sale atribuido a "Minnesota
Timberwolves". Es la clase Trae Young -> Portland, viva en el corpus legacy.
CUANTIFICACION (invariante INTERNO del documento: el equipo de una fila debe
ser uno de los dos del Matchup):
  ITEXT_V1  7 416 filas evaluables, 324 incoherentes (4.37%)
  ITEXT_V2 36 380 filas evaluables, 1 835 incoherentes (5.04%)
  356 fechas afectadas, mediana 5 filas por fecha afectada.
En las filas inspeccionadas la incoherencia esta en el MATCHUP (rancio), no en
el equipo: en 2019-01-15, las 11 filas incoherentes son jugadores de Houston y
Toronto correctamente atribuidos a su equipo con matchup "CHI@LAL" heredado.
El caso VanVleet demuestra que la variante con el EQUIPO equivocado tambien
existe; su tasa NO quedo cuantificada. Manifestacion ruidosa adicional: 114
instancias (todas ITEXT_V1, 28 fechas) con la celda de equipo conteniendo DOS
nombres concatenados ("Sacramento Kings Portland Trail Blazers", "Timberwolves
Dallas Mavericks") o un fragmento ("Minnesota") — excluidas por sin_match_equipo,
es decir el defecto fallo RUIDOSAMENTE ahi.

ESTO NO CONTRADICE EL VERDE DE D-RES-3, y la distincion importa: el listado
que Antonio auditó (audit_listados_legacy.py) muestra equipo, jugador,
estatus, razon, game_date y NYS — NUNCA la columna Matchup. La adjudicacion
verde cubre lo que el listado mostraba, y en las dos fechas auditadas el
equipo esta bien (2019-01-15 tiene 11 incoherencias, todas de matchup;
2021-02-10 tiene cero). El invariante 3 del pre-registro de D-RES-3 ("todo
game_date igual a la fecha del PDF o al dia siguiente") paso VACUAMENTE en los
PDFs donde todas las filas heredan la fecha del propio PDF — tercer invariante
de esa familia que pasa sin medir nada.

IMPACTO SOBRE ESTA MEDICION, ACOTADO Y MEDIDO: la definicion de instancia
("filas con game_date == fecha objetivo") NO hace lo que dice en el corpus
legacy — con la fecha rancia conserva tambien filas de los partidos de
MANANA. El mecanismo de exclusion las absorbio en vez de contaminar: de las
1 639 instancias sin_partido, 1 517 (92.6%) son de equipos que juegan al DIA
SIGUIENTE, y quedaron fuera de los denominadores. La contaminacion residual
son los equipos que juegan AMBOS dias, cuyas filas de manana si caen sobre el
partido de hoy: 30 instancias incluidas (0.07% de 40 986) con el mismo jugador
contado dos veces el mismo dia, 22 de ellas con estatus distinto entre copias
(caso legible: Denver el 2018-12-28, cuatro jugadores como Out y como Available
a la vez). Cota superior de la contaminacion silenciosa por fecha: 0.07%.
D-EXP-1 NO esta afectado: el parser GemBox lee la fecha fila por fila (su
sin_partido era 0/47/70). PRODUCCION NO esta afectada: el feed en vivo usa la
ruta GemBox.

CONSECUENCIA PARA LA RE-MEDICION DE D-EXP-2 (por la que existe D-EXP-5): sigue
siendo viable y sigue siendo la unica via cuantificada de resolver la pregunta
de disponibilidad ponderada, pero entra con dos deudas nombradas: (a) el
defecto de propagacion debe acotarse en su variante de EQUIPO, porque mover la
ausencia de un jugador al equipo vecino corrompe exactamente la feature que se
mide — no basta con la cota del 0.07% de fecha; (b) el clasificador de
Available debe leer category cuando exista, o los pesos se aplicarian sobre una
particion mal hecha. Ambas son de ingenieria, no de diseno experimental, y
ninguna se resolvio aqui (prohibido en el encargo).

REFINAMIENTO GRATUITO DEL ALCANCE DE D-RES-3d: el tramo sin lista blanca es
EXACTAMENTE 2019-11-15 -> 2019-12-17, 32 fechas publish contiguas, no las ~40
estimadas. Desde 2019-12-18 los PDFs ya parsean como ITEXT_V2 (19 fechas
verificadas hasta 2020-01-06), asi que el "primer dia V2" de D-RES-3c
(2020-01-07) era el primer dia MUESTREADO, no el primero real. D-RES-3d
encoge a 32 fechas.

EVIDENCIA EN DISCO (data/experiment_pjuega_legacy/): results.json,
instancias.json, tabla.txt, audit_dates.json (muestra congelada),
auditoria_2019-01-16.txt (ITEXT_V1, 68 instancias) y auditoria_2020-02-01.txt
(ITEXT_V2, 89 instancias) para el cotejo humano, diagnostico_Available.txt
(20 instancias, seed 42, disparado automaticamente por la divergencia > 5pp).
Script: scripts/experiment_pjuega_legacy.py. Tests: 39 unit nuevos
(tests/test_experiment_pjuega_legacy.py). Suite: 808 passed, 15 deselected
(769 de base + 39).

LOS AGREGADOS NO SON OFICIALES: dependen de la auditoria humana de los dos
listados congelados (protocolo 13e-1) Y, ademas, de la adjudicacion del
defecto de propagacion — es la primera vez en este proyecto que una medicion
llega a la mesa con un defecto de instrumento conocido y acotado en vez de
solo "pendiente de cotejo". El veredicto de portabilidad de los pesos (que es
lo que la re-medicion necesita) descansa en los cuatro estatus cuya tolerancia
se cumplio, y ninguno de ellos depende de la columna Matchup ni de la fecha
fila-por-fila.

RESULTADO: pendiente de auditoria humana.

## D-RES-3e (2026-09-25): defecto de propagación del parser legacy
CORREGIDO; D-RES-3 ACOTADO; instrumento listo para la re-medición

ACOTACIÓN DE D-RES-3 (append, no revocación): el verde del 2026-09-24
cubre lo que el listado auditado mostraba; la columna Matchup no
estaba en él y el defecto de propagación vivía ahí y en fronteras que
las dos fechas auditadas no ejercitaban. Lección: un listado de
auditoría debe mostrar TODA columna de la que dependa la atribución,
y la muestra debe incluir fronteras a propósito, no al azar.

DIAGNÓSTICO GEOMÉTRICO: dos manifestaciones, UNA causa raíz — una fila
excluida del CUERPO quedaba también excluida del ESTADO de propagación.
(a) FECHA SOBRE FILA NYS. En 2018-12-18_01PM la cadena "12/19/2018"
    aparece UNA sola vez en todo el documento, en la fila NOT YET
    SUBMITTED de CLE@CHA (página 2, top=409). El recorte de las bandas
    NYS se la llevaba consigo, así que las 52 filas salían fechadas
    12/18 aunque 12 de los 16 matchups del documento eran del 19. NO era
    universal: 2019-01-16 sí leía las dos fechas (68 del 16, 10 del 17)
    porque allí la transición cae sobre filas con jugadores — de ahí que
    ningún invariante la delatara.
(b) NOMBRE DE EQUIPO NYS EN DOS LÍNEAS. Página 3 del mismo PDF:
    "Minnesota" en top=275 y "Timberwolves" en top=283, con SUBMITTED
    centrado en 279. _nys_tops solo excluía 279 ± 2 pt, así que los dos
    fragmentos sobrevivían y caían en la banda de VanVleet — última
    ancla de la página, cuya banda llegaba hasta el pie. Un jugador de
    Toronto salía atribuido a "Minnesota Timberwolves": clase Trae Young
    -> Portland.
Tasa de EQUIPO equivocado medida antes del fix, con el matchup
reconstruido POR POSICIÓN (no el propagado, que es lo que estaba bajo
sospecha): 36/7421 = 0.49% en ITEXT_V1 (36 fechas) / 0/36408 = 0.00%
en ITEXT_V2. CORRECCIÓN DE LECTURA DE D-EXP-5: el 4.37%/5.04% que aquel
bloque reportó medía equipo-vs-matchup-PROPAGADO y estaba dominado por
matchup RANCIO, no por equipo mal atribuido. El defecto de equipo era
diez veces menor de lo que sugería ese número, y concentrado casi
enteramente en el único nombre de equipo del corpus que ocupa dos
líneas.

FIX: UNA REGLA, sin heurísticas apiladas — las bandas se delimitan por
punto medio entre anclas consecutivas, y un bloque NOT YET SUBMITTED es
un ancla como cualquier otra (antes solo lo eran los nombres de
jugador). Con eso la fila NYS deja de ser un hueco: tiene banda propia,
se lleva sus propios fragmentos (incluido un nombre de equipo partido en
dos líneas) y ENTREGA su valor de columna al estado de propagación. El
estado se actualiza con TODO evento del documento y solo los eventos de
jugador emiten fila; cruza de página sin reiniciarse y lo sobreescribe
el primer evento de la página nueva que traiga valor propio (los dos
casos con test). _rows_from_page se conserva como vista estrecha de
_events_from_page, así que los 11 tests de D-RES-3c siguen ejerciendo la
geometría por el mismo camino.

INVARIANTE INTERNO en el parser: el equipo de cada fila debe ser uno de
los dos tricodes de su Matchup; violación -> coherent=False + WARNING con
jugador, equipo, matchup y fecha, y la fila SIGUE en la lista — jamás se
corrige ni se descarta en silencio (un parser que arregla calladamente
miente igual que uno que calla). Nombre de equipo fuera de la referencia
= NO EVALUABLE, nunca marcado: el invariante avisa, no inventa. Umbral de
guarda agregada INJURY_INCOHERENT_WARN_RATE = 1% en config, con el
porqué escrito: cero convertiría el log en ruido y enseñaría a
ignorarlo. SIGUE SIENDO GUARDA, NO PRUEBA: un error que respete el
matchup pasa sin hacer ruido. Los tricodes viven en un mapa estático
del módulo (30 equipos + el alias "LA Clippers"); es dato de referencia
que solo alimenta un WARNING, y un renombre de franquicia produciría
log ruidoso, nunca parseo roto.

CLASIFICADOR de Available portable: nba_predictor/ingestion/injury_classify.py
(is_gleague_or_twoway / available_bucket) lee Category cuando el layout
la trae y cae a la razón cuando no. Función ÚNICA importada por los
scripts de D-EXP-2 y D-EXP-5; la regex duplicada de
experiment_disponibilidad_v2.py se fue y quedó como alias. Los pesos
oficiales de D-EXP-1 NO cambian: cambia la PARTICIÓN sobre la que se
aplican. La métrica de concordancia se conservó midiendo category vs
RAZÓN-SOLA (100 coinciden / 94 discrepan), porque esa discrepancia ES la
evidencia del hallazgo — portarla habría borrado la prueba de que el
instrumento estaba sesgado. Lo cazó un test propio de D-EXP-5 al ponerse
rojo.

RE-CORRIDA D-EXP-5 sobre el instrumento reparado (mismas definiciones,
mismos seeds, 693 fechas, 42 051 instancias):
  incoherencias equipo-matchup   0.49% -> 0.00% (0 de 7 054)  ITEXT_V1
                                 0.00% -> 0.00% (0 de 34 997) ITEXT_V2
  sin_partido                    1 639 -> 134  (-91.8%)
  sin_match_equipo                 114 -> 47
  pesos pooleados (P_antes -> P_después, delta):
    Out          0.0070 -> 0.0071  (+0.01pp)   n=31 360
    Doubtful     0.0540 -> 0.0548  (+0.08pp)   n=857
    Questionable 0.4930 -> 0.4950  (+0.20pp)   n=5 855
    Probable     0.8810 -> 0.8819  (+0.09pp)   n=2 370
    Available    0.6950 -> 0.7024  (+0.74pp)   n=1 166
  Available con clasificador portable, estandarizado a la mezcla de
  GemBox: -3.77pp (crudo -8.40pp). Mezcla G-League del corpus legacy
  25.0% con category contra 15.1% de GemBox; tasas gleague 0.3505 (n=291)
  / resto 0.8194 (n=875).
VEREDICTO CONTRA LAS EXPECTATIVAS PRE-REGISTRADAS: las CUATRO se
cumplen. (a) incoherencias < 0.5% por layout: cero exacto en ambos, sin
residuales que listar. (b) sin_partido cae drásticamente: -91.8%, y las
filas del día siguiente ahora llevan su fecha real y se capturan en su
propio día. (c) los cuatro pesos se mueven < 1pp: el mayor es
Questionable con +0.20pp — el fix corrigió la atribución sin mover la
medición, que es exactamente lo que debía pasar. (d) Available dentro de
±5pp: -3.77pp, algo mejor que el ~-4.7pp estimado.
DESVIACIÓN DECLARADA de la expectativa (a): estaba redactada contra el
4.37%/5.04% de D-EXP-5, y la medición independiente mostró que la tasa
de EQUIPO ya era 0.49%/0.00% ANTES de tocar nada. La expectativa se
cumplía casi sola porque la métrica del bloque anterior conflaba dos
defectos. Se adjudica como error de redacción del pre-registro, no como
resultado: la regla de computar la magnitud antes de prometer un umbral
(D-PROD-1b) aplica también a los umbrales de mejora, no solo a los de
presupuesto.

RESIDUOS CARACTERIZADOS, ambos RUIDOSOS (fallan excluyendo, nunca
misatribuyendo) y ninguno silencioso — 0 filas marcadas incoherentes en
42 051:
- sin_match_equipo 47, todas ITEXT_V1, 11 fechas, con la celda de equipo
  conteniendo dos nombres ("Sacramento Kings Atlanta Hawks", "Portland
  Trail Blazers Los Angeles Lakers"). Es una FRONTERA DISTINTA: un
  encabezado de bloque al pie de página cuya primera fila de jugador
  está en la página siguiente, así que no tiene ancla de ningún tipo.
  Candidato a defecto propio, nombrado y no corregido aquí.
- sin_partido 134: 2019-20 17, 2020-21 86, 2021-22 31, cero en 2018-19 y
  2022-23. Concentrado en 2020-03-11 (17 — la noche de la suspensión de
  la temporada) y en las posposiciones COVID de ene-2021 y dic-2021. Es
  mundo real (equipo en el reporte de un partido que no se jugó), no
  instrumento.

LISTADOS DIRIGIDOS emitidos para Antonio (protocolo 13e-1), con la
columna MATCHUP, el veredicto COH del invariante y las fronteras
marcadas (PAG-INI / PAG-FIN / POST-NYS / FIN-BLOQUE) — porque el defecto
vive en las fronteras y una muestra al azar puede no ejercitar ninguna:
2021-02-10 tenía cero incoherencias y pasó la auditoría de D-RES-3 en
verde mientras el defecto seguía vivo:
  auditoria_dirigida_2019-01-17.txt  ITEXT_V1, 44 filas
  auditoria_dirigida_2020-02-02.txt  ITEXT_V2, 42 filas
  regresion_2018-12-18.txt           52 filas, TODAS las del documento
    sin filtrar por fecha: las 7 de Toronto con fecha 12/19/2018 bajo
    IND@TOR y VanVleet atribuido a Toronto Raptors con COH=True, marcado
    PAG-FIN,FIN-BLOQUE — la frontera exacta que lo rompía.
Muestra congelada ANTES de computar (seed 42, fechas NUEVAS, excluidas
las dos de la corrida anterior): audit_dates_d3e.json sellado
2026-09-25T23:47:08Z contra results.json 18:11:12 local — orden
verificado por timestamps.

TESTS: 28 nuevos en tests/test_injury_report_legacy_d3e.py, archivo
APARTE para que se vea que el de D-RES-3c sigue intacto. Uno por
manifestación sobre geometría sintética con las coordenadas medidas
(fecha-sobre-NYS, nombre NYS en dos líneas), dos de cruce de página,
siete del invariante (marcada y avisada, jamás descartada; no evaluable
sin referencia; umbral agregado) y trece del clasificador. Los 91 tests
de 13e-1 y los 11 de D-RES-3c pasan SIN UNA SOLA EDICIÓN: la regla de
detenerse no se activó, aunque estuvo a punto — cinco rojos aparecieron
al renombrar un privado y al añadir un parámetro, y se resolvieron
quitando el parámetro y conservando la función como vista, no tocando
los tests. Los PDFs reales NO entran como fixtures.
Suite: 836 passed, 15 deselected (808 de base + 28).

RESULTADO: pendiente de auditoría humana dirigida. La re-medición de
D-EXP-2 queda gateada por ella.

## D-RES-3e CERRADO + D-EXP-5 OFICIAL (2026-09-26): parser legacy
auditado EN FRONTERAS, pesos portables confirmados

ADJUDICACION DE ANTONIO: los tres listados dirigidos
(auditoria_dirigida_2019-01-17 V1 44 filas, auditoria_dirigida_2020-02-02
V2 42 filas, regresion_2018-12-18 documento completo) cotejados contra
sus PDF con foco en las filas de frontera marcadas (PAG-INI/PAG-FIN/
POST-NYS/FIN-BLOQUE): VERDE. Equipo, fecha y matchup
coinciden en las fronteras; las 7 filas de Toronto del 2018-12-18
llevan 12/19 y VanVleet esta en Toronto.

CONSECUENCIAS:
- injury_report_legacy.py queda AUDITADO en fronteras para ITEXT_V1 e
  ITEXT_V2, con el invariante interno equipo-en-matchup como guarda
  permanente (0/42 051 incoherencias tras el fix). El verde de D-RES-3
  del 09-24 y esta auditoria dirigida se suman: la primera cubrio el
  cuerpo, la segunda las fronteras.
- Los agregados de la RE-CORRIDA de D-EXP-5 pasan a OFICIALES. Veredicto
  sustantivo: los pesos de D-EXP-1 (Out 0.00, D 0.02, Q 0.48, P 0.91,
  Available por clasificador portable) son PORTABLES a la era legacy;
  se usan como pesos unicos en la re-medicion. Ambas mediciones quedan
  registradas (GemBox y legacy) como exige la nota de vigencia de
  D-EXP-1.
- CORRECCION REGISTRADA: la tasa de equipo equivocado pre-fix era
  0.49% V1 / 0.00% V2 (matchup reconstruido por posicion), no el
  4.37/5.04% que D-EXP-5 reporto.

[NOTA DE TRANSCRIPCION 2026-09-26, de Code: el encargo de este bloque
llego SIN su delimitador de cierre y el texto termina en la linea de
arriba. Se transcribio VERBATIM lo recibido y se añadio unicamente el
punto final; NO se invento continuacion (ni "Suite:" ni "RESULTADO:",
que otros bloques de cierre suelen llevar). Si faltaba texto, se añade
como continuacion de este bloque.
Se resolvio tambien el condicional del encargo: la frase de
adjudicacion quedo como VERDE, la opcion por defecto que venia escrita,
porque nada en el encargo indico que el cotejo hubiera sido por
plausibilidad. Si lo fue, la sustitucion es de una frase y se registra
como enmienda: "revisados por plausibilidad, adjudicacion PARCIAL
revocable si aparece contradiccion".]

[CONTINUACION del bloque D-RES-3e CERRADO + D-EXP-5 OFICIAL, cuyo
encargo llego truncado tras "no el 4.37/5.04% que D-EXP-5 reporto";
Code transcribio lo recibido sin inventar la cola, y esta es la cola.]

  ...que D-EXP-5 reporto (dominado por matchup rancio). La gravedad
  real era un caso geometrico (nombre de equipo en dos lineas), hoy
  resuelto.
- REGLAS NUEVAS: (a) muestras de auditoria de parsers incluyen
  fronteras A PROPOSITO y muestran toda columna de la que dependa la
  atribucion (2021-02-10 paso una auditoria plena con el defecto vivo
  porque no ejercitaba fronteras ni mostraba Matchup); (b) los
  umbrales de mejora de un pre-registro se computan contra la
  magnitud MEDIDA, no contra la narrativa (la expectativa (a) de
  D-RES-3e nacio contra un 4.37% que no era la tasa relevante).
- DEUDAS NOMBRADAS, ruidosas, no bloqueantes: 47 celdas V1 con dos
  nombres (encabezado al pie de pagina con primera fila en la
  siguiente; fallan como sin_match_equipo), D-RES-3f si algun dia
  vale la tarde; D-RES-3d = 32 fechas (2019-11-15 -> 2019-12-17).

DESBLOQUEADO SIN CONDICIONES: re-medicion de D-EXP-2 con corpus
ampliado (2018-12-18 -> 2025-26 menos las 32 fechas de D-RES-3d),
instrumento limpio, pesos portables. Pre-registro con sus tres
requisitos (MDE en fase 0; v2 vs binario de misma construccion como
contraste primario; confound de construccion declarado) mas uno
añadido por D-EXP-5: pesos por fold desde el TRAIN de cada fold
(walk-forward puro), no globales.

## D-EXP-2b: RE-MEDICIÓN disponibilidad-v2 CON CORPUS AMPLIADO
(PRE-REGISTRO 2026-09-26)

Resuelve el null de resolución de D-EXP-2 con ~7 folds walk-forward
(corpus 2018-12-18 -> 2025-26, parser legacy auditado en D-RES-3/3e,
32 fechas de D-RES-3d excluidas). Tres brazos sobre filas idénticas:
BIN (binaria desde PDF), V2 (ponderada), CTL (B-limpia re-entrenada,
referencia con confound de construcción declarado). CONTRASTE
PRIMARIO: V2 vs BIN, la hipótesis "ponderar estabiliza" del brazo
diagnóstico de D-EXP-2, ahora formal. Pesos POR FOLD desde el train
(walk-forward puro), Available con clasificador portable.

FASE 0 (gate ejecutable): MDE extrapolado al n total desde el primer
fold; expectativa 0.004-0.006; MDE > 0.008 = SIN RESOLUCIÓN, se
detiene y reporta.

Expectativas: ganancia 0.000-0.008 LL; nulo plausible y legítimo;
> 0.015 = auditar leakage; coeficiente + en todos; pesos por fold
±5pp de los oficiales.

PUERTA con dos salidas DEFINITIVAS: gana fuera del ruido (agregado y
mayoría de folds) -> candidato sombra vía model_version durante la
ventana; nulo o negativo CON resolución -> la pregunta se CIERRA
permanentemente, pesos solo editoriales, sin reapertura salvo
información distinta (no más corpus).

RESULTADO: pendiente de fase 0 y ejecución.

## D-EXP-2b RESULTADO (2026-09-26): la puerta se cumple POR UN PELO y la
significancia la carga UN fold — adjudicación de Antonio pendiente

CORPUS: 1 177 fechas publish parseadas (2018-12-18 -> 2025-26), 78 561 filas
resueltas, sin-match de nombre 538 (0.68%), sin_match_equipo 47 (el residuo
conocido de D-RES-3e). EXCLUIDAS y contadas: las 32 fechas de D-RES-3d
(UnknownLayoutError, 2019-11-15 -> 2019-12-17; jamás se forzó un layout) y 63
fechas sin corte publish. Universo 7 256 partidos; 6 476 filas de validación
repartidas en SIETE folds walk-forward (2018-19 entra solo como train: su
archivo empieza el 18-dic y validar sobre una temporada truncada mediría el
hueco del archivo, no el modelo).

FASE 0 (gate ejecutable): sd de la diferencia pareada V2 vs BIN = 0.054626 en
el fold base (2019-20, n=653); MDE del fold base 0.005989; MDE extrapolado al
n total de 6 476 = **0.001902**. Techo pre-registrado 0.008 -> **CUBRE**. El
experimento SÍ puede distinguir su propia expectativa de cero, que es
exactamente lo que D-EXP-2 no podía.
DESVIACIÓN DECLARADA, al alza: se pre-registró un MDE de 0.004-0.006 y salió
0.0019, dos o tres veces MÁS potente. Mecanismo identificado, no casualidad:
V2 y BIN comparten construcción y difieren solo en la tabla de pesos, así que
sus predicciones por partido están muy correlacionadas y la sd de la
diferencia pareada es 0.0546 frente a los ~0.108 de V2-vs-CTL en D-EXP-2. El
requisito 2 de D-RES-3e (contraste de misma construcción como primario) no
solo limpió el confound: es lo que compró la resolución. Lección
transferible: en un contraste pareado, emparejar por construcción vale más
potencia que multiplicar el corpus.

TABLA POR FOLD — contraste PRIMARIO V2 vs BIN:
  fold valid      n_val   LL CTL   LL BIN    LL V2   V2-BIN      t   IC95 excl.0
  F0   2019-20      653  0.67547  0.67943  0.67848  +0.00095   0.44   no
  F1   2020-21      845  0.62954  0.63104  0.63158  -0.00053  -0.32   no
  F2   2021-22      999  0.65307  0.65342  0.65167  +0.00176   1.18   no
  F3   2022-23      999  0.65619  0.65750  0.65609  +0.00141   0.82   no
  F4   2023-24      998  0.62448  0.62438  0.62508  -0.00070  -0.46   no
  F5   2024-25      989  0.60715  0.60693  0.60603  +0.00090   0.57   no
  F6   2025-26      993  0.61557  0.62029  0.61528  +0.00501   2.75   SI
  AGREGADO n=6 476  media +0.001313  t=2.047  IC95 [+0.000056, +0.002571]
  V2 gana en 3 326 de 6 476 partidos (51.4%) y en 5 de 7 folds.

LA PUERTA PRE-REGISTRADA SE CUMPLE EN SU LETRA: el IC95 del agregado excluye
cero (+0.000056 de cota inferior) y V2 gana en la mayoría de los folds (5/7).
Por la regla congelada, eso es PROMOVER a candidato sombra. No se renegocia el
criterio después de ver el número — es justo lo que un pre-registro existe
para impedir.

Y AL MISMO TIEMPO, LA EVIDENCIA ES FRÁGIL DE FORMA MEDIDA (robustez que el
pre-registro no pidió y que el resultado obliga a reportar):
- SIN F6, los otros seis folds dan media +0.000643, t=0.943, IC95
  [-0.000693, +0.001979]: el cero VUELVE a entrar. La significancia del
  agregado la carga entero un solo fold.
- F6 (2025-26) es el ÚNICO fold cuyo IC excluye cero por separado (t=2.75,
  +0.00501, cuatro veces el agregado). Los otros seis son individualmente
  indistinguibles de cero, dos de ellos negativos.
- Prueba de signos 5 de 7: p = 0.453. La "mayoría de los folds" de la puerta
  se cumple por conteo, no por evidencia.
Esto NO es una aplicación de criterio nuevo ni una anulación de la puerta: es
el contenido que la adjudicación necesita. Adjudica Antonio.

EL HALLAZGO SUSTANTIVO, en los tres contrastes agregados (n=6 476):
  V2 vs BIN  (primario, misma construcción)  +0.001313  t=+2.05
  BIN vs CTL (magnitud del confound)         -0.001528  t=-1.27
  V2 vs CTL  (V2 contra la referencia)       -0.000214  t=-0.17
Leídos juntos dicen una sola cosa, y es coherente: construir el numerador
desde el PDF CUESTA ~0.0015 de log loss frente al numerador de activados del
boxscore, y ponderar RECUPERA ~0.0013 de eso. V2 termina EMPATANDO con el
control (t=-0.17). La hipótesis que sobrevivió al brazo diagnóstico de D-EXP-2
—"ponderar ESTABILIZA frente al binario de misma construcción"— queda
CORROBORADA con 7 folds en vez de 1, con su magnitud medida. Pero la
consecuencia práctica hay que decirla entera: ponderar no le gana a la feature
de producción, le devuelve a la construcción-desde-PDF lo que esa
construcción pierde. v1 ya consigue ese nivel SIN necesitar el PDF.

EXPECTATIVAS, UNA POR UNA:
(1) Ganancia 0.000-0.008: SE CUMPLE (+0.00131).
(2) > 0.015 = auditar leakage: NO se activó (dos órdenes de magnitud por
    debajo).
(3) Coeficiente de availability con signo + en todos los brazos y todos los
    folds: SE CUMPLE, 21 de 21 (rango +0.173 a +0.248).
(4) Pesos por fold dentro de ±5pp de los oficiales: SE CUMPLE en los cuatro
    estatus centrales, con una excepción marginal — Out y Questionable dentro
    de ±2.1pp en los siete folds; Doubtful +2.5 a +5.6pp y Probable -2.0 a
    -5.0pp, y las dos únicas violaciones (F0 Doubtful +5.59, F0 Probable
    -5.02) están en el fold cuyo train es 2018-19 PARCIAL, el más delgado
    (n=145 y n=321). NO se cumple en los buckets de Available (hasta -9.95pp),
    y eso es ERROR DE MI PRE-REGISTRO, declarado: los pesos oficiales de
    Available (0.45/0.77) los midió D-EXP-1 con el clasificador de RAZÓN SOLA,
    mientras que aquí la partición es la PORTABLE de D-RES-3e. No son las
    mismas poblaciones, así que compararlos contra ±5pp no era manzanas con
    manzanas. D-RES-3e ya había medido esa brecha (legacy gleague 0.3505 vs
    GemBox 0.448); extender la tolerancia de los cuatro estatus a los buckets
    fue un descuido al redactar, no un hallazgo del corpus.
(5) BIN vs CTL sin expectativa direccional: -0.0015 (t=-1.27), el confound
    medido y declarado.

MECANISMO VISIBLE (sin esto, una diferencia de log loss no tendría qué
explicarla): entre 96% y 98% de las filas de cada fold cambian de valor entre
V2 y BIN, con |Δ| media 0.041-0.052, p95 0.124-0.155 y máximo hasta 0.60. La
feature se mueve de verdad; la ganancia pequeña no viene de un cambio
pequeño.

EVIDENCIA EN DISCO (data/experiment_v2b/): mde_fase0.json (23:35:41) ANTES de
results.json (23:35:42), orden verificado por timestamps; audit_games.json
(muestra congelada seed 42, 30 partidos de un universo de 7 256, sellada
05:35:41Z antes de medir); results.json con los 7 folds, los tres contrastes,
pesos y n por fold, estabilidad y distribución; tabla.txt;
auditoria_muestra.txt (371 líneas) con las ausencias aplicadas por cada brazo
y el PDF de origen, para el spot-check.
NOTA DEL LISTADO: la muestra congelada se sorteó sobre TODO el corpus, así que
~1/7 cayó en 2018-19, que es train en los siete folds y no valida en ninguno.
Para esos partidos el listado etiqueta el rol y muestra los pesos de F0; la
primera versión los mostraba con V2=1.0 (un Out con peso 1.0, que no existe en
ninguna corrida) y se corrigió el LISTADO, no la muestra — re-sortearla tras
ver los números sería elegir la evidencia.

Script: scripts/experiment_disponibilidad_v2b.py, en dos etapas (resolve
parsea una vez; measure pondera siete veces) porque los pesos cambian por fold
y parsear siete veces no era opción. Aritmética de disponibilidad, métricas por
fold y diferencia pareada IMPORTADAS de D-EXP-2; pooleo y partición portable
de D-EXP-5 y del paquete. Cero ediciones al paquete, cero despliegue, B-limpia
y features_v1 intactos. Tests: 41 nuevos (tests/test_experiment_v2b.py), con
el invariante del walk-forward puro fijado por test (ninguna temporada de
validación aparece en su propio train) porque un leakage ahí produciría una
tabla de aspecto idéntico. Suite: 877 passed, 15 deselected (836 + 41).

RESULTADO: pendiente de adjudicación de Antonio. Las dos salidas siguen siendo
las pre-registradas y son definitivas; lo que esta corrida añade es que la
letra de la puerta apunta a PROMOVER mientras la robustez apunta a que la
señal vive en un fold. El spot-check de auditoria_muestra.txt es insumo de esa
adjudicación, no de la aritmética.

## D-EXP-2b RESULTADO (2026-09-27): CON RESOLUCION. Ponderar estabiliza
frente al binario (+0.0013, fragil) y NO supera a produccion (-0.0002).
Pregunta CERRADA PERMANENTEMENTE. Sin candidato sombra.

FASE 0: MDE agregado 0.0019 contra techo 0.008: CUBRE, y por debajo del
0.004-0.006 esperado. Desviacion al alza adjudicada como LECCION DE
DISEÑO: V2 y BIN comparten construccion, sus predicciones estan muy
correlacionadas y la sd de la diferencia pareada es 0.055 (contra ~0.108
de V2-vs-CTL en D-EXP-2). Emparejar por construccion compro mas
resolucion que triplicar el corpus. REGLA: la potencia de un contraste
pareado la gobierna la correlacion entre brazos, no solo el n; diseñar
el contraste primario entre brazos de misma construccion siempre que la
pregunta lo permita.

TABLA (7 folds, log loss, diferencias pareadas):
  V2 vs BIN (primario)         +0.001313  t=+2.05  IC95 [+0.000056,+0.002571]
  BIN vs CTL (confound medido) -0.001528  t=-1.27
  V2 vs CTL (vs referencia)    -0.000214  t=-0.17
  Sin F6 (2025-26):            +0.000643  t=+0.94  IC95 [-0.000693,+0.001979]
F6 es el unico fold con IC individual fuera de cero (+0.00501, t=2.75,
cuatro veces el agregado); los otros seis son indistinguibles de cero,
dos negativos; prueba de signos 5/7 p=0.453. Coeficientes positivos
21/21. 96-98% de filas cambian entre brazos (|delta| medio ~0.05): el
mecanismo existe, la ganancia pequeña no viene de un cambio pequeño.

ERROR PROPIO DE DISEÑO (sexto de la serie): la puerta "gana fuera del
ruido en el agregado Y en la mayoria de los folds" admite dos lecturas
((a) IC agregado excluye cero y mayoria con signo positivo: CUMPLIDA
5/7; (b) fuera del ruido en ambos: NO cumplida, 1/7). Elegir lectura
con los numeros delante seria renegociar en cualquier direccion. REGLA
NUEVA: toda puerta se escribe con criterios numericos sin lectura
alternativa (p.ej. IC agregado excluye cero Y >= k folds con IC
individual fuera de cero Y prueba de signos p < x).

SALIDA DE LA AMBIGUEDAD SIN RENEGOCIAR: bajo la lectura mas favorable
(a), promover = experimento sombra V2 vs B-limpia en vivo (~470
partidos). La regla de D-EXP-2 exige su MDE antes de construirlo: sobre
470 pareados es ~0.01, entre 10 y 50 veces el efecto esperado (+0.0013
vs BIN, -0.0002 vs CTL). El sombra nace SIN RESOLUCION por construccion
y no se corre. Bajo (b) no se promueve. Ambas lecturas convergen: NO SE
CONSTRUYE el candidato sombra. Adjudicado por Antonio.

LECTURA SUSTANTIVA, definitiva:
- Pregunta cientifica ("ponderar estabiliza frente al binario de misma
  construccion", hipotesis del brazo diagnostico de D-EXP-2): SI,
  +0.0013, real pero fragil (vive en F6; hipotesis post-hoc no
  investigada: reportes mas limpios en la era GemBox reciente).
- Pregunta de producto ("V2 supera a la feature de produccion"): NO.
  El triptico lo explica: construir el numerador desde el PDF cuesta
  ~0.0015 y ponderar recupera ~0.0013; v1 llega al mismo sitio sin
  necesitar el PDF.
- La pregunta "ponderar disponibilidad mejora el modelo" queda CERRADA
  PERMANENTEMENTE con respuesta con resolucion: NO. Sin reapertura
  salvo informacion DISTINTA (no mas corpus). Los pesos de D-EXP-1
  viven en la capa editorial, donde ya sirven en el mensaje 2.

ERRORES DECLARADOS DE LA EJECUCION, aceptados: (1) tolerancia ±5pp mal
aplicada a buckets de Available de poblaciones distintas (oficiales
0.45/0.77 por clasificador de razon; aqui clasificador portable); sin
efecto en el veredicto, los cuatro estatus centrales cumplen (Out y Q
dentro de ±2.1pp; unicas violaciones en F0, el train mas delgado). (2)
La muestra de auditoria se sorteo sobre todo el corpus: 6/30 en
2018-19 (nunca valida); corregido el listado, NO re-sorteada la
muestra (re-sortear tras ver numeros seria elegir evidencia).

NOTA DE LECTURA para quien compare con D-EXP-2: aqui los pesos son
MEDIDOS por fold desde el train (Out 0.0064-0.0086, Q 0.48-0.50, P
0.86-0.89, D 0.05-0.08, Available 0.80-0.86 / gleague 0.41), no los
oficiales redondeados; ordenamiento intacto en todos los folds; efecto
sobre el veredicto despreciable.

AUDITORIA HUMANA PLENA (Antonio, 2026-09-27): 30 partidos de la muestra
congelada cotejados contra su PDF publish; estatus, pesos y BIN
correctos fila por fila. LIMITACION registrada: la muestra no contiene
partidos de F6 (ni F2) por azar del seed; el fold que carga el
resultado no tiene spot-check de instrumento. No cambia decisiones (no
se construye nada); suplemento post-hoc de F6 solo si algun dia se
reabre con informacion distinta.

Suite: 877 passed, 15 deselected.

ESTADO DEL LABORATORIO: cero preguntas de modelo abiertas. Camino 5
queda con: ponderacion temporal, ventanas 5/15/20, SRS, calibracion
explicita y comparacion vs Vegas (ya instrumentada via market_odds,
adjudicacion en enero). Ninguna tiene urgencia antes de la ventana.

## CANDIDATO DIFERIDO: INGESTA NO ESTRUCTURADA (anotado 2026-10-08)

Existe un documento de diseño (Antonio, pre-septiembre) para una capa
de ingesta de fuentes no estructuradas (RSS de noticias, conferencias
de prensa en YouTube, notas de reporteros, reporte de lesiones) con
extraccion por LLM hacia features del modelo. Patrones de ingenieria
RESCATADOS de Agent-Reach (repo descartado por shell remoto,
typosquatting y cookies contra ToS): registro de canales con backends
en orden y probe(), doctor con canario, circuit breaker, ruteo por
URL, niveles de riesgo por fuente, herramientas tipadas sin shell,
exposicion como MCP y tools de LangGraph. Principios: published_at y
fetched_at reales con corte temporal estricto, esquema comun de
documento, dedup por hash, contenido externo no confiable, deps con
hash, secretos en Secret Manager. Costos estimados (oct 2026):
~$20/mes con Haiku extrayendo y Sonnet redactando.

ADJUDICACION (chat de diseño 2026-10-08): NO SE CONSTRUYE para el
pipeline predictivo. Razones: (1) su premisa ("informacion no
estructurada que mueve resultados antes de las estadisticas") ya tuvo
su prueba mas limpia con la fuente que el propio documento señala
como mas valiosa, el reporte oficial de lesiones: construido, auditado
y medido en D-EXP-2b, C2 de D-EXP-3 y D-EXP-5, con resolucion y
resultado nulo sobre el modelo de equipo; noticias y transcripts son
versiones mas ruidosas y tardias de la misma informacion. (2) Conflicto
frontal con la frontera cerrada de Fase 6: el LLM es redactor, jamas
fuente de datos del pipeline (auditabilidad de features = producto).
(3) Los pendientes del documento ("que predice el modelo") estan
respondidos desde Fase 3.

DESTINO: Fase 7 candidata, "infraestructura de canales para la CAPA
EDITORIAL" (el agente redactor de Fase 6 leyendo contexto no
estructurado con la frontera intacta). Gateada por la decision de
comercializar (enero 2027). Reapertura del lado predictivo SOLO si el
analisis de CLV de enero muestra CLV positivo en el subconjunto de
injury report ambiguo: entonces la pregunta seria de VELOCIDAD
(publicar antes de que la linea se mueva), no de informacion, y
exigiria arquitectura en tiempo real, no la de las 13:00.

LO QUE SI SOBREVIVE COMO EXPERIMENTO: continuidad de roster desde
datos ESTRUCTURADOS (quintetas del boxscore), sin LLM ni canal nuevo.
Ver D-EXP-6.

## D-EXP-6: CONTINUIDAD DE ROSTER DESDE DATOS ESTRUCTURADOS
(PRE-REGISTRO 2026-10-08)
Última feature pendiente del Camino 5, medida sin LLM ni canal nuevo:
dos definiciones (C_a quintetas, C_b rotación por minutos), diferencia
local−visitante, walk-forward oficial, contraste pareado CAND vs CTL
de misma construcción, anti-leakage por grupo. Fase 0 con MDE
esperado 0.0015-0.0025 (techo 0.005). Expectativa 0.000-0.005 LL,
nulo legítimo, > 0.015 auditar leakage. PUERTA NUMÉRICA: IC agregado
excluye cero Y >= 4/6 folds positivos Y >= 2 folds con IC fuera de
cero; cruza con >= 0.005 -> candidato a v2 post-enero (no sombra);
cruza con < 0.005 -> real pero no accionable sola; no cruza -> cierre
con resolución. RESULTADO: pendiente.

## D-PRE-1: REVISIÓN DE PREPARACIÓN PRE-TEMPORADA (2026-10-11)

Revisión de solo lectura (logs, config, código) diez días antes de la
ventana. Cinco hallazgos, tres que habrían mordido el 21-22 de
octubre. Ninguno ejercitado jamás en condiciones de temporada.
INSTANCIAS 14, 15 y 16 DEL PATRÓN DE SILENCIO. REGLA NUEVA: antes de
toda fecha gate, revisión de preparación de solo lectura obligatoria:
intentos del Scheduler (status Y duración), errores de Cloud Run,
límites de recursos, y lista explícita de caminos de código que nunca
corrieron en la condición real.

BUG 1 (CRÍTICO): descubrimiento del injury report arrancaba en
11_45PM con budget 20 (hasta 07_00PM); a las 15:00 ET el último corte
es 03_00PM (índice 35). En temporada: feed caído declarado los 72
días, job sin archivar nunca (el WARNING diario "benigno de offseason"
era estructural). Cuatro verificaciones verdes sin la condición real
(spike sobre fecha pasada completa, offseason sin PDFs, dry-run de
fecha futura, pretemporada sin partidos 002). FIX: candidatos desde la
hora ET actual cuando la fecha es hoy, reloj inyectado por el borde;
fecha futura = 0 sondas; job archiva el último corte de ayer + el de
hoy si existe. Deploy predictions-api v12 (predictions-api-00013-cvk), ingest-job v7.
Verificación en vivo pretemporada: ingest-job, ejecucion manual nba-ingest-job-45ctc a las 16:10 UTC (12:10 ET): HOY (10-11) sondeado desde 12_00PM (antes: 11_45PM), AYER (10-10) desde 11_45PM; sin PDF en ninguno, exit 0. Control manual: 6/6 cortes de mediodia de pretemporada (10-08 y 10-10) dan 403 contra 200 del corte 2026-03-13_01_15PM: la NBA NO publica reporte en pretemporada -> 'pretemporada sin reporte' (verde, segundo caso). Endpoint sin ?date: 200 heartbeat en 2.25 s, pero la busqueda NO corre (retorno temprano sin partidos 002, antes del paso del injury report), asi que la ruta del ENDPOINT solo es verificable en vivo desde el 21-oct; ?date de una fecha pasada tampoco la ejercita (schedule CDN de 2026-27, 0 partidos). Suite 926 passed, 15 deselected (905 + 21 nuevos).

BUG 2 (ALTO): el webhook de n8n responde 500 desde 2026-09-24 (17
días): rama false del IF vacía + Respond "When Last Node Finishes" +
"First Entry JSON" = sin item = 500. El mensaje SÍ se publica; la
señal del Scheduler queda en rojo permanente y un fallo real sería
indistinguible. ERROR PROPIO (octavo): la rama desnuda se recomendó
en el chat de diseño afirmando que el 200 sobrevivía. CORRECCIÓN
DOCUMENTAL: el bloque D-PROD-1c CERRADA dice "disparo de prueba
post-publicación: verde, status {}"; los logs muestran un 200 de
0.109 s (incompatible con la cadena; limbo o warmup) seguido de 500.
La afirmación se RETIRA. REGLA: el 200 del Scheduler certifica la
cadena solo si su duración es compatible con ella; "verde" entra al
registro solo con log literal (status + duración). FIX (UI, Antonio):
Webhook Response Data = "No Response Body"; republicar; disparo de
prueba con lectura del log; verificar que fallo de nodo sigue dando
no-2xx. PROHIBIDO añadir reintentos (duplicarían publicaciones).
Estado: PENDIENTE.

BUG 3 (ALTO): ingest-job con 512Mi; paso 2 (rebuild de features) jamás
corrió en la nube; pico local 586.6 MiB. El 22-oct moriría y con él el
reentrenamiento del paso 3. FIX: 2Gi + flag --force-features +
ejecución única en la nube antes del 21 con verificación nivel 2
(parquet reconstruido idéntico en contenido al vigente). Estado:
PENDIENTE.

BUG 4 (MEDIO): odds-capture no filtra eventos iniciados ni persiste
commence_time; evening/late en octubre caen post-tipoff y el MERGE
sobrescribiría odds previas con odds en vivo (verificación
pre-registrada en D-EXP-1 convertida en invariante). FIX: filtro
commence_time > ahora en captura + columna commence_time. Estado:
PENDIENTE.

PRETEMPORADA, desviaciones de pre-registro: (a) market_odds sin filas
matched=false: The Odds API no lista pretemporada; expectativa
fallida por razón benigna, registrada. (b) sin PDFs de pretemporada
en el archivo: confundido con el bug 1; se re-evalúa tras v12. (c) 5
eventos del 4-oct que no reaparecieron: sin explicar, baja prioridad.

CALENDARIO: v12 el 13 como tarde; bug 3 el 13-14; UI del bug 2 el 13;
bug 4 el 14-15; del 16 al 20 CONGELADO (solo verificación pasiva). Lo
no verificado el 17 se difiere a post-ventana, salvo el bug 1.

[NOTA DE EJECUCION D-PRE-1 (2026-10-11), de Code — desviaciones y hallazgos
de la tarea, añadida tras el bloque transcrito verbatim:
(1) TAG: el bloque dice "ingest-job v7" porque asi venia el encargo, pero el
tag v7 YA EXISTIA: D-ODDS construyo ingest-job:v7..v10 el 2026-09-20/21 y
nba-odds-capture corre sobre ingest-job:v10. Por la convencion (vN
incremental, jamas reescribir un tag) la imagen desplegada es
ingest-job:v11 (build a71df5d0, 2026-10-11). nba-odds-capture NO se toco
(sigue en v10, bug 4 es tarea aparte); memoria del job sin tocar (512Mi,
bug 3).
(2) HALLAZGO LATERAL — EL CATALOGO DE NOMBRES NUNCA SE MANTUVO: hasta hoy
nba-ingest-job corria ingest-job:v6 (2026-08-28). El mantenimiento de la
tabla players por el job (D-PROD-1d, 2026-09-24: "mantenida por el ingest
job, paso 1, best-effort") se escribio y testeo pero JAMAS se desplego: la
tabla seguia congelada en el backfill (1 660 jugadores). Un novato de
2026-27 con minutos de titular habria entrado a los destacados sin nombre
en el catalogo. v11 se construyo desde el arbol actual y lo despliega como
efecto colateral (diff v6..v11 del codigo del job: _collect_player_names +
_persist_player_names, aditivo; storage con metodos nuevos). Primer
ejercicio real: 22-oct, cuando haya partidos nuevos. REGLA DERIVADA: un
bloque que dice "mantenido por el job" no esta cerrado hasta que el job
desplegado lo contiene — verificar la imagen que corre, no el codigo del
repo.
(3) PRE-REGISTRO (b) de pretemporada ADJUDICADO: no hay PDFs porque la NBA
no publica reporte en pretemporada (6/6 403 en cortes de mediodia contra
200 del control); no era el bug 1.
(4) TESTS: la simulacion del servidor usa el retraso de creacion medido
(corte 12_00AM creado a las 00:00:04). Con el, a las 15:00:00 exactas el
03_00PM aun no existe y se cae al 02_45PM en 2 sondas; a las 08:00:00 al
07_45AM. Los tests exigen "ultimo corte existente en <= 2 sondas", que es
lo que el encargo pedia con sus dos ejemplos. 21 tests nuevos en
tests/test_injury_discovery_clock.py, rojos antes del fix (21/21) y verdes
despues; ningun test existente editado. Ruff: 3 avisos preexistentes en
HEAD, ninguno nuevo.
(5) OBSERVACION SIN CORREGIR (fuera de alcance): server.py toma la fecha
por defecto de date.today() en UTC. Una invocacion manual sin ?date
despues de las 20:00 ET (00:00 UTC) pediria la fecha de MAÑANA. El cron de
las 13:00 CDMX no se ve afectado.]

## D-PRE-1 / BUG 2 CERRADO (2026-10-11): webhook 500 resuelto con
nodo Respond; diagnóstico corregido

CAUSA REAL (corrige el diagnóstico de la revisión): el Webhook YA
tenía "No Response Body"; el 500 lo produce n8n con Respond "When Last
Node Finishes" cuando el último nodo ejecutado (IF por rama false
vacía) no emite ningún item ("No item to return was found"),
independientemente del modo de cuerpo. La rama desnuda recomendada en
el chat de diseño era incompatible con ese modo de respuesta.

FIX (UI, Antonio): Webhook con Respond "Using Respond to Webhook
node"; nodo Respond to Webhook (texto fijo, 200) con DOS entradas: la
salida del Send del mensaje 2 (rama true) y la rama false del IF. El
200 vuelve a certificar cadena completa en ambas ramas; lo que la rama
false lleva es plomería de respuesta, no contenido (Decisión 10
intacta).

EVIDENCIA LITERAL (request log de n8n, status y latencia):
  2026-10-11T16:20:31Z  500  2.40 s   antes del fix (rama false)
  2026-10-11T16:30:56Z  200  2.09 s   fix, URL limpia, rama false,
                                      heartbeat en canal
  2026-10-11T16:32:41Z  200  74.24 s  fix, ?date=2026-10-21 temporal,
                                      rama true, DOS mensajes en canal
                                      (cold start incluido, consistente
                                      con el dry-run de septiembre)
Parámetro de fecha revertido y republicado con URL limpia (verificado
en la versión publicada por Antonio).

REGLA CONSOLIDADA: "verde" del Scheduler entra al registro solo con
status Y latencia literales; latencia de décimas de segundo es
incompatible con la cadena y se trata como fallo aunque el status sea
200. El bloque D-PROD-1c CERRADA queda corregido en este punto (ver
D-PRE-1). Señal del Scheduler restaurada para la auditoría operativa
de la ventana: 17 días de 500 pre-ventana registrados, fuera del gate.

## D-PRE-1 ADDENDUM (2026-10-11): regla "de record" CONGELADA antes
del primer dato; latencia del endpoint medida; fecha futura verificada

REGLA "DE RECORD" (precisión de la definición de predictions_log,
escrita ANTES de que exista un partido de la ventana; no renegocia el
criterio, cierra una ambigüedad de implementación del query):
servida de record del partido G con fecha D = entre las filas con
game_id = G cuya predicted_at_utc convertida a America/Mexico_City cae
en la fecha calendario D, la de predicted_at más cercana a las 13:00
CDMX dentro de [12:50, 13:30]; si ninguna cae en esa ventana, la más
temprana de ese día (rescate manual: fallo de autonomía en el gate
operativo, regla ya escrita); si ninguna cae en D, el partido no tiene
predicción de record (fallo de publicación, gate operativo). Misma
regla para player_predictions_log por (game_id, player_id).
CONSECUENCIAS: las 99 filas de dry-run con game_date=2026-10-21
(88 del 09-24, 11 del 10-11) y sus 198 de destacados quedan EXCLUIDAS
por fecha de predicción sin borrar nada (append-only; son evidencia de
los dry-runs); las llamadas manuales nocturnas que por date.today en
UTC devuelven el día siguiente quedan excluidas; servidas extra del
mismo día (warmup, rescate) no crean ambigüedad. MOTIVO: la lectura
"primera por game_date" habría elegido una fila del 24 de septiembre
como predicción de record del día uno.

LATENCIA DEL ENDPOINT (corrige la atribución del bloque del bug 2):
el 200 de 74.24 s del 2026-10-11 16:32Z correspondió a 72.61 s de
predictions-api, no a cold start de n8n (log: 16:32:42Z 200
72.614 s). El warmup de las 12:58 despierta n8n, NO predictions-api;
el frío del endpoint vive dentro del cron. Dentro del deadline de 300 s
con margen. PRE-REGISTRO: medir el 21-oct en el log de predictions-api;
si supera ~150 s, evaluar segundo warmup; nada antes de medir. El
dry-run de septiembre no dejó latencia registrada (deuda documental
menor).

FECHA FUTURA EN PRODUCCIÓN: verificada en v12 con log literal
("fecha FUTURA respecto a la hora ET actual ... 0 sondas").

## D-PRE-1 ADDENDUM 2 (2026-10-11): regla operativa de la ventana sobre
disparos manuales

Caso límite de la regla "de record": un disparo manual en [12:50,
13:30] CDMX del día de partido compite con el cron (13:00:02) y solo
ganaría a menos de 2 s de distancia. NO se cierra con código (exigiría
que n8n marque sus llamadas y el endpoint lo persista: cambio en
producción contra el congelamiento del 16-oct, por un riesgo
despreciable). SE CIERRA CON REGLA OPERATIVA: del 2026-10-21 al
2026-12-31, PROHIBIDO todo disparo manual del webhook, del Scheduler
de publicación o del endpoint con la fecha del día entre 12:50 y
13:30 CDMX; cualquier disparo manual en la ventana, a cualquier hora,
se registra con timestamp en CLAUDE.md el mismo día. Las verificaciones
con ?date de fechas pasadas o futuras siguen permitidas: la regla "de
record" las excluye por fecha de predicción. Si por error ocurriera un
disparo en la franja, la adjudicación de enero lo resuelve a favor de
la fila del cron identificada por latencia y hora exacta, y lo
declara.

## D-PRE-1 / BUG 3 CERRADO (2026-10-11): ingest job a 2Gi, paso 2
verificado en la nube
Memoria 512Mi -> 2Gi (revisión generación 9 de nba-ingest-job; imagen ingest-job:v12 en la generación 10). Ejecución forzada del paso 2 en
la nube: exit 0, duración 32.1 s (ejecución nba-ingest-job-tk7vt, 16:54:21 -> 16:54:53 UTC; paso 2 en 16.2 s), memoria RSS pico del proceso 515.4 MiB (ru_maxrss) — POR ENCIMA del límite anterior de 512Mi con 0 partidos nuevos, 25% de 2Gi; sin warnings de memoria en logs,
veredicto nivel 2 contra el parquet canónico: IDÉNTICO (9643 x 19 en ambos; SHA reconstruido 883900aa... vs canónico re-serializado 605e8d88..., difiere por serialización como en la Decisión 1). El flag escribe a nombre de verificación, jamás al canónico;
cadencia del paso 3 intacta. Suite 942 passed, 15 deselected (926 + 16 nuevos).
CORRECCIÓN DOCUMENTAL (append): D-PROD-1d declaró el mantenimiento del
catálogo players "mantenido por el ingest job"; el job siguió en v6
(2026-08-28) hasta v11 (2026-10-11): el mantenimiento se construyó y
verificó en local pero NUNCA se desplegó; la tabla quedó en 1 660
jugadores un mes. INSTANCIA 17 del patrón: "desplegado" sin jobs
update. REGLA: todo bloque que diga desplegado lleva tag o revisión
literal verificable con gcloud describe. Primer run real del catálogo:
2026-10-22.

[NOTA DE EJECUCION BUG 3 (2026-10-11), de Code:
(1) REVISION: los Cloud Run Jobs no tienen revisiones como los Services;
lo verificable con gcloud describe es metadata.generation. Memoria
aplicada en la generación 9 (512Mi -> 2Gi, resto heredado: 1 CPU,
timeout 1800 s, ingest-job-sa); imagen ingest-job:v12 en la generación 10.
v12 en lugar de v11 porque el flag es código nuevo (el encargo lo
preveía). nba-odds-capture sigue en ingest-job:v10, sin tocar.
(2) EL BUG ERA REAL, NO UNA PROYECCION: el pico medido dentro del
contenedor (515.4 MiB) supera los 512Mi incluso sin partidos nuevos; el
22-oct el job habría muerto por OOM. La métrica de Cloud Monitoring
container/memory/utilizations dio p99 ~8% (~164 MiB) para ese minuto: es
muestreada y NO captura un pico de segundos; el ru_maxrss del kernel es
la evidencia del pico y desde v12 el job lo loggea en cada paso 2.
(3) La medición de RSS se toma tras escribir el parquet y ANTES de cargar
el canónico para comparar; en modo normal no hay esa carga extra.
(4) Nivel 2 con UNA implementación: el job importa
_compare_features_content de scripts/rebuild_cloud.py (viaja en la imagen),
la misma función de la reconstrucción del 2026-08-14. Contenido distinto
en modo forzado = RuntimeError, exit != 0.
(5) Parquet de verificación BORRADO de GCS tras el veredicto (decisión
declarada: la evidencia vive en el log de la ejecución; un
features_v1_forcecheck.parquet junto al canónico invitaría a cargar el
equivocado). El canónico features_v1.parquet quedó intacto: 1 124 695
bytes, timestamp 2026-08-14T16:44:11Z, antes y después.
(6) Efecto colateral de la ejecución forzada: corrió también el paso 1
(0 partidos), el archivo del injury report (sin PDF, pretemporada) y la
evaluación del paso 3 (sin reentrenar, cadencia no cumplida). Ruff: 1
aviso preexistente en scripts/ingest_job.py (F401 google.cloud.storage),
ninguno nuevo.]

## D-PRE-1 / BUG 4 CERRADO (2026-10-11): market_odds solo pre-partido;
REVISIÓN DE PREPARACIÓN COMPLETA
Columna commence_time añadida (aditivo, filas previas NULL); captura
omite eventos con commence_time <= capture_ts (contador + log), clave
intacta. Contaminación previa: imposible (max(game_date) = 2026-12-25; la cifra que decide es min(game_date) = 2026-10-20 > hoy, ningún partido de la tabla había empezado, y max(capture_ts) previo = 2026-10-11 03:15 UTC; 0 filas con game_date < hoy vs
hoy 2026-10-11 (ET)). Deploy nba-odds-capture ingest-job:v13 (generación 5); ejecución manual: 83
filas con commence_time, 0 omitidos. Suite 953 passed, 15 deselected (942 + 11 nuevos).
PRE-REGISTRO 21-oct: late con omitidos > 0 y cero filas para
iniciados; publish con 0 omitidos. Cierra la verificación pendiente
de D-EXP-1 como conteo.
ESTADO D-PRE-1: 4 de 4 bugs cerrados con evidencia literal (bug 1 2026-10-11, predictions-api-00013-cvk + ingest-job:v11; bug 2 2026-10-11, UI n8n con log 200 de 2.09 s y 74.24 s; bug 3 2026-10-11, generación 10 de nba-ingest-job con 2Gi; bug 4 2026-10-11, nba-odds-capture ingest-job:v13).
LECCIÓN DE INSTRUMENTO (bug 3): Cloud Monitoring p99 muestrea y pierde
picos de segundos (mostró ~164 MiB con pico real de 515.4 MiB);
ru_maxrss del kernel es la evidencia y queda loggeado en cada paso 2.
CONGELAMIENTO: desde el cierre de este bloque hasta el 20-oct, cero
cambios a producción; solo verificación pasiva y el commit.

[NOTA DE EJECUCION BUG 4 (2026-10-11), de Code — detalle, desviaciones y DOS
HALLAZGOS que el bloque de arriba no contempla y que requieren adjudicación:
(1) ESQUEMA: ALTER TABLE ... ADD COLUMN commence_time TIMESTAMP con
descripción, ejecutado ANTES del deploy (el MERGE con la columna nueva
fallaría contra la tabla vieja). bq show: columna al final, TIMESTAMP.
264 filas previas, 264 NULL tras el ALTER. ACL de tabla INTACTA: mismo etag
BwZb8wgQmNs= antes y después, única binding nba-odds-capture-sa como
dataEditor.
(2) EJECUCION MANUAL nba-odds-capture-7xmrg (17:28 UTC, label evening): 46
eventos de la API, 1200 partidos en el índice, "Eventos iniciados omitidos:
0 de 46", "MERGE completado: 83 filas, 46 eventos emparejados, 0 sin match,
0 iniciados omitidos", exit 0. Las 83 filas ACTUALIZARON las evening
existentes (total de la tabla sigue en 264; 181 NULL = filas publish/late
aún no re-capturadas, se poblarán solas en la próxima captura de cada
label). 0 filas escritas con commence_time <= capture_ts. Cuota: 468
créditos restantes.
(3) CONTAMINACION: el placeholder pedía max(game_date) vs hoy, pero en una
tabla de odds de partidos FUTUROS el máximo siempre es futuro y no prueba
nada; la cifra decisiva es el MÍNIMO (2026-10-20 > 2026-10-11) más
"0 filas con game_date < hoy". Se completaron ambas.
(4) TAG: ingest-job:v13 (v12 lo usa ya nba-ingest-job desde el bug 3). El
repo de imágenes es COMPARTIDO por los dos jobs, así que "siguiente tag
libre" avanza por los dos. nba-ingest-job sigue en v12, sin tocar.
(5) TEST EDITADO, declarado: test_los_dieciseis_campos_del_schema pasó a
test_los_diecisiete_campos_del_schema (añade commence_time). Es la guarda
del schema y existe para moverse cuando el schema cambia; contorsionar el
código para no tocarla habría sido peor. 11 tests nuevos en
tests/test_odds_prepartido.py (iniciado excluido, futuro incluido, frontera
exacta excluida, contador, commence ilegible delegado al matching,
capture_ts naive = error, commence persistido con y sin match, columna
TIMESTAMP en el MERGE, filtro antes del matching con el MISMO reloj).
(6) HALLAZGO A — LA TEMPORADA REGULAR EMPIEZA EL 2026-10-20, NO EL 21.
Tres partidos el martes 20: 0022600001 BOS@DET (15:00 ET), 0022600002
PHI@NYK (19:00 ET), 0022600003 OKC@SAS (21:30 ET). Confirmado por dos
fuentes independientes (schedule CDN y The Odds API, mismos game_id y
horas). El dato estaba en el repo desde el 2026-09-20 (fixture
_SCHEDULE_REAL de tests/test_odds_logic.py: "0022600001", 2026-10-20) y
nadie lo leyó como "día uno". Todo el registro asume el 21 como primer día
real: el hito, los pre-registros de "primer día real", el "congelado del
16 al 20" y el PRE-REGISTRO de este mismo bloque. El 20 es día de
publicación real: dos mensajes, filas en ambos logs, primer ejercicio de
casi todo. La ventana del criterio (2026-10-21 -> 2026-12-31) es letra
congelada: los 3 partidos del 20 quedan FUERA de ella por su letra; no se
propone renegociarla. Adjudicación de Antonio pendiente sobre: mover el
"día uno" operativo y los pre-registros al 20, y si el congelamiento
termina el 19. Primera ejecución real del ingest job con partidos nuevos:
2026-10-21 12:00 UTC (no el 22).
(7) HALLAZGO B — PARTIDOS QUE EMPIEZAN ANTES DE LA PUBLICACION. La
publicación sale a las 13:00 CDMX (cron 13:00:02, mensaje ~15-20 s
después). Del calendario CDN, partidos 2026-10-20..12-31 con tipoff <=
13:00:20 CDMX: 7 de 481 (más 6 con hora TBD de NBA Cup):
  2026-10-20 BOS@DET 15:00 ET = 13:00 CDMX   (fuera de la ventana)
  2026-10-31 POR@UTA 15:00 ET = 13:00 CDMX
  2026-11-15 IND@PHI 13:00 ET = 12:00 CDMX
  2026-11-21 MIL@PHI 13:00 ET = 12:00 CDMX
  2026-12-25 SAS@NYK 12:00 ET = 11:00 CDMX
  2026-12-26 POR@WAS 13:00 ET = 12:00 CDMX
  2026-12-31 SAS@CHA 13:00 ET = 12:00 CDMX
Para esos partidos el sistema publicará y registrará en predictions_log una
predicción con predicted_at POSTERIOR al tipoff: no es pre-partido y
contradice la promesa de 13e-2.4 ("timestamp pre-tip-off verificable"). El
modelo no gana información (sus features no leen el partido en curso),
pero la evidencia deja de ser verificablemente previa. La regla "de record"
no los excluye. El fix de bug 4 ya los saca de market_odds (sus odds
publish quedarán omitidas como iniciadas), así que el par predicción/odd
de cierre tampoco existirá para ellos. Opciones a adjudicar, SIN
recomendación implícita: excluirlos del criterio por regla congelada
antes del dato (tipoff <= predicted_at = sin predicción válida); o cambiar
la publicación esos días (cambio en producción contra el congelamiento).
Nada se tocó.
(8) Ruff limpio en los 4 archivos tocados.]

## D-PRE-2 (2026-10-11): calendario real, exclusión de partidos
iniciados y marcador de producto. CONGELAMIENTO desde su cierre.

HALLAZGO A: la temporada regular empieza el MARTES 2026-10-20 (BOS@DET
15:00 ET, PHI@NYK 19:00, OKC@SAS 21:30; CDN y The Odds API coinciden;
el fixture lo tenía desde 09-20 y nadie lo leyó). El criterio de
comercialización (ventana 10-21 → 12-31) NO se toca: esos 3 partidos
quedan FUERA y se reportan en enero como "día cero". CALENDARIO
OPERATIVO CORREGIDO: primer día real con publicación de dos mensajes,
feed en vivo y filas en ambos logs = 10-20 (ensayo con partidos reales
fuera del criterio, el mejor dry-run posible); primer ingest con
partidos nuevos = 10-21; congelamiento hasta el 10-19 inclusive.

HALLAZGO B: 7 de 481 partidos (6 en ventana) tienen tipoff <= 13:00
CDMX: POR@UTA 10-31, IND@PHI 11-15, MIL@PHI 11-21, SAS@NYK 12-25
(11:00), POR@WAS 12-26, SAS@CHA 12-31, más BOS@DET 10-20 (día cero);
6 NBA Cup con hora TBD. Su predicción de record se emite DESPUÉS del
tipoff, rompiendo la promesa de timestamp pre-partido de 13e-2.4. El
modelo no gana información (las features no leen el partido), pero la
promesa sí se rompe.
REGLA PRE-REGISTRADA (antes de que exista dato): partido cuyo tipoff
real (gameDateTimeUTC del schedule CDN archivado) <= predicted_at_utc
de su servida de record queda EXCLUIDO del criterio predictivo (Brier
vs B0, calibración) y del CLV; contado y declarado en la adjudicación.
Computado en el query, log intocable. 6/~470 = 1.3%, sin efecto en
potencia. Consistencia: el filtro del bug 4 ya deja esos partidos sin
odds publish; el CLV los excluye solo. TBD de NBA Cup: se adjudican
por su tipoff real cuando se publique.
MARCADOR DE PRODUCTO (13e-2.5, degradación declarada): línea
"⏱ Iniciado al publicar · predicción sin información del partido en
curso" bajo el partido afectado, reloj inyectado por el borde, resto
del mensaje byte a byte intacto (goldens sin edición), campo
started_at_publish en data, logs sin cambio de esquema. Deploy
predictions-api v13 (predictions-api-00014-f6k); dry-run 2026-10-20: 200 en 31.3 s; 3 partidos (BOS@DET 13:00 CDMX, PHI@NYK 17:00, OKC@SAS 19:30); started_at_publish=False en los tres y CERO marcadores en el mensaje (reloj real del 10-11, los tres futuros); tipoff_tbd_count=0; DOS mensajes (players_message de 441 caracteres, 6 destacados con nombres reales); feed_down=True con razón "fecha FUTURA ... 0 sondas" (correcto para una fecha futura); filas: 3 en predictions_log con model_version poblado y 6 en player_predictions_log, served_by predictions-api-00014-f6k (excluidas por la regla de record: predicted_at 10-11). Suite
972 passed, 15 deselected (953 + 19 nuevos). VERIFICACIÓN EN VIVO PRE-REGISTRADA: 10-20, cron 13:00:02
CDMX vs tipoff BOS@DET 13:00:00: marcador ENCENDIDO solo en ese
partido, apagado en los otros dos. Cambiar la hora de publicación por
día: DESCARTADO (rompe la uniformidad de la regla de record y toca
producción sin necesidad).

CONGELAMIENTO: desde el cierre de este bloque hasta el 10-19
inclusive, cero cambios a producción (predictions-api v13, ingest-job
v12, odds-capture v13, n8n publicado), solo verificación pasiva y el
commit.

PRE-REGISTRO DEL DÍA CERO (10-20): dos mensajes; feed_down=False con
sufijo coherente con las 15:00 ET (True = hallazgo inmediato);
marcador en BOS@DET; 200 del Scheduler con latencia de decenas de
segundos medida en el log de predictions-api; filas en ambos logs con
model_version; market_odds publish con 0 omitidos y late con omitidos
> 0; 10-21: ingest con 3 partidos nuevos, paso 2 ejecutado con
ru_maxrss loggeado, catálogo players creciendo.

[NOTA DE EJECUCION D-PRE-2 (2026-10-11), de Code:
(1) POSICION DEL MARCADOR: "bajo el partido" se implementó como la línea
inmediatamente bajo el encabezado del partido (VISITANTE @ LOCAL · hora),
ANTES de la línea de probabilidad: el lector sabe qué está leyendo antes
de leer el número. Fijado en test con el texto exacto del mensaje.
(2) TBD, DOBLE SEÑAL: el payload real marca la hora indefinida con
gameStatusText "TBD" y una hora de relleno 00:00 ET = 05:00Z (verificado en
scheduleLeagueV2_2026-10-11.json, gameId 0022601201). Leída como real, esa
hora encendería el marcador el día del partido ("ya empezó" a las 23:00
CDMX de la víspera). Se detecta por el texto "TBD" O por la hora 00:00 ET
(ningún partido NBA empieza a medianoche ET); con TBD, tip_off_utc = None y
started_at_publish = None (sin marcador), contado en tipoff_tbd_count.
(3) UN SOLO INSTANTE POR REQUEST: server.py lee el reloj una vez y deriva
now_et (descubrimiento del injury report, D-PRE-1) y now_cdmx (esta regla)
del mismo instante. El sello de los logs (predicted_at_utc) sigue siendo
su propia lectura posterior, sin cambios; para la regla de exclusión eso
es conservador (un predicted_at más tardío excluye, nunca incluye de más).
(4) started_at_publish es bool | None: None = no determinable (sin reloj,
sin hora UTC, TBD). El marcador exige `is True`; None y False reproducen el
mensaje vigente byte a byte (test parametrizado contra el GamePrediction
sin el campo).
(5) FIXTURE REAL del día 20 recortado del schedule que el ingest job
archivó hoy en GCS (raw/schedules/scheduleLeagueV2_2026-10-11.json). El
recorte "real" de tests/test_future_schedule.py empezaba en el 21-oct
("semana inaugural") y omitía los tres partidos del 20: así se escondió el
hallazgo A dentro de un fixture que se presentaba como fuente de verdad.
(6) future_schedule.py lo usan también ingest-job (v12) y odds-capture
(v13); el cambio solo AÑADE campos al ScheduledGame, así que sus imágenes
desplegadas se comportan igual y no se reconstruyeron (prohibición del
encargo).
(7) OBSERVACION SIN CORREGIR (fuera de alcance, el resto del mensaje es
byte a byte): un partido TBD que llegara al día de publicación con la hora
aún sin definir mostraría "· 23:00 CDMX" en el encabezado (la hora de
relleno convertida). En la práctica la NBA define la hora de la NBA Cup
días antes; el contador tipoff_tbd_count lo delataría en data.
(8) TESTS: 19 nuevos en tests/test_started_at_publish.py; ningún test
existente editado (diff vacío en test_daily_predictions.py,
test_daily_players_section.py y test_server.py). Ruff limpio.]

## D-PRE-3 (2026-10-11): guarda de cobertura del parser en el endpoint
(opción b, adjudicada por Antonio). El congelamiento de D-PRE-2 empieza
DESPUÉS de este bloque.

RIESGO QUE CIERRA: el endpoint llamaba a parse_pdf() directo, sin
detección de layout ni guarda de cobertura. Su modo de fallo REAL ante un
generador distinto (D-RES-3, corpus iTextSharp) es devolver CERO filas SIN
lanzar. En producción eso publicaría "Bajas: –" en todos los partidos con
feed_down=False: la mentira silenciosa que 13e-2.5 prohíbe, invisible
porque los pre-registros del día cero esperan precisamente feed_down=False.
La NBA ya cambió de generador una vez entre temporadas (iTextSharp ->
GemBox, offseason 2023); el primer PDF 2026-27 no existe todavía y su
formato es desconocido.

DISEÑO: tras parsear (y tras archivar el snapshot, para que el PDF ilegible
quede como evidencia), el endpoint compara las filas del parser contra un
conteo INDEPENDIENTE de líneas con nombre "Apellido,Nombre" en la capa de
texto (extract_text + regex; nada de la maquinaria del parser — un conteo
circular no vigila nada, lección del 2026-08-22). Si filas < 80% de líneas,
ParseCoverageError -> el endpoint lo DECLARA como feed caído (13e-2.5 caso
2): se predice igual, con la línea de reporte no disponible y la razón en
data. Documento sin nombres (todos NYS) no dispara. PDF ilegible para el
contador = None, sin veredicto (parse_pdf ya habría fallado antes). Umbral
0.8 = el mismo de la guarda legacy (_MIN_COVERAGE): una sola vara. parse_pdf
NO se tocó; funciones nuevas en injury_report.py (count_name_lines,
check_parse_coverage, ParseCoverageError, PARSE_MIN_COVERAGE).

HALLAZGO DE DISEÑO: la regex de candidatas del parser legacy
(^[A-Z][^,]+, [A-Z]) exige espacio tras la coma; la capa de texto GemBox no
lo emite ("Allen,Jarrett"). Medido sobre las dos fixtures: CERO candidatas.
Reutilizada tal cual, la guarda habría estado MUERTA justo en el formato
vigente — un guardián que nunca ladra con aspecto de guardián. La nueva
regex acepta el espacio opcional y no va anclada al inicio de línea (la
primera fila de cada bloque lleva fecha, matchup y equipo delante).

EVIDENCIA (corpus archivado completo en GCS, 2 420 PDFs, solo lectura):
  iTextSharp (2018-12-17 .. 2023-04-09)  1 451 PDFs  DISPARA 1 451 (100%)
     parse_pdf devuelve 0 filas en TODOS, sin lanzar (ratio 0.000)
  GemBox     (2023-10-24 .. 2026-04-12)    969 PDFs  DISPARA     0 (0%)
     ratio filas/líneas: mín 0.923, p1 0.988, mediana 1.000, máx 1.000
     (peor caso 2023-12-04_01PM, 12/13); 1 PDF sin nombres, sin disparo
  Fixtures auditadas: el contador reproduce EXACTO los conteos oficiales
  de 13e-1 (73 y 160).
Cero falsos positivos en el formato vigente, detección total en el cambio
de formato real conocido, margen ancho contra el umbral.

Deploy predictions-api v14 (predictions-api-00015-w2n). Dry-run 2026-10-20: 200 en 27.16 s; 3 partidos, started_at_publish=False en los tres, cero marcadores, dos mensajes (players_message 441 caracteres), feed_down=True por 'fecha FUTURA ... 0 sondas' (correcto: la guarda no se ejercita sin PDF; su evidencia es el corpus); 3+6 filas de dry-run fechadas 10-11, excluidas por la regla de record.
Primera publicación autónoma con el fix del bug 2 (cron del 2026-10-11):
VERDE — Scheduler AttemptFinished 200 a las 19:00:22Z, webhook n8n 200 en 11.41 s (latencia compatible con la cadena), predictions-api 200 en 10.19 s en predictions-api-00014-f6k (v13), cero 'No item to return'; primer 200 autónomo del Scheduler desde 2026-09-24. Suite 995 passed, 15 deselected (972 + 23 nuevos en
tests/test_parse_coverage_guard.py; ningún test existente editado). Ruff:
2 avisos preexistentes en injury_report.py, ninguno nuevo.

LÍMITE DECLARADO: la guarda detecta el fallo de LECTURA (el parser no ve
filas que existen). NO detecta la misatribución (filas leídas con equipo o
estatus equivocados): eso sigue siendo dominio de la auditoría humana
(protocolo 13e-1). Por eso el primer PDF 2026-27 se coteja igual a mano en
cuanto exista (19 o 20 de octubre), antes de las 13:00 CDMX del 20.

CONGELAMIENTO: desde el cierre de este bloque hasta el 10-19 inclusive,
cero cambios a producción (predictions-api v14, ingest-job v12,
odds-capture v13, n8n publicado); solo verificación pasiva y el commit.

## D-PRE-3 ADDENDUM (2026-10-11): proceso del congelamiento y cotejo
del primer PDF de la temporada
PROCESO: la guarda de cobertura (v14) se desplegó durante el
congelamiento declarado en D-PRE-2 sin autorización explícita; el
contenido la justifica (categoría bug, 13e-2.5) y el deploy se ACEPTA;
el congelamiento se REINICIA desde predictions-api v14 (predictions-api-00015-w2n) hasta
el 10-19. REGLA: durante congelamiento, todo cambio a producción
requiere el "adelante" explícito de Antonio en el chat ANTES del
deploy, aunque sea una guarda; la opción de reportar y esperar cuesta
una hora, no días.
VERIFICACIÓN DEL BUG 2 EN CRON: 2026-10-11 19:00:22Z, Scheduler 200,
n8n 11.41 s, predictions-api 10.19 s, sin "No item to return". Primer
200 autónomo desde el 09-24.
LÍMITE DE LA GUARDA Y TAREA DEL DÍA CERO: la guarda detecta cero
filas, no misatribución. Cotejo humano del primer PDF de 2026-27 con
partidos del 10-20 (aparece el 19 por la tarde o el 20 por la mañana;
el job de las 08:00 ET del 20 archiva el último corte del 19) ANTES de
las 13:00 del 20, con scripts/listado_pdf_vigente.py (solo lectura).
Protocolo 13e-1: equipos, estatus y NYS contra el PDF; si el generador
cambió, la guarda ya lo declara; si las filas están pero mal
atribuidas, es hallazgo inmediato y el 20 se publica con feed_down
forzado a mano SOLO si Antonio lo decide antes del cron (sería la
única intervención permitida, y se registra).

[NOTA DE EJECUCION D-PRE-3 ADDENDUM (2026-10-11), de Code:
(1) INSTRUMENTO: scripts/listado_pdf_vigente.py (funciones puras + CLI,
borrable; CloudDataStore explícito, solo lectura). Reutiliza las MISMAS
piezas del endpoint (parse_pdf, NameIndex sobre la tabla players,
_build_norm_team_map de daily_predictions, count_name_lines y
check_parse_coverage) y muestra fecha, hora, matchup, equipo del PDF,
equipo resuelto, coherencia equipo-en-matchup, jugador, nombre resuelto o
SIN MATCH, estatus y razón; NYS con fecha. "latest" ordena por (fecha,
minuto del corte), no por nombre: '01_15PM' < '11_45AM' alfabéticamente.
(2) PRUEBA DEL INSTRUMENTO contra el PDF más reciente del corpus GemBox
(2026-04-12_11_15PM): 230 filas del parser = 230 líneas con nombre, guarda
NO dispara, 0 equipos fuera de su matchup, 0 NYS, estatus Available=33 /
Out=197. Conteos sanos. 1 jugador SIN MATCH (Sorber,Thomas: Out por cirugía
de ACL, nunca jugó, no figura en ningún boxscore y por tanto tampoco en el
catálogo players; benigno).
(3) HALLAZGO — BUG LATENTE EN PRODUCCIÓN, CAZADO POR LA PRUEBA DEL
INSTRUMENTO: "equipos sin resolver = 7" filas, todas de dos equipos. El
mapa de equipos del endpoint (_build_norm_team_map + _normalize_name, un
normalizador pensado para NOMBRES DE PERSONA) no resuelve dos tokens del
PDF GemBox:
    'Philadelphia76ers' -> 'philadelphiaers'  vs clave 'philadelphia ers'
    'LAClippers'        -> 'laclippers'       vs clave 'los angeles clippers'
(los demás, p.ej. 'LosAngelesLakers' y 'NewYorkKnicks', sí resuelven).
En daily_predictions._fetch_absences, un equipo sin match se DESCARTA con
un WARNING y su NYS se pierde sin rastro. Consecuencias para todo partido
de PHI o LAC: sus Out no entran a availability_diff, el mensaje publica
"Bajas PHI: –" / "Bajas LAC: –", un NYS de esos equipos no se declara, y
la elegibilidad de destacados no ve sus estatus (un Out podría salir como
destacado). Alcance medido en el calendario CDN: 62 partidos entre el
10-20 y el 12-31, 61 dentro de la ventana (12.8%), empezando por PHI@NYK
el DÍA CERO. Silencioso: aspecto sano, sin error, solo un WARNING en logs.
GENEALOGÍA: el problema se conocía desde D-EXP-1 (2026-09-21: "_normalize_name
del parser es para personas ('Philadelphia 76ers' -> 'philadelphia ers');
el experimento usa normalizador alfanumérico propio con alias LAClippers")
y se resolvió DENTRO del script del experimento, sin portarlo al endpoint.
Misma familia que el catálogo players de D-PROD-1d: el arreglo vive en un
script y nunca llega a producción.
REMEDIO DISPONIBLE, NO APLICADO: el repo ya tiene un normalizador que
resuelve ambos (odds_logic.normalize_team: alfanumérico + alias
laclippers -> losangelesclippers; verificado: 'Philadelphia76ers' y
'Philadelphia 76ers' -> 'philadelphia76ers'; 'LAClippers' y 'Los Angeles
Clippers' -> 'losangelesclippers'). Usarlo en el mapa de equipos del
endpoint es un cambio pequeño, pero es cambio a producción durante el
congelamiento: por la REGLA de este mismo bloque, requiere el "adelante"
de Antonio antes del deploy. No se tocó nada.
(4) OBSERVACIÓN SOBRE "feed_down forzado a mano": hoy NO existe un
mecanismo para forzarlo sin desplegar (ni env var ni parámetro del
endpoint). Si el cotejo del 20 lo exige, forzarlo sería un deploy o un
cambio de configuración; conviene decidir el mecanismo ANTES del 20 para
no improvisarlo bajo reloj.]

## D-PRE-4 (2026-10-11): normalización de equipos del endpoint y
interruptor manual de feed. v15. Congelamiento reiniciado.
BUG (instancia 19): el mapa de equipos del endpoint normalizaba
"Philadelphia76ers" y "LAClippers" con el normalizador de personas
("philadelphiaers", "laclippers") y no coincidía con teams; las filas
se descartaban con WARNING: bajas de PHI y LAC ignoradas, "Bajas: –"
falso, NYS no declarado, destacados sin estatus. 61 partidos de la
ventana (12.8%), desde PHI@NYK del día cero. D-EXP-1 lo resolvió
dentro de su script el 09-21 y nunca llegó al endpoint (misma
subfamilia que el catálogo players sin desplegar). REGLA: todo ajuste
de instrumento en un script que corrija algo que también vive en
producción genera ticket nombrado el mismo día; "resuelto en el
script" no es resuelto. FIX: odds_logic.normalize_team como única
verdad; test 30/30; equipo sin resolver = degradación declarada, no
WARNING. Listado tras el fix: scripts/listado_pdf_vigente.py latest (2026-04-12_11_15PM): 230 filas del parser = 230 líneas con nombre, guarda no dispara, equipos sin resolver 0 (antes: 7 filas, todas de PHI y LAC), 0 equipos fuera de su matchup; Philadelphia76ers -> Philadelphia 76ers, LAClippers -> Los Angeles Clippers.
INTERRUPTOR MANUAL: NBA_PREDICTOR_FORCE_FEED_DOWN=1 declara feed caído
sin código ni build. Activar: gcloud run services update
predictions-api --update-env-vars NBA_PREDICTOR_FORCE_FEED_DOWN=1
--region=us-south1 --project=predictorsnonprod. Desactivar:
--remove-env-vars NBA_PREDICTOR_FORCE_FEED_DOWN. Todo uso en la
ventana se registra con timestamp (es intervención manual: cuenta
como degradación declarada, no como fallo de autonomía, porque la
publicación sigue siendo autónoma).
PROCESO: hallazgo reportado y deploy autorizado explícitamente antes
de ejecutarse (regla de D-PRE-3 cumplida). Deploy v15 (predictions-api-00016-mcn). Suite
1012 passed, 15 deselected (995 + 17 nuevos). Congelamiento REINICIADO desde v15 hasta el 10-19.

[NOTA DE EJECUCION D-PRE-4 (2026-10-11), de Code:
(1) LISTAS REALES EN EL TEST: los 30 nombres de la tabla teams (BigQuery,
2026-10-11) y los 30 tokens que parse_pdf extrae del PDF GemBox vigente
(corpus archivado). 30/30 resueltos y biyectivos (ningún equipo colapsa
con otro). El PDF vigente escribe "LosAngelesLakers", no "LALakers"; el
alias "lalakers" se AÑADIÓ a odds_logic._ALIAS porque el encargo lo
exigía y D-EXP-1 registró esa forma en el corpus (un alias de más no
rompe ningún match). nba-odds-capture NO se reconstruyó: sigue en
ingest-job:v13 y tomará el alias en su próximo build.
(2) EQUIPO SIN RESOLVER, DISEÑO: el token llega con su matchup del PDF
("PHI@NYK"); el partido del schedule con ese matchup se marca con
"disponibilidad sin confirmar" en AMBOS equipos (marcador del caso NYS),
porque se sabe que faltan filas de ese partido pero no de qué lado. Las
bajas que SÍ se resolvieron siguen aplicándose al modelo; solo cambia lo
que el mensaje afirma. data gana unresolved_team_count y
unresolved_team_tokens. LÍMITE: un NYS con equipo sin resolver no trae
matchup y no puede atribuirse a un partido; se cuenta en data y se loggea
como ERROR, sin marcar ningún partido. Con 30/30 no ocurre hoy.
(3) INTERRUPTOR: la env var se lee en server.py por request y viaja como
force_feed_down=True a build_daily_predictions; la función pura no lee
el entorno (test). Con el interruptor activo no se descubre, no se
descarga ni se parsea el PDF (test: discover jamás llamado), y su razón
manda sobre la del catálogo vacío. Hoy la env var NO está definida en el
servicio (interruptor apagado, verificado con describe).
(4) DRY-RUN ?date=2026-10-20 en v15: 200 en 27.7 s, 3 partidos, cero
marcadores, tipoff_tbd_count 0, unresolved_team_count 0, feed_down por
"fecha FUTURA ... 0 sondas"; message y players_message IDÉNTICOS byte a
byte a los del dry-run en v14. Goldens sin edición (diff vacío en los
archivos de tests de formato y servidor). 17 tests nuevos en
tests/test_team_resolution_and_force.py. Ruff limpio en lo tocado.
(5) DESLIZ DE ORDEN, declarado: el deploy se lanzó leyendo solo el exit
code 0 de la suite, antes de ver su línea literal; la línea (1012 passed)
se leyó inmediatamente después. Sin consecuencia, pero el orden correcto
es línea literal primero, deploy después.]

## D-PRE-5 (2026-10-19): 200 del webhook ≠ entrega; n8n intacto, la
auditoría diaria verifica el canal

HALLAZGO: el 2026-10-18 el nodo Telegram registró ECONNRESET
(19:00:18Z) y el webhook respondió 200 en 15.9 s; el mensaje SÍ llegó
al canal (confirmado por Antonio): Telegram procesó el envío y se
perdió la respuesta. Un fallo real de envío también devolvería 200,
así que el 200 del Scheduler certifica que la cadena corrió, NO que el
mensaje se entregó.
DECISIÓN (Antonio): no se toca n8n durante la ventana. Reintentos
DESCARTADOS (Telegram no es idempotente; el 10-18 habría duplicado).
La entrega se verifica en la auditoría operativa diaria: mensaje
presente en el canal (o ejecución verde en Executions de n8n). Un día
sin mensaje en el canal cuenta como fallo de autonomía aunque el
Scheduler marque 200.
COSMÉTICO (post-congelamiento): el log "Injury report encontrado" usa
%d con Content-Length (str) y emite un traceback de logging sin efecto
(primer hit real: ingest 2026-10-19 12:00:18Z, PDF archivado igual).

## D-PRE-6 (2026-10-20): destacados por EQUIPO ACTUAL; traspasados con su
equipo nuevo y sus últimos 10. v16. Congelamiento reiniciado.

HALLAZGO (Antonio, día cero, mensaje 2 publicado): Jaylen Brown salió como
destacado de BOS; fue traspasado a PHI en el verano. Ningún componente usa
el roster actual: el sistema no tiene fuente de plantillas y deduce el
equipo de los partidos jugados. Para los destacados era BUG, no el rezago
aceptado: la regla vieja ("último partido CON ESTE equipo dentro de 250
días", D-PROD-1f) lo mantenía como candidato de BOS hasta el 2026-12-16
(último partido con BOS 2026-04-10 + 250), porque sus 10 partidos con
Boston nunca se renuevan. Contradice la adjudicación de D-PROD-1c
("anomalía transitoria, se disuelve en 2-3 semanas cuando la ventana
rueda"): para un traspasado la ventana del equipo viejo NO rueda.
Discriminante del criterio: contradice lo escrito = bug, se corrige de
inmediato. INSTANCIA 20 del patrón: la recencia "por equipo" se diseñó
contra el caso Luol Deng y nadie la ejercitó contra un traspaso de verano.

REGLA NUEVA (adjudicada por Antonio): (1) equipo actual de un jugador =
equipo de su fila MÁS RECIENTE en toda la liga, cualquier fila incluida
la de 0 minutos (fila = activado, Decisión 9); (2) candidato solo de ese
equipo, con la recencia de 250 días medida sobre esa fila; (3)
estadísticas = sus ÚLTIMOS 10 partidos jugados con CUALQUIER equipo.
Consecuencia buscada por Antonio: un traspasado (Brown, LeBron) aparece
como destacado de su equipo NUEVO con sus números reales recientes,
aunque los haya hecho con el anterior. Coherente con el contrato
congelado ("top 1 por minutos de los últimos 10 jugados"): el "con ese
equipo" era del código, no del contrato. El modelo NO cambia: sus
features arrastran plantillas del año previo exactamente como en el
entrenamiento (walk-forward 0.63138 incluye ese efecto).
LÍMITE DECLARADO: antes de la primera fila con su equipo nuevo, el sistema
no puede saber que el jugador cambió de equipo; en el primer partido de
cada equipo un traspasado puede salir todavía con el anterior. Cerrarlo
exige una fuente de rosters actuales (spike post-ventana; stats.nba.com
bloqueado en datacenter, el CDN usado no la expone).

EVIDENCIA: 8 tests nuevos en tests/test_highlights_current_team.py con
ids reales (BOS/PHI/Brown/Maxey): ROJOS contra el módulo anterior (5 de 8;
los 3 que pasan son el límite declarado, la recencia y el estatus) y
verdes con el fix; los 63 tests de destacados preexistentes pasan sin
edición. Suite 1020 passed, 15 deselected (1012 + 8). Ruff limpio.
Deploy predictions-api v16 (predictions-api-00017-9bs, 1Gi). Dry-run
?date=2026-10-21: 200 en 66.2 s, 11 partidos, 22 destacados, 0 equipos sin
resolver, sin errores de memoria (filas de dry-run fechadas 10-20,
excluidas por la regla de record). Brown debutó con PHI esta noche; desde
el ingest del 21 su fila más reciente es de PHI y deja de ser candidato de
BOS.

PROCESO: hallazgo reportado y deploy autorizado explícitamente por Antonio
antes de ejecutarse (regla de D-PRE-3). Congelamiento REINICIADO desde v16.

DÍA CERO (2026-10-20), verificación pre-registrada, resultado: Scheduler
200 (19:00:59Z), n8n 200 en 47.95 s, predictions-api 200 en 45.87 s
(00016-mcn, frío); corte del injury report 03_00PM descubierto y archivado;
guarda 26/26; 0 equipos sin resolver; los 6 equipos del día entregaron
(ningún NYS para el 20); predictions_log 3 filas a las 19:00:45Z con
v1_logistic_bclean_2026-10-17 y ausencias aplicadas en los 3 partidos
(PHI incluida, el caso de D-PRE-4); player_predictions_log 6 filas con
nombres reales; job archivó 2026-10-19_11_45PM (política de "ayer").
market_odds publish: "0 iniciados omitidos" CORRECTO — The Odds API
movió el tipoff de BOS@DET a 19:10Z (CDN: 19:00Z) y la captura fue a las
19:00:21Z; 0 filas de market_odds capturadas tras su commence_time.
MATIZ (sin cambio en ventana): el marcador ⏱ usa la hora PROGRAMADA del
CDN; el balón probablemente subió después de la publicación. Es
conservador (nunca afirma "previo" en falso); afinar el texto queda para
post-ventana. Aviso de sklearn "fitted without feature names" en el log:
existe desde 00008-2bw (2026-09-24), no es nuevo.

## D-PRE-6b (2026-10-20): cotejo, rollback de la supresión y LIMITACIÓN
ACEPTADA del primer partido

1. COTEJO (Antonio, fuente nba.com/news/nba-offseason-deals-2026 del
   2026-10-19): 4 de 22 destacados del dry-run del 21 con equipo viejo
   por traspaso de verano:
     jugador          mostrado   está en   movimiento
     Julius Randle    MIN        BKN       trade a 4 equipos, julio
     Brandon Ingram   TOR        LAC       trade por Kawhi, 30-jun
     Kawhi Leonard    LAC        TOR       mismo trade
     Cody Williams    UTA        MIN       trade, 29-ago
   Los otros 18 sin movimiento registrado.
2. DECISIÓN (Antonio): se descarta la supresión del mensaje 2 (flag
   NBA_PREDICTOR_HIGHLIGHTS_ENABLED, implementada y revertida sin
   desplegar; v17 construida, nunca servida, tag no reutilizable).
   Producción sigue en v16 (predictions-api-00017-9bs) con la regla de
   D-PRE-6.
3. LIMITACIÓN ACEPTADA Y DECLARADA, vigente para toda la ventana: un
   jugador traspasado sale con su equipo anterior hasta su primera fila
   de boxscore con el nuevo; después sale con el nuevo y sus últimos 10
   partidos de cualquier equipo. Es consecuencia de diseño (el sistema
   no tiene fuente de plantillas y no afirma lo que no ha visto), NO
   bug: las publicaciones del 21 al 24 con esos 4 casos NO computan para
   el gate operativo (b). Discriminante del criterio aplicado: esto es
   lo que CLAUDE.md dice desde hoy; una publicación que lo contradiga sí
   sería bug.
4. La publicación del 2026-10-20 (Brown en BOS) es anterior a la ventana
   y no computa para ningún gate. Instancia 20 del patrón.
5. D-PRE-8 (mejora, primera semana de ventana, con pre-registro propio):
   señal de plantilla desde boxscores de PRETEMPORADA (ingesta a
   raw/boxscores_preseason/ y tabla roster_signals; jamás a games ni
   features) y desde el equipo del injury report. Reduce el desfase al
   primer partido de pretemporada visto. Habría resuelto los 4 casos
   antes del 21. Spike previo: disponibilidad del liveData/boxscore
   pre-partido a las 13:00 CDMX (solo HEAD).
6. PENDIENTE DE PRE-REGISTRO antes del primer caso en ventana: regla de
   evaluación de Brier para partidos con tipoff programado <=
   predicted_at_utc (propuesta: fuera del primario, dentro del
   secundario).
7. Verificación pendiente de Antonio: Devin Carter bajo BOS en el PDF
   del 20 y bajo ATL en la página oficial de movimientos.

[NOTA DE TRANSCRIPCIÓN D-PRE-6b (2026-10-20), de Code — dos cruces con el
registro que conviene adjudicar, sin cambiar lo transcrito arriba:
(a) El punto 6 se cruza con D-PRE-2 (2026-10-11), que YA pre-registró
una regla para el mismo caso: "partido cuyo tipoff real (gameDateTimeUTC
del schedule CDN archivado) <= predicted_at_utc de su servida de record
queda EXCLUIDO del criterio predictivo (Brier vs B0, calibración) y del
CLV". La propuesta del punto 6 (fuera del primario, DENTRO del
secundario) no contradice la exclusión del primario, pero sí añade su
inclusión en un análisis secundario que D-PRE-2 no contemplaba. Si se
adopta, conviene redactarla como ENMIENDA explícita de D-PRE-2 y no como
regla nueva, para que no convivan dos textos sobre el mismo partido.
Primer caso en ventana: POR@UTA del 2026-10-31.
(b) "Tipoff programado" coincide con lo que mide D-PRE-2:
gameDateTimeUTC del CDN es la hora PROGRAMADA (BOS@DET 19:00Z), mientras
The Odds API registró el tipoff real a las 19:10Z (D-PRE-6, día cero).
Ninguna de las dos reglas usa la hora real de salto.
(c) Estado de producción verificado al escribir este bloque:
predictions-api-00017-9bs (imagen predictions-api:v16) al 100% del
tráfico; el código local de la supresión revertido con git
checkout de config.py, daily_predictions.py y server.py, y borrado
tests/test_highlights_suppressed.py; tests de destacados, mensajes y
servidor 127/127 en verde tras el rollback.]

- [CIERRE 2026-10-20] Punto 6 RETIRADO: D-PRE-2 (2026-10-11) ya lo
   cubre y prevalece: partidos con tipoff programado <= publicación
   quedan fuera del criterio y del CLV. La hora que gobierna es la
   PROGRAMADA del CDN, única disponible a las 13:00; la hora real de
   salto (The Odds API) es dato de mercado, no de calendario. Primer
   caso en ventana POR@UTA 2026-10-31: sin decisión nueva.
- [RESUELTO 2026-10-20] Devin Carter: SAC->ATL (trade, jun), ATL lo
   cortó (11-sep), BOS lo firmó (Exhibit 10, 14-sep; conversión
   14-oct). El PDF del 20 era correcto; la página oficial de movimientos
   NO lista waivers ni Exhibit 10. Lección: para plantillas, el injury
   report es más completo que la página de movimientos. Refuerza
   D-PRE-8.

## D-PRE-8 (PRE-REGISTRO 2026-10-20): señales de plantilla para destacados

Objetivo: reducir el desfase de plantilla del mensaje 2 con dos señales
que el sistema ya ve: boxscores de PRETEMPORADA y el equipo bajo el que
aparece cada jugador en el injury report. Filosofía intacta: equipo =
lo visto más reciente; solo se amplía qué cuenta como visto.
Alcance: SOLO el mensaje 2. games, team_game_stats, player_game_stats,
el parquet de features, ausencias y modelo no cambian. Test de no
contaminación obligatorio (hashes idénticos antes/después).
Fuente y almacén: partidos con prefijo 001 de la temporada efectiva,
mismo scheduleLeagueV2 y mismo endpoint liveData/boxscore vía S3; RAW a
raw/boxscores_preseason/{game_id}.json; tabla roster_signals
(player_id, team_id, game_id_o_snapshot, game_date, source ∈
{preseason, injury_report}, observed_at), MERGE idempotente. El injury
report alimenta la misma tabla con el equipo del PDF y la fecha del
corte, solo filas con match de player_id.
Regla: equipo actual = fila más reciente entre player_game_stats y
roster_signals. Estadísticas: solo temporada regular, últimos 10 de
cualquier equipo. Pretemporada da equipo, nunca números.
Ingesta: paso nuevo en ingest_job, best-effort (WARNING, nunca tumba la
ingesta regular). Backfill único de la pretemporada 2026 al desplegar.
Expectativa: tras el backfill, Randle (BKN), Ingram (LAC), Kawhi (TOR)
y Cody Williams (MIN) resuelven a su equipo correcto. Cobertura
(jugadores con fila regular 2025-26 que tienen señal de pretemporada
2026): conjetura >85%. Desviación = hallazgo.
Límite declarado: jugadores de pretemporada cortados antes del inicio
pueden conservar señal de un equipo donde no están; casi nunca son
candidatos (minutos bajos). Aceptado.
Prerrequisito: este spike (estructura del boxscore 001 == regular).
Deploy fuera de 12:50-13:30, congelamiento reiniciado, junto con
D-PRE-9.

## D-PRE-9 (PRE-REGISTRO 2026-10-20): dos destacados por equipo

Regla: top 2 por minutos rolling, mismos filtros de estatus y mínimo
de partidos que el top 1; columna rank (1|2) en player_predictions_log;
test de longitud del mensaje con el día de mayor carga del calendario
2026-27; si el mensaje excede 3 800 caracteres, recorte DECLARADO a
top 1 ese día con una línea que lo diga. Se despliega en el mismo
deploy que D-PRE-8.

## D-PRE-8 SPIKE RESULTADO (2026-10-20)

Pre-registro sellado en CLAUDE.md a las 01:44:35Z (2026-10-21 UTC) ANTES de
correr el spike. Script: scripts/spike_preseason_boxscore.py (funciones
puras + CLI; LocalDataStore explícito; solo lectura de GCS y del CDN/S3;
único archivo escrito: data/spike_preseason/report.json). 23 descargas en
total; cero escrituras en GCS o BigQuery. Corrido a las 19:46 CDMX.

TABLA a — partidos 001 en scheduleLeagueV2_2026-10-20.json (seasonYear
2026-27):
  partidos 001        67
  rango de fechas     2026-10-03 -> 2026-10-16
  con gameStatus 3    67 (todos jugados; ningún otro estado)

DIFF c — estructura del boxscore (liveData/boxscore vía S3):
  0012600009 (TOR vs MIA, 2026-10-03, primero de octubre) vs 0022600001
    (BOS@DET, 2026-10-20): diff VACÍO
  0012600063 (DEN@LAL, 2026-10-16, último jugado) vs 0022600001: diff VACÍO
  Niveles comparados: claves de nivel superior, game, team,
  team.statistics, player (13 claves), player.statistics (33 claves),
  valores de status. Las tres firmas: minutes en ISO 8601 (PT##M##.##S,
  p.ej. PT06M59.00S / PT04M29.00S / PT02M45.00S), status = {ACTIVE}.
  Normalizadores de la Decisión 9 (_normalize_cdn_team_stats y
  _normalize_cdn_player_stats) aceptan los tres payloads sin error:
  2 filas de equipo cada uno; 42 / 41 / 28 filas de jugador activo.

TABLA d — los 4 casos del cotejo en boxscores 001 de 2026-27 (ids desde
la tabla players por nombre; 21 partidos 001 involucran a BKN, MIN, LAC,
TOR o UTA y se revisaron TODOS, sin muestreo: 20 descargas para este
paso, bajo la cota de 40):
  jugador          player_id  apariciones 001  equipo visto  última fila 001
  Julius Randle       203944                4  BKN           2026-10-14, 27:45 min
  Brandon Ingram     1627742                2  LAC           2026-10-14, 0 min (ACTIVE)
  Kawhi Leonard       202695                3  TOR           2026-10-15, 22:34 min
  Cody Williams      1642262                6  MIN           2026-10-16, 27:23 min
  Ninguno aparece con su equipo anterior en ningún boxscore 001.

VEREDICTO:
  estructura compatible: SÍ (diff vacío en ambos pares; parser de la
  Decisión 9 acepta los payloads 001 tal cual).
  casos visibles en pretemporada: 4 de 4, todos con su equipo CORRECTO
  (el que nba.com lista tras los traspasos). La expectativa
  pre-registrada (los 4 resuelven tras el backfill) queda sostenida por
  el spike; se adjudica en la implementación con el backfill real.
NOTA: la última fila 001 de Ingram es de 0 minutos con status ACTIVE: es
señal de plantilla válida bajo la regla de D-PRE-8 ("activado" = visto)
y no aporta números (la pretemporada nunca da números). La conjetura de
cobertura >85% NO se midió aquí (fuera del alcance del spike); queda para
la implementación.

## D-PRE-8 + D-PRE-9 RESULTADO (2026-10-25): señales de plantilla y dos
destacados por equipo EN PRODUCCIÓN. Congelamiento reiniciado desde
ingest v14 / api v18.

PROCESO: visto bueno de Antonio el 2026-10-20 con condición previa (segunda
guarda de longitud, cumplida en local: suite 1081 passed, 15 deselected). La
Parte D estaba fechada para el 21 después de las 13:30 y NO se ejecutó ese
día; se pidió y obtuvo un "adelante" nuevo el 2026-10-25 (regla de D-PRE-3:
un permiso con fecha no se estira). Código en el commit f0e3ac9; ambas
imágenes construidas desde ese árbol limpio.

PASO 1 — DDL: tabla roster_signals creada con schema explícito (player_id,
team_id INT64; source, ref_id STRING; game_date DATE; observed_at TIMESTAMP;
todos REQUIRED). predictions-api-sa con roles/bigquery.dataViewer A NIVEL
TABLA, única binding (etag BwZepOAiUYQ=). ingest-job-sa escribe por su
dataEditor de dataset (necesario: el MERGE con staging crea tablas).
PASO 2 — ALTER: player_predictions_log gana rank INT64 como columna 17, al
final; verificado con bq show antes de seguir; 310 filas históricas con rank
NULL (= 1 implícito); ACL intacta (etag BwZcMQU3q4M=, única binding
predictions-api-sa dataEditor).
PASO 3 — ingest-job:v14 (build 9740da7d, generación 11 de nba-ingest-job,
2Gi heredado). Se ESPERÓ al cron de las 12:00 UTC (v12, 8 partidos nuevos
del 24, features reconstruidas, sin reentrenamiento) para que la ejecución
manual viera 0 partidos nuevos y los hashes fueran comparables. Ejecución
manual nba-ingest-job-wrt8j: exit 0 en 68 s; paso 1 con 0 nuevos; paso 1b
{preseason_terminados 67, preseason_nuevos 67, fallidos 0, señales
preseason 2513, pdfs 2, señales injury_report 142}; catálogo de nombres
actualizado (654 jugadores vistos; tabla players en 1 800); paso 2 omitido;
paso 3 sin reentrenamiento. Los dos ERROR del log son el traceback cosmético
del %d ya registrado en D-PRE-5.
  roster_signals: preseason 2 513 filas / 654 jugadores / 67 partidos
  (2026-10-03 -> 2026-10-16); injury_report 142 filas / 101 jugadores / 2
  snapshots (2026-10-24, 2026-10-25). raw/boxscores_preseason/: 67 objetos.
NO CONTAMINACIÓN EN LA NUBE (BIT_XOR(FARM_FINGERPRINT(TO_JSON_STRING(fila)))
+ COUNT, antes y después de la ejecución manual):
  games              14 465   3761456664913468706    idéntico
  team_game_stats    28 930  -6654812698214788786    idéntico
  player_game_stats 372 244   2030149853047931963    idéntico
  features_v1.parquet md5 kqWDm5+yOyiEWddMFF234w==, generación
                     1792929648344863 (el objeto no se reescribió)
  filas con prefijo 001 en las tres tablas STRUCTURED: 0.
PASO 4 — predictions-api:v18 (build c7508314), revisión
predictions-api-00018-cv8 al 100%, 1Gi, predictions-api-sa. Dry-run
?date=2026-10-26: 200 en 60.2 s; 9 partidos; mensaje 2 de 2 276 caracteres,
36 destacados (18 rank 1 + 18 rank 2), sin recorte (highlights_truncated
null), cero nombres "#"; model_version v1_logistic_bclean_2026-10-24;
feed_down por "fecha FUTURA ... 0 sondas" (correcto). Evidencia: 36 filas
en player_predictions_log (rank 1: 18, rank 2: 18, NULL: 0) y 9 en
predictions_log, served_by predictions-api-00018-cv8, game_date 2026-10-26
(excluidas por la regla de record: predicted_at 10-25).
VERIFICACIÓN DE QUE v18 USA LAS SEÑALES: la API no exporta INFO a Cloud
Logging, así que el log no lo prueba. Se recalcularon los destacados del 26
en local sobre los datos de la nube (CloudDataStore explícito, solo
lectura), con y sin roster_signals: la salida de v18 es IDÉNTICA al cálculo
con señales (2 655 leídas) y distinta al cálculo sin ellas en 3 de 9
partidos (MIA: Bez Mbeng por Davion Mitchell; PHX: Kennedy Chandler por
Miles Bridges; UTA: John Konchar por Bez Mbeng).

DESVIACIONES DECLARADAS:
(1) COBERTURA bajo la conjetura pre-registrada: 82.9% (490 de 591 jugadores
con fila regular 2025-26 tienen señal de pretemporada 2026; medido en el
backfill local del 2026-10-20) contra >85%. En rotación (>=20 min de media)
94.8% (275 de 290). De los 97 sin señal, 36 jugaron menos de 10 partidos
(llamados de abril: Okani, Andersson Garcia, Lucas Williamson...); el resto
incluye veteranos sin señal (Westbrook, Cam Thomas, Lonzo Ball, Vince
Williams Jr.), con la lectura probable de agentes libres sin firmar o
lesionados, NO verificada. Hallazgo, no fallo: la conjetura era optimista,
como las anteriores de la serie.
(2) LÍMITE NUEVO VISIBLE: las señales mueven jugadores de equipo pero no sacan
a nadie. Un jugador sin señal sigue siendo candidato por sus filas de abril
dentro de los 250 días (en el dry-run: Okani y Williamson en MEM, Garcia en
UTA). Se disuelve solo cuando la ventana de 10 partidos rueda con 2026-27
(criterio de selección adjudicado en D-PROD-1c, opción A). Cambiarlo es
cambio de contrato: backlog.
(3) LONGITUD: margen medido en el día de mayor carga de 2026-27 (2026-11-02,
15 partidos, empatado con 2026-12-23 y 2027-04-11). Con nombres de ~18
caracteres y sin marcas: 3 752 de 3 800 (48 de margen); el peor caso
sintético con dos por equipo mide 7 187 (recorte declarado a top 1 = 3 770).
SEGUNDA GUARDA (condición de Antonio): si aun con uno por equipo el mensaje 2
pasa de 4 000, se sustituye por "🏀 Destacados · {fecha}: omitidos hoy por
longitud del día.", data lleva highlights_truncated='omitted' y
player_predictions_log no registra nada (no se publicó ningún destacado); los
calculados siguen en data. Escalera: None / "top1" / "omitted".
(4) NOMBRES DE NOVATOS: el paso 1b escribe en la tabla players los nombres de
los boxscores de pretemporada (declarado; players no estaba en la lista de
prohibidos). En local, load_player_names lee también raw/boxscores_preseason/.
(5) DRY-RUN del 26 y no del 22 del encargo: el 22 ya había pasado y sus dos
partidos no tocaban a ninguno de los cuatro traspasos. No se hizo dry-run del
día en curso: escribiría filas fechadas hoy y, si el cron fallara, la regla
de record tomaría la más temprana (la del dry-run).
(6) TERCER LECTOR de scheduleLeagueV2 (preseason_played_games, prefijo 001):
los otros dos descartan la pretemporada por diseño. Crece el backlog del
doble parser.
(7) TESTS EXISTENTES EDITADOS (6): cinco con per_team=1 explícito porque
fijaban el contrato de un destacado por equipo, y la guarda del schema del
log de 16 a 17 campos.

DISPAROS MANUALES EN LA VENTANA (regla de D-PRE-1 addendum 2), fuera de la
franja 12:50-13:30 CDMX:
  2026-10-25T12:02:28Z  ejecución manual de nba-ingest-job (v14, backfill)
  2026-10-25T12:05:43Z  GET /predictions/today?date=2026-10-26 (dry-run v18)

REGISTROS ATRASADOS DE LOS PRE-REGISTROS DEL 21 (auditoría de solo lectura,
2026-10-25):
- Ingest con partidos nuevos 21-24: cuatro ejecuciones en verde; games
  2026-27 = 28 partidos hasta el 23 (3+11+2+12); paso 2 corrió con RSS pico
  535.9 MiB el 24 y 533.0 MiB el 25 (dentro de 2Gi); reentrenamiento por
  cadencia el 24 (v1_logistic_bclean_2026-10-24).
- Catálogo players creciendo: 1 660 -> 1 708 (al 24) -> 1 800 (tras v14).
- Latencia de predictions-api en el cron: 82.4 s (21), 37.7 s (22), 90.2 s
  (23), 69.3 s (24). Umbral de ~150 s NO alcanzado: sin segundo warmup.
- Servida de record diaria entre 13:00 y 13:01 CDMX, con model_version.
- market_odds: cero filas escritas con commence_time <= capture_ts en todo
  20-24; publish con 0 omitidos los cinco días; late con 1-5 omitidos;
  evening también con omitidos 1-5 (efecto EDT predicho en D-EXP-1).
  Verificación pre-registrada de D-PRE-1/bug 4 CUMPLIDA.
- Entrega en el canal 21-24: pendiente de confirmación de Antonio (D-PRE-5).

PRE-REGISTRO 2026-10-25, 13:00 CDMX (reprogramado del 22, que suponía el
deploy del 21): los cuatro traspasos juegan hoy (IND@BKN, LAC@OKC, TOR@MIN).
El mensaje 2 debe mostrar a Randle en BKN, Ingram en LAC, Kawhi en TOR y
Cody Williams en MIN, para los que entren en el top 2 de su equipo; dos por
equipo; sin recorte (7 partidos); filas con rank 1 y 2 en
player_predictions_log servidas por predictions-api-00018-cv8.

BACKLOG (post-ventana, sin tocar ahora):
- D-PRE-8b: filtrar señales por observed_at en vez de game_date < target_date
  (hoy se descarta el PDF de las 08:00 ET del propio día, anterior a la
  publicación; conservador, no leakage).
- Destacados extendidos (3 o 5 por equipo): 3 con recorte a 2 cabe sin
  recorte el 87% de los días; 5 exige varios mensajes con bucle en n8n.
- Redacción del título: "(últimos 10 partidos)" -> "últimos 10 de temporada
  regular" (no hay playoffs en la base; prefijo 002 únicamente).
- Cosmético del %d en el log del injury report (D-PRE-5).
- Unificación de los lectores de scheduleLeagueV2.

TRAMPAS DE ENTORNO NUEVAS (PowerShell 5 + bq): (a) una consulta multilínea
pasada como argumento a bq llega truncada a la primera línea ("Unexpected end
of script"); (b) pasada por pipe, PowerShell le antepone un BOM ("Illegal
input character \357") aun fijando $OutputEncoding sin BOM; (c) bq en Git
Bash no arranca (python3.12 no encontrado). Vía que funcionó: consulta de UNA
línea entre comillas simples, con las comillas internas de SQL duplicadas.
(d) El mensaje de commit con " -m " dentro de una sola cadena se fusiona en el
título (e924a8c y f0e3ac9): un -m por párrafo, cada uno con sus comillas.

Suite: 1081 passed, 15 deselected (1020 + 61: 43 de roster_signals y 18 de
dos destacados y guardas). Ruff limpio en lo tocado (1 aviso preexistente
F401 en scripts/ingest_job.py). Rollback disponible: predictions-api:v16
(revisión 00017-9bs) e ingest-job:v12; la tabla y la columna nuevas no
estorban a las versiones viejas.

CONGELAMIENTO: reiniciado desde ingest-job v14 (generación 11) /
predictions-api v18 (predictions-api-00018-cv8).

## D-PRE-10 (2026-10-25): destacado debe haber sido VISTO en la temporada;
jugadores fuera de la NBA publicados. v19. Congelamiento reiniciado.

HALLAZGO (cotejo de Antonio contra box scores de RealGM, 2026-10-25): Toby
Okani salió como destacado de MEM el 21 y el 23 sin equipo NBA (NYK lo cortó
el 30-sep), y Andersson Garcia de UTA el 23 jugando en el Río Breogán.
Verificado en BigQuery: los dos, y Lucas Williamson, con CERO señales de
2026-27 y CERO filas de esta temporada; última fila de marzo-abril de 2026,
dentro de los 250 días de recencia. Eran candidatos por sus filas de abril y
encabezaban a su equipo por minutos (suplentes de abril en equipos ya
eliminados). Contradice lo escrito en D-PROD-1f ("jamás un jugador rancio con
aspecto sano"): BUG por el discriminante del criterio, no limitación.
CORRECCIÓN DE REGISTRO PROPIO (append, no reescritura): el punto (2) del bloque
"D-PRE-8 + D-PRE-9 RESULTADO" de esta misma mañana describió este caso como un
límite que "se disuelve solo cuando la ventana de 10 partidos rueda". Es FALSO
para un jugador que no está en la NBA: su ventana nunca rueda porque nunca
vuelve a jugar. La lectura correcta es la de este bloque.

FIX (adelante de Antonio en el chat): un candidato debe tener al menos una
observación de la temporada en curso — fila de temporada regular desde el
inicio de la temporada o señal de plantilla (pretemporada o injury report) —
(player_highlights.observed_this_season). Solo con target_date y señales
cargadas y no vacías: si roster_signals no carga, la regla degrada a D-PRE-6
en vez de vaciar el mensaje 2 por un fallo de carga. Una señal de la
temporada pasada no cuenta. Ingram (solo señales) sigue siendo candidato.
Tests: 9 nuevos en tests/test_observed_this_season.py con ids reales (Okani
1643253, Williamson 1631351, Garcia 1643158, Morant 1629630, Ingram 1627742);
4 de 9 ROJOS contra el código anterior (las exclusiones de Okani, Williamson y
Garcia, y la señal de la temporada pasada) y 9/9 verdes con el fix. Suite
1090 passed, 15 deselected (1081 + 9). Ruff limpio en lo tocado.

DEPLOY: predictions-api:v19 construida a las 10:33 CDMX desde el árbol de
trabajo (f0e3ac9 + este cambio, SIN commitear al construirla). El deploy fue
bloqueado por el clasificador de permisos de Claude Code (despliegue a
producción) y NO se rodeó; no llegó antes de las 12:50. Aprobado por Antonio
y desplegado a las 16:29 CDMX: revisión predictions-api-00019-hq2, 100%,
1Gi. Dry-run ?date=2026-10-26 (disparo manual 2026-10-25T22:30:03Z): 200 en
57.7 s, 9 partidos, 36 destacados, 2 248 caracteres, sin recorte, cero "#",
ningún equipo con menos de dos. Diferencia contra v18 SOLO en MEM@UTA: salen
Okani, Williamson, Garcia y John Konchar; entran Ace Bailey, Lauri
Markkanen, Jahmai Mashack y Jerami Grant (cotejo de equipos pendiente de
Antonio).

PUBLICACIONES AFECTADAS (servidas de record, todas con v16/v18, antes del fix):
  2026-10-21  Toby Okani (MEM)
  2026-10-23  Toby Okani (MEM), Andersson Garcia (UTA)
  2026-10-25  Toby Okani y Lucas Williamson (MEM), Andersson Garcia y John
              Konchar (UTA), Russell Westbrook (SAC)
  (25-oct: 5 de 28 destacados sin ninguna observación de 2026-27; los que v19
  habría excluido. Konchar y Westbrook NO estaban en el cotejo de Antonio:
  por los datos parecen fuera de plantilla, sin confirmar.)
PENDIENTE DE ADJUDICACIÓN DE ANTONIO — gate operativo (b) de 13e-2.4:
"CERO publicaciones corruptas por bug: una -> gris global; dos -> rojo". Estas
publicaciones contienen un destacado equivocado por bug en tres días de la
ventana. El registro no define si "corrupta" significa mensaje malformado o
contenido equivocado; D-PRE-6b declaró aceptados solo los traspasos de verano
del 21 al 24, no a jugadores fuera de la NBA. Code no lo adjudica.

MEDICIÓN DE ACIERTOS DE ANTONIO (21-24 + día cero), lectura registrada:
puntos evaluados por el RANGO publicado: 44 de 60 dentro (73%; IC95 aprox.
[61%, 83%]) contra la cobertura histórica de [min,max] de 85.6% (D-EXP-4).
Por debajo, no concluyente, con explicación probable de arranque de temporada
(ventanas de abril). Rebotes, asistencias y triples evaluados por coincidencia
EXACTA con la mediana: no es lo que el mensaje publica y explica la mayor
parte del "30% de acierto" global. Decisión de Antonio: no basta con
"justificarse" con la mediana; se explora cómo acercar rango y valor central
al resultado real (D-EXP-7, pre-registro propio, a redactar).

DISPAROS MANUALES (regla de la ventana), fuera de 12:50-13:30 CDMX:
  2026-10-25T22:30:03Z  GET /predictions/today?date=2026-10-26 (dry-run v19)

CONGELAMIENTO: reiniciado desde predictions-api v19 (predictions-api-00019-hq2)
/ ingest-job v14 (generación 11, sin cambios).

## D-EXP-7: DESTACADOS MÁS CERCA DEL RESULTADO REAL — valor central y rango
(PRE-REGISTRO 2026-10-25, revisado por el chat de diseño)

MOTIVO: la medición de Antonio (20-24 oct) dio 73% de puntos dentro del rango
publicado (44/60) contra el 85.6% histórico, y medianas lejos del valor real
en rebotes y asistencias. Decisión de Antonio: no basta con "es la mediana";
se busca la forma de publicar un valor central y un rango más cercanos al
resultado. OFFLINE: cero despliegue; B-limpia, features y mensaje 1 intactos.

UNIVERSO: el de D-EXP-3/4 (minutes > 0, >= 10 jugados previos, 2016-17..
2025-26, mismos folds walk-forward, shift 1, cero leakage), importado de su
script — una reimplementación sería una segunda verdad.
POBLACIÓN PRIMARIA: selector exacto: top 2 por minutos rolling por equipo y
partido, reconstruido con la regla de producción; el filtro de estatus Out no
es reconstruible en histórico y se declara que la población histórica es
ligeramente más amplia.
  RESOLUCIÓN 1 (Antonio, opción a): la población primaria es el selector
  RESTRINGIDO AL UNIVERSO (>= 10 jugados previos), comparable con D-EXP-3/4.
  Los destacados del selector con 3 a 9 partidos en su ventana quedan como
  subconjunto DESCRIPTIVO (reportado, sin puerta).
  RESOLUCIÓN 2 (Antonio, a favor): en el histórico no existen señales de
  plantilla (roster_signals nace en 2026), así que el selector reconstruido
  usa la regla de D-PRE-6 (equipo = fila más reciente en la liga, recencia de
  250 días, mínimo 3 jugados, top 2 por minutos rolling) SIN señales y sin el
  filtro "visto en la temporada" de D-PRE-10, que por diseño de v19 no se
  aplica cuando no hay señales. Declarado, igual que el filtro de Out.
POBLACIONES SECUNDARIAS: titulares (rolling de minutos >= 28) y universo
completo.
SUBCONJUNTO DE ARRANQUE: los primeros 21 días de cada temporada regular, con
puerta propia (ver PUERTA DE ARRANQUE).
STATS: pts, reb, ast, fg3m.

BRAZOS — VALOR CENTRAL (parámetros FIJADOS AQUÍ, sin tuning sobre validación):
  B0  mediana de los últimos 10 jugados (lo publicado hoy).
  C1  media de los últimos 10.
  C2  media exponencial por partido, vida media 5 partidos.
  C3  shrinkage de arranque: (n_act * media_act + K * media_prev) /
      (n_act + K) con K = 5, donde n_act = partidos de la temporada en curso y
      media_prev = media de la temporada anterior; con n_act >= 10 se usa la
      media de los últimos 10 de la temporada en curso.
  C4  ajuste por contexto: OLS por fold sobre el RESIDUO (real - C1),
      ajustada solo en el train del fold, con rating defensivo rolling del
      rival, ritmo rolling del rival, local/visitante y back-to-back propio.
  C5  media expansiva de la temporada en curso; con n_act < 3, media de los
      últimos 10 de la temporada anterior.
BARRIDO DE SENSIBILIDAD (exploratorio, NO elegible para promoción, se corre
después de adjudicar): vida media {3,5,8} para C2; K {3,5,10} para C3;
ventanas {5,20} para B0/C1.

BRAZOS — RANGO:
  R0  [mínimo, máximo] de los últimos 10 (lo publicado hoy).
  R1  conformal escalado al 80% (PRIMARIO): centro ± q * dispersión_10, con
      dispersión_10 = desviación absoluta media de los últimos 10 y q = cuantil
      0.80 de |real - centro| / dispersión_10 medido en el TRAIN de cada fold.
      Si dispersión_10 = 0, el rango cae a R2 para ese jugador-partido.
  R2  conformal absoluto al 80% (secundario): centro ± q', q' = cuantil 0.80 de
      |real - centro| en el train del fold.
  R1 y R2 se comparan entre sí a cobertura idéntica, de modo que el ancho sea
  la única diferencia.
ORDEN: el experimento de RANGO corre UNA sola vez, con el centro ya adjudicado
por la puerta de CENTRO (el ganador, o B0 si ninguno cruza). No se evalúa el
rango con candidatos de centro no ganadores.

MÉTRICAS:
  valor central: MAE; diferencia pareada por jugador-partido contra B0.
  rango: cobertura (% del real dentro), ancho medio, e interval score de
         Winkler a alfa = 0.2.

FASE 0 (gate ejecutable, regla de D-EXP-2): MDE por stat y por población con
la sd de la diferencia pareada de los DOS primeros folds (2020-21 es temporada
corta), extrapolado al n total. MDE relativo > 3% del MAE de B0 = SIN
RESOLUCIÓN en esa celda (descriptiva). El subconjunto de arranque puede quedar
sin resolución: se declara.

PUERTAS NUMÉRICAS (sin lectura alternativa, regla de D-EXP-2b):
  CENTRO, por stat, en la población primaria: un candidato GANA si (a) el IC95
  del MAE pareado agregado excluye cero a su favor, Y (b) mejora en >= 5 de
  los folds de D-EXP-3, Y (c) la mejora relativa es >= max(3%, MDE relativo
  del stat). Si varios ganan, se publica el de mayor mejora; si ninguno,
  sigue B0.
  Multiplicidad: 4 stats x 5 brazos = 20 contrastes; se espera ~1 falso
  positivo al 95%. Un ganador con mejora entre el umbral y umbral + 1 punto
  porcentual y sin consistencia por fold se reporta como "débil, no
  desplegable".
  RANGO, por stat, en la población primaria: R1 GANA si (a) su cobertura
  agregada queda en [78%, 82%] Y en todos los folds en [75%, 85%], Y (b) su
  interval score es mejor que el de R0 con IC95 que excluye cero, Y (c) su
  ancho medio es menor que el de R0.
  PUERTA DE ARRANQUE (subconjunto de los primeros 21 días, solo C3 y R1): C3
  gana si mejora >= 3% del MAE de B0 en el subconjunto, con IC95 que excluye
  cero y mejora en >= 4 de 6 temporadas. Consecuencia: si gana en arranque y
  no en agregado, se despliega como REGLA DE RÉGIMEN (activa los primeros 21
  días de cada temporada; después, B0 o el ganador agregado). R1: cobertura
  en arranque reportada con banda [75%, 85%]; fuera de banda = hallazgo, no
  veto.

EXPECTATIVAS (con la serie de expectativas optimistas fallidas a la vista):
  centro: C1 algo mejor que B0 (~1% en pts, por D-EXP-4); C2 entre 0% y 1%;
  C3 ~0% en el agregado y 1-3% en el subconjunto de arranque; C4 entre 0% y
  1% (precedente nulo: C2 de D-EXP-3); C5 ~0-1% en agregado y, en arranque,
  peor que C3. Nulo plausible y legítimo.
  rango: R1 cumple la cobertura por construcción; ancho 20-35% menor que R0
  en la población primaria; interval score mejor que R0. En el subconjunto de
  arranque, R0 cubre menos que en el agregado (consistente con el 73% vivo).
  ESCENARIO PESIMISTA explícito: ningún centro cruza la puerta de 3%; R1
  cumple cobertura pero el ancho mejora < 15%.
  Mejora > 10% del MAE en el centro = auditar leakage antes de celebrar.

CONSECUENCIA DE PRODUCTO (si alguna puerta cruza): cambio del mensaje 2, no
del modelo. El rango solo puede llamarse "80%" si la cobertura MEDIDA lo
respalda (regla de D-EXP-4); rebotes y asistencias podrían ganar rango.
Diseño del formato, presupuesto de longitud computado (regla de D-PROD-1b) y
despliegue: tarea aparte, con adelante de Antonio y respetando el
congelamiento.
REGLA DEL MONITOR VIVO: si la cobertura viva del rango publicado, sobre los
últimos 100 jugador-partidos con game_date posterior a predicted_at_utc, baja
de 72%, el texto del mensaje vuelve a "rango de sus últimos 10" sin número de
cobertura, con deploy declarado.

ENTREGABLE PARALELO, sin pre-registro (no es experimento, es instrumento):
script de solo lectura que cruza player_predictions_log con player_game_stats
y emite la tabla diaria de cobertura del rango publicado y error de la
mediana (la medición manual de Antonio, automatizada). Cruza solo partidos
con game_date posterior a predicted_at_utc, misma regla que el grading del
criterio. Los destacados no tienen no-peeking (no hay criterio pre-registrado
sobre ellos).

FUERA DE ESTE PRE-REGISTRO: benchmark contra líneas de props de jugador =
D-EXP-8, experimento aparte, una sola medición diagnóstica con tope de
créditos.

RESULTADO: pendiente de fase 0 y ejecución.

SELLADO: 2026-10-25T22:45:37Z, ANTES de escribir el experimento y de computar la fase 0. Borrador de Code, 12 ajustes del chat de diseño aplicados verbatim, resoluciones 1 y 2 de Antonio.

## D-EXP-7 RESULTADO CENTRO + ARRANQUE (2026-10-25) y ADJUDICACIÓN

CORRIDA: scripts/experiment_destacados_d7.py centro. Primera corrida FALLIDA
antes de cualquier métrica (KeyError por is_home duplicado en el merge con el
contexto de equipo; no escribió fase 0 ni resultados); corregida y repetida.
Orden verificado por timestamps de la corrida válida: audit_sample_centro.json
16:55:45, mde_fase0.json 16:55:46, results_centro.json 16:55:48 (CDMX). Sanidad
contra experimentos previos: en el universo completo MAE B0 pts 4.781 (= D-EXP-4)
y MAE C1 pts 4.730 (= D-EXP-3).

CONTEOS: universo 148 484; población primaria (selector reconstruido, >= 10
previos) 22 745; titulares 51 184; seleccionados con 3-9 jugados (descriptivo)
122; subconjunto de arranque de la primaria 2 952.

FASE 0: todas las celdas CON resolución. MDE relativo de celda (peor
candidato): primaria 0.75-0.86%; titulares 0.51-0.58%; universo 0.32-0.40%;
arranque de la primaria 2.0-2.4%.

CENTRO, población primaria — mejora relativa del MAE contra B0 (mediana), con
folds con mejora sobre 6:
  stat   C1 media     C2 exponencial  C3 shrinkage   C4 contexto   C5 temporada
  pts    +1.14% 6/6   +2.87% 6/6      +1.78% 6/6     +1.00% 6/6    +2.23% 6/6
  reb    +1.10% 6/6   +2.68% 6/6      +1.54% 6/6     +0.99% 6/6    +2.50% 6/6
  ast    +0.68% 5/6   +2.53% 6/6      +1.48% 6/6     +0.55% 5/6    +1.78% 6/6
  fg3m   -0.45% 2/6   +1.32% 6/6      +0.11% 3/6     -0.72% 2/6    +1.69% 6/6
PUERTA DE CENTRO: ningún candidato cruza (umbral max(3%, MDE) = 3%) -> centro
B0 en los cuatro stats. Escenario pesimista pre-registrado REALIZADO en el
centro.
DESVIACIONES DE EXPECTATIVA (al alza, primera vez en la serie): C2 2.5-2.9%
contra 0-1% esperado; C3 agregado 1.5-1.8% contra ~0%; C5 2.2% contra 0-1%. C1
~1% y C4 0-1% como se esperaba.

PUERTA DE ARRANQUE (C3, primeros 21 días, primaria, n = 2 952): CRUZA 4/4.
  pts +5.84% (IC95 de la mejora [+0.277, +0.466] pts) 6/6
  reb +3.37% ([+0.045, +0.106]) 6/6
  ast +4.44% ([+0.058, +0.117]) 6/6
  fg3m +3.96% ([+0.029, +0.070]) 6/6

DIAGNÓSTICOS DESCRIPTIVOS pedidos por Antonio (no cambian ninguna puerta):
(a) Arranque de la primaria, mejora contra B0 (* = IC95 excluye cero):
  stat   C1             C2             C3             C5
  pts    +1.46% 6/6*    +3.46% 6/6*    +5.84% 6/6*    +0.02% 3/6
  reb    +0.63% 3/6     +1.79% 6/6*    +3.37% 6/6*    -1.98% 1/6
  ast    -1.08% 1/6     +1.28% 6/6*    +4.44% 6/6*    -2.99% 0/6
  fg3m   +0.02% 3/6     +2.05% 5/6*    +3.96% 6/6*    -1.08% 1/6
  Lectura: pasar de mediana a media explica a lo sumo ~1.5% del arranque; la
  ganancia de C3 es propia del shrinkage hacia la temporada anterior. C5 (media
  de la temporada en curso) es peor que C3 en el arranque, como se esperaba.
(b) Fallback de C3 (sin temporada anterior y n_act < 10): primaria 35 de
  22 745 (0.15%); arranque de la primaria 22 de 2 952 (0.75%). Despreciable.

ADJUDICACIÓN (Antonio, 2026-10-25):
1. Centro = B0 en pts, reb, ast, fg3m. C2 registrado como "mejora real
   (2.5-2.9%, IC excluye cero, 6/6), bajo umbral, no desplegable; sin
   renegociación". Hallazgo: toda media supera a la mediana.
2. Arranque: C3 CRUZA 4/4 -> REGLA DE RÉGIMEN adoptada (primeros 21 días
   de cada temporada regular, centro C3; después B0). Despliegue como
   tarea aparte (D-PRE-11) con: encabezado del mensaje 2 cambiado
   durante el régimen a "(inicio de temporada: mezcla con {temporada
   anterior})"; rango R0 sin cambio en régimen (declarado); tests con
   fecha dentro y fuera de los 21 días; dry-run; deploy fuera de
   12:50-13:30; congelamiento reiniciado.
3. Antes de escribir D-PRE-11, dos diagnósticos descriptivos (no
   cambian la puerta): (a) C1 vs B0 y C2 vs B0 en el subconjunto de
   arranque, para separar "media vs mediana" de "shrinkage"; (b) filas
   de fallback C3 (sin temporada anterior) dentro de la población
   primaria y del arranque, en número y porcentaje.
4. Etapa RANGO: correr UNA vez con --centro pts=B0,reb=B0,ast=B0,fg3m=B0.
5. Append a CLAUDE.md del RESULTADO de centro y arranque con las tablas,
   los diagnósticos de 3, y la adjudicación. Sin commit ni push.
(Los diagnósticos de 3 son los de arriba.)

INTERPRETACIONES DECLARADAS EN EL CÓDIGO (el texto sellado admitía más de una):
(i) sin temporada anterior, C3 usa C1 y C5 usa C1 mientras n_act < 3;
(ii) "débil, no desplegable" = cruza con mejora < umbral + 1 pp y sin mejora en
TODOS los folds; (iii) el MDE de la celda es el del peor candidato.

ETAPA RANGO — CORRIDA INVÁLIDA POR DEFECTO DE INSTRUMENTO (2026-10-25T23:16:52Z,
exit 0): R0 ([mín, máx] de los últimos 10) se calculó con un rolling sobre las
filas del UNIVERSO ya filtrado, no sobre la serie completa de jugados del
jugador: valores NaN en las primeras filas de cada jugador y, donde no son NaN,
una ventana distinta de la publicada. Su ancho y su interval score salieron NaN
y las condiciones (b) y (c) de la puerta fallaron por comparación con NaN, no
por los datos; su cobertura (82.5% pts) está sesgada. R1 y R2 NO dependen de
ese cálculo (su dispersión salió de la serie completa). Lo informativo, ya
visto: R1 cubre 79.0-81.1% en las seis temporadas y los cuatro stats (ancho
medio pts 21.0); R2 cubre solo 66-78% en la población de destacados (el
conformal absoluto calibrado sobre todo el universo se queda corto con los
titulares). El pre-registro permite UNA corrida de rango y el script lo
impide por diseño: repetirla tras ver resultados es adjudicación de Antonio.
Propuesta de Code: conservar el archivo como results_rango_INVALIDA_R0.json,
corregir R0 sobre la serie de jugados (sin parámetros que elegir), test que
compare R0 contra el [mín, máx] que publica producción, y repetir UNA vez.
RESULTADO DEL RANGO: pendiente de esa adjudicación.

Tests del experimento: 20 (tests/test_experiment_destacados_d7.py) + 6 del
instrumento de seguimiento (tests/test_seguimiento_destacados.py).

## D-EXP-7 RESULTADO RANGO (2026-10-25, corrida válida tras adelante de Antonio)

RE-RUN ADJUDICADO: la corrida de las 23:16:52Z quedó como evidencia en
data/experiment_destacados_d7/results_rango_INVALIDA_R0.json. Corrección
mecánica (sin parámetros): R0 y la dispersión se calculan ahora sobre la serie
COMPLETA de jugados (range_series), con test que iguala R0 al [mín, máx] de
player_window_stats de producción. Corrida válida 2026-10-26T02:26:34Z, exit 0,
centro adjudicado B0 en los cuatro stats. Población primaria (selector), n =
22 745.

TABLA (cobertura / ancho medio / interval score de Winkler a alfa 0.2):
  stat   R0 [mín,máx]            R1 conformal 80%         R2 absoluto 80%
  pts    82.8% / 22.52 / 30.20   79.3% / 21.01 / 29.40    68.1% / 15.00 / 29.91
  reb    86.8% /  7.87 / 10.35   80.2% /  7.43 / 10.22    77.5% /  6.00 / 10.17
  ast    87.1% /  6.91 /  9.14   79.5% /  6.46 /  9.10    66.7% /  4.00 / 10.03
  fg3m   91.0% /  4.31 /  5.57   80.6% /  4.15 /  5.90    66.0% /  2.33 /  6.76
  Cobertura de R1 por fold: pts 79.0-79.5%; reb 79.1-81.1%; ast 78.8-80.5%;
  fg3m 79.9-81.1% (las seis temporadas dentro de [75%, 85%]).
  Winkler R0 - R1 (positivo = R1 mejor), IC95: pts +0.805 [+0.639, +0.970];
  reb +0.130 [+0.068, +0.191]; ast +0.043 [-0.014, +0.100]; fg3m -0.335
  [-0.376, -0.294].

PUERTA DE RANGO: R1 CRUZA en pts y reb (cobertura en banda, interval score mejor
con IC que excluye cero, más estrecho). NO cruza en ast (IC del interval score
incluye cero) ni en fg3m (interval score PEOR que R0).
EXPECTATIVA vs RESULTADO: se esperaba R1 20-35% más estrecho que R0; salió
6.7% (pts), 5.6% (reb), 6.5% (ast), 3.7% (fg3m). Se cumple el ESCENARIO
PESIMISTA pre-registrado ("R1 cumple cobertura pero el ancho mejora < 15%"),
ahora también en el rango. Lectura: R1 compra una promesa medida (80%) y un
rango algo más estrecho a cambio de cubrir menos que R0 (79% contra 83-91%);
en triples el [mín, máx] de soporte corto es difícil de mejorar.
R2 descartado: el conformal absoluto calibrado sobre todo el universo se queda
en 66-78% de cobertura con los destacados.

PENDIENTE DE ADJUDICACIÓN DE ANTONIO: (1) si se adopta R1 en el mensaje 2 para
pts y reb (y cómo se redacta: el número "80%" solo con la cobertura medida que
lo respalda, regla de D-EXP-4; rebotes ganaría rango, hoy solo lleva mediana);
(2) el barrido de sensibilidad exploratorio (no elegible para promoción), que
el pre-registro manda correr después de adjudicar.

## D-EXP-7 ADJUDICACIÓN RANGO + D-PRE-11 RESULTADO (2026-10-25/26): régimen de
arranque C3 y rango de puntos R1 al 80% EN PRODUCCIÓN. v20. Congelamiento
reiniciado.

ADJUDICACIÓN D-EXP-7 RANGO (Antonio, 2026-10-25):
- pts: R1 ADOPTADO. Etiqueta "80%" solo fuera del régimen de arranque;
  en régimen, R0 sin etiqueta. Monitor vivo: últimos 100 jugador-partidos
  con game_date > predicted_at_utc; < 72% => reversión declarada a
  "rango de sus últimos 10" sin número, con deploy.
- reb: cruza, DIFERIDO por presupuesto de longitud; recomputar el peor
  caso con el rango de rebotes añadido antes de decidir (D-PROD-1b).
- ast, fg3m: sin cambio.
- Reportar cobertura de R1 en el subconjunto de arranque (punto 8 del
  pre-registro), banda [75%, 85%], como hallazgo.
- Barrido de sensibilidad: correr al final, exploratorio.

HALLAZGO (punto 8): R1 de puntos en el ARRANQUE de la población primaria cubre
80.66% (2 381 de 2 952), por temporada 79.6-81.9%: dentro de la banda
[75%, 85%].
PRESUPUESTO DE REBOTES (D-PROD-1b, computado): el día real de 15 partidos
(12-23, modo R1) mide 3 725 caracteres; con rango de rebotes (ancho medio R1
de reb de D-EXP-7, 7.43) serían ~4 104: pasa de 3 800, forzaría el recorte a
uno por equipo los días llenos. La decisión de diferirlo se sostiene.

CALIBRACIÓN DE R1 (constantes versionadas en config, sin la temporada actual):
PTS_R1_Q_SCALED = 1.902174, PTS_R2_Q_ABS = 7.5, n = 291 764, temporadas
2014-15..2025-26, centro B0, cuantil 0.80 (72 filas con dispersión 0 en la
calibración).

IMPLEMENTACIÓN (player_highlights + daily_predictions):
- highlight_mode: régimen si (fecha - primer partido 002 de la temporada en
  games).days < 21 (2026-27: 20-oct a 9-nov; R1 desde el 10-nov). Sin partidos
  de la temporada en games, el inicio es el propio día (día 0).
- Régimen: centro C3 (n_act >= 10: media de los últimos 10; si no,
  (n_act*media_act + 5*media_prev)/(n_act + 5)) en pts, reb, ast y 3P; rango
  [mín, máx] sin cambio; título "(inicio de temporada: mezcla con 2025-26)".
- Fuera: centro B0; rango de PUNTOS R1 = B0 ± 1.902174 * dispersión_10
  (dispersión 0 -> B0 ± 7.5); título "(últimos 10 partidos · rango de puntos
  al 80%)". Reb, ast y 3P sin cambio.
- El modo cambia lo publicado, NUNCA la selección (orden por minutos intacto).
DECISIONES DECLARADAS que el encargo no fijaba:
(a) extremos de R1 publicados como ENTEROS [ceil(lo), floor(hi)], acotados en
    0: contienen exactamente los mismos puntos posibles que [lo, hi] (test), así
    que la cobertura medida no cambia;
(b) R1 solo con la ventana COMPLETA de 10 (se calibró con 10); con 3 a 9
    jugados la línea conserva [mín, máx] y su sufijo "(últimos n)" — el título
    dice "80%" y esas líneas no lo cumplen: ADJUDICACIÓN PENDIENTE de Antonio
    (alternativas: marcar esas líneas, u omitir el 80% del título);
(c) C3 en producción usa el mínimo de producción (3 jugados) y, sin temporada
    anterior, la media de los últimos disponibles (el experimento exigía 10);
(d) sin cambio de schema: en player_predictions_log, pts_median & co. guardan
    el valor central PUBLICADO (C3 en régimen, B0 después) y pts_min/pts_max el
    rango publicado (R0 o R1); el modo se deriva de la fecha y del primer
    partido 002 de la temporada, y R1 de games_in_window = 10. El monitor del
    instrumento de seguimiento lo deriva así;
(e) el especialista de triples se decide sobre el centro publicado (C3 en
    régimen).

TESTS: 22 nuevos (tests/test_regime_r1.py): fecha en régimen y fuera; tope
exacto (11-09 último día de régimen, 11-10 primero de R1); C3 en sus cuatro
ramas; R1 contra el cálculo del experimento en 5 filas REALES de la muestra de
auditoría (tests/fixtures/d7_r1_audit_rows.json: Siakam, Zubac, Mitchell,
Anunoby, Murray); enteros equivalentes; dispersión 0; acotado en 0; sin R1 con
menos de 10; el modo no cambia el orden; modo desde el primer partido 002;
títulos; presupuesto con los títulos nuevos (día de 15 con nombres típicos:
3 752 / 3 772 en régimen / 3 777 en R1, bajo 3 800). Instrumento de
seguimiento: +2 tests del monitor R1 (8 en total). Suite 1142 passed, 15
deselected (1090 + 22 del experimento + 8 del seguimiento + 22 de D-PRE-11).
Un test existente pasó sin editarse gracias a un retorno tolerante en
build_daily_predictions (test_player_map_vacio_declara_feed_caido parchea
_attach_highlights con None). Ruff limpio.

DEPLOY: predictions-api:v20, revisión predictions-api-00020-99m, 100%, 1Gi,
desplegada a las 23:5x CDMX del 2026-10-25 (fuera de 12:50-13:30).
DRY-RUNS (disparos manuales de la ventana, registrados):
  2026-10-26T05:52:06Z  ?date=2026-10-26  200 en 58.2 s  9 partidos, 36
      destacados, 2 254 caracteres, modo régimen, sin recorte, cero "#"
  2026-10-26T05:53:04Z  ?date=2026-11-02  200 en 84.6 s  15 partidos, 60
      destacados, 3 698 caracteres, modo régimen, sin recorte
  2026-10-26T05:54:29Z  ?date=2026-12-23  200 en 83.2 s  15 partidos, 60
      destacados, 3 725 caracteres, modo R1, sin recorte (75 de margen)
  Evidencia: 36 / 60 / 60 filas en player_predictions_log (rank 1 y 2 a
  partes iguales, served_by 00020-99m); ancho medio del rango de puntos 22.9
  y 22.3 en régimen (R0) contra 19.8 con R1. Sin WARNING en la revisión.
  En régimen el rango no cambia y el centro sí (p. ej. Anthony Gill 10.5 ->
  4.9, su repunte de abril diluido; Anthony Edwards 20.5 -> 29.2).

INSTRUMENTO: scripts/seguimiento_destacados.py gana el MONITOR R1 (últimos
100 jugador-partidos con R1 publicado, alerta bajo 72%); hoy "insuficiente"
(R1 empieza el 10-nov). Tabla 20-24 oct reproduce el 44/60 = 73.3% del cotejo
manual de Antonio.
COTEJO PENDIENTE: DET aparece con Elijah Harkless y Taurean Prince como top 2
(publicado así el 25 y en el dry-run del 12-23); confirmar contra plantilla.
PENDIENTES: barrido de sensibilidad (exploratorio, al final); adjudicación del
punto (b).

CONGELAMIENTO: reiniciado desde predictions-api v20 (predictions-api-00020-99m)
/ ingest-job v14 (generación 11, sin cambios).

## D-PRE-11b + BARRIDO D-EXP-7 (2026-10-26)

REGLA ADJUDICADA (Antonio, 2026-10-26): "rango de puntos solo con ventana
completa de 10; con 3-9 jugados, centro y sufijo, sin rango". Razón: una línea
con [mín, máx] de 6 partidos bajo un título que dice 80% es la promesa sin
medición que D-EXP-4 prohíbe; en el histórico son 122 de 22 745 destacados.
ALCANCE APLICADO por Code, declarado para confirmación: SOLO en el modo R1 (el
que promete "80%" en el título). En el régimen de arranque el título no promete
cobertura y las líneas de ventana corta conservan [mín, máx] con su sufijo.
IMPLEMENTACIÓN: pts_min/pts_max = None en esas líneas; el formato publica
"• Nombre (TRI): 24 pts · 7 reb · 5 ast (últimos 6)"; player_predictions_log
guarda NULL en pts_min/pts_max; el instrumento de seguimiento evalúa solo el
centro de esas filas (contador sin_rango). Tests: +4 en test_regime_r1.py, +1
en test_seguimiento_destacados.py. Suite 1147 passed, 15 deselected (1142 + 5).
DEPLOY: predictions-api:v21 CONSTRUIDA (2026-10-26 00:28 CDMX); deploy
BLOQUEADO por el clasificador de permisos (despliegue a producción sin
autorización explícita en el chat durante el congelamiento) y NO rodeado.
PENDIENTE del adelante de Antonio. Producción sigue en v20.

DET CON HARKLESS Y PRINCE (lectura de Antonio, 2026-10-26, sin cambio): no es
bug. Prince es rotación real; Harkless es two-way (el PDF del 20 lo listaba
Out por G League) y sale por sus minutos de abril. Pasa el filtro de v19
porque el injury report es observación de la temporada. Los días que el PDF lo
marque Out, el filtro de estatus ya lo excluye, y en ~2 semanas la ventana
rueda y desaparece solo. Regla candidata NO adoptada ("un jugador Out por
asignación a G League no es candidato aunque el estatus del día sea
Available"): decisión de contrato, no se toma por un caso.

BARRIDO DE SENSIBILIDAD D-EXP-7 (exploratorio, NO elegible para promoción;
mismas 22 745 filas de la población primaria, contra el mismo B0 de 10):
  CENTRO, mejora relativa vs B0 (* = cruza la puerta de centro):
    stat  C2 hl3  C2 hl8   C3 k3  C3 k10  B0 w5   C1 w5   B0 w20  C1 w20
    pts   +1.32%  +3.56%*  +1.77% +1.70%  -5.42%  -2.38%  +2.07%  +2.65%
    reb   +1.02%  +3.57%*  +1.45% +1.58%  -5.55%  -2.54%  +2.42%  +2.81%
    ast   +0.98%  +3.21%*  +1.46% +1.43%  -4.99%  -2.49%  +2.03%  +2.34%
    fg3m  -0.19%  +2.21%   +0.03% +0.14%  -4.64%  -3.86%  +2.95%  +1.40%
  ARRANQUE, C3 con K=3 / K=10 (adjudicado con K=5: cruza 4/4):
    pts +5.83% / +5.17% cruza ambos; reb +2.73% NO cruza / +3.62% cruza;
    ast +4.29% / +4.05%; fg3m +3.45% / +4.11% (todos 6/6 folds).
RESPUESTA A LA ÚNICA PREGUNTA DEL BARRIDO — ¿algún valor habría cambiado una
adjudicación? SÍ, dos:
  (1) CENTRO: con vida media 8, C2 cruza la puerta en pts, reb y ast (+3.2% a
      +3.6%): el centro adjudicado (B0) habría cambiado en tres stats.
  (2) ARRANQUE reb: con K=3, C3 no cruza (+2.73%): la regla de régimen no se
      habría adoptado para rebotes. pts, ast y fg3m robustos a K.
LECTURA: patrón coherente — más historia suavizada mejora el centro (vida
media 8 > 5 > 3; ventana 20 > 10 > 5); la mediana de 10 es ruidosa. NO se
promueve nada: 8 variantes por stat son el "jardín de senderos" que el
pre-registro evita, y el valor 8 se eligió viendo resultados. VÍA LIMPIA
propuesta: pre-registro nuevo (D-EXP-7b) con vida media FIJADA en 8 antes de
mirar y evaluación sobre datos que ningún análisis ha tocado (2026-27 a medida
que se juega, vía el instrumento de seguimiento). El arranque de rebotes queda
ADOPTADO por la regla, anotado como frágil ante K.

## D-PRE-11b RESULTADO (2026-10-26): v21 EN PRODUCCIÓN. Congelamiento
reiniciado desde v21.

ALCANCE DE (b) CONFIRMADO (Antonio, 2026-10-26): solo en modo R1. En el
régimen de arranque las líneas de ventana corta conservan [mín, máx] con su
sufijo, porque ese título no promete cobertura.
REVISIÓN DEL CRON PREVIA AL DEPLOY (de solo lectura): nba-warmup-daily a las
18:58:05Z y nba-publish-daily a las 19:00:02Z con status {}; servida de record
del 26 a las 19:01:14Z (13:01:14 CDMX): 9 filas en predictions_log y 36 en
player_predictions_log, todas de predictions-api-00020-99m, model_version
poblado y rank en todas. LATENCIA de predictions-api en el cron NO MEDIDA:
Cloud Logging respondió 500 INTERNAL a cuatro consultas entre 19:55Z y 20:01Z
(fallo del servicio de Google). Queda como deuda de medición de ese día; el
200 del Scheduler y las filas del log son la evidencia disponible.
DEPLOY: predictions-api:v21 (la imagen construida el 2026-10-26T06:28Z),
revisión predictions-api-00021-vd8 al 100%, configuración heredada (1Gi).
Desplegada a las 19:56:58Z (13:56 CDMX), fuera de 12:50-13:30, con el
adelante de Antonio en el chat.
DRY-RUN (disparo manual de la ventana, registrado):
  2026-10-26T19:57:14Z  ?date=2026-12-23  200 en 89.2 s  15 partidos, modo
      r1, 60 destacados (30 rank 1 + 30 rank 2), 3 733 caracteres, sin
      recorte, cero "#", model_version poblado.
  Regla (b) verificada: las 5 líneas con ventana corta (novatos de 2026-27
  con 3 jugados: Caleb Wilson, Darryn Peterson, Cameron Boozer, Darius Acuff
  Jr., Yaxel Lendeborg) salen sin rango, p. ej. "• Caleb Wilson (CHI): 21 pts
  · 6 reb · 1 ast (últimos 3)"; las de ventana completa llevan R1, p. ej.
  "• Jalen Johnson (ATL): 20 pts (15-24) · 11 reb · 6 ast". En
  player_predictions_log hay 60 filas de 00021-vd8: 5 con pts_min NULL, las 5
  de ventana corta y ninguna de ventana completa; además 15 filas en
  predictions_log (fechadas el 10-26, así que la regla de record las excluye).
  WARNINGs de la revisión 00021-vd8 sin consultar por la misma caída de Cloud
  Logging.
NOTA DEL TAG api-v21 (Antonio): "construida antes del commit, árbol idéntico
en código". Verificado: 0b275c6..5acaac6 en nba_predictor/ es exactamente el
cambio (b); la última edición de código fue a las 00:16:04 CDMX y el build a
las 00:28:31; la imagen solo copia requirements.lock y nba_predictor/.
REGLA NUEVA (Antonio): a partir de v22, el orden es commit -> build ->
deploy -> tag, SIN EXCEPCIONES. Una imagen construida desde un árbol sin
commitear (v19, v21) deja la equivalencia entre imagen y código en una
verificación a posteriori en lugar de garantizarla por construcción.

CONGELAMIENTO: reiniciado desde predictions-api v21 (predictions-api-00021-vd8)
/ ingest-job v14 (generación 11, sin cambios).

## ADJUDICACIONES DE PENDIENTES (2026-10-26)

1. GATE OPERATIVO (b) Y LOS DESTACADOS DE D-PRE-10 (Antonio): "no cuenta
   como corrupto, ya que no es propio del modelo sino de los destacados.
   Eso se añadió después". Lectura registrada: el criterio de
   comercialización (13e-2.4) se congeló el 2026-08-25, antes de que
   existiera el mensaje 2 (D-PROD-1, 2026-09-22), así que la condición (b)
   se refiere a la publicación que el criterio conocía: el mensaje 1, las
   predicciones. Las publicaciones del 21, 23 y 25 de octubre con
   jugadores fuera de la NBA en el mensaje 2 NO computan para (b).
   CONSECUENCIA (derivada por Code al registrar, pendiente de que Antonio
   la confirme): el mensaje 2 queda entero fuera de (b), también para bugs
   futuros. Sus bugs se siguen corrigiendo de inmediato con post-mortem
   (discriminante de 13e-2.4), pero no gradúan el gate. La exención del
   punto 3 de D-PRE-6b queda contenida en esta regla.
2. KONCHAR (UTA) Y WESTBROOK (SAC): Antonio confirma que no están en esas
   plantillas. Los 5 de 28 destacados del 25-oct sin observación de
   2026-27 quedan confirmados como el bug de D-PRE-10, corregido en v19.
3. MEM@UTA, LOS 4 QUE ENTRARON CON v19: cerrado con datos (BigQuery, solo
   lectura, 2026-10-26). Ace Bailey y Lauri Markkanen en UTA, Jahmai
   Mashack y Jerami Grant en MEM: cada uno con 4 señales de pretemporada
   (última 2026-10-16) y 3 partidos de temporada regular 2026-27 con ese
   equipo (último 2026-10-25). Tener boxscores de esta temporada con el
   equipo publicado es evidencia más fuerte que el cotejo manual que se
   había pedido.
4. ENTREGA EN EL CANAL (D-PRE-5): Antonio confirma que hubo mensaje en el
   canal todos los días, del 2026-10-20 al 2026-10-26. Gate operativo (a)
   en lo que va de la ventana (desde el 21): 6 de 6.

[COMPLEMENTO D-PRE-11b (2026-10-27, 12:20 CDMX), de Code. El encargo del
chat de diseño de hoy pedía este deploy con el planeador desactualizado; su
paso 0 preveía este caso ("si ya sirve v21, salta al paso 3"). Verificación
de solo lectura: predictions-api-00021-vd8 sirve el 100% con la imagen
predictions-api:v21; el tag api-v21 apunta a 5acaac6; HEAD es 6f4adb5 y
entre 5acaac6 y HEAD solo cambió CLAUDE.md (bbcb0fd y 6f4adb5), así que el
código desplegado es el del tag; árbol limpio. No se redesplegó nada.
REGLA DE PROCESO desde v22, en la redacción completa del encargo: "commit ->
build -> deploy -> tag, sin excepciones; el hash del commit va en cada
RESULTADO; ninguna imagen se construye desde un árbol con cambios sin
commitear". Para v21: commit 5acaac6, tag api-v21.
NO EJECUTADO: el dry-run (b) del encargo (?date=2026-10-28, régimen, líneas
de ventana corta con [mín, máx]). Su paso 2 queda fuera por la rama del paso
0. Ese comportamiento está fijado en tests/test_regime_r1.py; se corre en
vivo solo si Antonio lo pide.]

## D-ODDS-7 (PRE-REGISTRO 2026-10-27)

Ampliación de la captura de The Odds API de h2h a h2h,spreads,totals.
Ingesta solamente: nada se publica, nada toca el modelo ni los mensajes; B0
del criterio sigue sin mercado.
  - Mercados: h2h, spreads, totals; misma región y bookmakers actuales.
  - Créditos: una llamada = mercados x regiones = 3. Tres snapshots
    diarios x ~31 días = ~279/mes de 500. Umbrales: 400 consumidos en el
    mes = WARNING; 450 = el job captura solo h2h y lo declara en el log
    (h2h es la evidencia del CLV y tiene prioridad). El consumo se lee de
    los headers x-requests-used / x-requests-remaining de cada respuesta
    y se loggea en cada captura.
  - Esquema: columnas nuevas en market_odds, todas nullable:
    spread_home, spread_home_price, spread_away, spread_away_price,
    total_line, over_price, under_price. Clave de idempotencia SIN
    cambio: (game_date, api_event_id, bookmaker, snapshot_label). Filas
    históricas quedan NULL en las columnas nuevas.
  - Expectativa: primera captura con spreads y totals poblados en >= 90%
    de los eventos emparejados; ausencia en algún bookmaker menor es
    normal y se registra. Desviación = hallazgo.
  - Invariante que no cambia: cero filas con capture_ts >= commence_time.

SELLADO: 2026-10-27, 14:09 CDMX, ANTES de tocar código o esquema.

## D-ODDS-7 RESULTADO (2026-10-27): h2h,spreads,totals EN PRODUCCIÓN

PROCESO (regla de v22 en adelante, cumplida): commit e916c71 de Antonio ->
verificado HEAD = e916c71 con el árbol limpio -> build -> jobs update ->
ejecución manual. Imagen us-south1-docker.pkg.dev/predictorsnonprod/
nba-predictor/ingest-job:v15 (build 09b6c958, digest
sha256:c2fdde5867f9d8baeaa39dbc31980e03297b690e91f6292937a01cbdac9fb438),
nba-odds-capture en la GENERACIÓN 6. Argumentos del job sin cambios (publish
por defecto; los tres Scheduler siguen igual). nba-ingest-job sigue en v14
(generación 11), sin tocar.

ESQUEMA: ALTER TABLE con las 7 columnas FLOAT64 nullable (spread_home,
spread_home_price, spread_away, spread_away_price, total_line, over_price,
under_price), verificado con bq show ANTES del código. 571 filas históricas,
571 con NULL en las columnas nuevas. ACL de la tabla intacta (etag
BwZb8wgQmNs= antes y después; única binding nba-odds-capture-sa dataEditor).
ESPEJO SQLITE: NO EXISTE para market_odds (el job escribe solo a BigQuery;
data/nba.sqlite no tiene la tabla). No se creó: habría sido inventar una
tabla. Desviación del paso 2 declarada.

CRÉDITOS GASTADOS EN LA TAREA: 6 (x-requests-used 81 -> 87). 3 por la
llamada del fixture y 3 por la ejecución manual válida. La primera ejecución
manual (nba-odds-capture-dnm5r, 20:42:37Z) falló con exit 2 ANTES de que
Python arrancara (0 créditos, 2 intentos por maxRetries=1). Causa: en
PowerShell, --args=a,b,c sin comillas se convierte en un array y llega a
gcloud como UN solo argumento unido por espacios ("can't open file
'/app/scripts/odds_capture_job.py --snapshot-label manual'"). Receta:
"--args=scripts/odds_capture_job.py,--snapshot-label,manual" con la
asignación completa entre comillas dobles. La consulta de cuota a /sports es
gratuita (x-requests-last 0, verificado dos veces).

EJECUCIÓN MANUAL VÁLIDA (nba-odds-capture-68tqj, 2026-10-27T20:44:15Z,
label 'manual', exit 0; disparo manual registrado, fuera de 12:50-13:30):
  Creditos (antes de la captura): x-requests-used=84 x-requests-remaining=416
  GET ... (markets=h2h,spreads,totals, bookmakers=draftkings,fanduel,betmgm)
  Creditos (tras la captura): x-requests-used=87 x-requests-remaining=413
  9 eventos devueltos por la API; 1148 partidos en el índice
  Eventos iniciados omitidos: 0 de 9
  MERGE completado: 19 filas, 9 eventos emparejados, 0 sin match,
    0 iniciados omitidos.
  Cobertura D-ODDS-7 (filas emparejadas): 19 con spread, 19 con total, de 19.
BigQuery (por snapshot_label):
  label    filas  emparejadas  con_spread  con_total  spread asim.  post-tipoff
  evening    158          158           0          0             0            0
  late       205          205           0          0             0            0
  manual      19           19          19         19             0            0
  publish    208          208           0          0             0            0
Total de la tabla 590 = 571 + 19: las filas manuales viven en su propia fila
por clave y NO tocaron ningún snapshot programado (última captura publish
19:00:15Z de hoy, anterior al deploy).

CONTRA LA EXPECTATIVA PRE-REGISTRADA: spreads y totals poblados en el 100% de
los eventos emparejados (9 de 9, 19 de 19 filas), contra >= 90%: SE CUMPLE.
Ningún bookmaker sin mercado en esta captura. Invariante de tipoff: 0 filas
con capture_ts >= commence_time en TODA la tabla: SE CUMPLE. spread_home ==
-spread_away en las 19 filas.

DECISIONES DE IMPLEMENTACIÓN DECLARADAS (aceptadas con el commit):
(a) label 'manual' admitido como constante aparte (MANUAL_LABEL); los tres
    labels programados siguen cerrados. Existe para que una verificación jamás
    pise por MERGE una fila programada.
(b) el consumo se lee ANTES de capturar con la llamada gratuita a /sports, y
    se loggea también el de la respuesta de la captura. Consumo desconocido
    (fallo de /sports) => solo h2h, declarado con "DEGRADACION DECLARADA".
(c) las 7 columnas son FLOAT64 (líneas fraccionarias; precios americanos como
    float). market sigue valiendo 'h2h' y el h2h sigue siendo obligatorio
    para que exista la fila. Totals con Over/Under de líneas distintas ->
    NULL con WARNING; spread asimétrico -> se archiva tal cual con WARNING.
(d) test existente editado: la guarda del esquema pasó de 17 a 24 campos.
Suite: 1174 passed, 15 deselected (1147 + 27 de tests/test_odds_mercados.py).

REGLAS AÑADIDAS (Antonio, 2026-10-27):
(1) Toda consulta de CLV o benchmark filtra snapshot_label IN
    ('publish','evening','late'). Una fila 'manual' dentro del análisis
    sería error nuestro, no del dato.
(2) La degradación a h2h por fallo de /sports queda contada en el log
    ("DEGRADACION DECLARADA"); si ocurre más de dos veces en una semana, es
    hallazgo a revisar.

VERIFICACIÓN PRE-REGISTRADA de las capturas automáticas de HOY (primeras con
v15): evening 17:45 CDMX (23:45Z) y late 21:15 CDMX (03:15Z del 28). Cada
una: los tres mercados poblados en >= 90% de sus filas emparejadas (línea
"Cobertura D-ODDS-7" del log), 0 filas con capture_ts >= commence_time, y 3
créditos por captura (x-requests-used esperado 90 tras evening y 93 tras
late; cualquier otro salto es hallazgo). Si la captura de las 17:45 falla, el
sospechoso es este cambio. Rollback: jobs update a ingest-job:v13, que
funciona con la tabla ampliada (no escribe las columnas nuevas).

VERIFICACIÓN 2026-10-28 (solo lectura, 13:35 CDMX, fuera de la franja
12:50-13:30): las dos primeras capturas automáticas con tres mercados,
contra lo pre-registrado arriba.

  dato                      evening                    late
  ejecución                 nba-odds-capture-tnk6g     nba-odds-capture-77t2x
  inicio (creación)         2026-10-27T23:45:05Z       2026-10-28T03:15:02Z
  exit                      0 (exit(0), 1 succeeded)   0 (exit(0), 1 succeeded)
  generación / imagen       6 / digest c2fdde58...     6 / digest c2fdde58...
                            (= ingest-job:v15)         (= ingest-job:v15)
  créditos antes            used=87 remaining=413      used=90 remaining=410
  créditos después          used=90 remaining=410      used=93 remaining=407
  eventos de la API         19                         17
  emparejados / sin match   18 / 0                     16 / 0
  iniciados omitidos        1 (BKN@BOS, commence       1 (POR@LAL, commence
                            23:41Z <= captura 23:45Z)  03:13Z <= captura 03:15Z)
  MERGE (log)               39 filas                   40 filas
  Cobertura D-ODDS-7 (log)  39 spread, 39 total de 39  40 spread, 40 total de 40

market_odds, filas de cada captura (snapshot_label + capture_ts de esa
ejecución):
  label    bookmaker   filas  emparejadas  con_spread  con_total  spread asim.
  evening  betmgm          8            8           8          8             0
  evening  draftkings     17           17          17         17             0
  evening  fanduel        14           14          14         14             0
  evening  (total)        39           39          39         39             0
  late     betmgm         12           12          12         12             0
  late     draftkings     16           16          16         16             0
  late     fanduel        12           12          12         12             0
  late     (total)        40           40          40         40             0
Invariante sobre TODA la tabla: 691 filas, 0 con capture_ts >=
commence_time. 15 filas con commence_time NULL (no evaluables por el
invariante): 5 por label publish/evening/late, game_date 2026-12-25, última
captura 2026-10-04/05; coinciden en fecha y número con los 5 eventos del
4-oct de la desviación (c) de D-PRE-1. 19 filas 'manual' (las de la
verificación del 27).
Rama de degradación: NO se activó en ninguna de las dos. Ninguna línea con
el WARNING de 400 créditos ni "DEGRADACION DECLARADA"; /sports respondió
en ambas (línea "Creditos (antes de la captura)").

CONTRA LA EXPECTATIVA:
- 3 créditos por captura, 90 tras evening y 93 tras late: SE CUMPLE
  exacto.
- spread y total poblados en >= 90% de las filas emparejadas: SE CUMPLE,
  100% en las dos (39/39 y 40/40) y en los tres bookmakers.
- 0 filas con capture_ts >= commence_time: SE CUMPLE.
- Generación 6 con ingest-job:v15: SE CUMPLE.
Sin desviaciones de lo pre-registrado. HALLAZGO REGISTRADO (el
pre-registro decía "ausencia en algún bookmaker menor es normal y se
registra"): no todos los bookmakers cotizan todos los eventos emparejados.
Evening: draftkings 17, fanduel 14, betmgm 8 de 18 eventos; late:
draftkings 16, fanduel 12, betmgm 12 de 16. Donde un bookmaker cotiza un
evento, trae los tres mercados.

## D-EXP-11: pisos calibrados ("X+") con cobertura medida
(PRE-REGISTRO SELLADO 2026-10-27; borrador del chat de diseño + las 15
lecturas de la revisión de Code, todas adjudicadas por Antonio el
2026-10-27)

PREGUNTA. ¿Puede el mensaje 2 publicar umbrales del tipo "18+ pts" con una
probabilidad medida, en el formato de los props de las casas, sin prometer
nada que no se haya verificado?

UNIVERSO Y POBLACIÓN. Los de D-EXP-7 sin cambio: jugados, >= 10 previos,
shift 1, folds 2020-21 a 2025-26. Población primaria: selector exacto
reconstruido restringido a >= 10 previos. Secundarias: titulares y
universo. POBLACIÓN DE LA PUERTA = primaria SIN las filas de arranque
(primeros 21 días de cada temporada): ahí el piso no se publica. El
arranque entra solo en el control descriptivo.

CENTRO. El adjudicado en D-EXP-7: B0 (mediana de 10) fuera del régimen de
arranque. En régimen el piso NO se publica (el centro es C3 y no se
calibró; declarado). Si un experimento posterior pre-registrado cambia el
centro, el piso se recalibra mediante amendment pre-registrado.

STATS. pts, reb, ast, fg3m, mismo método.

BRAZOS (conformal de UN solo lado):
  P90: piso = centro - q90 * dispersión_10, con q90 = cuantil 0.90 de
       (centro - real) / dispersión_10. Es R1 a un lado, mismo escalado
       (dispersión_10 = desviación absoluta media de los últimos 10).
  P80: igual con cuantil 0.80.
  CALIBRACIÓN: el cuantil se calcula sobre el UNIVERSO del train de cada
  fold (temporadas anteriores a la validada), como R1; la puerta se evalúa
  sobre la población de la puerta.
  FUNCIÓN: conformal_q_lower, NUEVA (residuo con signo, centro - real);
  conformal_q queda intacta, con test de que el q de R1 no cambia.
  RESPALDO: con dispersión_10 = 0 (medido: 0% en pts/reb/ast de la
  primaria, 2.4% en fg3m, todas ventanas de diez ceros) se usa la variante
  ABSOLUTA, piso = centro - q_abs, con q_abs = cuantil del residuo sin
  escalar (centro - real) AL MISMO NIVEL DEL BRAZO, sobre TODO el train.
  Las filas de fg3m con diez ceros tienen centro 0, quedan con piso 0 y
  cobertura del 100% por construcción, y CUENTAN hacia la regla del 30%.
  Piso acotado en 0.
  PUBLICACIÓN: piso redondeado HACIA ARRIBA al entero (misma regla que el
  extremo inferior de R1 en D-PRE-11, decisión a). La cobertura se mide
  sobre el ENTERO PUBLICADO, con real >= piso.
  VENTANA CORTA (3-9 jugados, solo producción): sin piso; centro y sufijo,
  igual que D-PRE-11b. En el experimento no aparece (la población exige
  >= 10 previos).
  Etapa nueva en experiment_destacados_d7.py ("pisos"), con marca propia
  de una sola corrida y bandas de puerta parametrizadas; range_series,
  range_gate y conformal_q no se modifican.

MÉTRICAS. Cobertura del piso = fracción con real >= piso publicado.
Informatividad = cociente de medias: suma de pisos / suma de centros (sin
división por cero cuando un centro es 0). Decide la cobertura; la
informatividad compara niveles.

FASE 0. MDE de cobertura: con n ~ 22 745, el error estándar de una
proporción al 90% es ~0.2 puntos; la banda inferior de 2 puntos tiene
resolución. Se escribe igual, antes de los resultados. ANOTACIÓN: ese error
estándar es una COTA OPTIMISTA, porque las filas de un mismo jugador no son
independientes.

PUERTAS (sin lectura alternativa; banda SOLO INFERIOR):
  - P90 cruza en un stat si cobertura agregada >= 88% Y en todos los folds
    >= 85%.
  - P80 cruza si agregada >= 78% Y por fold >= 75%.
  - Cobertura agregada > 95% = hallazgo "piso poco informativo", NO veto.
  - Se publica UN nivel, elegido ahora: P90. P80 se corre y se reporta
    como referencia de informatividad; no se publica aunque cruce.
  - Un stat con piso publicado (redondeado y acotado) igual a 0 en más del
    30% de la población de la puerta se declara "sin piso útil" y no se
    publica para ese stat aunque cruce cobertura.

EXPECTATIVAS. P90 cumple cobertura por construcción en pts y reb (+-1
punto). ast y fg3m: dentro de banda, piso 0 frecuente; fg3m probablemente
"sin piso útil". Informatividad en pts (cociente de medias): 0.65-0.75
(24 pts -> piso 16-18). Pesimista: cociente < 0.55 en pts, lo que diría
que al 90% casi no se puede afirmar nada de un partido NBA.

CONTROL DE ESPECIFICIDAD (descriptivo, no veto): cobertura por titulares
vs banca, arranque vs resto, local vs visitante. Desviación > 5 puntos
respecto a la cobertura agregada del mismo stat = hallazgo.

LEAKAGE. El de D-EXP-7: q solo del train, dispersión solo del pasado,
selector sin filas del día.

CONSECUENCIA DE PRODUCTO (pre-registrada). Si P90 cruza en pts: en modo
R1 el piso SUSTITUYE al rango R1. El "90%" va UNA vez en el título ("pisos
al 90%" en modo R1) y la línea queda "• Nombre (TRI): 23 pts · 18+". Si
cruza en reb y hay presupuesto: "15 reb · 11+". ast y fg3m solo si
cruzan y tienen piso útil. PRESUPUESTO COMPUTADO EN EL DISEÑO (regla de
D-PROD-1b): " · 18+" mide 6 caracteres contra 8 de " (15-24)", y " · 8+"
5 contra 7 de " (8-24)": la línea con piso pesa 2 caracteres MENOS que la
vigente con R1. El título "(últimos 10 partidos · pisos al 90%)" mide 36
contra 46 del vigente: 10 menos. Sobre el dry-run del 12-23 (3 733
caracteres, 55 líneas con R1) el mismo día mediría ~3 613. El peor caso de
15 partidos se RECOMPUTA antes de cualquier deploy. Constante de
producción: como PTS_R1_Q_SCALED, calibrada sobre toda la historia hasta
2025-26, versionada en config con fecha y n. Monitor vivo: evaluación
diaria sobre los últimos 100 pisos publicados con resultado; 14
evaluaciones consecutivas < 84% = reversión declarada al formato R1
vigente. Mientras el piso esté activo, el monitor del 72% de R1 queda
suspendido. Ninguna palabra de recomendación.

ORDEN. Antes de cualquier experimento posterior que cambie el centro.

LO QUE NO HACE. No toca mensaje 1, modelo ni features. No compara contra
líneas de casas (D-EXP-8).

RESULTADO: pendiente de fase 0 y ejecución.

SELLADO: 2026-10-27T22:02:17Z, ANTES de escribir la etapa "pisos".

## D-EXP-11 RESULTADO (2026-10-27): NINGÚN PISO CRUZA. P90 cubre 86-88%
en la población de la puerta contra el 88% exigido; el cuantil calibrado en
el universo no se transfiere a los destacados

CORRIDA ÚNICA: scripts/experiment_destacados_d7.py pisos, 2026-10-27
16:12:26 -> 16:13:39 CDMX, exit 0, a la primera (sin corrida inválida).
Orden verificado por timestamps: pisos_fase0.json 16:13:34.72,
results_pisos.json 16:13:39.54, tabla_pisos.txt 16:13:39.54. Pre-registro
sellado antes (22:02:17Z = 16:02:17 CDMX). Código de la etapa: solo
adiciones a experiment_destacados_d7.py (range_series, range_gate y
conformal_q sin tocar). Tests: 11 nuevos en
tests/test_experiment_destacados_d7.py; suite 1185 passed, 15 deselected
(1174 + 11).

CONTROLES DEL INSTRUMENTO:
- Primaria = 22 745 filas, idéntica a D-EXP-7. Puerta (primaria sin
  arranque) = 19 793; arranque de la primaria = 2 952.
- q de R1 reproducido: conformal_q sobre estos datos da EXACTAMENTE los
  q por fold de results_rango.json en los cuatro stats (diferencia máxima
  0.0). La función intacta calcula lo mismo que calibró R1.
- FASE 0: error estándar agregado 0.21 pp (P90) y 0.28 pp (P80); por fold
  0.50-0.74 pp. La banda inferior queda a 9.4 y 7.0 errores estándar del
  nominal. COTA OPTIMISTA (filas de un mismo jugador no independientes),
  como se anotó al sellar.

TABLA — población de la puerta (cobertura del piso publicado, real >= piso):
  stat nivel  agregada  por fold (2020-21 .. 2025-26)            piso  centro  inform  piso0  puerta
  pts  P90     85.9%   85.6 86.3 86.7 85.5 85.8 85.5          12.60   21.16   0.596   0.8%  no cruza
  pts  P80     76.0%   75.5 76.6 76.4 76.0 76.1 75.0          15.66   21.16   0.740   0.0%  no cruza
  reb  P90     87.5%   87.0 88.3 86.9 88.3 87.7 86.7           3.23    5.92   0.547   5.8%  no cruza
  reb  P80     77.1%   76.8 77.1 76.8 77.6 77.9 76.3           4.29    5.92   0.724   0.7%  no cruza
  ast  P90     86.1%   86.8 86.0 86.2 86.5 86.0 85.0           2.81    4.92   0.572   9.1%  no cruza
  ast  P80     76.0%   76.3 75.6 76.2 76.7 76.2 74.7           3.67    4.92   0.746   1.9%  no cruza
  fg3m P90     86.3%   86.6 85.7 86.9 86.8 85.7 86.1           0.98    2.01   0.490  34.5%  no cruza
  fg3m P80     73.9%   73.9 73.2 74.1 74.7 73.4 74.0           1.56    2.01   0.778  15.6%  no cruza
(inform = cociente de medias, suma de pisos / suma de centros; piso0 = piso
publicado igual a 0)

VEREDICTO POR STAT CONTRA LA PUERTA:
- pts P90: NO cruza. Agregada 85.9% < 88%. Los folds sí pasan su piso
  (mínimo 85.5% >= 85%); falla solo la condición agregada.
- reb P90: NO cruza (87.5% < 88%; un fold en 86.7%, también por encima de
  85%). Es el que queda más cerca, a 0.5 pp.
- ast P90: NO cruza (86.1%).
- fg3m P90: NO cruza (86.3%) y además "sin piso útil" (34.5% de pisos en 0
  > 30%).
- P80 (referencia, no publicable): no cruza en ninguno (73.9-77.1% < 78%).
- Ningún stat con cobertura > 95%: el hallazgo "poco informativo" no se
  activó.
CONSECUENCIA: por la regla pre-registrada, no hay piso publicable. El
formato vigente (R1 en puntos fuera del régimen) no cambia.

EL MECANISMO, a la vista en las secundarias (sin arranque):
  stat  universo P90  titulares P90  puerta P90
  pts      90.5%        87.0%          85.9%
  reb      90.2%        87.8%          87.5%
  ast      90.3%        87.2%          86.1%
  fg3m     90.7%        87.2%          86.3%
El cuantil se calibró en el universo del train y en el universo cubre el
90% por construcción (90.2-90.7%). La cobertura cae al pasar a titulares y
de nuevo a los destacados. Es la misma familia que R2 en D-EXP-7: lo
calibrado sobre todo el universo se queda corto con los destacados. R1, de
dos lados y escalado, sí se transfirió (79.3% contra 80%); el lado de
ABAJO, a solas, no. HIPÓTESIS POST-HOC, no conclusión: el residuo
negativo de un jugador de muchos minutos (salida por lesión, minutos
recortados en paliza, falta temprana) es más largo, en unidades de su
dispersión, que el del jugador promedio; la cola de abajo de los
destacados es más pesada que la del universo.

CONTRA LAS EXPECTATIVAS:
- "P90 cumple cobertura por construcción en pts y reb (±1 punto)":
  FALLIDA, 85.9% y 87.5%. La construcción vale en la población donde se
  calibra, no en la puerta.
- "ast y fg3m dentro de banda": FALLIDA, 86.1% y 86.3%.
- "fg3m probablemente sin piso útil": CUMPLIDA (34.5% de pisos en 0).
- "Informatividad en pts 0.65-0.75": FALLIDA, 0.596 (piso medio 12.6
  sobre centro 21.2).
- Pesimista ("cociente < 0.55 en pts"): NO realizado en pts (0.596), pero
  rozado. El nivel que sí cubre lo que promete (el universo) es aún menos
  informativo: 0.435 en pts.
Otra expectativa propia fallida de la serie, y de nuevo en dirección
optimista.

CONTROL DE ESPECIFICIDAD (descriptivo, desvío vs la agregada del mismo
stat): local/visitante dentro de ±1.1 pp en todo; arranque dentro de
±1.6 pp (ese día el piso no se publicaría). Los cinco "hallazgos" > 5 pp
son todos de la BANCA, que en la población de la puerta tiene n = 40 (el
selector elige a quien más minutos juega, así que casi no hay banca). Con
n = 40 un desvío de 5 pp son dos filas: no se adjudica nada sobre ellos.
Titulares = 19 753 de 19 793, la población entera.

ARCHIVOS: data/experiment_destacados_d7/pisos_fase0.json,
results_pisos.json y tabla_pisos.txt. La marca de una sola corrida es
results_pisos.json.

PENDIENTE DE ADJUDICACIÓN de Antonio: adopción en producción como tarea
aparte con adelante. Por la puerta pre-registrada no hay nada que adoptar.
Si se quisiera seguir, la vía limpia es un pre-registro NUEVO, no una
segunda corrida de este: por ejemplo calibrar el cuantil sobre la población
primaria del train (lo que el mecanismo de arriba sugiere), con su propia
expectativa y su propia puerta.

## D-EXP-11 ADJUDICACIÓN (Antonio, 2026-10-27): CERRADO SIN ADOPCIÓN

- VEREDICTO: ningún piso cruza la puerta pre-registrada; el rango R1 de
  puntos sigue vigente en el mensaje 2, sin cambios.
- NO se abre D-EXP-11b. Recalibrar el cuantil sobre la población primaria
  recuperaría cobertura a costa de informatividad: hoy 0.596 al 86% de
  cobertura; al 90% quedaría cerca o por debajo del 0.55 pesimista (el
  universo, que sí cubre el 90%, da 0.435 en pts). CONCLUSIÓN REGISTRADA:
  pisos de un solo lado al 90% no son informativos para destacados NBA.
- HIPÓTESIS NO PROBADA, registrada: cola inferior más pesada en jugadores
  de muchos minutos (salidas por lesión, minutos recortados en palizas).
  Si algún día se reabre, la vía es modelar minutos (D-EXP-9), no otro
  cuantil.
- LECCIÓN: lo calibrado en una población y evaluado en otra exige la misma
  forma de distribución; el conformal de dos lados (R1) se transfirió del
  universo a los destacados, el de un lado no. REGLA para próximos
  pre-registros: calibrar y evaluar sobre la misma población, salvo razón
  explícita escrita en el propio pre-registro.
- Cuarta expectativa fallida hacia el optimismo en la serie D-EXP-7/11
  (conteo de Antonio).

## D-EXP-9: minutos proyectados con redistribución por bajas
(PRE-REGISTRO SELLADO 2026-10-27; borrador del chat de diseño + las 14
lecturas de la revisión de Code, todas adjudicadas por Antonio el
2026-10-27)

PASO 0 — HUECO DEL 2025-02-13, CAUSA BENIGNA. El PDF publish archivado
(Injury-Report_2025-02-13_01PM.pdf, GemBox, /CreationDate 13:30:12 ET =
12:30 CDMX) existe y es legible: una página con los 5 partidos del día y los
10 equipos en NOT YET SUBMITTED, cero filas de jugador. parse_pdf lo lee
bien (0 filas, 10 NysEntry con fecha 02/13/2025; el conteo independiente
también da 0 líneas con nombre). D-EXP-1 cuenta solo filas de jugador, así
que no hay instancias. El corte late del mismo día (09PM) sí trae 58 filas.
No es fallo del parser ni de D-EXP-1: es el caso NYS de 13e-2.5 a escala de
un día completo. Fecha EXCLUIDA de D-EXP-9 por esa causa (2024-25, fuera
del arranque; sus 5 partidos se cuentan aparte).

PREGUNTA. ¿Mejora el valor central de los destacados si los minutos se
proyectan condicionados a los compañeros Out en el corte publish, en vez
del rolling propio? Es la capa que las casas de apuestas hacen mejor y la
única señal nueva disponible a las 13:00 que el sistema aún no usa para
esto.

UNIVERSO. El de D-EXP-7 (jugados, >= 10 previos, shift 1), restringido al
corpus GemBox con injury report parseado y auditado: 2023-24, 2024-25 y
2025-26. Folds walk-forward: train 2023-24 -> valida 2024-25; train
2023-24 + 2024-25 -> valida 2025-26. Dos folds, declarado como límite de
resolución.
FUENTE DE ESTATUS: las instancias publish OFICIALES de D-EXP-1
(data/experiment_pjuega/instancias.json, veredicto 'incluida', con game_id,
team_id y player_id), sin re-parsear; NYS a fecha objetivo desde
data/experiment_v2b/nys.parquet (corte publish, mismo parser). Se verifica
por assert que ambos usan el mismo sufijo publish por fecha.
EXCLUSIONES, contadas aparte: (i) fecha sin PDF publish (2023-10-24, sin
corte anterior a las 13:00); (ii) jugador-partido cuyo equipo O cuyo rival
está NYS en el corte publish (estatus desconocido: ni el filtro del
selector ni el tratamiento son calculables, y "sin bajas" sería la mentira
silenciosa que 13e-2.5 prohíbe). El 2025-02-13 cae entero en (ii).

POBLACIÓN PRIMARIA. Selector reconstruido (regla D-PRE-6 sin señales: top 2
por minutos rolling por equipo y partido) CON EL FILTRO DE PRODUCCIÓN: Out y
Doubtful del PDF publish excluidos, el puesto pasa al siguiente por minutos.
Restringido a >= 10 previos. DIFERENCIA DECLARADA con D-EXP-7, que no podía
aplicar el filtro: la primaria GemBox pasa de ~11 625 a ~13 555 filas
(medido en la revisión). Implementación: reconstruct_selector con top alto,
filtro y re-ranking posteriores; la función no se toca. Secundarias:
titulares (rolling >= 28) y universo.

VARIABLE DE TRATAMIENTO. min_perdidos = suma, sobre los compañeros Out en el
corte publish con rolling >= 25, de (rolling del compañero x fracción de los
últimos 10 partidos jugados del destacado en que ese compañero jugó con el
mismo equipo que el destacado). Una baja ya absorbida en el rolling del
destacado pesa 0; un compañero que jugó 10 de 10 aporta su rolling completo.
Compañero = team_id del PDF publish igual al equipo del destacado en ese
partido. Rolling del compañero = media de minutos de sus últimos 10 jugados
antes de la fecha, mínimo 3 (como el selector). Subconjunto A:
min_perdidos > 0. Subconjunto B: = 0 (solo descriptivo). Definición "nueva"
(al menos un compañero fuerte Out que jugó el partido anterior del equipo):
solo descriptiva.

BRAZOS DE MINUTOS (tope [0, 44] en M1 y M2):
  M0: rolling de 10 (roll_min de D-EXP-3, min_periods 10).
  M1: M0 + beta * min_perdidos, beta por OLS SIN INTERCEPTO sobre el
      residuo (minutos reales - M0), ajustado en la población PRIMARIA SIN
      ARRANQUE del train de cada fold (lección de D-EXP-11: calibrar y
      evaluar en la misma población). Un coeficiente. Sin intercepto, M1 es
      idéntico a M0 en B por construcción.
  M2: M0 + b1 * perdidos_mismo_grupo + b2 * perdidos_otro_grupo, sin
      intercepto, misma población de ajuste. Grupo = perímetro/interior
      según rebotes por minuto de su rolling hasta la fecha (promedio de
      razones de los últimos 10 jugados, mínimo 3) contra la mediana de la
      liga en el train (filas del universo del train). Grupo del compañero
      relativo al del destacado.
  fit_ols de D-EXP-3 (con intercepto) queda intacto: variante nueva
  fit_ols_sin_intercepto, con test contra cálculo directo.

VALOR CENTRAL. Tasa por minuto suavizada exponencialmente (vida media 5, la
de D-EXP-7) como PROMEDIO DE RAZONES (media exponencial de stat/minutos por
partido, sobre todos los jugados previos; convención de D-EXP-3, declarado)
x minutos del brazo. Mismo centro con M0, M1 y M2.

CONTRASTES Y PUERTAS (pts decide; reb, ast y fg3m se reportan SIN puerta y,
si se adopta, el cambio toca solo puntos). Población de la puerta =
primaria sin arranque, temporadas de validación, subconjunto A. Métrica:
MAE pareado (paired_mae_diff de D-EXP-3), IC95 agregado y por fold.
  - M1 gana a M0 si: (a) IC95 del MAE pareado excluye cero a su favor,
    (b) mejora en los 2 folds, (c) mejora relativa >= max(5%, MDE
    relativo).
  - M2 gana a M1 con (a)-(c) contra M1 en A, umbral 3%. Si no, M2 se
    descarta y la posición no se usa.
  - PLACEBO (control de especificidad): el mismo beta de M1 aplicado a las
    bajas fuertes del RIVAL, minutos_placebo = M0 + beta *
    min_perdidos_rival, tope [0, 44]. Para el rival no hay destacado propio,
    así que la fracción de absorción se mide sobre los últimos 10 partidos
    del EQUIPO RIVAL (interpretación declarada). Se evalúa contra M0 en las
    filas de la población de la puerta con min_perdidos_rival > 0. Si mejora
    más que el MDE de esa celda, la ganancia de M1 no es redistribución y el
    resultado se marca "no interpretable, auditar".
  - PUERTA DE PRODUCTO: tasa x M1 debe ganar a B0 (mediana de 10, lo
    publicado) DENTRO de A con los mismos (a)-(c) y umbral 5%. Sin esto no
    hay cambio de producto aunque M1 gane a M0. El agregado contra B0 queda
    descriptivo.
  - Arranque, B y la definición "nueva": descriptivos.

FASE 0. n = A ∩ primaria ∩ temporadas de validación ∩ sin arranque
(estimado en la revisión ~3 900). MDE con la sd pareada M1 vs M0 del primer
fold de validación (2024-25), extrapolada al n de ambos. n < 2 000 = SIN
RESOLUCIÓN (descriptivo, no se adjudica). MDE relativo > 3% del MAE de M0 en
A = sin resolución. Se escribe ANTES de cualquier métrica. El MDE del
placebo se calcula igual sobre su propia celda.

MUESTRA DE AUDITORÍA: 30 filas de A (población de la puerta), seed 42,
congelada ANTES de ajustar nada: 10 al azar y 20 de FRONTERA a propósito
(regla de D-RES-3e): 5 con fracción estrictamente entre 0 y 1, 5 con un
compañero de rolling en [25, 27), 5 con un compañero de 3 a 5 jugados en su
ventana, 5 de destacados que cambiaron de equipo dentro de su ventana.
Columnas: compañeros Out, sus rolling, fracción jugada, min_perdidos, M0,
M1, M2, real.

EXPECTATIVAS. A: M1 mejora 4-8% sobre M0 en pts; reb 2-5%; ast 2-5%; fg3m
~0. Placebo: 0 +- MDE. M2 sobre M1: 0-2%, probablemente no cruza. Puerta de
producto (tasa x M1 vs B0 en A): 0-4%, cruzar es incierto. Agregado contra
B0: M1 pierde o empata (D-EXP-3). Pesimista: A por debajo del 3%;
conclusión, la redistribución no es capturable con el PDF del mediodía y el
hueco frente a las casas es de información, no de modelo. Mejora > 15% en A
= auditar leakage.

LEAKAGE. Estatus solo del corte publish, nunca late ni boxscore. Rolling de
compañeros, fracciones y rebotes por minuto con filas estrictamente
anteriores a la fecha. Equipo del día por la regla de equipo actual con
filas anteriores al partido. Minutos reales solo como target del residuo en
el train. Las temporadas de validación no eligen nada.

CONSECUENCIA DE PRODUCTO (pre-registrada). Si M1 cruza contra M0 en A, el
placebo pasa Y la puerta de producto cruza: regla condicional en el mensaje
2, activa solo cuando el equipo del destacado tiene al menos una baja fuerte
no absorbida en el corte publish; ese día el centro de PUNTOS de ese jugador
es tasa x M1 con marca declarada, y el rango sigue siendo el vigente (R0 en
régimen, R1 fuera). Días sin bajas fuertes: sin cambio. Cambio del mensaje
2, tarea aparte con adelante, reinicio de congelamiento. Si no cruza: nada
cambia y se registra.

ENTREGABLE PARALELO. El script de seguimiento gana la columna
bajas_fuertes_equipo por destacado publicado, derivada de absences_applied
de predictions_log + rolling de compañeros, solo lectura, con la definición
de min_perdidos de arriba.

LO QUE NO HACE. No toca mensaje 1, modelo ni features. No usa el corte
late. No usa props ni mercado.

RESULTADO: pendiente de fase 0 y ejecución.

SELLADO: 2026-10-28T01:44:23Z, ANTES de escribir código.

## D-EXP-9 RESULTADO (2026-10-27): NINGUNA PUERTA CRUZA. beta sale NEGATIVA
y diminuta; el diseño sin intercepto la confunde con la regresión a la media
del selector (error propio de la revisión de Code)

CORRIDA ÚNICA: scripts/experiment_destacados_d9.py (script aparte; d7 solo
se importa, sus funciones protegidas y fit_ols de D-EXP-3 sin tocar),
2026-10-28T01:57:52Z -> 01:58:58Z (19:57-19:58 CDMX del 27), exit 0, a la
primera. Orden verificado por timestamps: pre-registro sellado 01:44:23Z;
audit_sample.json 01:58:57.506Z (antes del ajuste), fase0.json
01:58:57.660Z, results.json 01:58:58.094Z. Tests: 23 nuevos en
tests/test_experiment_destacados_d9.py (tratamiento ponderado, absorbida =
0, 10 de 10 = rolling completo, mismo equipo, placebo con ventana del
rival, OLS sin intercepto contra cálculo directo, fit_ols de D-EXP-3 con
intercepto intacto, filtro Out/Doubtful con re-ranking, tope [0, 44], y
ningún brazo ve el partido que predice). Suite 1208 passed, 15 deselected
(1185 + 23). Ruff limpio.

INSTRUMENTO Y CONTEOS:
- Estatus: 37 095 instancias publish de D-EXP-1, 35 607 incluidas; NYS 525
  pares equipo-fecha; sufijo publish idéntico entre D-EXP-1 y D-EXP-2b en
  las 483 fechas comparables (assert pasado). 24 995 bajas Out, 7 934
  fuertes (rolling >= 25).
- Primaria con filtro de producción: 13 555 (4 588 / 4 532 / 4 435), contra
  11 625 del selector de D-EXP-7: la estimación de la revisión, exacta. El
  selector de D-EXP-7 tenía entre sus top 2 a 1 988 Out y 107 Doubtful.
- Excluidas de la primaria: NYS propio o rival 515 / 555 / 411; sin PDF 8
  (2023-10-24). El 2025-02-13 aporta 15 de las NYS de 2024-25.
- Población de la puerta (validación, sin arranque): 6 985 (3 448 /
  3 537); A = 3 738 (1 771 / 1 967); B = 3 247; A con baja "nueva" 1 114;
  celda del placebo 3 680.

FASE 0: n_A = 3 738 >= 2 000. MDE relativo en pts 0.09% (abs 0.0056), en
reb, ast y fg3m 0.06-0.09%: CON RESOLUCIÓN en todo. Placebo: MDE abs
0.0058. NOTA DE LECTURA: el MDE sale minúsculo porque beta es casi cero y
M1 casi coincide con M0, así que la sd de la diferencia pareada es pequeña
por construcción. La resolución es real para detectar el 5% pre-registrado,
pero no dice nada sobre la señal.

COEFICIENTES (minutos ganados por minuto perdido no absorbido):
  fold 2024-25: beta_M1 -0.0066 | M2 mismo -0.0057, otro -0.0073 |
                mediana reb/min del train 0.158 | n_train 3 564 (A 1 468)
  fold 2025-26: beta_M1 -0.0116 | M2 mismo -0.0115, otro -0.0118 |
                mediana 0.159 | n_train 7 012 (A 3 239)
  Tope de 44 minutos: nunca se activó.
Con min_perdidos medio de 26.5 en A, M1 resta unos 0.2-0.3 minutos.

TABLA — población de la puerta, MAE pareado (positivo = el candidato
acierta más):
  stat  contraste                n     mejora    rel      IC95                 2024-25   2025-26
  pts   M1 vs M0 (A)          3 738   -0.0078  -0.13%  [-0.0134, -0.0022]     -0.07%    -0.19%
  pts   M2 vs M1 (A)          3 738   -0.0001  -0.00%  [-0.0003, +0.0001]     -0.00%    -0.00%
  pts   placebo vs M0         3 680   +0.0070  +0.12%  [+0.0012, +0.0129]     +0.12%    +0.12%
  pts   tasa x M1 vs B0 (A)   3 738   +0.1269  +2.05%  [+0.0734, +0.1805]     +3.19%    +0.97%
  reb   M1 vs M0 (A)                   +0.0022  +0.10%  [+0.0004, +0.0040]     +0.04%    +0.16%
  reb   tasa x M1 vs B0 (A)            +0.0538  +2.48%  [+0.0333, +0.0743]     +3.03%    +1.97%
  ast   M1 vs M0 (A)                   +0.0009  +0.05%  [-0.0005, +0.0023]     +0.04%    +0.05%
  ast   tasa x M1 vs B0 (A)            +0.0500  +2.65%  [+0.0319, +0.0682]     +1.33%    +3.84%
  fg3m  M1 vs M0 (A)                   +0.0023  +0.19%  [+0.0016, +0.0030]     +0.14%    +0.25%
  fg3m  tasa x M1 vs B0 (A)            +0.0282  +2.34%  [+0.0143, +0.0421]     +3.77%    +0.99%
  (reb, ast, fg3m: M2 vs M1 = 0.00% y placebo +0.08% a +0.14% en los tres;
  detalle completo en tabla.txt)

VEREDICTO POR PUERTA (pts):
- M1 vs M0: NO CRUZA. -0.13%, con IC que excluye cero EN CONTRA y pérdida
  en los dos folds.
- M2 vs M1: NO CRUZA (0.00%). M2 se descarta y la posición no se usa.
- PLACEBO: la regla SE ACTIVÓ. Mejora +0.0070 contra un MDE de 0.0058; por
  la regla pre-registrada, "no interpretable, auditar". La activación es
  coherente con el mecanismo de abajo: una beta negativa aplicada a
  CUALQUIER baja recorta minutos, y recortar minutos a un top 2 corrige
  parte de su sesgo.
- PRODUCTO (tasa x M1 vs B0 en A): NO CRUZA. Pasa (a) el IC y (b) los dos
  folds, pero +2.05% < 5%.
CONSECUENCIA: por la regla pre-registrada, nada cambia en el mensaje 2.

EL MECANISMO, y el error propio que lo explica. Residuo medio (minutos
reales - M0) en la población de la puerta: A -0.49, B -0.85. Los dos son
negativos: el selector elige a los dos con más minutos rolling y esos
jugadores regresan a la media al partido siguiente. Sin intercepto, el
único modo que tiene el ajuste de absorber el residuo medio negativo de las
filas de A es una beta negativa: confunde la regresión a la media de la
selección con la redistribución. La lectura 3 (sin intercepto) la propuso
Code en su revisión para que el control en B no quedara contaminado, SIN
medir antes el residuo medio de la población de ajuste, que es distinto de
cero POR CONSTRUCCIÓN cuando se selecciona por minutos. Es otro error
propio de la serie, y de nuevo de diseño, no de ejecución. REGLA: antes de
fijar un ajuste sin intercepto, medir el residuo medio de la población de
ajuste; si no es ~0, el intercepto (o un control equivalente) es parte del
diseño. HIPÓTESIS POST-HOC, no probada: la brecha A - B (+0.36 minutos a
favor de A) es compatible con una redistribución real pero pequeña, del
orden del 1-2% de los minutos perdidos; medirla exigiría un pre-registro
nuevo con intercepto y otro control de especificidad.

DESCRIPTIVOS:
- tasa x M0 vs B0: +2.17% en A y +2.29% en B (pts). La ganancia sobre lo
  publicado viene de la construcción del centro (tasa exponencial x minutos
  rolling frente a la mediana de 10), con bajas o sin ellas. Es la familia
  de C2 de D-EXP-7 (+2.87% en la primaria), ya registrada como "mejora real
  bajo umbral, no desplegable". No es hallazgo nuevo.
- tasa x M1 vs B0 en toda la población de la puerta: +2.16% (pts).
- M1 vs M0 en A: arranque -0.08% (n 536); baja "nueva" -0.20% (n 1 114);
  titulares -0.05%; universo -0.19%. En ningún subgrupo aparece la señal.
- En minutos (no en puntos): M1 empeora el MAE de minutos en A en 0.014.

CONTRA LAS EXPECTATIVAS:
- "A: M1 mejora 4-8% en pts": FALLIDA (-0.13%). reb 2-5%: FALLIDA
  (+0.10%). ast 2-5%: FALLIDA (+0.05%). fg3m ~0: CUMPLIDA (+0.19%).
- "Placebo 0 +- MDE": FALLIDA (+0.12%, sobre el MDE).
- "M2 sobre M1 0-2%, probablemente no cruza": CUMPLIDA.
- "Puerta de producto 0-4%, cruzar incierto": CUMPLIDA (+2.05%, no cruza).
- "Agregado contra B0: M1 pierde o empata": FALLIDA en la dirección
  contraria (+2.16% a favor de M1), por la construcción del centro y no por
  las bajas.
- Pesimista ("A por debajo del 3%"): REALIZADO. Con este diseño, la
  redistribución no se captura con el PDF del mediodía; queda abierto si es
  el diseño (beta sin intercepto) o la información.
- Alarma de leakage (> 15%): no se activó.
Otra expectativa propia fallida hacia el optimismo, y la segunda vez que el
número sale con el signo contrario.

DESVIACIONES DECLARADAS:
(1) MUESTRA DE AUDITORÍA: 25 filas, no 30. El estrato "compañero con 3 a 5
    jugados en su ventana" salió VACÍO en A fuera del arranque: ningún
    compañero fuerte con tan pocos partidos había jugado con el destacado.
    No se re-sorteó ni se rellenó. El encabezado del listado dice "30
    filas" por descuido; la cifra buena es 25 (10 al azar, 5 fracción
    parcial, 5 rolling en [25, 27), 5 destacados que cambiaron de equipo).
(2) ENTREGABLE PARALELO (columna bajas_fuertes_equipo del script de
    seguimiento): NO implementado en esta tarea; el encargo no lo incluía
    en el paso 2. Queda pendiente.
(3) El encargo llegó truncado en "PROHIBIDO: ... segunda corrida sin"; se
    leyó "sin adjudicación", como en los encargos anteriores.

ARCHIVOS: data/experiment_destacados_d9/audit_sample.json, fase0.json,
results.json, tabla.txt y auditoria_muestra.txt (con las columnas de
frontera: compañeros Out, rolling, n, fracción, aporte, jugó el partido
anterior; M0/M1/M2; real; bajas del rival). La marca de una sola corrida es
results.json.

PENDIENTE DE ADJUDICACIÓN de Antonio (auditoría de la muestra de 30 —25— y
decisión de producto). Por las puertas pre-registradas no hay nada que
adoptar. Si se quisiera seguir, la vía limpia es un pre-registro NUEVO con
intercepto y un control de especificidad que no dependa del signo de beta,
no una segunda corrida de este.

## D-EXP-9 ADJUDICACIÓN (Antonio, 2026-10-27): CERRADO SIN ADOPCIÓN

- VEREDICTO: ninguna puerta cruza; el mensaje 2 no cambia.
- ERROR DE DISEÑO COMPARTIDO: la lectura 3 la propuso Code y la adjudicó el
  chat de diseño. Sin intercepto, beta absorbe la regresión a la media de
  los top 2 por minutos, no la redistribución. REGLA DERIVADA: antes de
  fijar la forma de un ajuste, medir el residuo medio de la población donde
  se ajusta.
- NO se abre D-EXP-9b: la brecha A - B de +0.36 minutos equivale a menos
  del 1% del MAE de puntos; ningún diseño la convertiría en producto.
  CONCLUSIÓN: la redistribución por bajas que se ve en el PDF del mediodía
  es real y pequeña; el hueco frente a las casas es de información, no de
  modelo. La definición ponderada de baja (absorbida = 0) se conserva para
  un futuro con fuente de alineaciones.
- HALLAZGO ACUMULADO: tasa x M0 (media exponencial) gana a B0 en ~2.2%;
  es la tercera observación independiente (C2 de D-EXP-7, barrido con vida
  media 8, D-EXP-9). Ninguna de las tres es desplegable; se adjudica en
  D-EXP-7b.
- PENDIENTES MENORES: la columna bajas_fuertes_equipo del seguimiento
  (lectura 14) se DESCARTA por falta de uso; la muestra tiene 25 filas y no
  30, declarado; auditoría humana de la muestra: PENDIENTE [RESUELTO, ver
  abajo] (el encargo traía el marcador "[resultado de Antonio]" sin
  rellenar; se registrará cuando Antonio lo comunique).
- [2026-10-28] AUDITORÍA HUMANA DE LA MUESTRA (Antonio, sobre
  data/experiment_destacados_d9/auditoria_legible.txt, 25 fichas con
  nombres, reconstruidas con las mismas funciones del experimento y
  cotejadas sin discrepancias contra auditoria_muestra.txt): OK 25 de 25.
  Definición 0, Cálculo 0, Dudoso 0. NOTA DE TRANSCRIPCIÓN: el mensaje
  traía Cálculo y Dudoso sin rellenar (marcadores "([filas y qué falla])"
  y "[n]"); se registran como 0 porque cada ficha lleva una sola marca y
  las 25 son OK, no porque viniera escrito. Sin casos de Cálculo ni de
  Definición: la definición ponderada de baja se conserva sin nota y el
  script no se toca. Verificaciones automáticas del listado: M1 dentro de
  [0,44] en las 25, ningún compañero con bandera (fracción > 0.5 y último
  partido hace más de 30 días). D-EXP-9 queda cerrado también en
  auditoría.

## D-EXP-7b (PRE-REGISTRO SELLADO, 2026-10-28T03:50:36Z): C2 con vida media 8, evaluación prospectiva sobre 2026-27

HIPÓTESIS ÚNICA: C2 (media exponencial por partido del stat, vida
media 8, adjust=True, shift 1, como en D-EXP-7) mejora el MAE de puntos
sobre B0 (mediana de 10, lo publicado) en >= 3% en la población de
destacados PUBLICADOS (servida de record de player_predictions_log),
sobre partidos de 2026-27 con game_date posterior a este sello y fuera
del régimen de arranque. Un brazo. Sin barrido. Nada que elegir
después.
ORIGEN DECLARADO: el valor 8 se eligió viendo el barrido de D-EXP-7
(2026-10-26); por eso la evaluación es solo prospectiva. Tres
observaciones previas no independientes de la misma señal: D-EXP-7 C2
(2.5-2.9%), barrido hl8 (3.2-3.6%), D-EXP-9 tasa x M0 (2.2%).
DATOS: destacados publicados cruzados con el resultado real (script de
seguimiento); C2 recalculado offline con el histórico as-of de cada
fecha, con test de que ningún valor usa el partido predicho.
MÉTRICA: MAE pareado por jugador-partido, IC95; reb, ast, fg3m solo
descriptivos.
FASE 0: MDE con la sd pareada de C2 vs B0 en la primaria de D-EXP-7,
extrapolada al n esperado (~3 800 destacados fuera de arranque en
2026-27); cota optimista por dependencia entre filas del mismo jugador.
CORTES: 2026-12-31, lectura intermedia que NO adjudica; fin de
temporada regular 2026-27, lectura que adjudica. No se mira antes.
PUERTA: IC95 excluye cero a favor de C2 Y mejora relativa >= max(3%,
MDE). Sin lectura alternativa.
EXPECTATIVA: 2.5-3.5%; cruzar es incierto. Pesimista: < 2%, y entonces
el patrón de las tres observaciones era selección.
CONSECUENCIA: si cruza, C2 vida media 8 sustituye a B0 como centro
desde 2027-28 con pre-registro de producto propio; nunca durante
2026-27. Si no, cerrado.
LEAKAGE: los partidos anteriores al sello quedan fuera; el régimen de
arranque queda fuera; C2 solo con partidos anteriores a cada fecha.

RESULTADO: pendiente de fase 0 y de los cortes.

ENMIENDA D-EXP-7b (Antonio, 2026-10-27), append bajo el bloque sellado,
antes del primer dato válido (2026-11-10):
  1. Ventana corta (3-9 jugados): FUERA del contraste; se cuentan y
     reportan. B0 = mediana de 10 y universo >= 10 previos, heredados de
     D-EXP-7 (misma regla que D-PRE-11b para el rango).
  2. Destacados sin resultado evaluable: FUERA, contados aparte en tres
     categorías (no jugó, equipo distinto, evaluado), como el script de
     seguimiento; solo "evaluado" entra al MAE. Si los no evaluados
     superan el 15% en un corte, hallazgo sobre el selector, no sobre C2.
  3. "No se mira antes": el seguimiento diario de B0 sigue (instrumento
     operativo, no calcula C2). C2 no se calcula para ningún partido de
     2026-27 antes del 2026-12-31; esa fecha produce la lectura
     intermedia completa, que no adjudica.

## D-ODDS-8 (2026-10-28): cobertura de snapshots y hora del Este

HALLAZGO (conteo de solo lectura, 2026-10-28): 56 partidos de temporada
regular 2026-27 ya jugados (2026-10-20 -> 2026-10-27). Snapshot válido =
fila emparejada de publish/evening/late con capture_ts < commence_time.
  definición                         1 snap  2 snaps  3 snaps  solo publish
  cualquier captura previa al tipoff      0       13       43   0 de 56 (0%)
  solo capturas del mismo día (ET)       27       29        0   27 de 56 (48.2%)
Por combinación de labels:
  cualquier captura      mismo día (ET)    partidos  fechas
  publish+late+evening   publish+evening         29  10-20 -> 10-27
  publish+late+evening   publish                 14  10-20 -> 10-26
  publish+late           publish                 13  10-24 -> 10-27
CAUSA: los tres horarios estaban fijados en America/Mexico_City y los
tipoffs se programan en America/New_York, que en octubre lleva DST (ET =
CDMX + 2 h). Evening (17:45 CDMX = 19:45 ET) cae después de los partidos de
las 19:00 y 19:30 ET, que se omiten por iniciados. Late (21:15 CDMX = 23:15
ET) cae después de prácticamente todos. La fila late que existe para cada
partido es la de la NOCHE ANTERIOR (~20 h antes del tipoff, más vieja que
el publish del propio día), porque el MERGE conserva la última captura
válida por label.

DECISIONES:
(1) REGLA PRE-REGISTRADA: proxy de cierre = último snapshot emparejado
    con capture_ts < commence_time, ordenado por capture_ts, nunca por
    label. Todo análisis de CLV reporta horas_antes_del_tipoff del
    snapshot usado, por partido.
(2) ENMIENDA de horario: nba-odds-evening a 18:40 America/New_York y
    nba-odds-late a 21:40 America/New_York; publish sin cambio (13:00
    America/Mexico_City). Declarado: el dataset de CLV tiene cobertura
    distinta antes y después de esta fecha; el campo de horas lo hace
    explícito. Créditos sin cambio.
(3) PENDIENTE: causa de los 13 partidos sin ninguna fila evening.
    [Resuelto abajo, paso 3.]
(4) NOTA: el MERGE conserva solo la última captura por label; el
    movimiento de línea no se guarda, por diseño (D-ODDS). Tabla aparte
    si algún día se quiere.

APLICACIÓN DE (2) (2026-10-28T20:30Z, 14:30 CDMX, con el encargo de
Antonio como adelante): gcloud scheduler jobs update http con --schedule y
--time-zone=America/New_York, solo en los dos jobs. describe después:
  nba-odds-evening  schedule "40 18 * * *"  America/New_York  ENABLED
                    próximo disparo 2026-10-28T22:40:00Z (16:40 CDMX)
  nba-odds-late     schedule "40 21 * * *"  America/New_York  ENABLED
                    próximo disparo 2026-10-29T01:40:00Z (19:40 CDMX del 28)
  nba-odds-publish  sin cambio: "0 13 * * *" America/Mexico_City
Body (args del label), URI, cuenta de servicio, attemptDeadline 320s y
maxRetryDuration 0s idénticos a antes. Imágenes sin tocar. Ningún
disparo cae en 12:50-13:30 CDMX, ni con EDT ni con EST.
CORRECCIÓN DE CONVERSIÓN, declarada: el encargo daba "18:40 ET = 17:40
CDMX" y "21:40 ET = 20:40 CDMX". Eso vale desde el 2026-11-01 (fin del DST
en EE. UU., ET = CDMX + 1 h). Hasta el 31 de octubre son 16:40 y 19:40
CDMX. Los disparos siguen la hora ET, así que la cobertura no cambia
con el DST; lo que se mueve es la hora CDMX.

PASO 3, CAUSA DE LOS 13 PARTIDOS SIN EVENING (solo lectura): NO es el
emparejamiento. Desde el 2026-10-20, todas las capturas registran "0 sin
match", y no hay ninguna fila evening con matched=false. La respuesta cruda
de la API no se archiva y el MERGE pisa, así que no hay prueba evento por
evento; la evidencia es de conteo y de horizonte:
- Los 13 partidos empiezan antes de las 23:45Z de su día, así que la
  evening del mismo día los omitió por iniciados (o no los tenía).
- Su fila más antigua que queda es la late de las 03:15Z de su fecha UTC
  (la noche anterior en ET), la primera captura posterior a la evening del
  día previo. Una fila evening del día previo, de haber existido, no se
  habría pisado: la evening del día del partido los omite.
- Los conteos del log son consistentes con eventos que aparecen en la API
  entre la evening (23:45Z) y la late (03:15Z): p. ej., la evening del
  10-23 dejó 11 eventos no iniciados y la late de las 03:15Z del 10-24
  tenía 13.
- Comprobación directa hoy: GET /v4/sports/basketball_nba/events
  (gratuito, x-requests-last 0, x-requests-used 96) a las ~20:28Z lista 17
  eventos: 12 de 2026-10-28 y 5 de 2026-12-25. NINGÚN partido del 10-29.
  La API todavía no lista los partidos de mañana a media tarde, ni
  siquiera sin cuotas.
CAUSA: el evento no existía todavía en The Odds API a la hora de la
evening del día anterior. Pasado el arranque de temporada, el horizonte
de listado es de menos de un día. Contraste: el 10-20 la API listaba 46
eventos de varios días; desde el 10-23, entre 9 y 19. No es bug; con los
horarios nuevos, la evening y la late del propio día cubren a estos
partidos.

PASO 4 — VERIFICACIÓN PRE-REGISTRADA para el 2026-10-29: capturas de hoy
con el horario nuevo (18:40 ET y 21:40 ET), exit 0, créditos +3 cada una
(x-requests-used 99 tras evening, 102 tras late).
MÉTRICA RETIRADA: "fracción de partidos de hoy con snapshot del mismo día
anterior al tipoff (esperada: > 90%)" — retirada: se cumple con cualquier
horario (el publish de las 15:00 ET precede a todo tipoff).
MÉTRICA QUE DECIDE (Antonio, 2026-10-28): fracción de partidos del día con
snapshot evening o late del mismo día, posterior al publish y anterior al
tipoff. Expectativa para el 2026-10-28: 12/12 con evening; late solo
LAL@LAC; OKC@DAL (commence 01:40:00Z) omitido por la regla >=, caso límite
registrado, no hallazgo.
NOTAS DE CODE AL PRE-REGISTRO (redactadas antes de la decisión; la (b)
queda resuelta por la métrica que decide):
(a) Horas CDMX de hoy: 16:40 (evening, 22:40Z) y 19:40 (late, 01:40Z).
    Créditos esperados: x-requests-used 96 -> 99 tras evening -> 102 tras
    late.
(b) Tal como está escrita, la fracción la cumple ya el publish de las
    13:00 CDMX (15:00 ET), anterior a todo tipoff: daría 100% con
    cualquier horario. La que mide la enmienda es la fracción con un
    snapshot del mismo día POSTERIOR al publish (evening o late) y
    anterior al tipoff. Esperado calculado con los commence_time de la
    API para los 12 partidos de hoy: evening (22:40Z) precede a los 12
    (12/12; con el horario viejo de 23:45Z habrían sido 7/12). Late
    (01:40Z) precede solo a LAL@LAC (02:40Z). OKC@DAL tiene commence
    01:40:00Z, el minuto exacto de la captura late, y saldrá como
    iniciado. Cuál de las dos fracciones decide la verificación es
    adjudicación de Antonio.

## D-PO-0 (2026-10-28): inventario para playoffs en sombra

Inventario de solo lectura (15:14-15:25 CDMX) para preparar D-PO-1
(playoffs 2027 en modo sombra, D-ODDS-6). Sin propuesta de diseño.

1. RAW. JSON de boxscore con prefijo 004 (playoffs) o 005 (play-in): 0 en
   gs://.../raw/boxscores/ (14 429 objetos, todos 002), 0 en
   raw/boxscores_live/ (56 objetos, 2026-27), 0 en raw/boxscores_preseason/,
   y 0 en data/raw/ local (14 429 de 002 + 67 de 001 en
   boxscores_preseason/). Es 0 en todas las temporadas 2016-17..2025-26:
   el histórico no trae playoffs.
2. BigQuery. Filas por prefijo de game_id: games 002 = 14 485; team_game_stats
   002 = 28 970; player_game_stats 002 = 372 789. Ninguna fila 004 ni 005
   en las tres tablas: se cumple la expectativa (universo regular por
   decisión cerrada).
3. Calendario. scheduleLeagueV2_2026-10-28.json (el archivado más
   reciente), seasonYear 2026-27: 001 = 67, 002 = 1 206, 006 = 1. Lista 0
   partidos 004 y 0 partidos 005, ni siquiera como TBD. El 006 único no se
   inspeccionó.
4. market_odds. Filas emparejadas por prefijo: todas 002 (691); 0 con
   game_id 004/005. Filas matched=false: 0 en toda la tabla, y 0 filas de
   cualquier tipo con game_date entre 2026-04-01 y 2026-06-30. El job de
   captura no existía en abril-junio de 2026: opera desde el 2026-09-21. La
   captura más antigua que queda es del 2026-10-04 porque el MERGE pisa.
5. Injury reports (raw/injury_reports/, 2 449 objetos, 2018-12-17 ->
   2026-10-28). Fechas entre el 14-abr y el 30-jun:
     2024: 2 PDFs, ambos del 2024-04-14 (último día de la temporada
           regular 2023-24, no playoffs)
     2025: 0 (último archivado del semestre: 2025-04-13)
     2026: 0 (último archivado del semestre: 2026-04-12)
   No hay ningún PDF de playoffs ni de play-in archivado. El backfill de
   D-RES-2 tomó solo fechas de temporada regular, y el archivo diario del
   job empieza con la temporada 2026-27 (primer PDF posterior a julio:
   2026-10-19). La retención de ak-static para PDFs de playoffs no se
   sondeó.
   Desliz de instrumento, declarado: el primer conteo dio 0 en los tres
   años porque buscaba el patrón "Injury-Report_", y el archivo guarda los
   objetos como {fecha}_{sufijo}.pdf. Recontado con el patrón correcto.
6. Código: puntos donde se fija el tipo de partido por prefijo o constante
   (lista, sin cambios):
     nba_predictor/ingestion/cdn_client.py:60   _REGULAR_SEASON_PREFIX = "002"
     nba_predictor/ingestion/cdn_client.py:276  filtro de _normalize_cdn_schedule
                                                (ingest_job paso 1)
     nba_predictor/ingestion/cdn_client.py:67   _GAME_TYPE_MAP (gameType de
                                                boxscores; 2 = "Regular Season")
     nba_predictor/ingestion/cdn_client.py:303  season_type constante
                                                "Regular Season"
     nba_predictor/ingestion/future_schedule.py:114  startswith("002")
                                                (endpoint y, por import,
                                                odds-capture)
     nba_predictor/api/daily_predictions.py:977 startswith("002") (inicio de
                                                temporada para el régimen)
     nba_predictor/jobs/roster_signals.py:35    PRESEASON_PREFIX = "001"
     nba_predictor/ingestion/schedule.py:84     season_type = "Regular Season"
                                                (legacy)
     nba_predictor/ingestion/nba_client.py:58   season_type_nullable =
                                                "Regular Season" (legacy
                                                stats.nba.com)
   odds_logic.py no tiene prefijo propio: su índice sale de
   future_schedule, así que hereda el "002", y los eventos que no casan se
   archivan con matched=false. ingest_logic.py tampoco tiene prefijo
   propio: depende del normalizador de cdn_client. En scripts/ (ingest_job,
   odds_capture_job, rebuild_cloud, backfill_*) no hay ningún filtro de
   prefijo.

## D-EXP-12: umbrales binarios con nivel declarado ("X+ (80%)" y línea 50/50)
(PRE-REGISTRO SELLADO; borrador del chat de diseño + las 10 lecturas de la
revisión de Code, adjudicadas por Antonio el 2026-10-28)

PREGUNTA. ¿Puede el mensaje 2 publicar afirmaciones binarias del tipo
"20+ pts (80%)" y "28+ pts (50%)" cuyo acierto real coincida con el nivel
declarado, para los cuatro stats?

ANTECEDENTE Y DIFERENCIAS CON D-EXP-11. D-EXP-11 probó pisos al 90%
calibrados sobre el universo y cerró sin adopción: cobertura 85.9-87.5% en
destacados (no se transfirió) e informatividad 0.596. Aquí cambian dos cosas,
fijadas de antemano: nivel 80% (más informativo; falla 1 de cada 5 y lo
declara) y calibración sobre la población del selector (lección de D-EXP-11).
No es una repetición: es un pre-registro nuevo con parámetros distintos,
fijados antes de correr.

UNIVERSO Y POBLACIÓN. Los de D-EXP-7: jugados, >= 10 previos, shift 1, folds
de validación 2020-21 a 2025-26. Población primaria: el selector exacto
reconstruido, CON el filtro de producción Out/Doubtful donde hay PDF publish
(GemBox 2023-24..2025-26, instancias de D-EXP-1; legacy desde el 2018-12-18
hasta 2022-23, instancias de D-EXP-5). El puesto pasa al siguiente por
minutos, como en D-EXP-9. Las fechas sin PDF (antes del 2018-12-18, las 32
fechas de D-RES-3d y las fechas sin corte publish) quedan SIN filtro,
contadas y declaradas. Restringida a >= 10 previos y sin filas de arranque.
Se excluyen y cuentan los jugador-partidos cuyo equipo o rival está NYS en el
corte publish, como en D-EXP-9. Calibración y evaluación sobre la misma
población. Secundarias: titulares y universo (sin arranque), descriptivas,
con los mismos umbrales.

CENTRO. B0 (mediana de 10), el adjudicado. En el régimen de arranque no se
publica (el centro es C3 y no está calibrado; declarado). Si un experimento
posterior pre-registrado cambia el centro (p. ej. D-EXP-7b), los umbrales se
recalibran mediante una enmienda pre-registrada, como en D-EXP-11.

STATS. pts, reb, ast, fg3m.

BRAZOS (conformal de un lado, residuo con signo centro - real,
conformal_q_lower de D-EXP-11):
  U80: umbral = centro - q80 * dispersión_10, con q80 = cuantil 0.80 del
       residuo escalado en el train del fold. Publicado como "X+": X
       redondeado hacia arriba al entero, acotado en 0.
  U50: igual con el cuantil 0.50. Es la línea 50/50. Dirección esperada:
       umbral POR DEBAJO del centro (umbral/centro < 1), porque la selección
       por minutos regresa a la media (residuo medio de minutos -0.49 en
       D-EXP-9).
  U70: igual con el cuantil 0.70. DESCRIPTIVO, NO ELEGIBLE: se reporta su
       acierto e informatividad para informar un pre-registro futuro.
  Con dispersión_10 = 0: variante absoluta al mismo nivel, como en D-EXP-11.
  CALIBRACIÓN: el train del fold es la población primaria SIN arranque de las
  temporadas anteriores a la validada (misma población que la puerta).

MÉTRICAS. Acierto = fracción con real >= umbral publicado. Informatividad =
suma de umbrales / suma de centros (cociente de medias). Fracción de umbral 0.

FASE 0. El n se computa y se escribe aquí antes de cualquier métrica:
población de la puerta (seis folds, sin arranque, con filtro de estatus donde
hay PDF), estimada en ~23 000. Error estándar de una proporción por nivel,
agregado y por fold; es una cota optimista por dependencia entre filas del
mismo jugador. También se cuentan las filas sin filtro (fecha sin PDF) en el
train y en la puerta, y las exclusiones por NYS.

PUERTAS (sin lectura alternativa):
  - U80 calibra en un stat si el acierto agregado está en [78%, 82%] Y todos
    los folds en [75%, 85%]. La banda es de dos lados a propósito: aquí
    pasarse sí importa, porque un "80%" que acierta el 90% no dice nada.
  - U50 calibra si el agregado está en [48%, 52%] Y por fold en [45%, 55%].
  - Informatividad, SOLO para U80: publicable solo si umbral/centro >= 0.70
    en el stat. La puerta SE MANTIENE en 0.70.
  - Umbral publicado = 0 en más del 30% de la población de la puerta: "sin
    umbral útil" para ese par, en los DOS niveles.
  - Cada par (stat, nivel) se publica solo si cruzó todas sus puertas. La
    línea 50/50 se publica solo en puntos.

CUENTA PREVIA (regla de D-PROD-1b y D-RES-3e: magnitud computada antes de
prometer). Interpolando linealmente entre P80 y P90 de D-EXP-11 al 80% de
acierto: pts (76.0% / 0.740 y 85.9% / 0.596) da ~0.68; reb (77.1% / 0.724 y
87.5% / 0.547) ~0.67; ast (76.0% / 0.746 y 86.1% / 0.572) ~0.68. Calibrar
sobre la primaria lleva el acierto al 80% bajando el umbral, y eso baja la
informatividad hacia ~0.68.

EXPECTATIVAS. CENTRAL: U80 calibra en banda en pts, reb y ast y NO cruza la
informatividad (~0.68 < 0.70). fg3m sin umbral útil. U50 en banda, con
umbral/centro entre 0.95 y 1.0 y dirección < 1. U70 (descriptivo):
informatividad mayor que la de U80, sin expectativa numérica.

CONSECUENCIA ESCALONADA (en puntos, que decide el formato):
  (a) U80 de pts cruza acierto e informatividad -> formato completo: el
      mensaje 2 en modo R1 pasa a umbrales binarios, con cada par (stat,
      nivel) solo si cruzó y la línea 50/50 solo en puntos:
      "• Jokić (DEN): 20+ pts (80%) · 28+ (50%) · 10+ reb (80%) · 7+ ast
      (80%)". R1 se retira del mensaje y queda en el log.
  (b) U80 de pts calibra pero no cruza la informatividad -> se publica solo
      U50 en pts (si cruzó su puerta) junto al rango R1 vigente; U80 queda
      cerrado.
  (c) U80 de pts no calibra -> nada cambia.
En (a) y (b): presupuesto de longitud recomputado en el diseño (D-PROD-1b).
Monitor vivo: acierto de cada nivel publicado sobre los últimos 100; U80
fuera de [72%, 88%] o U50 fuera de [40%, 60%] durante 14 evaluaciones
diarias consecutivas = reversión declarada al formato vigente. El marcador
semanal en el canal queda FUERA de este pre-registro: va a D-PROD-2.

CONTROL DESCRIPTIVO. Acierto por subgrupos fijados de antemano: titulares y
banca, local y visitante, y arranque (con el umbral B0 que tendría; ahí no
se publicaría). Desvío > 5 puntos respecto al agregado del stat = hallazgo.

MUESTRA DE AUDITORÍA. 30 filas de la población de la puerta (seed 42), con
umbral U80 y U50, centro y real, escritas ANTES de cualquier métrica.

LEAKAGE. El de D-EXP-7: q solo del train, dispersión solo del pasado,
selector sin filas del día, estatus solo del corte publish.

ORDEN. Corre ahora (offline). Adopción, si algo cruza, como D-PROD-2 después
del 10 de noviembre, nunca el mismo día que el estreno de R1.

LO QUE NO HACE. No toca el mensaje 1, el modelo ni las features. No compara
contra líneas de casas (D-EXP-8). No publica umbrales sin nivel. No modifica
la etapa "pisos" de D-EXP-11.

RESULTADO: pendiente de fase 0 y ejecución.

SELLADO: 2026-10-28T21:38:50Z, ANTES de escribir código.

## D-EXP-12 RESULTADO (2026-10-28): EXPECTATIVA CENTRAL CUMPLIDA. U80
calibra en los cuatro stats y no cruza la informatividad (0.65-0.69).
Escalonada (b): solo U50 en puntos es publicable

CORRIDA ÚNICA: scripts/experiment_destacados_d12.py, un script aparte (no una
etapa de d7): la etapa "pisos" y las funciones protegidas de d7 solo se
importan, sin tocarlas. 2026-10-28T21:48:48Z -> 21:49:56Z, exit 0, a la
primera. Orden verificado por timestamps: pre-registro sellado 21:38:50Z;
fase0.json 21:49:55.953Z; audit_sample.json 21:49:55.988Z; results.json
21:49:56.36Z. Tests: 30 nuevos (tests/test_experiment_destacados_d12.py:
puerta de dos lados en los cuatro bordes de U80 y U50, calibración sobre la
primaria contra cálculo directo, filtro de estatus con fixture GemBox y
legacy, redondeo hacia arriba, regla del 30%, unión de estatus sin
duplicados y con conflicto ruidoso, escalonada). Suite 1238 passed, 15
deselected (1208 + 30). Ruff limpio.

INSTRUMENTO Y CONTEOS:
- Estatus: GemBox 35 607 incluidas (483 fechas), legacy 41 608 (693
  fechas); unidos 77 215 sin un solo par (game_id, player_id) duplicado ni
  en conflicto. Sufijo publish idéntico al de D-EXP-2b en las 483 + 693
  fechas (assert pasado). PDF del 2018-12-18 al 2026-04-12 (1 177 fechas);
  NYS 1 309 pares.
- Control: sin filtro, el selector reproduce EXACTO a D-EXP-7/11
  (primaria 22 745, puerta 19 793). Ese selector traía entre sus top 2 a
  4 556 Out y 253 Doubtful.
- Puerta (primaria con filtro, sin arranque, sin NYS): 20 505 = 2 921 /
  3 483 / 3 552 / 3 564 / 3 448 / 3 537. DESVIACIÓN de la estimación
  (~23 000): el +17% de D-EXP-9 no se trasladaba a seis folds sin
  arranque y sin NYS.
- Excluidas por NYS en validación: 2 714. Puerta sin filtro por falta de
  PDF: 0.
- Train por fold (primaria sin arranque, temporadas anteriores): 21 746 ->
  38 714. De él, 16 823 filas SIN filtro (anteriores al 2018-12-18), las
  mismas en todos los folds: 77% del train de 2020-21 y 43% del de 2025-26.
  Declarado como lo exige la lectura 2.
- FASE 0: error estándar agregado 0.28 pp (U80), 0.35 (U50) y 0.32 (U70);
  cota optimista.

TABLA — población de la puerta (acierto = real >= umbral publicado):
  stat nivel acierto  por fold (2020-21 .. 2025-26)   umbral centro inform umbral0  veredicto
  pts  U80   80.5%   80.2 81.4 81.6 80.4 79.8 79.6   13.86  20.22  0.686   0.3%  calibra, NO informa
  pts  U50   50.9%   51.4 52.3 50.5 50.7 51.2 49.3   20.30  20.22  1.004   0.0%  CRUZA, publicable
  pts  U70   70.3%   70.0 71.2 70.6 70.4 69.9 69.8   16.36  20.22  0.809   0.0%  descriptivo
  reb  U80   80.2%   79.7 80.1 79.8 80.4 81.6 79.6    3.95   5.79  0.683   1.3%  calibra, NO informa
  reb  U50   52.9%   54.3 52.1 52.8 51.9 53.6 52.9    6.00   5.79  1.037   0.0%  NO calibra (alto)
  reb  U70   70.3%   70.2 69.8 70.1 70.2 70.8 70.5    4.77   5.79  0.823   0.1%  descriptivo
  ast  U80   79.5%   79.9 79.6 79.6 79.8 80.2 78.3    3.24   4.68  0.693   4.3%  calibra, NO informa
  ast  U50   55.3%   56.1 55.5 55.7 55.6 55.4 53.6    4.87   4.68  1.042   0.2%  NO calibra (alto)
  ast  U70   69.7%   70.5 69.7 70.3 69.8 70.2 67.7    3.94   4.68  0.843   1.0%  descriptivo
  fg3m U80   80.1%   79.7 79.9 80.6 80.6 79.2 80.3    1.26   1.94  0.649  23.1%  calibra, NO informa
  fg3m U50   60.4%   61.5 60.0 61.0 61.2 59.1 59.5    2.10   1.94  1.080   9.3%  NO calibra (alto)
  fg3m U70   70.2%   70.3 70.0 70.7 70.1 69.9 70.2    1.68   1.94  0.866  12.6%  descriptivo
Secundarias con los mismos umbrales (U80, sin arranque): titulares
80.0-80.7%; universo 82.9-85.9%, porque calibrar sobre la primaria deja
holgado al universo, la imagen espejo de D-EXP-11.

VEREDICTO POR PAR (stat, nivel):
- U80: CALIBRA en los cuatro stats (agregado y los seis folds en banda) y
  NO cruza la informatividad en ninguno (pts 0.686, reb 0.683, ast 0.693,
  fg3m 0.649 < 0.70). Ninguno es publicable.
- U50: CRUZA solo en pts (50.9%, folds 49.3-52.3) y es publicable. En reb,
  ast y fg3m no calibra: el acierto queda POR ENCIMA de la banda (52.9 /
  55.3 / 60.4%). Además, por la regla de la línea 50/50, solo puntos
  podía publicarse.
- U70 (descriptivo, no elegible): acierto 69.7-70.3%, informatividad
  0.809 (pts), 0.823 (reb), 0.843 (ast), 0.866 (fg3m).
- Regla del 30% de umbral 0: no se activó en ningún par (máximo: fg3m U80
  con 23.1%).

CONSECUENCIA ESCALONADA QUE APLICA: (b). U80 de puntos calibra (80.5%) pero
no cruza la informatividad (0.686). Por la regla pre-registrada se publica
solo U50 en puntos, junto al rango R1 vigente, y U80 queda CERRADO.
OBSERVACIÓN PARA LA ADJUDICACIÓN DE PRODUCTO (es un hecho, no una
recomendación): U50 de puntos es B0 - 0.07 * dispersión, redondeado hacia
arriba (q50 escalado entre 0.07 y 0.08 según el fold), con umbral/centro
1.004. En la práctica es la mediana publicada redondeada hacia arriba, con
"(50%)" al lado.

CONTRA LAS EXPECTATIVAS:
- CENTRAL ("U80 calibra en banda en pts, reb y ast y NO cruza la
  informatividad, ~0.68"): CUMPLIDA. La cuenta previa interpolada desde
  D-EXP-11 acertó: 0.686 / 0.683 / 0.693 contra ~0.68 / 0.67 / 0.68.
- "fg3m sin umbral útil": FALLIDA. Umbral 0 en el 23.1%, por debajo del 30%;
  fg3m calibra en U80 con informatividad 0.649.
- "U50 en banda": CUMPLIDA solo en pts; FALLIDA en reb, ast y fg3m (por
  arriba).
- "U50 umbral/centro 0.95-1.0 con dirección < 1": FALLIDA en los cuatro;
  sale > 1 (1.004 / 1.037 / 1.042 / 1.080). Hallazgo declarado. Hecho
  medido que lo acompaña: el q50 escalado es exactamente 0.0 en reb, ast y
  fg3m, así que U50 = B0 redondeado hacia arriba. HIPÓTESIS POST-HOC, no
  probada: empates en stats discretos con mucha masa en real == B0 (el
  cuantil cae en 0 y la fracción con real >= umbral pasa del 50%), más el
  redondeo hacia arriba sobre centros pequeños.
- Pesimista ("U80 en banda pero umbral/centro < 0.70 en pts"): REALIZADO,
  tal como se esperaba de antemano.

CONTROL DESCRIPTIVO: la BANCA (n = 471 en la puerta; con el filtro de
estatus entran más no titulares que en D-EXP-11, donde eran 40) se desvía
más de 5 pp en todos los pares: +5.4 a +14.1 pp por encima del agregado.
Hallazgo por la regla. Local y visitante dentro de ±1.6 pp; arranque dentro
de ±2.4 pp.

ARCHIVOS: data/experiment_destacados_d12/fase0.json, audit_sample.json,
auditoria_muestra.txt (30 filas con centro, dispersión, U80, U50 y real por
stat), results.json (marca de una sola corrida) y tabla.txt.

PENDIENTE: auditoría humana de Antonio y adjudicación de producto.
[Adjudicación de producto RESUELTA, ver abajo; la auditoría humana sigue
pendiente.]

## D-EXP-12 ADJUDICACIÓN (Antonio, 2026-10-28)

- U80: CERRADO. Calibra (confirma la lección de D-EXP-11: calibrar y evaluar
  sobre la misma población) pero no cruza la informatividad de 0.70. No se
  reabre.
- U50 en pts: PUBLICABLE por la rama (b), como "línea". Pasa a D-PROD-2 con
  dos condiciones pre-registradas:
  (1) Línea en .5, sin empates. El hallazgo de q50 = 0 y acierto por encima
      de la banda en reb, ast y fg3m se atribuye a empates del ">=", como
      HIPÓTESIS NO PROBADA. Antes de publicar, verificación histórica de que
      el acierto de la línea en .5 cae en [48%, 52%].
  (2) Marcador semanal de acierto en el canal, OBLIGATORIO.
- U70: candidato prospectivo D-EXP-12b. No es elegible aquí porque se eligió
  tras ver los resultados. Puerta: acierto en [68%, 72%] Y informatividad
  >= 0.75, sobre destacados publicados de 2026-27 posteriores al sello, con
  los mismos cortes y el mismo script que D-EXP-7b. Adopción en 2027-28 si
  cruza. Lleva pre-registro propio, sellado antes de su primer dato.
- HALLAZGOS REGISTRADOS SIN ACCIÓN: n de la puerta 20 505 (estimado
  ~23 000); fg3m con umbral útil (23.1% de umbral 0, contra la expectativa
  de "sin umbral útil"); banca con n 471, solo descriptiva; train sin filtro
  de estatus antes del 2018-12-18.
- PENDIENTE: auditoría humana de Antonio sobre la muestra de 30
  (data/experiment_destacados_d12/auditoria_muestra.txt: umbral U80, U50,
  centro y real). Se marcará "[RESUELTO]" cuando llegue.

## D-PO-0b (2026-10-28): sondeos de histórico de playoffs

Solo lectura desde la laptop (IP local), 16:40-16:55 CDMX. Cero escrituras en
GCS o BigQuery; cero cambios de código. Script de un solo uso en el
scratchpad, fuera del repositorio.
DESVIACIÓN DECLARADA: scripts/backfill_injury_reports.py NO tiene modo de
solo sondeo (run_season descarga y persiste en GCS, y solo recorre fechas de
temporada regular). Se hizo un envoltorio que IMPORTA sus funciones de sondeo
(detect_era, find_cut, SeasonProber, con su sleep de 0.5 s, timeout y reglas
de parada) y hace SOLO HEAD, sin descargar. Las fechas sin ninguna sonda de
era viva se registraron como "sin PDF", sin barrer la familia completa (se
evitan ~120 HEAD por día sin partidos).

1. BOXSCORES DE PLAYOFFS Y PLAY-IN EN CDN/S3 (CDNClient: HEAD por base y, si
   200, GET para validar el JSON, sin persistir). 20 game_ids:
     control regular 0022400001 (ATL@BOS)                     S3 200, JSON ok
     2024-25 playoffs 0042400101 0042400171 0042400201
             0042400301 0042400311 0042400401 0042400407     S3 200, JSON ok
             (G7 de las Finales 2025: IND@OKC, 2025-06-23Z)
     2024-25 play-in  0052400101 0052400201                   S3 200, JSON ok
     2023-24 playoffs 0042300101 0042300201 0042300301
             0042300401 0042300405 (G5 de las Finales 2024)   S3 200, JSON ok
     control negativo 0042300406 (G6 de 2024, no existió)     403 en las dos bases
     2023-24 play-in  0052300101 0052300201                   S3 200, JSON ok
     Finales 2023 G1 0042200401, Finales 2022 G1 0042100401   S3 200, JSON ok
   El CDN frontal da 403 en las 20, como siempre (Decisión 9). En todas,
   game.gameId coincide y el matchup es el correcto. Disponibles AL MENOS
   desde los playoffs de 2021-22 (el más antiguo sondeado); los anteriores
   no se sondearon. El control negativo confirma que S3 discrimina
   existencia.

2. PDFs DE INJURY REPORT EN FECHAS DE PLAYOFFS (solo HEAD, 392 requests,
   regla de parada NO activada):
     2025-04-15 -> 2025-06-25 (72 fechas): 61 con PDF, 11 sin PDF. Las 61
       con publish Y late (61/61 cada uno; 84.7% de las fechas de
       calendario). Era vieja en todas, como corresponde (frontera
       2025-12-21/22). Corte uniforme: publish 02PM, late 10PM. Las 11 sin
       PDF son todas de junio: 06-01, 02, 03, 06, 09, 14, 17, 20, 23, 24 y
       25. Son compatibles con días sin partido ni partido al día siguiente,
       pero no se verificó contra un calendario de playoffs.
     2024, muestra de 10 fechas (seed 42 sobre 2024-04-16 -> 2024-06-17):
       9 con publish y late (02PM / 10PM); 2024-06-02 sin PDF.
     2023, muestra de 10 fechas (seed 42 sobre 2023-04-11 -> 2023-06-12):
       10/10 con publish y late (02PM / 10PM).
   NO ESTÁN ARCHIVADOS: viven en ak-static y no se persistieron (fuera del
   encargo). NOTA: los playoffs de 2023 son de la temporada 2022-23, cuyo
   productor de PDF era iTextSharp (cambio a GemBox fijado en el offseason
   de 2023). Sin descargarlos no se sabe qué generador tienen, ni si los
   lee el parser GemBox o el legacy.

3. EL PARTIDO 006 DEL CALENDARIO 2026-27: 0062600001, final de la Emirates
   NBA Cup ("Championship"), viernes 2026-12-11, 8:00 pm ET
   (2026-12-12T01:00Z, 19:00 CDMX), en sede neutral (Hinkle Fieldhouse,
   Indianápolis), con equipos TBD (teamId 0 en los dos lados). Es el ÚNICO
   partido de ese día. Hoy el endpoint filtra por prefijo "002"
   (future_schedule.py:114), así que ese día da games=[] y publica el
   heartbeat "🏀 Predicciones NBA · 11 dic 2026 / Sin partidos hoy.", sin
   mensaje 2. El día cae dentro de la ventana del criterio (10-21 -> 12-31),
   como día sin partidos de temporada regular.

DECISIONES (Antonio, 2026-10-28):
  - Histórico de playoffs RECUPERABLE: boxscores 004/005 en S3 desde al
    menos 2021-22; PDFs publish y late en ak-static para 2024 y 2025
    (GemBox) y 2023 (iTextSharp, pendiente de D-RES-3c). Backfill a
    prefijos propios y tabla aparte, jamás a games: tarea de D-PO-1,
    enero 2027. Habilita dry-runs reales en marzo y sombra retrospectiva
    de B-limpia sobre playoffs pasados antes de abril.
  - Final NBA Cup 2026-12-11 (0062600001): fuera del alcance por diseño,
    no bug. Pre-registrado: la rama de descanso del mensaje 1 pasa a
    decir "Sin partidos de temporada regular hoy", cambio de redacción
    incluido en el deploy de D-PROD-2; market_odds registrará ese día
    una fila matched=false por la final, esperado y correcto.

[NOTA DE TRANSCRIPCIÓN (2026-10-28), de Code. Arriba va el texto
recibido, verbatim; tres precisiones, sin cambiar las decisiones:
(a) "pendiente de D-RES-3c": D-RES-3c (parser legacy) está CERRADO y
    auditado para ITEXT_V1/ITEXT_V2 (D-RES-3, D-RES-3e). Lo pendiente
    para los PDFs de playoffs de 2023 es verificar su productor y su
    layout contra esa lista blanca; si no coinciden, caen en
    UnknownLayoutError, igual que el tramo de D-RES-3d.
(b) La rama de descanso del mensaje 1 se publica TODOS los días sin
    partidos 002, no solo el 11-dic (p. ej. 2026-12-07 y 2027-02-22).
    El cambio edita el golden congelado del heartbeat en los tests de
    format_daily_message: es el primer cambio al texto del mensaje 1
    desde su validación y se declara así en el deploy de D-PROD-2.
(c) market_odds escribe una fila por bookmaker que cotice el evento y
    por snapshot, no una sola: "una fila" se lee como "filas
    matched=false del evento de la final". Además, el evento solo
    aparecerá cuando The Odds API lo liste: con equipos definidos y, en
    temporada, con menos de un día de antelación (D-ODDS-8, paso 3).]

## D-PROD-2: línea 50/50 y split con/sin en el mensaje 2
(DISEÑO SELLADO; borrador del chat de diseño + revisión de Code + adjudicaciones de Antonio del 2026-10-28)

BORRADOR (verbatim, 2026-10-28; las ADJUDICACIONES de abajo prevalecen donde lo contradicen):

  Punto de partida medido. Modo R1, día de 15 partidos, dos destacados por
  equipo: 3 725 caracteres en el dry-run de v20, 60 líneas de jugador, unos
  60 caracteres cada una. Umbrales vigentes: 3 800 recorte a uno por equipo,
  4 000 omisión. Margen real: 75 caracteres. Ninguna pieza cabe añadida a las
  60 líneas en un día lleno. El diseño parte de eso.

  Las tres piezas, de más barata a más cara

  Línea 50/50 en puntos (de D-EXP-12, rama b). Sustituye a la mediana, no se
  añade: 28 pts pasa a 27.5 pts. El .5 lleva la semántica; el título la
  explica. Coste: +2 caracteres por línea, 120 en el día lleno. Verificación
  pre-registrada antes de publicar: acierto histórico de "real ≥ línea .5" en
  la primaria sin arranque, en [48%, 52%] agregado y [45%, 55%] por fold. Si
  no cae en banda, no se publica y se registra.
  Línea .5 = mediana de 10 redondeada hacia arriba al entero menos 0.5 si la
  mediana es entera; si la mediana ya es x.5, se queda. Regla única, sin
  parámetros.
  Split con/sin compañero. Solo cuando ese día el equipo del destacado tiene
  una baja fuerte en el corte publish (Out con rolling ≥ 25, la definición
  auditada 25/25 de D-EXP-9), y solo si el destacado tiene ≥ 5 partidos con y
  ≥ 5 sin ese compañero en sus últimos 20 jugados. Segunda línea: sin Murray:
  29.1 pts en 6. Unos 32 caracteres. Solo puntos, solo rank 1. Descriptivo:
  media simple, sin promesa. Frecuencia esperada: 15-25% de los rank 1, 4 a 8
  líneas extra en un día lleno, 130-260 caracteres.
  Frecuencia contra umbral redondo. 25+ en 6/10, con umbral = mayor múltiplo
  de 5 menor o igual a la mediana (27.5 → 25; 12 → 10; mínimo 5). Solo
  puntos, solo rank 1. Unos 13 caracteres, 30 líneas, 390 en el día lleno.
  Descriptivo: es historia, no probabilidad.

  Presupuesto computado, día lleno (15 partidos, 60 líneas):
    Composición                      Caracteres  Contra 3 800
    Actual (R1)                           3 725  cabe
    + línea .5                            3 845  recorte
    + línea .5 + split (6 líneas)        ~4 040  omisión
    + las tres                           ~4 430  omisión
  Conclusión: en días llenos no cabe ni la pieza más barata sin recorte. Hay
  que cambiar la escalera de degradación, no las piezas.

  Escalera de degradación nueva (sustituye a la actual, pre-registrada):
  Formato completo. Si > 3 800: quitar frecuencias (pieza 3). Pie: (sin
  frecuencias hoy por longitud). Si sigue > 3 800: quitar splits (pieza 2).
  Pie: (sin splits hoy por longitud). Si sigue > 3 800: recorte a uno por
  equipo (regla actual). Si > 4 000: omisión (regla actual).
  Con esto, un día de 15 partidos probablemente baja a la etapa 3 o 4; un día
  de 10 partidos (unas 40 líneas, ~2 500 base) lleva las tres piezas. Code
  computa la tabla exacta de "hasta cuántos partidos cabe cada etapa" con el
  calendario real, en el diseño, no en el test.

  Marcador semanal. No es tercer mensaje (eso toca n8n). Los lunes, el
  mensaje 2 lleva una cabecera extra: 📊 Semana: líneas 50/50 acertaron 52 de
  101 · rango 80% cubrió 79 de 98. ~90 caracteres, un día a la semana, entra
  en la escalera como parte del formato completo. Sale del script de
  seguimiento (ya calcula ambas cosas). Pre-registro: solo se publica con
  ≥ 50 observaciones evaluables en la semana; si no, 📊 Semana: muestra
  insuficiente.

  Qué no cambia. Mensaje 1, modelo, features, ausencias. R1 sigue con su
  monitor del 72%. En régimen de arranque (próximo: octubre 2027) las tres
  piezas se apagan con el título de régimen, porque dependen de la ventana de
  10 de la temporada en curso.

  Monitores. Línea .5: acierto sobre los últimos 100 publicados fuera de
  [40%, 60%] durante 14 evaluaciones seguidas = reversión declarada a
  mediana. Frecuencia y split no tienen promesa que monitorear; solo un test
  de no leakage (ningún valor usa el partido del día).

  Calendario. Revisión de Code mañana; sello y verificación de la línea .5 el
  jueves; implementación jueves y viernes; deploy dormido el 4 o 5 de
  noviembre (activo solo fuera de régimen, así que dormido por construcción
  hasta el 10); dry-runs diarios de fecha forzada del 5 al 9; activación
  automática el 10 junto con R1, un solo formato nuevo ensayado cinco veces.

ADJUDICACIONES (Antonio, 2026-10-28):
a. FRECUENCIA contra umbral: FUERA, pospuesta sin fecha. Sesgo por
   construcción (umbral <= mediana <= 5º valor más alto de los 10: siempre
   da >= 5/10); solo tiene sentido con un umbral externo (D-EXP-8).
b. ESCALERA: (1) formato completo -> (2) sin splits, con pie único
   "(sin splits hoy por longitud)" -> (3) top 1 con formato completo (línea,
   splits y marcador) -> (4) omisión si aun así > 4 000. Umbral de los
   escalones 1-3: 3 800; omisión: 4 000 (regla vigente).
c. ACTIVACIÓN 2026-11-12 por constante de fecha en config (respeta D-EXP-12:
   "nunca el mismo día que el estreno de R1", que es el 10-nov). Un solo
   deploy dormido. La constante se retira tras esta temporada.
d. LECTURAS DE LA REVISIÓN DE CODE, tal cual:
   - Fuente de estatus: los estatus EN MEMORIA del mismo snapshot publish
     que sustenta el mensaje 1 (regla de D-PROD-1c); jamás absences_applied
     leído de vuelta de predictions_log (se escribe después; sirve para
     auditar).
   - Línea .5 = ceil(mediana) - 0.5 = entero publicado hoy - 0.5 (28 ->
     27.5; 27 -> 26.5; mediana 27.5 -> 27.5). Se gradúa como real >= entero
     publicado. pts_median del log sigue guardando B0 (D-EXP-7b intacto).
   - Ventana corta (3-9 jugados): sin línea; centro entero y sufijo, como
     D-PRE-11b.
   - Split: UN compañero por destacado (el de mayor rolling entre las bajas
     fuertes que cumplan >= 5 con y >= 5 sin; empate por player_id; jamás
     combinaciones); solo Out (no Doubtful); solo partidos con el EQUIPO
     ACTUAL del destacado; "sin" cuenta solo desde la llegada del compañero
     (su primera fila con ese equipo); nombre completo; enteros (redondeo
     medio-arriba); se muestra también el "con", ambos como media de la
     misma ventana de 20.
   - Formato: el split es sublínea bajo el jugador; todo lo de puntos junto:
       • Nombre (TRI) (Q): 27.5 pts (21-35) · 8 reb · 5 ast · 3 3P
         ↳ sin Nombre Apellido: 29 pts en 6 · con: 24 en 14
   - Marcador: los lunes (target_date en lunes, CDMX); semana cerrada lunes
     a domingo por fecha de partido ET; solo servida de record y jugadores
     evaluados; la línea cuenta solo las publicadas como .5 y el rango solo
     los R1; mínimo 50 por métrica, por separado ("muestra insuficiente"
     para la que no llegue); la escalera NUNCA lo quita (va también en la
     línea de omisión); un lunes sin mensaje 2 (esta temporada 2026-12-07 y
     2027-02-22) pasa al siguiente día con mensaje 2, con la misma semana;
     primer marcador 2026-11-16 [con la activación del 12-nov cubre del 12
     al 15, nota de Code]; el cálculo vive en el PAQUETE, no en el script de
     seguimiento, con lector por filas del query result (lección de la capa
     5, sin to_dataframe).
   - Pie único: "(sin splits hoy por longitud)".
e. ALTER AUTORIZADO: columnas nullable en player_predictions_log para la
   línea publicada y el split publicado, en la secuencia del deploy.
f. ENSAYO: arnés local con PDF real para splits y marcador (el modo R1 se
   fuerza en el arnés, jamás con un parámetro del endpoint); dry-runs de
   fecha forzada para lo demás; cinco antes del 12-nov.
g. Texto de descanso del mensaje 1: "Sin partidos de temporada regular hoy"
   (D-PO-0b), en el mismo deploy.

PRESUPUESTO RECOMPUTADO SIN FRECUENCIAS (Code, 2026-10-28). Destacados con
el código de producción en modo R1, as-of historial hasta el 2026-10-27,
nombres reales, calendario CDN desde el 2026-11-10 (139 fechas con partidos
002; 3 de ellas de NBA Cup con equipos TBD, 2 partidos, etapa 1).
Validación: el formato vigente de un día de 15 mide 3 733, idéntico al
dry-run de v21 del 12-23. Supuestos: título "(últimos 10 · línea 50/50 y
rango de puntos al 80%)"; split "  ↳ sin {nombre completo}: NN pts en N ·
con: NN en NN"; marcador solo los lunes. Escenario esperado: splits en el
20% de los rank 1, sin Q. Cota pesimista: 50% de splits y todos los rank 1
con Q más la leyenda.
  Caracteres (mín-máx), escenario esperado:
  partidos (días)  vigente      1 completo   2 sin splits  3 top 1       etapa
  10 (12)          2 499-2 541  2 790-2 903  2 615-2 728   1 598-1 695   1
  11 (5)           2 726-2 779  3 052-3 101  2 881-2 920   1 734-1 800   1
  12 (5)           2 974-3 007  3 328-3 356  3 105-3 138   1 905-1 937   1
  13 (3)           3 238-3 251  3 604-3 620  3 377-3 390   2 059-2 076   1
  14 (1)           3 485        3 914        3 632         2 248         2
  15 (2)           3 733        4 162-4 170  3 888         2 378-2 386   3
  Cota pesimista (completo / sin splits / top 1): 10 -> 3 231-3 357 (1);
  12 -> 3 833-3 871 / 3 256-3 289 (2); 13 -> 4 175-4 188 / 3 536-3 549
  (2); 14 -> 4 482 / 3 799 (2, a 1 carácter del umbral); 15 -> 4 794-4 799
  / 4 063 / 3 010-3 015 (3).
  Hasta cuántos partidos cabe cada etapa: esperado 1 <= 13, 2 = 14, 3 = 15;
  pesimista 1 <= 11, 2 = 12-14, 3 = 15. Omisión: nunca.
  Días de la temporada por etapa (139): esperado 136 / 1 (2027-04-09) / 2
  (2026-12-23, 2027-04-11) / 0; pesimista 128 / 9 / 2 / 0.
  Límites declarados: los nombres cambian al rodar la ventana; 5 novatos
  hoy con ventana corta (sin rango y con sufijo: -4 caracteres netos cada
  uno al completar 10); la frecuencia real de splits no está medida.

SELLADO: 2026-10-29T02:30:32Z

## D-PROD-2a: verificación histórica de la línea .5 (PRE-REGISTRO SELLADO)

PREGUNTA. ¿La línea .5 de D-PROD-2 (línea = ceil(mediana de 10) - 0.5,
graduada como real >= ceil(mediana)) acierta el 50% en la población
histórica de destacados?

NATURALEZA. Medición NUEVA, no segunda corrida de D-EXP-12: su
results.json queda intacto y sus puertas no se re-evalúan. Una sola
corrida, con archivo propio (data/experiment_dprod2a/results.json como
marca de corrida única). Script aparte; d12, d7 y d9 solo se importan,
sin tocarlos.

POBLACIÓN. La de la puerta de D-EXP-12, reconstruida con construir() de
d12: primaria con el filtro de producción Out/Doubtful, sin arranque, sin
NYS, temporadas de validación 2020-21 a 2025-26. CONTROL DE INSTRUMENTO
(se verifica antes de medir; si falla, se DETIENE sin medir): n = 20 505 y
por fold 2 921 / 3 483 / 3 552 / 3 564 / 3 448 / 3 537, exactos. Ventana de
10: por construcción (universo >= 10 previos; B0 = mediana de los 10); se
afirma con assert.

EVENTO. real >= ceil(B0), en pts, reb, ast y fg3m.

PUERTA (por stat; decide SOLO pts): acierto agregado en [48%, 52%] Y cada
fold en [45%, 55%]. Sin lectura alternativa.

EXPECTATIVAS.
- pts: acierto <= 50.9% (el U50 de D-EXP-12). Razón: el evento coincide con
  U50 salvo en filas de dispersión alta (mediana entera con q·d >= 1;
  mediana x.5 con q·d >= 0.5), donde la línea pide un punto más.
- reb, ast y fg3m: reproducción EXACTA del U50 de D-EXP-12 (52.9% / 55.3% /
  60.4% agregados y los mismos folds), porque ahí q50 = 0 y q_abs = 0 hacen
  que U50 = ceil(B0) por identidad. Cualquier diferencia = defecto de
  instrumento, se declara y no se adjudica la puerta de pts hasta
  explicarla. No cruzan (por arriba), sin consecuencia: solo decide pts.

DESCRIPTIVOS (no deciden): en pts, n y acierto por mediana entera vs x.5;
n y acierto de las filas donde la línea difiere de U50 (U50 recalculado con
los q por fold SELLADOS en el results.json de D-EXP-12, sin recalibrar).

CONSECUENCIA. Si pts cruza: D-PROD-2 publica la línea .5 y el marcador
mide línea y rango. Si no cruza: D-PROD-2 sale SIN línea (la mediana
entera sigue) y el marcador mide solo el rango R1.

SALIDA. data/experiment_dprod2a/results.json y tabla.txt, con acierto
agregado y por fold de los cuatro stats.

SELLADO: 2026-10-29T02:30:58Z, ANTES de escribir el script y de correrlo.

## D-PROD-2a RESULTADO (2026-10-28): la línea .5 CRUZA en puntos (50.10%).
D-PROD-2 sale CON línea

CORRIDA ÚNICA: scripts/experiment_dprod2a.py (script aparte; d12, d7 y d9
solo se importan, sin tocarlos), 2026-10-29T02:32:08Z -> 02:33:16Z (20:32 a
20:33 CDMX del 28), exit 0, a la primera. Orden verificado por timestamps:
pre-registro sellado 02:30:58Z, script escrito después, results.json y
tabla.txt a las 02:33:16Z. Tests: 10 nuevos (tests/test_experiment_dprod2a.py:
línea = ceil(mediana) - 0.5 en mediana entera y x.5, evento real >= entero,
puerta de dos lados en los ocho bordes). Suite 1248 passed, 15 deselected
(1238 + 10). Ruff limpio.

CONTROL DE INSTRUMENTO (verificado antes de medir): n = 20 505 y por fold
2 921 / 3 483 / 3 552 / 3 564 / 3 448 / 3 537, exactos. B0 sin NaN en los
cuatro stats (ventana de 10 por construcción).

TABLA (acierto = real >= ceil(mediana), población de la puerta de D-EXP-12):
  stat  acierto  por fold (2020-21 .. 2025-26)          U50 D-EXP-12  puerta
  pts   50.10%   50.46 51.22 49.58 49.97 50.70 48.80     50.85%       CRUZA
  reb   52.86%   54.30 52.05 52.76 51.85 53.60 52.87     52.86%       no cruza
  ast   55.27%   56.08 55.47 55.69 55.58 55.37 53.58     55.27%       no cruza
  fg3m  60.37%   61.52 59.98 61.01 61.22 59.14 59.51     60.37%       no cruza

VEREDICTO (decide solo pts): CRUZA. Agregado 50.10% en [48%, 52%]; los seis
folds entre 48.80% y 51.22%, dentro de [45%, 55%]. Por la consecuencia
pre-registrada, D-PROD-2 publica la línea .5 y el marcador mide línea y
rango. reb, ast y fg3m no cruzan (por arriba), sin consecuencia.

CONTRA LAS EXPECTATIVAS:
- pts <= 50.9%: CUMPLIDA (50.10%).
- reb, ast y fg3m reproducen EXACTO el U50 de D-EXP-12: CUMPLIDA, agregado
  y los seis folds idénticos (diferencia < 1e-12). El instrumento es el
  mismo.

DESCRIPTIVOS:
- pts, por mediana: entera n = 10 433, acierto 50.85%; x.5 n = 10 072,
  acierto 49.33%.
- pts, filas donde la línea difiere de U50: 3 273; ahí la línea acierta
  48.64% y U50 53.22%. Es la dispersión alta, donde la línea pide un punto
  más; por eso el agregado baja de 50.85% a 50.10%, en la dirección
  pre-registrada.
- reb / ast / fg3m, por mediana: entera 56.16% / 59.07% / 66.14%; x.5
  48.41% / 49.34% / 47.52%. Las medianas x.5 aciertan cerca del 50% y las
  enteras muy por encima: es consistente con la hipótesis de empates del
  ">=" que D-EXP-12 dejó NO PROBADA (con mediana entera, real == mediana
  cuenta como acierto). Descriptivo, no pre-registrado como prueba: la
  hipótesis sigue sin probarse formalmente.

DESVIACIÓN DECLARADA: el U50 de pts recalculado con los q por fold del
results.json de D-EXP-12 da 50.836%, contra 50.851% sellado (3 filas de
20 505). Causa probable, no verificada: esos q están guardados redondeados
a 6 decimales y algunas filas en el borde de ceil cambian. Afecta solo al
descriptivo "filas donde la línea difiere de U50" (posible error de unas 3
filas en 3 273), no a la puerta ni al veredicto: el evento de la línea no
usa q.

ARCHIVOS: data/experiment_dprod2a/results.json (marca de corrida única) y
tabla.txt.

ESTADO: la implementación de D-PROD-2 NO empieza hasta el adelante de
Antonio, con este resultado en mano.

## D-PROD-2 RESULTADO (2026-10-28/29): línea 50/50, split y marcador EN PRODUCCIÓN,
dormidos hasta el 2026-11-12. v22. Congelamiento reiniciado

PROCESO: adelante de Antonio para implementar (2026-10-28) y adelante aparte
para el deploy con el hash ("commit hecho, despliega esta noche", 2026-10-28).
Commit 2ad22ad, verificado HEAD == 2ad22ad con el árbol limpio antes del build.
Tag de imagen v22, el siguiente libre (v16..v21 existentes). Tag del commit:
api-v22. Todo fuera de la franja 12:50-13:30 CDMX (22:03 a 22:13 CDMX del 28).

SECUENCIA:
1. ALTER TABLE player_predictions_log ADD COLUMN (7 columnas nullable):
   pts_line FLOAT64, split_mate_id INT64, split_mate_name STRING, split_n_with
   INT64, split_pts_with FLOAT64, split_n_without INT64, split_pts_without
   FLOAT64. bq show: 24 campos, los 7 nuevos al final; 738 filas previas con
   NULL. ACL de la tabla intacta (etag BwZcMQU3q4M=, única binding
   predictions-api-sa dataEditor). Hecho ANTES del deploy: el código escribe
   los 24 campos y contra la tabla vieja la escritura fallaría (best-effort,
   WARNING, evidencia perdida).
2. Build predictions-api:v22 desde 2ad22ad (build 84e8cc0b, 1M60S, digest
   sha256:7d2818ed2ece2055decab0590f8c628a6826a7b3355fc832585e0629f717c5ca).
3. Deploy: revisión predictions-api-00022-mek. DESVIACIÓN DECLARADA de la
   secuencia: se desplegó con --no-traffic --tag=dprod2 para llamar a v21 y a
   v22 A LA VEZ en el dry-run (1) — el 29 ya era "hoy" en ET y el corte del PDF
   cambia cada 15 minutos, así que dos llamadas separadas podían diferir por el
   dato y no por el código. Pasados los tres dry-runs: update-traffic 100% a
   00022-mek y --remove-tags=dprod2 (04:12:04Z). describe: 100% 00022-mek,
   imagen v22, 1Gi, 1 CPU, predictions-api-sa, NBA_PREDICTOR_MODE=cloud,
   maxScale 20, startup-cpu-boost (configuración heredada). Sin registros
   WARNING o peores de la revisión en Cloud Logging.

DRY-RUNS (disparos manuales de la ventana, todos fuera de la franja):
  2026-10-29T04:04:32Z  v21 ?date=2026-10-29 (línea base): 200 en 42.5 s
  2026-10-29T04:09:09Z  v21 y v22 a la vez ?date=2026-10-29: 200 en 36.0 / 26.5 s
  2026-10-29T04:10:05Z  v22 ?date=2026-11-12: 200 en 30.9 s
  2026-10-29T04:10:36Z  v22 ?date=2026-11-16: 200 en 49.7 s
  Filas escritas (excluidas por la regla de record: predicted_at del 28 en
  CDMX): v22 12/16/32 en player_predictions_log y 3/4/8 en predictions_log
  para 10-29 / 11-12 / 11-16; la línea base y la llamada simultánea de v21
  escribieron también sus filas de 10-29.
(1) 2026-10-29, régimen: mensaje 2 de v22 IDÉNTICO byte a byte al de v21 en la
    llamada simultánea (834 caracteres) y también a la línea base de las
    04:04Z; mensaje 1 idéntico. highlights_dprod2 False, sin línea ni split
    en data, scoreboard None. Regla de rollback NO activada.
(2) 2026-11-12, R1 activo: 4 partidos, modo r1, highlights_dprod2 True, título
    "(últimos 10 · línea 50/50 y rango de puntos al 80%)", 16 de 16 destacados
    con línea .5, 0 splits (fecha futura: feed_down "fecha FUTURA ... 0
    sondas", declarado), 1 115 caracteres, sin recorte, cero "#". HALLAZGO 4
    (abajo): la cabecera "📊 Semana 2-8 nov: muestra insuficiente" apareció un
    jueves.
(3) 2026-11-16, lunes: "📊 Semana 9-15 nov: muestra insuficiente" — NO
    "marcador no disponible": predictions-api-sa lee player_predictions_log
    en la nube. 8 partidos, 32 destacados, 29 con línea (3 de ventana corta,
    sin línea, como manda la regla), 2 096 caracteres.

CAMBIO AL GOLDEN DEL MENSAJE 1: la rama de descanso pasa de "Sin partidos
hoy." a "Sin partidos de temporada regular hoy." (D-PO-0b). Primer cambio al
texto del mensaje 1 desde agosto, solo en esa rama; tres tests editados y
declarados (golden de test_daily_predictions y dos aserciones de servidor).

LOS TRES HALLAZGOS DE LA IMPLEMENTACIÓN:
1. Borde pesimista del día de 14 partidos sin splits: 3 789 caracteres, no los
   3 799 del presupuesto sellado. La simulación del presupuesto puso el .5
   también en las 5 líneas de ventana corta de ese día, que por regla no lo
   llevan (5 x 2 = 10). El test fija 3 789.
2. El marcador declara su propio fallo en real: contra BigQuery ANTES del
   ALTER respondió "400 Unrecognized name: pts_line" y la cabecera salió como
   "marcador no disponible hoy", sin tumbar el mensaje 2.
3. Muchos menos splits que los supuestos: en los ensayos con PDF real, 1 de 18
   rank 1 el 2026-10-26 (Shai Gilgeous-Alexander sin Jalen Williams, 10 con y
   10 sin) y 0 de 8 el 2026-10-27, contra el 20% del presupuesto. Las bajas
   fuertes de este arranque son sobre todo jugadores con pocos partidos con
   su equipo (Drake Powell, Keegan Murray...), que no llegan a 5 con y 5 sin.

HALLAZGO 4 DEL DEPLOY (no bloqueante, adjudicación pendiente): la regla "un
lunes sin mensaje 2 pasa al siguiente día" se evalúa sobre los partidos YA
JUGADOS de la semana (tabla games). En un dry-run de fecha FUTURA esos días
aún no se jugaron, así que el jueves 12-nov forzado lleva la cabecera del
marcador. En vivo no ocurre: el 12-nov a las 13:00, los partidos del 9 al 11
ya están ingeridos y el marcador no sale. Límite derivado en vivo: si la
ingesta de un día falla, la cabecera puede repetirse al día siguiente
(benigno: es la misma semana, declarada). Fix posible (no aplicado; requiere
commit y deploy): evaluar la regla contra el calendario de la temporada en
vez de contra lo jugado.

ARNÉS: scripts/arnes_dprod2.py (solo lectura; PDF publish archivado por el
propio endpoint, el mismo _fetch_absences, modo R1 y D-PROD-2 forzados en el
arnés). Ensayos: 2026-10-27 (4 partidos, 22 Out, 0 splits, 1 149 caracteres)
y 2026-10-26 lunes (9 partidos, 1 split, 2 541 caracteres). Marcador de la
semana 19-25 oct: el cálculo real del paquete da "muestra insuficiente" (no
hay líneas ni R1 publicados esa semana); la SIMULACIÓN declarada (línea desde
pts_median, que en régimen es C3, y [mín, máx] de régimen como R1) dio 32 de
79 y 59 de 79: ensaya la mecánica, NO es comparable con el 50% esperado.

Suite: 1294 passed, 15 deselected (1248 + 46 de tests/test_dprod2.py). Golden
v21 del mensaje 2 generado con el código del tag api-v21 sobre días reales de
13, 14 y 15 partidos (tests/fixtures/dprod2_golden_v21.json).

PLAN DE DRY-RUNS DIARIOS (2026-10-30 -> 2026-11-11), cinco como mínimo antes
del 12 (adjudicación f): cada día, fuera de 12:50-13:30 CDMX (propuesta: entre
20:00 y 22:00 CDMX), GET /predictions/today?date=2026-11-12 con el calendario
real del día. Se registra cada uno aquí como disparo manual (timestamp,
status, latencia). Verificación por corrida: 200; título nuevo; todas las
líneas de ventana 10 con .5 y las cortas sin línea; cero "#"; longitud del
mensaje 2; escalón de la escalera; cabecera "📊 Semana 2-8 nov: muestra
insuficiente" presente (ESPERADA por el hallazgo 4, artefacto de la fecha
futura); filas en ambos logs con served_by 00022-mek (excluidas por la regla
de record). Cualquier "#", un 500, o una línea .5 en ventana corta = hallazgo
inmediato. El primer split en vivo y el primer marcador real no se ensayan
así (fecha futura sin PDF ni resultados): llegan con el 12-nov y el 16-nov.

PRE-REGISTRO EN VIVO:
- 2026-11-10 y 11-11 (R1, antes de la activación): mensaje 2 con el título
  R1 vigente, sin .5, sin splits ni marcador — byte a byte el formato de v21.
- 2026-11-12 (activación): título nuevo, líneas .5, splits donde el PDF lo
  permita, SIN marcador (no es lunes y la semana ya tuvo partidos).
- 2026-11-16 (primer lunes): marcador de la semana 9-15 nov, con la línea
  contada solo del 12 al 15 y el rango R1 del 10 al 15.

ROLLBACK disponible: predictions-api:v21 (revisión 00021-vd8). Las 7
columnas nuevas no estorban a v21.

CONGELAMIENTO: reiniciado desde predictions-api v22 (predictions-api-00022-mek)
/ ingest-job v14 (generación 11, sin cambios) / odds-capture v15 (generación
6, sin cambios).

DECISIONES POST-DEPLOY (Antonio, 2026-10-29):
1. RECETA ADOPTADA para deploys dormidos de predictions-api (la desviación
   declarada del deploy de v22 pasa a ser el procedimiento):
   (a) desplegar la revisión nueva SIN tráfico y con etiqueta temporal
       (gcloud run deploy ... --no-traffic --tag=<etiqueta>);
   (b) llamar A LA VEZ a la revisión vigente (URL canónica) y a la nueva (URL
       de la etiqueta) con la MISMA ?date, para que el corte del PDF y el
       resto del dato sean idénticos y la única variable sea el código;
   (c) comparar byte a byte los mensajes que deben quedar intactos;
   (d) SOLO DESPUÉS, el cambio de tráfico (gcloud run services
       update-traffic predictions-api --to-revisions=<revisión>=100) y el
       retiro de la etiqueta (--remove-tags=<etiqueta>), con describe al
       final. Cualquier diferencia en (c) = la revisión nueva no recibe
       tráfico y se reporta.
   Todo fuera de la franja 12:50-13:30 CDMX; las llamadas cuentan como
   disparos manuales y se registran.
2. FIX PENDIENTE DEL MARCADOR (hallazgo 4): la regla "un lunes sin mensaje 2
   pasa al siguiente día" se evaluará contra el CALENDARIO de la temporada
   (días con partidos 002 programados), no contra lo jugado (tabla games).
   Así se corrigen dos cosas: el artefacto de los dry-runs con fecha futura y
   la repetición de la cabecera si falla la ingesta de un día. SIN deploy
   propio: entra en el siguiente deploy de predictions-api que haya, con su
   test. Hasta entonces, la cabecera "📊 Semana 2-8 nov: muestra
   insuficiente" sigue siendo ESPERADA en los dry-runs con ?date=2026-11-12.
