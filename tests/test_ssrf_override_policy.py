"""Regression tests for pinned webhook delivery and strict SSRF policy."""
import socket
import ssl
from pathlib import Path

import pytest

from api import safe_http, subscription_store
from api.safe_http import _PinnedHTTPSConnection, _PinnedHTTPConnection


def _answer(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 8000))]


def test_legacy_localhost_override_does_not_authorize_private_destinations(monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_SSRF_ALLOW_LOCALHOST", "true")
    monkeypatch.delenv("CHALAMANDRA_WEBHOOK_PRIVATE_HOST_ALLOWLIST", raising=False)
    monkeypatch.setattr(
        "api.subscription_store.socket.getaddrinfo",
        lambda *args, **kwargs: _answer("172.23.0.5"),
    )
    assert subscription_store.resolve_safe_url("http://hmac-sink:8000/callback") is None


def test_explicit_private_hostname_allowlist_is_exact_and_narrow(monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_SSRF_ALLOW_LOCALHOST", "true")
    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_PRIVATE_HOST_ALLOWLIST", "receiver")
    monkeypatch.setattr(
        "api.subscription_store.socket.getaddrinfo",
        lambda *args, **kwargs: _answer("172.23.0.5"),
    )

    target = subscription_store.resolve_safe_url("http://receiver:8000/webhook")
    assert target is not None
    assert target.addresses == ("172.23.0.5",)
    assert target.request_target == "/webhook"
    assert subscription_store.resolve_safe_url("http://other-host:8000/webhook") is None


def test_public_https_only_and_rejects_mixed_dns_answers(monkeypatch):
    monkeypatch.delenv("CHALAMANDRA_WEBHOOK_PRIVATE_HOST_ALLOWLIST", raising=False)
    monkeypatch.setattr(
        "api.subscription_store.socket.getaddrinfo",
        lambda *args, **kwargs: _answer("93.184.216.34"),
    )
    target = subscription_store.resolve_safe_url("https://example.com/hooks?x=1")
    assert target is not None
    assert target.addresses == ("93.184.216.34",)
    assert target.request_target == "/hooks?x=1"
    assert subscription_store.resolve_safe_url("http://example.com/hooks") is None

    monkeypatch.setattr(
        "api.subscription_store.socket.getaddrinfo",
        lambda *args, **kwargs: _answer("172.23.0.5") + _answer("93.184.216.34"),
    )
    assert subscription_store.resolve_safe_url("https://mixed.example/hooks") is None


def test_metadata_and_link_local_are_always_rejected(monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_PRIVATE_HOST_ALLOWLIST", "169.254.169.254")
    assert subscription_store.resolve_safe_url(
        "https://169.254.169.254/latest/meta-data/"
    ) is None
    assert subscription_store.resolve_safe_url(
        "http://metadata.google.internal/callback"
    ) is None


def test_pinned_connection_uses_validated_ip_without_dns_lookup(monkeypatch):
    calls = []
    sentinel = object()

    def connect(address, port, timeout):
        calls.append((address, port, timeout))
        return sentinel

    def forbidden_dns(*args, **kwargs):
        raise AssertionError("DNS must not be resolved again during connection")

    monkeypatch.setattr(safe_http, "_connect_to_ip", connect)
    monkeypatch.setattr("api.safe_http.socket.getaddrinfo", forbidden_dns)

    connection = _PinnedHTTPConnection("receiver", "172.23.0.5", 8000, 5.0)
    connection.connect()

    assert calls == [("172.23.0.5", 8000, 5.0)]
    assert connection.sock is sentinel
    connection.sock = None


def test_numeric_socket_connect_does_not_resolve_hostname_again(monkeypatch):
    calls = []

    class FakeSocket:
        def settimeout(self, timeout):
            calls.append(("timeout", timeout))

        def connect(self, sockaddr):
            calls.append(("connect", sockaddr))

        def close(self):
            calls.append(("close",))

    def socket_factory(family, kind):
        calls.append(("socket", family, kind))
        return FakeSocket()

    def forbidden_dns(*args, **kwargs):
        raise AssertionError("numeric IP connect must not invoke DNS")

    monkeypatch.setattr("api.safe_http.socket.socket", socket_factory)
    monkeypatch.setattr("api.safe_http.socket.getaddrinfo", forbidden_dns)

    stream = safe_http._connect_to_ip("172.23.0.5", 8000, 5.0)
    assert isinstance(stream, FakeSocket)
    assert calls == [
        ("socket", socket.AF_INET, socket.SOCK_STREAM),
        ("timeout", 5.0),
        ("connect", ("172.23.0.5", 8000)),
    ]


def test_pinned_https_keeps_original_hostname_for_tls_sni(monkeypatch):
    raw_socket = object()
    wrapped_socket = object()
    calls = []

    class FakeContext:
        def wrap_socket(self, stream, *, server_hostname):
            calls.append((stream, server_hostname))
            return wrapped_socket

    monkeypatch.setattr(
        safe_http,
        "_connect_to_ip",
        lambda address, port, timeout: raw_socket,
    )
    connection = _PinnedHTTPSConnection(
        "hooks.example.com",
        "93.184.216.34",
        443,
        5.0,
        ssl.create_default_context(),
    )
    connection._ssl_context = FakeContext()
    connection.connect()

    assert calls == [(raw_socket, "hooks.example.com")]
    assert connection.sock is wrapped_socket
    connection.sock = None


def test_subscription_ledger_corruption_fails_closed(tmp_path: Path):
    path = tmp_path / "subscriptions.jsonl"
    path.write_text('{"subscription_id":"valid"}\n{broken json}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="línea 2"):
        subscription_store._read_all(path)
