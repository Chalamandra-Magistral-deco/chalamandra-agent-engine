import json
import os
import subprocess
import sys
import threading
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch

import jwt
import pytest
from nacl.signing import SigningKey

from api.crypto_signer import create_canonical_payload
from api.server import create_server

TEST_JWT_SECRET = "test-jwt-secret-32-bytes-minimum"
TEST_VERIFIER_SEED_HEX = "11" * 32
TEST_VERIFIER_KEY = SigningKey(bytes.fromhex(TEST_VERIFIER_SEED_HEX))
REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFIER_CLI = REPO_ROOT / "verifier" / "independent_acceptance_verifier.py"


def make_jwt(scopes="jobs:write jobs:read results:read acceptance:write"):
    import time

    payload = {"scope": scopes, "exp": int(time.time()) + 3600}
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


def make_decision(*, candidate_id="test-123", digest="a" * 64, outcome="accepted"):
    return {
        "decision_id": "dec-1",
        "candidate_id": candidate_id,
        "outcome": outcome,
        "criteria_checked": ["schema", "evidence_digest", "traceability"],
        "reasons": [],
        "missing_evidence": [],
        "verifier_id": "independent-test-verifier",
        "evidence_digest": digest,
        "decided_at": "2026-10-28T12:00:00+00:00",
        "trace_id": "1234567890abcdef1234567890abcdef",
    }


def sign_test_decision(payload):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO_ROOT),
        "CHALAMANDRA_ED25519_SEED": TEST_VERIFIER_SEED_HEX,
        "CHALAMANDRA_VERIFIER_ID": payload["verifier_id"],
    }
    action = "ACCEPT" if payload["outcome"] == "accepted" else "REJECT"
    confirmation = f"{action}:{payload['candidate_id']}"
    result = subprocess.run(
        [sys.executable, str(VERIFIER_CLI), "--confirm", confirmation],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"Isolated verifier failed: {result.stderr.strip()}")
    signed = json.loads(result.stdout)
    return signed["signature"]


