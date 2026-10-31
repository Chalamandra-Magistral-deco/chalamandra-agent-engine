"""Despacho durable de webhooks con firma, idempotencia y SSRF revalidado."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

from api import distribution_store, subscription_store
from api.trace_parser import parse_traceparent


def _state_dir() -> Path:
    return Path.home() / ".local" / "state" / "chalamandra" / "agents"


def _dead_letter_file() -> Path:
    return _state_dir() / "dead_letters.jsonl"


def _webhook_secret() -> bytes:
    secret = os.environ.get("CHALAMANDRA_WEBHOOK_SECRET", "").strip()
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError(
            "CHALAMANDRA_WEBHOOK_SECRET must be configured with at least 32 bytes"
        )
    return secret.encode("utf-8")


def _sign_payload(payload: dict) -> str:
    payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.new(
        _webhook_secret(), payload_str.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _log_dead_letter(url: str, payload: dict, error: str) -> None:
    path = _dead_letter_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "url": url,
        "payload": payload,
        "error": error[:500],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _child_traceparent(
    trace_id: str, parent_traceparent: str | None = None
) -> str | None:
    """Build a W3C child span without changing the accepted result's trace ID."""
    import re

    if not re.fullmatch(r"[0-9a-f]{32}", trace_id) or trace_id == "0" * 32:
        return None
    parent = parse_traceparent(parent_traceparent)
    flags = (
        parent["flags"]
        if parent and parent["trace_id"] == trace_id
        else "01"
    )
    return f"00-{trace_id}-{uuid.uuid4().hex[:16]}-{flags}"


def dispatch_event(
    event_type: str,
    result_id: str,
    trace_id: str,
    *,
    traceparent: str | None = None,
    event_id: str | None = None,
) -> int:
    """Dispatch once per accepted result, with durable per-subscriber retries.

    A result is globally marked dispatched only when every active subscriber is
    either newly delivered or already durably recorded as delivered. Failed
    deliveries remain retryable and reuse the original event identity.
    """
    subscriptions = subscription_store.list_subscriptions()
    active_subs = [
        sub
        for sub in subscriptions
        if sub.get("status") == "enabled"
        and event_type in sub.get("event_types", [])
    ]

    requested_event_id = event_id or str(uuid.uuid4())
    reservation = distribution_store.reserve_dispatch(
        result_id, requested_event_id, trace_id
    )
    reservation_status = reservation.get("status")
    if reservation_status == "already_dispatched":
        return int(reservation.get("delivered_count", 0))
    if reservation_status == "in_progress":
        return 0
    if reservation_status != "reserved":
        raise RuntimeError("unexpected_distribution_reservation_state")

    stable_event_id = str(reservation["event_id"])
    success_count = 0
    payload = {
        "event_id": stable_event_id,
        "event_type": event_type,
        "occurred_at": reservation.get("occurred_at")
        or datetime.now(timezone.utc).isoformat(),
        "result_id": result_id,
        "trace_id": trace_id,
        "result_url": f"/v1/results/{result_id}",
    }

    try:
        if not active_subs:
            distribution_store.finish_dispatch(
                result_id,
                stable_event_id,
                status="failed",
                delivered_count=0,
                error_code="no_active_subscriptions",
            )
            return 0

        signature = _sign_payload(payload)
        data = json.dumps(
            payload, ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        outbound_traceparent = _child_traceparent(trace_id, traceparent)

        for sub in active_subs:
            subscription_id = sub.get("subscription_id")
            url = sub.get("endpoint_url")
            if not isinstance(subscription_id, str) or not subscription_id.strip():
                _log_dead_letter(str(url or ""), payload, "subscription_id_missing")
                continue
            if not isinstance(url, str) or not url.strip():
                _log_dead_letter("", payload, "subscription_endpoint_missing")
                continue

            delivery = distribution_store.reserve_subscription_delivery(
                result_id,
                subscription_id,
                stable_event_id,
                trace_id,
            )
            delivery_status = delivery.get("status")
            if delivery_status == "already_delivered":
                success_count += 1
                continue
            if delivery_status == "in_progress":
                continue
            if delivery_status != "reserved":
                continue

            error_code = None
            if not subscription_store.is_safe_url(url):
                error_code = "ssrf_revalidation_failed"
                _log_dead_letter(url, payload, error_code)
            else:
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "Chalamandra-Webhook/1.0",
                    "X-Chalamandra-Signature": signature,
                    "X-Trace-Id": trace_id,
                }
                if outbound_traceparent:
                    headers["traceparent"] = outbound_traceparent

                delivered = False
                for attempt in range(3):
                    try:
                        with requests.post(
                            url,
                            data=data,
                            headers=headers,
                            timeout=5,
                            allow_redirects=False,
                        ) as response:
                            if 200 <= response.status_code < 300:
                                delivered = True
                                break
                            error_code = f"http_status_{response.status_code}"
                    except requests.RequestException as exc:
                        error_code = type(exc).__name__
                    if attempt < 2:
                        time.sleep(2**attempt)

                if delivered:
                    finished = distribution_store.finish_subscription_delivery(
                        result_id,
                        subscription_id,
                        stable_event_id,
                        status="delivered",
                    )
                    if not finished:
                        raise RuntimeError("subscriber_delivery_lease_lost")
                    success_count += 1
                    continue

                _log_dead_letter(url, payload, error_code or "delivery_failed")

            finished = distribution_store.finish_subscription_delivery(
                result_id,
                subscription_id,
                stable_event_id,
                status="failed",
                error_code=error_code or "delivery_failed",
            )
            if not finished:
                raise RuntimeError("subscriber_delivery_lease_lost")

        all_delivered = success_count == len(active_subs)
        distribution_store.finish_dispatch(
            result_id,
            stable_event_id,
            status="dispatched" if all_delivered else "failed",
            delivered_count=success_count,
            error_code=None if all_delivered else "partial_or_failed_delivery",
        )
        return success_count
    except Exception:
        # Make the result retryable rather than leaving a permanent in-flight lease.
        distribution_store.finish_dispatch(
            result_id,
            stable_event_id,
            status="failed",
            delivered_count=success_count,
            error_code="dispatcher_exception",
        )
        raise
