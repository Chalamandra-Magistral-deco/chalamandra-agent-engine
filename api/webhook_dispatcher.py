"""Despachador de Webhooks con firma, reintentos y SSRF revalidado (P1)."""
import hashlib
import hmac
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from api import subscription_store

from api import jsonl_store

def _state_dir() -> Path:
    return jsonl_store.state_dir()

def _dead_letter_file() -> Path:
    return _state_dir() / "dead_letters.jsonl"

def _webhook_secret() -> bytes:
    secret = os.environ.get("CHALAMANDRA_WEBHOOK_SECRET", "default-webhook-secret")
    return secret.encode("utf-8")

def _sign_payload(payload: dict) -> str:
    payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.new(_webhook_secret(), payload_str.encode("utf-8"), hashlib.sha256).hexdigest()

def _log_dead_letter(url: str, payload: dict, error: str):
    _dead_letter_file().parent.mkdir(parents=True, exist_ok=True)
    record = {"url": url, "payload": payload, "error": error, "timestamp": datetime.now(timezone.utc).isoformat()}
    with _dead_letter_file().open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

def dispatch_event(event_type: str, result_id: str, trace_id: str) -> int:
    subs = subscription_store.list_subscriptions()
    active_subs = [s for s in subs if s.get("status") == "enabled" and event_type in s.get("event_types", [])]
    if not active_subs:
        return 0

    import uuid
    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "result_id": result_id,
        "trace_id": trace_id,
        "result_url": f"/v1/results/{result_id}"
    }
    signature = _sign_payload(payload)
    data = json.dumps(payload).encode("utf-8")
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
                "X-Chalamandra-Signature": signature
            },
            method="POST"
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if 200 <= resp.status < 300:
                        success_count += 1
                        break
            except Exception as e:
                if attempt == 2:
                    _log_dead_letter(url, payload, str(e))
                else:
                    time.sleep(2 ** attempt)
    return success_count
