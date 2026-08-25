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

### Fase 6 — Monetización + agente (documentada; NO implementar)
Especificación completa en la sección "Fase 6" de decisiones (arriba).
El agente LLM original queda dentro: capa de explicación/interacción sobre
el canal, con el aislamiento de seguridad ya especificado.

### Camino 5 — Mejora del modelo (siempre-después)
Ponderación temporal, ventanas 5/15/20, SRS, calibración explícita, injury
reports históricos para G5 (archivo diario vía método 15 + experimento
P(juega|Doubtful/Questionable) pre-registrado), comparación vs Vegas
(benchmark final).

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
- Filosofía: fallar ruidosamente, nunca datos a medias en silencio.
