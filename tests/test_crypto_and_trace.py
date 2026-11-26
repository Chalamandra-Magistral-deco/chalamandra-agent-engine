"""Crypto and W3C tracing tests; signatures originate in the separate verifier process."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from nacl.signing import SigningKey

import api.crypto_signer as crypto_signer
from api.crypto_signer import verify_decision
from api.trace_parser import parse_traceparent

REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFIER_CLI = REPO_ROOT / "verifier" / "independent_acceptance_verifier.py"
TEST_SEED_HEX = "a" * 64
TEST_KEY = SigningKey(bytes.fromhex(TEST_SEED_HEX))
os.environ["CHALAMANDRA_VERIFIER_PUBLIC_KEY"] = TEST_KEY.verify_key.encode().hex()


def get_base_record():
    return {
        "candidate_id": "cand-1",
        "outcome": "accepted",
        "evidence_digest": "a" * 64,
        "trace_id": "1234567890abcdef1234567890abcdef",
        "verifier_id": "test-verifier",
        "decision_id": "d1",
        "criteria_checked": ["evidence_digest", "traceability"],
        "reasons": [],
        "missing_evidence": [],
        "decided_at": "2026-10-25T12:00:00Z",
    }


def _sign_in_separate_process(record):
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "CHALAMANDRA_ED25519_SEED": TEST_SEED_HEX,
        "CHALAMANDRA_VERIFIER_ID": record["verifier_id"],
    }
    result = subprocess.run(
        [
            sys.executable,
            str(VERIFIER_CLI),
            "--confirm",
            f"{'ACCEPT' if record['outcome'] == 'accepted' else 'REJECT'}:{record['candidate_id']}",
        ],
        input=json.dumps(record),
        capture_output=True,
        text=True,
        env=environment,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["signature"]


def test_api_crypto_module_has_no_signing_entrypoint():
    assert not hasattr(crypto_signer, "sign_decision")


def test_verification_requires_public_key_even_if_caller_has_private_seed(monkeypatch):
    record = get_base_record()
    signature = _sign_in_separate_process(record)
    monkeypatch.setenv("CHALAMANDRA_ED25519_SEED", TEST_SEED_HEX)
    monkeypatch.delenv("CHALAMANDRA_VERIFIER_PUBLIC_KEY", raising=False)
    with pytest.raises(ValueError, match="CHALAMANDRA_VERIFIER_PUBLIC_KEY"):
        verify_decision(record, signature)


def test_signature_fails_on_mutated_reasons():
    record = get_base_record()
    signature = _sign_in_separate_process(record)
    record["reasons"] = ["tampered"]
    assert verify_decision(record, signature) is False


def test_signature_fails_on_mutated_decided_at():
    record = get_base_record()
    signature = _sign_in_separate_process(record)
    record["decided_at"] = "2026-10-25T12:05:00Z"
    assert verify_decision(record, signature) is False


def test_w3c_rejects_uppercase_hex():
    assert parse_traceparent("00-12345678901234567890123456789012-1234567890123456-00") is not None
    assert parse_traceparent("00-1234567890123456789012345678901A-1234567890123456-00") is None


def test_w3c_accepts_sampled_traceparent_flags():
    parsed = parse_traceparent("00-12345678901234567890123456789012-1234567890123456-01")
    assert parsed is not None
    assert parsed["flags"] == "01"

def test_w3c_rejects_all_zero_trace_id():
    assert parse_traceparent("00-00000000000000000000000000000000-1234567890123456-00") is None


def test_w3c_rejects_all_zero_parent_id():
    assert parse_traceparent("00-12345678901234567890123456789012-0000000000000000-00") is None


def test_w3c_rejects_version_ff():
    assert parse_traceparent("ff-12345678901234567890123456789012-1234567890123456-00") is None
