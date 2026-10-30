"""Despachador de Webhooks con firma, reintentos y SSRF revalidado (P1)."""
import hashlib
import hmac
import json
import os
import time
import requests
from urllib.parse import urlparse
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from api import subscription_store
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
    return hmac.new(_webhook_secret(), payload_str.encode("utf-8"), hashlib.sha256).hexdigest()

def _log_dead_letter(url: str, payload: dict, error: str):
    _dead_letter_file().parent.mkdir(parents=True, exist_ok=True)
    record = {"url": url, "payload": payload, "error": error, "timestamp": datetime.now(timezone.utc).isoformat()}
    with _dead_letter_file().open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

def _child_traceparent(trace_id: str, parent_traceparent: str | None = None) -> str | None:
    """Build a W3C child span on the accepted result's trace, never inventing a trace ID."""
    import re
    import uuid
    if not re.fullmatch(r"[0-9a-f]{32}", trace_id) or trace_id == "0" * 32:
        return None
    parent = parse_traceparent(parent_traceparent)
    if parent and parent["trace_id"] == trace_id:
        flags = parent["flags"]
    else:
        flags = "01"
    span_id = uuid.uuid4().hex[:16]
    return f"00-{trace_id}-{span_id}-{flags}"


def dispatch_event(
    event_type: str,
    result_id: str,
    trace_id: str,
    *,
    traceparent: str | None = None,
    event_id: str | None = None,
) -> int:
    subs = subscription_store.list_subscriptions()
    active_subs = [s for s in subs if s.get("status") == "enabled" and event_type in s.get("event_types", [])]
    if not active_subs:
        return 0

    import uuid
    payload = {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "result_id": result_id,
        "trace_id": trace_id,
        "result_url": f"/v1/results/{result_id}"
    }
    signature = _sign_payload(payload)
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    outbound_traceparent = _child_traceparent(trace_id, traceparent)
    success_count = 0

    for sub in active_subs:
        url = sub["endpoint_url"]
        # Revalidar SSRF antes de enviar
        if not subscription_store.is_safe_url(url):
            _log_dead_letter(url, payload, "SSRF revalidation failed")
            continue

        req = urllib.request.Request(
            url, data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Chalamandra-Webhook/1.0",
                "X-Chalamandra-Signature": signature,
                "X-Trace-Id": trace_id,
                **({"traceparent": outbound_traceparent} if outbound_traceparent else {}),
            },
            method="POST"
        )
        for attempt in range(3):
            try:
                with requests.get(req.full_url, timeout=5, allow_redirects=False) as resp:
                    if 200 <= resp.status < 300:
                        success_count += 1
                        break
            except Exception as e:
                if attempt == 2:
                    _log_dead_letter(url, payload, str(e))
                else:
                    time.sleep(2 ** attempt)
    return success_count
