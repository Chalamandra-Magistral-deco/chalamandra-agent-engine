"""Tests for optional provider credentials and Azure Key Vault fallback."""

from core.orchestrator import Orchestrator


PROVIDER_KEYS = (
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)


def _clear_provider_environment(monkeypatch) -> None:
    for name in (*PROVIDER_KEYS, "AZURE_KEYVAULT_URI"):
        monkeypatch.delenv(name, raising=False)


def test_environment_key_takes_priority_without_key_vault(monkeypatch) -> None:
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    orchestrator = Orchestrator()

    assert "ollama" in orchestrator.available_models()
    assert "gemini" in orchestrator.available_models()
    assert orchestrator.credential_status["GEMINI_API_KEY"] == "environment"
    assert orchestrator.credential_status["OPENAI_API_KEY"] == "not_configured"


def test_cloud_keys_can_load_from_key_vault(monkeypatch) -> None:
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv(
        "AZURE_KEYVAULT_URI",
        "https://example-test-vault.vault.azure.net/",
    )

    calls: list[str] = []

    def fake_get_secret(name: str) -> str:
        calls.append(name)
        return f"test-value-for-{name}"

    monkeypatch.setattr("security.keyvault.get_secret", fake_get_secret)

    orchestrator = Orchestrator()

    assert set(PROVIDER_KEYS).issubset(orchestrator.credential_status)
    expected_vault_names = {name.lower().replace("_", "-") for name in PROVIDER_KEYS}
    assert expected_vault_names.issubset(calls)
    assert {"gemini", "deepseek", "gpt", "claude"}.issubset(
        orchestrator.available_models()
    )
    assert all(
        orchestrator.credential_status[name] == "azure_key_vault"
        for name in PROVIDER_KEYS
    )


def test_key_vault_failure_does_not_block_local_ollama(monkeypatch) -> None:
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv(
        "AZURE_KEYVAULT_URI",
        "https://example-test-vault.vault.azure.net/",
    )

    def fail_get_secret(name: str) -> str:
        raise RuntimeError("simulated key vault outage")

    monkeypatch.setattr("security.keyvault.get_secret", fail_get_secret)

    orchestrator = Orchestrator()

    assert orchestrator.available_models() == ["ollama"]
    assert all(
        orchestrator.credential_status[name] == "unavailable:RuntimeError"
        for name in PROVIDER_KEYS
    )
