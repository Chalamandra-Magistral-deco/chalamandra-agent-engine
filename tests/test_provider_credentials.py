"""Tests for environment-only optional provider credentials."""

from core.orchestrator import Orchestrator


PROVIDER_KEYS = (
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)


def _clear_provider_environment(monkeypatch) -> None:
    for name in PROVIDER_KEYS:
        monkeypatch.delenv(name, raising=False)


def test_environment_key_registers_only_its_provider(monkeypatch) -> None:
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    orchestrator = Orchestrator()

    assert "ollama" in orchestrator.available_models()
    assert "gemini" in orchestrator.available_models()
    assert orchestrator.credential_status["GEMINI_API_KEY"] == "environment"
    assert orchestrator.credential_status["OPENAI_API_KEY"] == "not_configured"


def test_missing_cloud_provider_keys_do_not_block_local_ollama(monkeypatch) -> None:
    _clear_provider_environment(monkeypatch)

    orchestrator = Orchestrator()

    assert orchestrator.available_models() == ["ollama"]
    assert all(
        orchestrator.credential_status[name] == "not_configured"
        for name in PROVIDER_KEYS
    )
