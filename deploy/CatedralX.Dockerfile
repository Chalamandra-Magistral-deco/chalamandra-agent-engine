FROM python:3.12-slim
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
