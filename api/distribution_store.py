"""Append-only, process-safe idempotency state for accepted-result distribution."""
from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _state_dir() -> Path:
    return Path.home() / ".local" / "state" / "chalamandra" / "agents"


def _events_file() -> Path:
    return _state_dir() / "distribution_events.jsonl"


def _deliveries_file() -> Path:
    return _state_dir() / "distribution_deliveries.jsonl"


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read_all(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"distribution_events.jsonl inválido en línea {line_number}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"Registro de distribución no-objeto en línea {line_number}")
            records.append(record)
    return records


def _append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reserve_dispatch(
    result_id: str,
    event_id: str,
    trace_id: str,
    *,
    lease_seconds: int = 300,
) -> dict[str, Any]:
    """Reserve one dispatch for a logical result ID across processes and event IDs.

    Completed result IDs are globally deduplicated. A recent in-flight reservation
    blocks concurrent retries. Failed or stale reservations can be retried. All state
    transitions are appended; existing audit records are never rewritten or deleted.
    """
    if not isinstance(result_id, str) or not result_id.strip() or len(result_id) > 256:
        raise ValueError("result_id inválido para deduplicación")
    if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 128:
        raise ValueError("event_id inválido para deduplicación")
    if not isinstance(trace_id, str) or not _TRACE_ID_RE.fullmatch(trace_id) or trace_id == "0" * 32:
        raise ValueError("trace_id inválido para deduplicación")
    if not isinstance(lease_seconds, int) or lease_seconds < 1:
        raise ValueError("lease_seconds debe ser un entero positivo")

    path = _events_file()
    now = _now()
    with _locked(path):
        records = _read_all(path)
        latest_by_result: dict[str, dict[str, Any]] = {}
        for record in records:
            prior_event_id = record.get("event_id")
            prior_result_id = record.get("result_id")
            if prior_event_id == event_id and prior_result_id != result_id:
                raise ValueError("event_id ya está asignado a otro result_id")
            if isinstance(prior_result_id, str):
                latest_by_result[prior_result_id] = record

        previous = latest_by_result.get(result_id)
        if previous and previous.get("status") == "dispatched":
            return {
                "status": "already_dispatched",
                "result_id": result_id,
                "event_id": previous.get("event_id"),
                "trace_id": previous.get("trace_id"),
                "delivered_count": previous.get("delivered_count", 0),
                "occurred_at": previous.get("occurred_at") or previous.get("recorded_at"),
            }
        if previous and previous.get("status") == "dispatching":
            try:
                started = datetime.fromisoformat(str(previous["recorded_at"]))
                if started.tzinfo is None or started.utcoffset() is None:
                    raise ValueError("timestamp sin zona horaria")
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Reserva de distribución con timestamp inválido") from exc
            if (now - started.astimezone(timezone.utc)).total_seconds() < lease_seconds:
                return {
                    "status": "in_progress",
                    "result_id": result_id,
                    "event_id": previous.get("event_id"),
                    "trace_id": previous.get("trace_id"),
                    "delivered_count": 0,
                }

        # Keep a logical result's event identity stable across retries after failure.
        stable_event_id = (
            previous.get("event_id")
            if previous and previous.get("status") == "failed"
            else event_id
        )
        stable_occurred_at = (
            previous.get("occurred_at") or previous.get("recorded_at")
            if previous and previous.get("status") == "failed"
            else now.isoformat()
        )
        reservation = {
            "result_id": result_id,
            "event_id": stable_event_id,
            "trace_id": trace_id,
            "status": "dispatching",
            "recorded_at": now.isoformat(),
            "occurred_at": stable_occurred_at,
            "delivered_count": 0,
        }
        _append(path, reservation)
        return {**reservation, "status": "reserved"}


def finish_dispatch(
    result_id: str,
    event_id: str,
    *,
    status: str,
    delivered_count: int,
    error_code: str | None = None,
) -> bool:
    """Append a terminal transition only if this event still owns the reservation."""
    if status not in {"dispatched", "failed"}:
        raise ValueError("Estado terminal de distribución inválido")
    if not isinstance(delivered_count, int) or delivered_count < 0:
        raise ValueError("delivered_count debe ser entero no negativo")
    path = _events_file()
    with _locked(path):
        records = _read_all(path)
        latest = next((record for record in reversed(records) if record.get("result_id") == result_id), None)
        if not latest or latest.get("event_id") != event_id or latest.get("status") != "dispatching":
            return False
        record = {
            "result_id": result_id,
            "event_id": event_id,
            "trace_id": latest.get("trace_id"),
            "status": status,
            "recorded_at": _now().isoformat(),
            "occurred_at": latest.get("occurred_at") or latest.get("recorded_at"),
            "delivered_count": delivered_count,
        }
        if error_code:
            record["error_code"] = str(error_code)[:80]
        _append(path, record)
        return True



def reserve_subscription_delivery(
    result_id: str,
    subscription_id: str,
    event_id: str,
    trace_id: str,
    *,
    lease_seconds: int = 300,
) -> dict[str, Any]:
    """Reserve one subscriber delivery per logical result; failed deliveries remain retryable."""
    if not isinstance(result_id, str) or not result_id.strip() or len(result_id) > 256:
        raise ValueError("result_id inválido para entrega")
    if not isinstance(subscription_id, str) or not subscription_id.strip() or len(subscription_id) > 256:
        raise ValueError("subscription_id inválido para entrega")
    if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 128:
        raise ValueError("event_id inválido para entrega")
    if not isinstance(trace_id, str) or not _TRACE_ID_RE.fullmatch(trace_id) or trace_id == "0" * 32:
        raise ValueError("trace_id inválido para entrega")
    if not isinstance(lease_seconds, int) or lease_seconds < 1:
        raise ValueError("lease_seconds debe ser un entero positivo")

    path = _deliveries_file()
    now = _now()
    with _locked(path):
        records = _read_all(path)
        previous = next((row for row in reversed(records)
                         if row.get("result_id") == result_id
                         and row.get("subscription_id") == subscription_id), None)
        if previous and previous.get("status") == "delivered":
            return {**previous, "status": "already_delivered"}
        if previous and previous.get("status") == "dispatching":
            try:
                started = datetime.fromisoformat(str(previous["recorded_at"]))
                if started.tzinfo is None or started.utcoffset() is None:
                    raise ValueError("timestamp sin zona horaria")
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Reserva de entrega con timestamp inválido") from exc
            if (now - started.astimezone(timezone.utc)).total_seconds() < lease_seconds:
                return {**previous, "status": "in_progress"}

        reservation = {
            "result_id": result_id,
            "subscription_id": subscription_id,
            "event_id": event_id,
            "trace_id": trace_id,
            "status": "dispatching",
            "recorded_at": now.isoformat(),
        }
        _append(path, reservation)
        return {**reservation, "status": "reserved"}


def finish_subscription_delivery(
    result_id: str,
    subscription_id: str,
    event_id: str,
    *,
    status: str,
    error_code: str | None = None,
) -> bool:
    """Append delivery success/failure only while the caller owns its reservation."""
    if status not in {"delivered", "failed"}:
        raise ValueError("Estado terminal de entrega inválido")
    path = _deliveries_file()
    with _locked(path):
        records = _read_all(path)
        latest = next((row for row in reversed(records)
                       if row.get("result_id") == result_id
                       and row.get("subscription_id") == subscription_id), None)
        if not latest or latest.get("event_id") != event_id or latest.get("status") != "dispatching":
            return False
        record = {
            "result_id": result_id,
            "subscription_id": subscription_id,
            "event_id": event_id,
            "trace_id": latest.get("trace_id"),
            "status": status,
            "recorded_at": _now().isoformat(),
        }
        if error_code:
            record["error_code"] = str(error_code)[:80]
        _append(path, record)
        return True
