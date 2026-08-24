# Cloud Run Job de ingesta incremental diaria — Fase 5b (Decisión 7).
#
# Imagen mínima: python:3.11-slim.
# Orden de capas: dependencias → código (cache de deps se mantiene si solo cambia código).
#
# Construir:
#   docker build -t nba-ingest-job .
# Correr localmente (requiere ADC o cuenta de servicio montada):
#   docker run --rm \
#     -e NBA_PREDICTOR_MODE=cloud \
#     -e GCP_PROJECT_ID=predictorsnonprod \
#     -v ~/.config/gcloud:/root/.config/gcloud \
#     nba-ingest-job

FROM python:3.12-slim

WORKDIR /app

# Capa 1: dependencias (se reconstruye solo si requirements.lock cambia)
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Capa 2: código fuente
COPY nba_predictor/ nba_predictor/
COPY scripts/ scripts/

# nba_predictor se importa como módulo local (no instalado como paquete)
ENV PYTHONPATH=/app

ENTRYPOINT ["python", "scripts/ingest_job.py"]
