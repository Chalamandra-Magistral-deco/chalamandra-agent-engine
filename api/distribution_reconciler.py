"""Reconciliación periódica de distribuciones fallidas tras aceptación (C-4).

Antes, una distribución fallida tras POST /v1/acceptance solo se reintentaba
a mano (replay del POST o /internal/v1/distributions). Este reconciliador lo
hace el worker de forma automática y acotada:

- Solo mira decisiones aceptadas (las rechazadas no se distribuyen).
- Solo reintenta si el último evento de distribución es 'failed' o no hay
  evento; un 'dispatched' o un 'dispatching' vigente no se toca.
- Acota el reintento con un intervalo mínimo entre intentos por resultado
  (defecto 300 s, CHALAMANDRA_DISTRIBUTION_RETRY_INTERVAL_SECONDS): sin él,
  el worker reintentaría en cada iteración y llenaría el ledger de eventos
  fallidos.
- Nunca levanta excepciones por resultado: un fallo de despacho se cuenta
  como still_failed y la reconciliación sigue con los demás.

Elección frente a un endpoint de reintento documentado: el endpoint manual ya
existe (/internal/v1/distributions); lo que faltaba era el reintento
automático, y el worker ya es el proceso que reconcilia publicaciones
pendientes (candidate_store.reconcile_pending_publications), así que este
reconciliador sigue ese mismo patrón en el mismo loop.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from api import acceptance_store, distribution_store, webhook_dispatcher

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


def reconcile_failed_distributions(
    *,
    min_interval_seconds: int | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Retry accepted results whose last distribution failed or never ran."""
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
    }
    for decision in listing["decisions"]:
        result_id = str(decision["candidate_id"])
        trace_id = str(decision["trace_id"])
        decision_id = str(decision["decision_id"])
        report["examined"] += 1
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
            # Reserva en vuelo vigente: la deja el lease, no este reconciliador.
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
