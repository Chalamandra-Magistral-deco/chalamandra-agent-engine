import json
import re
import pytest
from unittest.mock import MagicMock, patch
from api import subscription_store
import api.webhook_dispatcher as webhook_dispatcher

@patch('api.distribution_store.finish_dispatch')
@patch('api.distribution_store.finish_subscription_delivery')
@patch('api.distribution_store.reserve_subscription_delivery')
@patch('api.distribution_store.reserve_dispatch')
@patch('api.subscription_store.is_safe_url', return_value=True)
@patch('api.subscription_store.list_subscriptions')
@patch('requests.post')
def test_dispatch_event(mock_post, mock_list_subs, mock_safe_url, mock_reserve_dispatch, mock_reserve_sub_delivery, mock_finish_sub_delivery, mock_finish_dispatch, monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_SECRET", "test-webhook-secret-32-bytes-minimum")
    trace_id = "6c0a78fc04a34d9b8e8f1fbced8a0041"
    mock_list_subs.return_value = [{"subscription_id": "1", "endpoint_url": "https://example.com/hook", "event_types": ["result.accepted"], "status": "enabled"}]
    mock_reserve_dispatch.return_value = {"status": "reserved", "event_id": "evt-test-1", "occurred_at": "2026-10-30T00:00:00+00:00"}
    mock_reserve_sub_delivery.return_value = {"status": "reserved"}
    mock_finish_sub_delivery.return_value = True
    mock_finish_dispatch.return_value = True
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value.__enter__.return_value = mock_response
    sent = webhook_dispatcher.dispatch_event("result.accepted", "res-123", trace_id)
    assert sent == 1
    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs.get("allow_redirects") is False
    assert kwargs.get("timeout") == 5
    assert kwargs.get("data")
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert json.loads(kwargs["data"].decode())["result_id"] == "res-123"

def test_dispatch_event_propagates_w3c_child_traceparent(monkeypatch):
    trace_id = "6c0a78fc04a34d9b8e8f1fbced8a0041"
    parent = f"00-{trace_id}-00f067aa0ba902b7-01"
    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_SECRET", "test-webhook-secret-32-bytes-minimum")
    with patch('api.subscription_store.list_subscriptions', return_value=[{"subscription_id": "1", "endpoint_url": "https://example.com/hook", "event_types": ["result.accepted"], "status": "enabled"}]), patch('api.subscription_store.is_safe_url', return_value=True), patch('api.distribution_store.reserve_dispatch', return_value={"status": "reserved", "event_id": "evt-trace-1", "occurred_at": "2026-10-30T00:00:00+00:00"}), patch('api.distribution_store.reserve_subscription_delivery', return_value={"status": "reserved"}), patch('api.distribution_store.finish_subscription_delivery', return_value=True), patch('api.distribution_store.finish_dispatch', return_value=True), patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_post.return_value.__enter__.return_value = mock_response
        sent = webhook_dispatcher.dispatch_event("result.accepted", "result-1", trace_id, traceparent=parent, event_id="evt-trace-1")
    assert sent == 1
    _, kwargs = mock_post.call_args
    headers = kwargs.get("headers", {})
    data = kwargs.get("data", b"")
    outbound = headers.get("traceparent")
    assert outbound
    match = re.fullmatch(r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})", outbound)
    assert match and match.group(1) == trace_id and match.group(2) != "00f067aa0ba902b7" and match.group(3) == "01"
    assert headers.get("X-Trace-Id") == trace_id
    payload = json.loads(data.decode())
    assert payload["event_id"] == "evt-trace-1" and payload["trace_id"] == trace_id
    assert kwargs.get("allow_redirects") is False
    assert kwargs.get("timeout") == 5


def test_missing_or_short_webhook_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("CHALAMANDRA_WEBHOOK_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="CHALAMANDRA_WEBHOOK_SECRET"):
        webhook_dispatcher._webhook_secret()

    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        webhook_dispatcher._webhook_secret()


def test_ssrf_protection_blocks_http(monkeypatch):
    monkeypatch.delenv("CHALAMANDRA_SSRF_ALLOW_LOCALHOST", raising=False)
    assert not subscription_store.is_safe_url("http://example.com/hook")


def test_ssrf_protection_blocks_loopback(monkeypatch):
    monkeypatch.delenv("CHALAMANDRA_SSRF_ALLOW_LOCALHOST", raising=False)
    assert not subscription_store.is_safe_url("https://127.0.0.1/hook")
    assert not subscription_store.is_safe_url("https://localhost/hook")


def test_ssrf_protection_blocks_metadata(monkeypatch):
    monkeypatch.delenv("CHALAMANDRA_SSRF_ALLOW_LOCALHOST", raising=False)
    assert not subscription_store.is_safe_url(
        "https://169.254.169.254/latest/meta-data/"
    )


@patch("socket.getaddrinfo")
def test_ssrf_protection_allows_public_https(mock_dns, monkeypatch):
    import socket

    monkeypatch.delenv("CHALAMANDRA_SSRF_ALLOW_LOCALHOST", raising=False)
    mock_dns.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
    ]
    assert subscription_store.is_safe_url("https://example.com/hook")
