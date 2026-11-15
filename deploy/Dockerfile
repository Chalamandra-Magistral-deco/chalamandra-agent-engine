ARG CATEDRALX_BASE_IMAGE=catedralx:v1.0-canonico
FROM ${CATEDRALX_BASE_IMAGE} AS catedralx
# Imagen base pinneada por digest (python:3.12-slim, 2026-11-14).
# Para subirla: consultar el digest en
# https://hub.docker.com/v2/repositories/library/python/tags/3.12-slim
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ARG AGENTS_REVISION=unknown
ARG AGENTS_CODE_DIRTY=unknown
ARG CATEDRALX_REVISION=unknown
ARG CATEDRALX_CODE_DIRTY=unknown
LABEL com.chalamandra.agents.revision=$AGENTS_REVISION \
      com.chalamandra.agents.code.dirty=$AGENTS_CODE_DIRTY \
      com.chalamandra.catedralx.revision=$CATEDRALX_REVISION \
      com.chalamandra.catedralx.code.dirty=$CATEDRALX_CODE_DIRTY
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    CATEDRALX_PATH=/app/CatedralX \
    CATEDRALX_STATE_PATH=/home/chalamandra/.local/state/chalamandra/catedralx/hormonas.json \
    API_BIND_HOST=0.0.0.0 \
    CHALAMANDRA_API_HOST=0.0.0.0 \
    ALLOW_CONTAINER_BIND=true \
    API_PORT=8766 \
    CHALAMANDRA_API_PORT=8766 \
    CHALAMANDRA_STATE_DIR=/home/chalamandra/.local/state/chalamandra
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash chalamandra \
    && mkdir -p /home/chalamandra/.local/state/chalamandra/catedralx /home/chalamandra/.local/state/chalamandra/agents \
    && chown -R chalamandra:chalamandra /home/chalamandra
COPY pyproject.toml ./
COPY requirements.lock ./
COPY api/ ./api/
COPY scripts/ ./scripts/
COPY core/ ./core/
COPY pipeline/ ./pipeline/
COPY adapters/ ./adapters/
COPY config/ ./config/
COPY registry/ ./registry/
COPY telemetry/ ./telemetry/
COPY dynamics/ ./dynamics/
# Dependencias de runtime pinneadas (requirements.lock, generado con
# scripts/gen_requirements_lock.py desde el venv que pasó la suite);
# el paquete en sí se instala sin dependencias, que ya están en el lock.
RUN python -m pip install --no-cache-dir -r requirements.lock \
    && python -m pip install --no-cache-dir --no-deps .
COPY --from=catedralx /app/elite /app/CatedralX/elite
COPY --from=catedralx /app/BIN /app/CatedralX/BIN
RUN chown -R chalamandra:chalamandra /app /home/chalamandra/.local
USER chalamandra
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:8766/healthz >/dev/null || exit 1
CMD ["python","-m","api.server"]
