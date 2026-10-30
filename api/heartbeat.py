"""Atomic worker heartbeat for the Docker healthcheck."""
import json
import os
import time
from pathlib import Path


def write_worker_heartbeat(status, task_id=None):
    """Write atomic heartbeat to CHALAMANDRA_WORKER_HEARTBEAT_PATH."""
    if status not in {"idle", "processing"}:
        raise ValueError("invalid worker heartbeat status: " + status)
    path = os.getenv("CHALAMANDRA_WORKER_HEARTBEAT_PATH")
    if not path:
        return
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"status": status, "pid": os.getpid(), "ts": int(time.time())}
    if task_id is not None:
        payload["task_id"] = str(task_id)
    tmp = target.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, allow_nan=False)
    os.replace(tmp, target)
