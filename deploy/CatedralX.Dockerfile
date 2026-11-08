ARG CATEDRALX_REVISION=unknown
ARG CATEDRALX_CODE_DIRTY=unknown
FROM python:3.12-slim
ARG CATEDRALX_REVISION
ARG CATEDRALX_CODE_DIRTY
LABEL com.chalamandra.catedralx.revision=$CATEDRALX_REVISION \
      com.chalamandra.catedralx.code.dirty=$CATEDRALX_CODE_DIRTY
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    CATEDRALX_SNAPSHOT_PATH=/dev/shm/cathedral_snapshot.json \
    PATH="/app/BIN:${PATH}"
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash catedralx
COPY CatedralX/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY CatedralX/elite/ ./elite/
COPY CatedralX/BIN/ ./BIN/
RUN chmod +x BIN/catedral && chown -R catedralx:catedralx /app
USER catedralx
CMD ["catedral", "status"]
