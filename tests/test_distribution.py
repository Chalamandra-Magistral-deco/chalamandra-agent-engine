from unittest.mock import patch, MagicMock
import json
import pytest
from api import webhook_dispatcher, subscription_store

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
    assert not subscription_store.is_safe_url("https://169.254.169.254/latest/meta-data/")

@patch('socket.getaddrinfo')
def test_ssrf_protection_allows_public_https(mock_dns, monkeypatch):
    monkeypatch.delenv("CHALAMANDRA_SSRF_ALLOW_LOCALHOST", raising=False)
    import socket
    mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    assert subscription_store.is_safe_url("https://example.com/hook")

@patch('api.subscription_store.list_subscriptions')
@patch('urllib.request.urlopen')
@patch('api.subscription_store.is_safe_url', return_value=True)
def test_dispatch_event(mock_safe_url, mock_urlopen, mock_list_subs, monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_SECRET", "test-webhook-secret-32-bytes-minimum")
    # Simulamos una suscripción activa
    mock_list_subs.return_value = [
        {"subscription_id": "1", "endpoint_url": "https://example.com/hook", "event_types": ["result.accepted"], "status": "enabled"}
    ]
    # Simulamos una respuesta exitosa del servidor externo
    mock_response = MagicMock()
    mock_response.status = 200
    mock_urlopen.return_value.__enter__.return_value = mock_response

    sent = webhook_dispatcher.dispatch_event("result.accepted", "res-123", "trace-123")
    
    assert sent == 1
    mock_urlopen.assert_called_once()
    args, kwargs = mock_urlopen.call_args
    assert args[0].full_url == "https://example.com/hook"
    assert kwargs["timeout"] == 5


def test_dispatch_event_propagates_w3c_child_traceparent(monkeypatch):
    from unittest.mock import patch, MagicMock
    import re

    trace_id = "6c0a78fc04a34d9b8e8f1fbced8a0041"
    parent = f"00-{trace_id}-00f067aa0ba902b7-01"
    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_SECRET", "test-webhook-secret-32-bytes-minimum")
    with patch('api.subscription_store.list_subscriptions', return_value=[
        {"subscription_id": "1", "endpoint_url": "https://example.com/hook", "event_types": ["result.accepted"], "status": "enabled"}
    ]), patch('api.subscription_store.is_safe_url', return_value=True), patch('urllib.request.urlopen') as mock_urlopen:
        response = MagicMock()
        response.status = 202
        mock_urlopen.return_value.__enter__.return_value = response
        sent = webhook_dispatcher.dispatch_event(
            "result.accepted", "result-1", trace_id, traceparent=parent, event_id="evt-trace-1"
        )
    assert sent == 1
    req = mock_urlopen.call_args.args[0]
    outbound = req.get_header("Traceparent")
    assert outbound
    match = re.fullmatch(r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})", outbound)
    assert match
    assert match.group(1) == trace_id
    assert match.group(2) != "00f067aa0ba902b7"
    assert match.group(3) == "01"
    assert req.get_header("X-trace-id") == trace_id
    payload = json.loads(req.data.decode())
    assert payload["event_id"] == "evt-trace-1"
    assert payload["trace_id"] == trace_id
