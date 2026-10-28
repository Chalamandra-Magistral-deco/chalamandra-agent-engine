import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from api.crypto_signer import verify_decision

REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFIER_CLI = REPO_ROOT / "verifier" / "independent_acceptance_verifier.py"
SEED_HEX = "44" * 32
VERIFIER_KEY = SigningKey(bytes.fromhex(SEED_HEX))


def _decision():
    return {
        "decision_id": "decision-isolated-1",
        "candidate_id": "candidate-isolated-1",
        "outcome": "accepted",
        "criteria_checked": ["evidence_digest", "traceability", "policy_boundary"],
        "reasons": [],
        "missing_evidence": [],
        "verifier_id": "isolated-test-verifier",
        "evidence_digest": "a" * 64,
        "decided_at": "2026-10-28T12:00:00+00:00",
        "trace_id": "trace-isolated-1",
    }


def _run_verifier(decision, *, confirm=None, verifier_id="isolated-test-verifier", seed=SEED_HEX):
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if seed is not None:
        environment["CHALAMANDRA_ED25519_SEED"] = seed
    if verifier_id is not None:
        environment["CHALAMANDRA_VERIFIER_ID"] = verifier_id
    action = "ACCEPT" if decision["outcome"] == "accepted" else "REJECT"
    confirmation = confirm or f"{action}:{decision['candidate_id']}"
    return subprocess.run(
        [sys.executable, str(VERIFIER_CLI), "--confirm", confirmation],
        input=json.dumps(decision),
        text=True,
        capture_output=True,
        env=environment,
        timeout=5,
        check=False,
    )


def test_independent_process_signs_after_explicit_candidate_confirmation(monkeypatch):
    decision = _decision()
    completed = _run_verifier(decision)

    assert completed.returncode == 0, completed.stderr
    signed = json.loads(completed.stdout)
    assert signed["candidate_id"] == decision["candidate_id"]
    assert len(signed["signature"]) == 128

    monkeypatch.setenv(
        "CHALAMANDRA_VERIFIER_PUBLIC_KEY", VERIFIER_KEY.verify_key.encode().hex()
    )
    monkeypatch.delenv("CHALAMANDRA_ED25519_SEED", raising=False)
    monkeypatch.delenv("CHALAMANDRA_VERIFIER_SECRET", raising=False)
    monkeypatch.delenv("CHALAMANDRA_HMAC_SECRET", raising=False)
    assert verify_decision(decision, signed["signature"]) is True


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"confirm": "ACCEPT:another-candidate"}, "operator_confirmation_mismatch"),
        ({"verifier_id": "wrong-identity"}, "verifier_identity_mismatch"),
        ({"seed": None}, "ed25519_seed_not_configured"),
    ],
)
def test_independent_process_fails_closed(kwargs, error):
    completed = _run_verifier(_decision(), **kwargs)
    assert completed.returncode == 2
    assert error in completed.stderr
    assert completed.stdout == ""


def test_independent_process_refuses_acceptance_with_missing_evidence():
    decision = _decision()
    decision["missing_evidence"] = ["source authenticity"]
    completed = _run_verifier(decision)
    assert completed.returncode == 2
    assert "accepted_decision_cannot_have_reasons_or_missing_evidence" in completed.stderr


def test_independent_process_refuses_acceptance_without_checked_criteria():
    decision = _decision()
    decision["criteria_checked"] = []
    completed = _run_verifier(decision)
    assert completed.returncode == 2
    assert "accepted_decision_requires_checked_criteria" in completed.stderr


def test_independent_process_rejects_unapproved_or_invalid_digest_shape():
    decision = _decision()
    decision["evidence_digest"] = "not-a-sha256-digest"
    completed = _run_verifier(decision)
    assert completed.returncode == 2
    assert "evidence_digest_must_be_sha256_hex" in completed.stderr
