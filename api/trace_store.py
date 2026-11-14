"""Append-only W3C-correlated HTTP request trace log; never records credentials or bodies."""
from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api.locking import (
    DEFAULT_TRACE_LOCK_TIMEOUT_SECONDS,
    acquire_exclusive_lock,
)

_LOCK = threading.Lock()
_LAST_SYNC = 0.0
_SYNC_INTERVAL_SECONDS = 1.0
_TRACE_TARGET_READY: str | None = None


def _trace_file() -> Path:
    """
    Resuelve la ruta del archivo JSONL de trazas de solicitudes HTTP.

    Args:
        None; consulta la ruta configurada y la raíz local de estado.

    Returns:
        Path: Ruta expandida para el almacenamiento de trazas de API.

    Raises:
        OSError: Puede propagarse si falla la resolución de la ruta configurada.

    Side effects:
        Lee configuración/paths; no crea ni lee el archivo por sí sola.

    Example:
        path = _trace_file()
    """

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
    candidate_id: str | None = None,
    decision_id: str | None = None,
) -> None:
    """Persist safe request metadata atomically enough for concurrent handlers.

    C-6: cuando el handler conoce el candidato o la decisión que sirvió la
    petición, se registran como `candidate_id`/`decision_id` para que una
    traza se reconstruya sin cruzar cuatro fuentes. Solo se incluyen cuando
    se conocen; el resto de registros conserva su forma anterior.
    """
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
    if candidate_id is not None:
        record["candidate_id"] = candidate_id
    if decision_id is not None:
        record["decision_id"] = decision_id
    target = _trace_file()
    encoded = json.dumps(
        record, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    global _LAST_SYNC, _TRACE_TARGET_READY
    target_key = str(target.resolve())
    with _LOCK:
        # Apply private directory/file permissions once per process and target path,
        # not on every request in the hot logging path.
        if _TRACE_TARGET_READY != target_key:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(target.parent, 0o700)
            target.touch(mode=0o600, exist_ok=True)
            os.chmod(target, 0o600)
            _TRACE_TARGET_READY = target_key
        with target.open("a", encoding="utf-8") as stream:
            # F-7: espera acotada (antes LOCK_EX sin timeout). log_message
            # captura OSError, y TimeoutError lo es: la traza se pierde de
            # forma observable en lugar de colgar la respuesta HTTP.
            acquire_exclusive_lock(
                stream.fileno(), timeout_seconds=DEFAULT_TRACE_LOCK_TIMEOUT_SECONDS
            )
            try:
                stream.write(encoded)
                stream.flush()
                now = time.monotonic()
                # Traces are operational telemetry, not the acceptance/evidence ledger.
                # Bound sync cost while retaining periodic durable flushes.
                if now - _LAST_SYNC >= _SYNC_INTERVAL_SECONDS:
                    os.fsync(stream.fileno())
                    _LAST_SYNC = now
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
