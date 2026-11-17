"""Reconciliación periódica de distribuciones fallidas tras aceptación (C-4).

El worker reintenta distribuciones aceptadas con intervalo acotado. Antes de
despachar, también repara la sincronización del TaskBus: una decisión firmada
puede haberse persistido justo antes de que el proceso cayera al actualizar
los estados operativos. La aceptación criptográfica es la fuente de verdad,
pero no basta para distribuir hasta que todas las tareas vinculadas estén
marcadas accepted.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from api import (
    acceptance_store,
    distribution_store,
    task_bus,
    webhook_dispatcher,
)

DEFAULT_RETRY_INTERVAL_SECONDS = 300
DEFAULT_DISPATCH_LEASE_SECONDS = 300


def _retry_interval_seconds() -> int:
    raw = os.getenv("CHALAMANDRA_DISTRIBUTION_RETRY_INTERVAL_SECONDS", "")
    if not raw.strip():
        return DEFAULT_RETRY_INTERVAL_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            "CHALAMANDRA_DISTRIBUTION_RETRY_INTERVAL_SECONDS debe ser entero"
        ) from exc
    if not 60 <= value <= 86400:
        raise ValueError(
            "CHALAMANDRA_DISTRIBUTION_RETRY_INTERVAL_SECONDS debe estar entre 60 y 86400"
        )
    return value


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _synchronize_accepted_tasks(result_id: str) -> bool:
    """Repair the acceptance-to-TaskBus transition; fail closed without task rows."""
    task_states = task_bus.get_result_task_statuses(result_id)
    if not task_states:
        return False
    if any(item.get("status") != "accepted" for item in task_states):
        task_bus.mark_result_accepted(result_id)
        task_states = task_bus.get_result_task_statuses(result_id)
    return bool(task_states) and all(
        item.get("status") == "accepted" for item in task_states
    )


def reconcile_failed_distributions(
    *,
    min_interval_seconds: int | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Retry accepted results only after TaskBus confirms operational acceptance."""
    interval = (
        min_interval_seconds
        if min_interval_seconds is not None
        else _retry_interval_seconds()
    )
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 0:
        raise ValueError("min_interval_seconds debe ser un entero no negativo")
    now_dt = now or datetime.now(timezone.utc)
    listing = acceptance_store.list_accepted_decisions()
    report = {
        "examined": 0,
        "retried": 0,
        "delivered": 0,
        "still_failed": 0,
        "skipped_throttled": 0,
        "skipped_invalid": int(listing["invalid_skipped"]),
        "skipped_operational_sync": 0,
    }
    for decision in listing["decisions"]:
        result_id = str(decision["candidate_id"])
        trace_id = str(decision["trace_id"])
        decision_id = str(decision["decision_id"])
        report["examined"] += 1

        # Recover a crash between durable acceptance and TaskBus status update.
        # Never distribute an orphaned result or one with blocked/failed tasks.
        try:
            operationally_accepted = _synchronize_accepted_tasks(result_id)
        except Exception:
            operationally_accepted = False
        if not operationally_accepted:
            report["skipped_operational_sync"] += 1
            continue

        latest = distribution_store.latest_dispatch(result_id)
        if latest is not None and latest.get("status") == "dispatched":
            continue
        last_time = _parse_time(latest.get("recorded_at")) if latest else None
        age = (
            (now_dt - last_time).total_seconds()
            if last_time is not None
            else None
        )
        if (
            latest is not None
            and latest.get("status") == "dispatching"
            and age is not None
            and age < DEFAULT_DISPATCH_LEASE_SECONDS
        ):
            continue
        if age is not None and age < interval:
            report["skipped_throttled"] += 1
            continue
        try:
            delivered = webhook_dispatcher.dispatch_event(
                event_type="result.accepted",
                result_id=result_id,
                trace_id=trace_id,
                event_id=f"acceptance:{decision_id}",
            )
        except (OSError, RuntimeError, ValueError):
            report["still_failed"] += 1
            continue
        report["retried"] += 1
        if delivered >= 1:
            report["delivered"] += 1
        else:
            report["still_failed"] += 1
    return report
