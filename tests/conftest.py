"""Aísla state dirs para que ningún test escriba en producción."""
import pytest


@pytest.fixture(autouse=True)
def isolate_state_dirs(tmp_path, monkeypatch):
    from api import (
        task_bus,
        candidate_store,
        subscription_store,
        acceptance_store,
        distribution_store,
        trace_store,
        webhook_dispatcher,
    )

    monkeypatch.setattr(task_bus, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(candidate_store, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(subscription_store, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(acceptance_store, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(distribution_store, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(webhook_dispatcher, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(trace_store, "_trace_file", lambda: tmp_path / "traces.jsonl")
    monkeypatch.setenv("CHALAMANDRA_CANDIDATE_HMAC_KEY", "a5" * 32)
    yield


# Fixed, non-production key used only to create signed market-data fixtures.
TEST_MARKET_SOURCE_SEED_HEX = "42" * 32
TEST_MARKET_SOURCE_KEY_ID = "pytest-market-source-v1"


def sign_market_payload(payload):
    from nacl.signing import SigningKey
    from api.task_contracts import market_source_attestation_payload

    key = SigningKey(bytes.fromhex(TEST_MARKET_SOURCE_SEED_HEX))
    payload["source_attestation"] = {
        "key_id": TEST_MARKET_SOURCE_KEY_ID,
        "signature": key.sign(market_source_attestation_payload(payload)).signature.hex(),
    }
    return payload


@pytest.fixture(autouse=True)
def configure_market_source_test_verifier(monkeypatch):
    from nacl.signing import SigningKey

    key = SigningKey(bytes.fromhex(TEST_MARKET_SOURCE_SEED_HEX))
    monkeypatch.setenv(
        "CHALAMANDRA_MARKET_SOURCE_PUBLIC_KEY",
        key.verify_key.encode().hex(),
    )
    monkeypatch.setenv("CHALAMANDRA_MARKET_SOURCE_KEY_ID", TEST_MARKET_SOURCE_KEY_ID)
