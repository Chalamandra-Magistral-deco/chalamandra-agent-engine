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

from api.jsonl_index import JsonlIndex

_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _state_dir() -> Path:
    """
    Resuelve el directorio persistente local de AGENTS.

    Args:
        None.

    Returns:
        Path: Directorio `~/.local/state/chalamandra/agents`.

    Raises:
        No opera sobre archivos al obtener la ruta.

    Side effects:
        Ninguno.

    Example:
        directory = _state_dir()
    """

    return Path.home() / ".local" / "state" / "chalamandra" / "agents"


def _events_file() -> Path:
    """
    Obtiene la ruta del ledger append-only de eventos de distribución.

    Args:
        None.

    Returns:
        Path: Ruta de `distribution_events.jsonl`.

    Raises:
        No crea ni abre el archivo.

    Side effects:
        Ninguno.

    Example:
        path = _events_file()
    """

    return _state_dir() / "distribution_events.jsonl"


def _deliveries_file() -> Path:
    """
    Obtiene la ruta del ledger de intentos/entregas de distribución.

    Args:
        None.

    Returns:
        Path: Ruta de `distribution_deliveries.jsonl` bajo el estado de AGENTS.

    Raises:
        No crea ni abre el archivo.

    Side effects:
        Ninguno.

    Example:
        path = _deliveries_file()
    """

    return _state_dir() / "distribution_deliveries.jsonl"


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """
    Proporciona exclusión mutua para operaciones sobre el almacén de distribución.

    Args:
        path (Path): Archivo asociado al lock.

    Returns:
        ContextManager: Contexto de bloqueo para usar con `with`.

    Raises:
        OSError: Si no puede abrirse/crearse o bloquearse el archivo lock.

    Side effects:
        Abre un artefacto auxiliar y mantiene el bloqueo durante el contexto.

    Example:
        with _locked(_events_file()):
            events = _read_all(_events_file())
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read_all(path: Path) -> list[dict[str, Any]]:
    """
    Decodifica los registros del almacén de distribución en orden de persistencia.

    Args:
        path (Path): Archivo JSONL que se leerá.

    Returns:
        list[dict]: Registros disponibles, o lista vacía si el archivo no existe.

    Raises:
        OSError: Si no puede leerse el archivo.
        ValueError: Si una línea del archivo no es un objeto JSON válido según el contrato del almacén.

    Side effects:
        Solo lectura; no corrige ni elimina registros históricos defectuosos.

    Example:
        events = _read_all(_events_file())
    """

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
    """
    Añade un registro de evento/entrega al ledger JSONL.

    Args:
        path (Path): Archivo de destino.
        record (dict[str, Any]): Registro serializable.

    Returns:
        None.

    Raises:
        OSError: Si falla la creación/escritura.
        TypeError: Si el registro no es serializable a JSON.

    Side effects:
        Anexa una línea; el control de concurrencia corresponde al contexto `_locked` de la capa llamadora.

    Example:
        _append(_events_file(), event)
    """

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
    """
    Devuelve la hora actual en UTC para marcar eventos de distribución.

    Args:
        None.

    Returns:
        datetime: Instante actual con zona horaria UTC.

    Raises:
        No genera excepciones de dominio deliberadas.

    Side effects:
        Lee el reloj del sistema; no persiste la marca temporal.

    Example:
        timestamp = _now().isoformat()
    """

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
    store = JsonlIndex(path, ("result_id", "event_id"))
    now = _now()
    with store.locked() as db:
        event_matches = store.find(db, "event_id", event_id)
        if any(record.get("result_id") != result_id for record in event_matches):
            raise ValueError("event_id ya está asignado a otro result_id")
        result_matches = store.find(db, "result_id", result_id)
        previous = result_matches[-1] if result_matches else None
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
        store.append(db, reservation)
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
    store = JsonlIndex(path, ("result_id", "event_id"))
    with store.locked() as db:
        records = store.find(db, "result_id", result_id)
        latest = records[-1] if records else None
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
        store.append(db, record)
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
    store = JsonlIndex(path, ("result_id", "subscription_id"))
    now = _now()
    with store.locked() as db:
        records = store.find_pair(db, result_id, subscription_id)
        previous = records[-1] if records else None
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
        store.append(db, reservation)
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
    store = JsonlIndex(path, ("result_id", "subscription_id"))
    with store.locked() as db:
        records = store.find_pair(db, result_id, subscription_id)
        latest = records[-1] if records else None
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
        store.append(db, record)
        return True
