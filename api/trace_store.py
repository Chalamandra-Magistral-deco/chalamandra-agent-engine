"""Append-only W3C-correlated HTTP request trace log; never records credentials or bodies."""
from __future__ import annotations

import fcntl
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def _trace_file() -> Path:
    configured = os.getenv("CHALAMANDRA_TRACE_LOG_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    state_root = Path(
        os.getenv(
            "CHALAMANDRA_STATE_DIR",
            str(Path.home() / ".local" / "state" / "chalamandra"),
        )
    ).expanduser()
    return state_root / "agents" / "traces.jsonl"


def record_request(
    *,
    trace_id: str,
    traceparent: str | None,
    method: str,
    path: str,
    status: int | None,
    client_ip: str,
) -> None:
    """Persist safe request metadata atomically enough for concurrent handlers."""
    if not trace_id or not method or not path.startswith("/"):
        raise ValueError("trace_id, method y path válidos son obligatorios")
    record: dict[str, Any] = {
        "schema_version": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "trace_id": trace_id,
        "traceparent": traceparent,
        "method": method,
        "path": path,
        "status": status,
        "client_ip": client_ip,
    }
    target = _trace_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        record, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    with _LOCK, target.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
