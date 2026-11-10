"""Task-scoped process resource observations for ETU.

CPU time, wall time and RSS are process observations during one worker task.
They are not electrical-energy measurements and must not be relabelled as Wh/J.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar

import psutil

F = TypeVar("F", bound=Callable[..., Any])


def _resource_path() -> Path | None:
    explicit = os.getenv("CHALAMANDRA_RESOURCE_TRACE_PATH")
    if explicit and explicit.strip():
        return Path(explicit).expanduser()
    state_dir = os.getenv("CHALAMANDRA_STATE_DIR")
    if state_dir and state_dir.strip():
        return Path(state_dir).expanduser() / "agents" / "task_resources.jsonl"
    # Do not create untracked local files in developer/test checkouts by default.
    return None


def _rss_mb(process: psutil.Process) -> float | None:
    try:
        return round(process.memory_info().rss / (1024 * 1024), 3)
    except (psutil.Error, OSError):
        return None


def _append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except Exception:
        # fdopen owns fd once entered; close only if ownership was not transferred.
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def record_task_resources(function: F) -> F:
    """Record bounded resource observations without changing task outcomes."""

    @wraps(function)
    def wrapped(task: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        path = _resource_path()
        if path is None:
            return function(task, *args, **kwargs)
        process = psutil.Process(os.getpid())
        wall_start = time.perf_counter()
        cpu_start = sum(process.cpu_times()[:2])
        rss_before = _rss_mb(process)
        result: Any = None
        raised = False
        try:
            result = function(task, *args, **kwargs)
            return result
        except BaseException:
            raised = True
            raise
        finally:
            if path is not None:
                try:
                    cpu_end = sum(process.cpu_times()[:2])
                    record = {
                        "schema_version": 1,
                        "sample_id": str(uuid.uuid4()),
                        "recorded_at": datetime.now(timezone.utc).isoformat(),
                        "task_id": task.get("task_id"),
                        "trace_id": task.get("trace_id"),
                        "worker_pid": os.getpid(),
                        "scope": "worker_process_execution_window",
                        "cpu_seconds": round(max(0.0, cpu_end - cpu_start), 6),
                        "wall_time_s": round(max(0.0, time.perf_counter() - wall_start), 6),
                        "memory_rss_before_mb": rss_before,
                        "memory_rss_after_mb": _rss_mb(process),
                        "energy_wh": None,
                        "energy_status": "not_measured_by_vm_runtime",
                        "result_status": (
                            result.get("status") if isinstance(result, dict) else None
                        ),
                        "candidate_id": (
                            result.get("candidate_id") if isinstance(result, dict) else None
                        ),
                        "raised": raised,
                    }
                    _append_record(path, record)
                except (OSError, psutil.Error, TypeError, ValueError):
                    # Observability must never turn a successful task into a failure.
                    pass

    return wrapped  # type: ignore[return-value]
