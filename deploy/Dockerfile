FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    CATEDRALX_PATH=/app/CatedralX \
    CATEDRALX_STATE_PATH=/home/chalamandra/.local/state/chalamandra/catedralx/hormonas.json
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash chalamandra
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY api/ ./api/
COPY core/ ./core/
COPY pipeline/ ./pipeline/
COPY adapters/ ./adapters/
COPY config/ ./config/
COPY registry/ ./registry/
COPY security/ ./security/
COPY telemetry/ ./telemetry/
COPY dynamics/ ./dynamics/
RUN chown -R chalamandra:chalamandra /app
USER chalamandra
HEALTHCHECK --interval=30s CMD python -m pytest tests/ -q | tail -1 || exit 1
CMD ["python","-m","api.server"]