@pytest.fixture
def api_server(monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", TEST_JWT_SECRET)
    # The API gets only the public key; remove any private signing seeds/secrets.
    monkeypatch.setenv(
        "CHALAMANDRA_VERIFIER_PUBLIC_KEY",
        TEST_VERIFIER_KEY.verify_key.encode().hex(),
    )
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_ID", "independent-test-verifier")
    monkeypatch.delenv("CHALAMANDRA_ED25519_SEED", raising=False)
    monkeypatch.delenv("CHALAMANDRA_VERIFIER_SECRET", raising=False)
    monkeypatch.delenv("CHALAMANDRA_HMAC_SECRET", raising=False)
    server = create_server("127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _post(url, data, headers=None):
    headers = dict(headers or {})
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=json.dumps(data).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def test_get_result_blocked_without_acceptance(api_server):
    with patch("api.candidate_store.get_candidate", return_value={"result_id": "test-123", "status": "candidate"}), patch(
        "api.acceptance_store.get_acceptance", return_value=None
    ):
        status, body = _get(
            f"{api_server}/v1/results/test-123",
            {"Authorization": f"Bearer {make_jwt()}"},
        )
    assert status == 202
    assert body["status"] == "pending_verification"


def test_get_result_delivered_with_acceptance(api_server):
    with patch("api.candidate_store.get_candidate", return_value={"result_id": "test-123", "status": "candidate"}), patch(
        "api.acceptance_store.get_acceptance", return_value={"outcome": "accepted"}
    ):
        status, body = _get(
            f"{api_server}/v1/results/test-123",
            {"Authorization": f"Bearer {make_jwt()}"},
        )
    assert status == 200
    assert body["result_id"] == "test-123"


def test_get_result_blocked_with_rejection(api_server):
    with patch("api.candidate_store.get_candidate", return_value={"result_id": "test-123", "status": "candidate"}), patch(
        "api.acceptance_store.get_acceptance",
        return_value={"outcome": "rejected", "reasons": ["bad data"]},
    ):
        status, body = _get(
            f"{api_server}/v1/results/test-123",
            {"Authorization": f"Bearer {make_jwt()}"},
        )
    assert status == 403
    assert body["error"] == "result_not_accepted"


@patch("api.candidate_store.get_candidate")
@patch("api.candidate_store.compute_evidence_digest")
def test_post_acceptance_persists_record(mock_digest, mock_get_candidate, api_server, tmp_path):
    mock_get_candidate.return_value = {"candidate_id": "test-123", "status": "candidate", "evidence": []}
    mock_digest.return_value = "a" * 64
    payload = make_decision()
    payload["signature"] = sign_test_decision(payload)

    with (
        patch("api.acceptance_store._acceptance_file", return_value=tmp_path / "acceptance_store.jsonl"),
        patch("api.task_bus.mark_result_accepted", return_value=0) as mark_accepted,
    ):
        status, body = _post(
            f"{api_server}/v1/acceptance",
            payload,
            {"Authorization": f"Bearer {make_jwt()}"},
        )
        assert status == 201
        assert (tmp_path / "acceptance_store.jsonl").exists()
        assert body["outcome"] == "accepted"

        repeated_status, repeated_body = _post(
            f"{api_server}/v1/acceptance",
            payload,
            {"Authorization": f"Bearer {make_jwt()}"},
        )
        assert repeated_status == 201
        assert repeated_body["idempotent_replay"] is True
        assert len((tmp_path / "acceptance_store.jsonl").read_text().splitlines()) == 1
        assert mark_accepted.call_count == 1


def test_acceptance_fails_closed_without_configured_verifier_identity(api_server, monkeypatch):
    monkeypatch.delenv("CHALAMANDRA_VERIFIER_ID", raising=False)
    payload = make_decision()
    payload["signature"] = sign_test_decision(payload)
    status, body = _post(
        f"{api_server}/v1/acceptance",
        payload,
        {"Authorization": f"Bearer {make_jwt()}"},
    )
    assert status == 400
    assert "CHALAMANDRA_VERIFIER_ID" in body["error"]


def test_acceptance_rejects_signed_but_unexpected_verifier_identity(api_server):
    payload = make_decision()
    payload["verifier_id"] = "unexpected-verifier"
    payload["signature"] = sign_test_decision(payload)
    status, body = _post(
        f"{api_server}/v1/acceptance",
        payload,
        {"Authorization": f"Bearer {make_jwt()}"},
    )
    assert status == 400
    assert "identidad del verificador" in body["error"].lower()


def test_acceptance_without_signature_fails(api_server):
    payload = make_decision()
    status, body = _post(
        f"{api_server}/v1/acceptance",
        payload,
        {"Authorization": f"Bearer {make_jwt()}"},
    )
    assert status == 400
    assert "signature" in body["error"].lower() or "firma" in body["error"].lower()


def test_acceptance_with_invalid_signature_fails(api_server):
    payload = make_decision()
    payload["signature"] = "bad-hash"
    status, body = _post(
        f"{api_server}/v1/acceptance",
        payload,
        {"Authorization": f"Bearer {make_jwt()}"},
    )
    assert status == 400
    assert "firma" in body["error"].lower()


def test_legacy_shared_hmac_signature_is_rejected(api_server, monkeypatch):
    import hashlib
    import hmac

    monkeypatch.setenv("CHALAMANDRA_HMAC_SECRET", "legacy-shared-secret-for-test-only")
    payload = make_decision()
    payload["signature"] = hmac.new(
        b"legacy-shared-secret-for-test-only",
        create_canonical_payload(payload),
        hashlib.sha256,
    ).hexdigest()
    status, body = _post(
        f"{api_server}/v1/acceptance",
        payload,
        {"Authorization": f"Bearer {make_jwt()}"},
    )
    assert status == 400
    assert "firma" in body["error"].lower()


def test_acceptance_with_tampered_field_fails(api_server):
    payload = make_decision()
    payload["signature"] = sign_test_decision(payload)
    payload["reasons"] = ["tampered after signing"]
    status, body = _post(
        f"{api_server}/v1/acceptance",
        payload,
        {"Authorization": f"Bearer {make_jwt()}"},
    )
    assert status == 400
    assert "firma" in body["error"].lower()


@patch("api.candidate_store.get_candidate")
@patch("api.candidate_store.compute_evidence_digest")
def test_acceptance_cannot_publish_blocked_candidate(
    mock_digest, mock_get_candidate, api_server
):
    mock_get_candidate.return_value = {
        "candidate_id": "blocked-123",
        "status": "blocked",
        "evidence": [],
    }
    mock_digest.return_value = "b" * 64
    payload = make_decision(candidate_id="blocked-123", digest="b" * 64)
    payload["signature"] = sign_test_decision(payload)
    status, body = _post(
        f"{api_server}/v1/acceptance",
        payload,
        {"Authorization": f"Bearer {make_jwt()}"},
    )
    assert status == 400
    assert "resultados bloqueados" in body["error"]


def test_acceptance_store_corruption_fails_closed(tmp_path):
    from api.acceptance_store import get_acceptance

    path = tmp_path / "acceptance_store.jsonl"
    path.write_text('{"candidate_id":\n', encoding="utf-8")
    with patch("api.acceptance_store._acceptance_file", return_value=path):
        with pytest.raises(ValueError, match="acceptance_store.jsonl inválido"):
            get_acceptance("test-123")
