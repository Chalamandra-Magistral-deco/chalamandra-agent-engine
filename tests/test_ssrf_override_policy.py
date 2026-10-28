"""Test-mode SSRF exceptions remain narrowly scoped to private local targets."""
import socket

from api import subscription_store


def _answer(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 8000))]


def test_test_override_allows_private_docker_sink_but_not_public_http_or_metadata(monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_SSRF_ALLOW_LOCALHOST", "true")

    def resolve(host, port, type=0):
        mapping = {
            "hmac-sink": "172.23.0.5",
            "example.com": "93.184.216.34",
            "169.254.169.254": "169.254.169.254",
        }
        return _answer(mapping.get(host, "93.184.216.34"))

    monkeypatch.setattr("api.subscription_store.socket.getaddrinfo", resolve)
    assert subscription_store.is_safe_url("http://hmac-sink:8000/callback") is True
    assert subscription_store.is_safe_url("https://hmac-sink:8000/callback") is True
    assert subscription_store.is_safe_url("http://example.com/hook") is False
    assert subscription_store.is_safe_url("https://169.254.169.254/latest/meta-data/") is False
    assert subscription_store.is_safe_url("http://metadata.google.internal/callback") is False


def test_test_override_does_not_allow_mixed_public_private_dns_answers(monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_SSRF_ALLOW_LOCALHOST", "true")
    monkeypatch.setattr(
        "api.subscription_store.socket.getaddrinfo",
        lambda *args, **kwargs: _answer("172.23.0.5") + _answer("93.184.216.34"),
    )
    assert subscription_store.is_safe_url("http://mixed.example/hook") is False
    assert subscription_store.is_safe_url("https://mixed.example/hook") is False
