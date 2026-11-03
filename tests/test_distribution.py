"""Tests for durable webhook dispatch using the pinned transport."""
import json
import re
from unittest.mock import Mock

from api import subscription_store
from api.safe_http import ResolvedWebhookTarget
import api.webhook_dispatcher as webhook_dispatcher


TRACE_ID = "6c0a78fc04a34d9b8e8f1fbced8a0041"
TARGET = ResolvedWebhookTarget(
    scheme="https",
    hostname="example.com",
    port=443,
    request_target="/hook",
    addresses=("93.184.216.34",),
)


def _patch_dispatch(monkeypatch, *, safe_target=TARGET, status_code=202):
    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_SECRET", "test-webhook-secret-32-bytes-minimum")
    monkeypatch.setattr(
        subscription_store,
        "list_subscriptions",
        lambda: [{
            "subscription_id": "1",
            "endpoint_url": "https://example.com/hook",
            "event_types": ["result.accepted"],
            "status": "enabled",
        }],
    )
    monkeypatch.setattr(subscription_store, "resolve_safe_url", lambda url: safe_target)
    monkeypatch.setattr(webhook_dispatcher, "post_json", Mock(return_value=status_code))
    monkeypatch.setattr(webhook_dispatcher.distribution_store, "reserve_dispatch", lambda *args, **kwargs: {
        "status": "reserved",
        "event_id": args[1],
        "occurred_at": "2026-10-30T00:00:00+00:00",
    })
    monkeypatch.setattr(webhook_dispatcher.distribution_store, "reserve_subscription_delivery", lambda *a, **k: {
        "status": "reserved",
    })
    monkeypatch.setattr(webhook_dispatcher.distribution_store, "finish_subscription_delivery", lambda *a, **k: True)
    monkeypatch.setattr(webhook_dispatcher.distribution_store, "finish_dispatch", lambda *a, **k: True)
    monkeypatch.setattr(webhook_dispatcher, "_log_dead_letter", lambda *a, **k: None)
    monkeypatch.setattr(webhook_dispatcher.time, "sleep", lambda *_: None)


def test_dispatch_uses_pinned_transport_and_hmac(monkeypatch):
    _patch_dispatch(monkeypatch)
    trace_id = TRACE_ID

    sent = webhook_dispatcher.dispatch_event("result.accepted", "res-123", trace_id)
    assert sent == 1

    post = webhook_dispatcher.post_json
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == TARGET
    assert kwargs["timeout"] == 5
    payload = json.loads(kwargs["data"].decode())
    assert payload["result_id"] == "res-123"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert re.fullmatch(r"[0-9a-f]{64}", kwargs["headers"]["X-Chalamandra-Signature"])


def test_dispatch_propagates_w3c_child_traceparent(monkeypatch):
    _patch_dispatch(monkeypatch)
    parent = f"00-{TRACE_ID}-00f067aa0ba902b7-01"

    sent = webhook_dispatcher.dispatch_event(
        "result.accepted",
        "result-1",
        TRACE_ID,
        traceparent=parent,
        event_id="evt-trace-1",
    )
    assert sent == 1

    _, kwargs = webhook_dispatcher.post_json.call_args
    outbound = kwargs["headers"]["traceparent"]
    match = re.fullmatch(r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})", outbound)
    assert match
    assert match.group(1) == TRACE_ID
    assert match.group(2) != "00f067aa0ba902b7"
    assert match.group(3) == "01"
    payload = json.loads(kwargs["data"].decode())
    assert payload["event_id"] == "evt-trace-1"
    assert payload["trace_id"] == TRACE_ID


def test_dispatch_fails_closed_when_destination_is_not_allowed(monkeypatch):
    _patch_dispatch(monkeypatch, safe_target=None)

    sent = webhook_dispatcher.dispatch_event("result.accepted", "result-2", TRACE_ID)
    assert sent == 0
    webhook_dispatcher.post_json.assert_not_called()


def test_missing_or_short_webhook_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("CHALAMANDRA_WEBHOOK_SECRET", raising=False)
    try:
        webhook_dispatcher._webhook_secret()
    except RuntimeError as error:
        assert "CHALAMANDRA_WEBHOOK_SECRET" in str(error)
    else:
        raise AssertionError("missing secret must fail closed")

    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_SECRET", "too-short")
    try:
        webhook_dispatcher._webhook_secret()
    except RuntimeError as error:
        assert "at least 32 bytes" in str(error)
    else:
        raise AssertionError("short secret must fail closed")
