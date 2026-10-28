from unittest.mock import patch, MagicMock
import pytest
from api import webhook_dispatcher, subscription_store

def test_missing_or_short_webhook_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("CHALAMANDRA_WEBHOOK_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="CHALAMANDRA_WEBHOOK_SECRET"):
        webhook_dispatcher._webhook_secret()

    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        webhook_dispatcher._webhook_secret()


def test_ssrf_protection_blocks_http():
    assert not subscription_store.is_safe_url("http://example.com/hook")

def test_ssrf_protection_blocks_loopback():
    assert not subscription_store.is_safe_url("https://127.0.0.1/hook")
    assert not subscription_store.is_safe_url("https://localhost/hook")

def test_ssrf_protection_blocks_metadata():
    assert not subscription_store.is_safe_url("https://169.254.169.254/latest/meta-data/")

@patch('socket.gethostbyname')
def test_ssrf_protection_allows_public_https(mock_dns):
    mock_dns.return_value = "8.8.8.8"
    assert subscription_store.is_safe_url("https://example.com/hook")

@patch('api.subscription_store.list_subscriptions')
@patch('urllib.request.urlopen')
def test_dispatch_event(mock_urlopen, mock_list_subs, monkeypatch):
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
