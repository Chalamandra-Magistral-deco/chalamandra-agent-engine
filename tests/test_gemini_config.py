"""Pruebas de configuración del adaptador Gemini sin llamadas externas."""

from adapters.gemini import GeminiAdapter


def test_gemini_model_is_configurable_from_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-model")

    adapter = GeminiAdapter(api_key="test-key")

    assert adapter.model == "gemini-test-model"


def test_explicit_gemini_model_overrides_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-env-model")

    adapter = GeminiAdapter(api_key="test-key", model="gemini-explicit-model")

    assert adapter.model == "gemini-explicit-model"


def test_gemini_model_uses_default_when_environment_is_empty(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "")

    adapter = GeminiAdapter(api_key="test-key")

    assert adapter.model == GeminiAdapter.default_version
