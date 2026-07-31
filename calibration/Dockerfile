# Calibration runner — the Cloud Run image.
#
# Serves both route families from one container: `/tasks/*` for Cloud
# Scheduler and `/api/*` for the operator dashboard. One image rather than two
# services because they share a Postgres pool, a Firestore client, and a
# config surface — and because a second service would double the cold starts
# to save nothing.
#
# Build from the repository root so the build context is this directory only:
#   docker build -t anizai-calibration:0.1.0 calibration/

FROM python:3.11-slim AS base

# Fail fast and loudly rather than buffering output into a void — on Cloud Run
# an unflushed stdout means a crash with no log line explaining it.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# psycopg2-binary ships its own libpq, so no build toolchain is needed. Kept
# as a note rather than a commented-out apt line: if psycopg2 is ever swapped
# for the source distribution, this is where libpq-dev and gcc go.

# Dependencies first, as their own layer — application code changes on every
# deploy, dependencies change rarely, and this ordering is what keeps rebuilds
# to seconds.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir \
        "fastapi==0.115.0" \
        "uvicorn[standard]==0.30.6" \
        "firebase-admin==6.5.0"

COPY src/ ./src/

ENV PYTHONPATH=/app/src

# Run as a non-root user. Cloud Run does not require it, but the container may
# outlive Cloud Run, and root-by-default is a decision nobody makes on purpose.
RUN useradd --create-home --uid 1000 calibration \
    && chown -R calibration:calibration /app
USER calibration

# Cloud Run injects PORT and it is not always 8080. Honouring it is what
# separates "the service starts" from "the service is reachable".
ENV PORT=8080
EXPOSE 8080

# Single worker on purpose. The tasks are I/O-bound and scheduled minutes
# apart; a second worker would double the Postgres pool for no throughput, and
# Cloud Run scales by adding instances rather than workers.
CMD exec uvicorn calibration.server:app \
        --host 0.0.0.0 \
        --port ${PORT} \
        --workers 1 \
        --timeout-keep-alive 65
