"""Non-destructive reconciliation rules for duplicated signed acceptance records."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from nacl.signing import SigningKey

from api import acceptance_store, candidate_store
from api.crypto_signer import create_canonical_payload


def _signed_record(key: SigningKey, *, decision_id: str, decided_at: str, outcome: str = "accepted", reasons=None):
    record = {
        "decision_id": decision_id,
        "candidate_id": "candidate-duplicate-test",
        "outcome": outcome,
        "criteria_checked": ["signature", "evidence_digest", "traceability"],
        "reasons": list(reasons or []),
        "missing_evidence": [],
        "verifier_id": "reconciliation-test-verifier",
        "evidence_digest": "a" * 64,
        "decided_at": decided_at,
        "trace_id": "1234567890abcdef1234567890abcdef",
    }
    record["signature"] = key.sign(create_canonical_payload(record)).signature.hex()
    return record


def _configure(monkeypatch, tmp_path: Path, key: SigningKey):
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_PUBLIC_KEY", key.verify_key.encode().hex())
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_ID", "reconciliation-test-verifier")
    monkeypatch.setattr(
        candidate_store,
        "get_candidate",
        lambda candidate_id: {
            "candidate_id": candidate_id, "status": "candidate", "evidence": [],
            "trace_id": "1234567890abcdef1234567890abcdef",
        },
    )
    monkeypatch.setattr(candidate_store, "compute_evidence_digest", lambda candidate: "a" * 64)
    path = tmp_path / "acceptance_store.jsonl"
    return path


def _write(path: Path, records: list[dict]) -> bytes:
    content = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in records)
    path.write_text(content, encoding="utf-8")
    return content.encode("utf-8")


def test_identical_signed_acceptances_reconcile_to_first_record_without_mutating_history(monkeypatch, tmp_path):
    key = SigningKey(bytes.fromhex("11" * 32))
    path = _configure(monkeypatch, tmp_path, key)
    first_at = datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc).isoformat()
    second_at = (datetime(2026, 10, 28, 18, 10, tzinfo=timezone.utc)).isoformat()
    records = [
        _signed_record(key, decision_id="decision-first", decided_at=first_at),
        _signed_record(key, decision_id="decision-replay", decided_at=second_at),
    ]
    original = _write(path, records)

    with patch("api.acceptance_store._acceptance_file", return_value=path):
        result = acceptance_store.get_acceptance("candidate-duplicate-test")

    assert result is not None
    assert result["decision_id"] == "decision-first"
    assert result["outcome"] == "accepted"
    assert path.read_bytes() == original
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_semantically_conflicting_signed_acceptances_fail_closed(monkeypatch, tmp_path):
    key = SigningKey(bytes.fromhex("22" * 32))
    path = _configure(monkeypatch, tmp_path, key)
    first_at = datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc).isoformat()
    second_at = (datetime(2026, 10, 28, 18, 10, tzinfo=timezone.utc)).isoformat()
    records = [
        _signed_record(key, decision_id="decision-accept", decided_at=first_at),
        _signed_record(key, decision_id="decision-reject", decided_at=second_at, outcome="rejected", reasons=["evidence insufficient"]),
    ]
    _write(path, records)

    with patch("api.acceptance_store._acceptance_file", return_value=path):
        with pytest.raises(ValueError, match="semántico conflictivo"):
            acceptance_store.get_acceptance("candidate-duplicate-test")


def test_invalid_signature_in_duplicate_group_fails_closed(monkeypatch, tmp_path):
    key = SigningKey(bytes.fromhex("33" * 32))
    path = _configure(monkeypatch, tmp_path, key)
    at = datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc).isoformat()
    first = _signed_record(key, decision_id="decision-valid", decided_at=at)
    second = _signed_record(key, decision_id="decision-invalid", decided_at=at)
    second["signature"] = "00" * 64
    _write(path, [first, second])

    with patch("api.acceptance_store._acceptance_file", return_value=path):
        with pytest.raises(ValueError, match="Firma Ed25519 inválida"):
            acceptance_store.get_acceptance("candidate-duplicate-test")
