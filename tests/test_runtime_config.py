"""Pruebas para los valores de configuración que afectan cada solicitud."""

import pytest

from adapters.base import ModelRequest


def test_model_request_uses_environment_defaults(monkeypatch):
    monkeypatch.setenv("DEFAULT_MAX_TOKENS", "512")
    monkeypatch.setenv("DEFAULT_TIMEOUT_SECONDS", "12.5")

    request = ModelRequest(prompt="Prueba")

    assert request.max_tokens == 512
    assert request.timeout_seconds == 12.5


def test_explicit_request_values_override_environment(monkeypatch):
    monkeypatch.setenv("DEFAULT_MAX_TOKENS", "512")
    monkeypatch.setenv("DEFAULT_TIMEOUT_SECONDS", "12.5")

    request = ModelRequest(
        prompt="Prueba",
        max_tokens=128,
        timeout_seconds=5.0,
    )

    assert request.max_tokens == 128
    assert request.timeout_seconds == 5.0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DEFAULT_MAX_TOKENS", "0"),
        ("DEFAULT_MAX_TOKENS", "invalid"),
        ("DEFAULT_TIMEOUT_SECONDS", "0"),
        ("DEFAULT_TIMEOUT_SECONDS", "nan"),
    ],
)
def test_invalid_environment_defaults_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        ModelRequest(prompt="Prueba")
