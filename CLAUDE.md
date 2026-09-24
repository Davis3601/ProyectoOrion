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
