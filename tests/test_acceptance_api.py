import json
import threading
import urllib.request
import urllib.error
from unittest.mock import patch
import pytest
import jwt
from api.server import create_server
from api import acceptance_store

TEST_JWT_SECRET = "test-verifier-secret"

def make_jwt(scopes="jobs:write jobs:read results:read acceptance:write"):
    import time
    payload = {"scope": scopes, "exp": int(time.time()) + 3600}
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")

@pytest.fixture
def api_server(monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_SECRET", TEST_JWT_SECRET)
    server = create_server("127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    thread.join()

def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

def _post(url, data, headers=None):
    headers = headers or {}
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

# === TESTS ORIGINALES RESTAURADOS (4) ===
@patch("api.candidate_store.get_candidate")
@patch("api.acceptance_store.get_acceptance")
def test_get_result_blocked_without_acceptance(mock_acc, mock_res, api_server):
    mock_res.return_value = {"result_id": "test-123", "status": "ACCEPTED"}
    mock_acc.return_value = None
    status, body = _get(f"{api_server}/v1/results/test-123", {"Authorization": f"Bearer {make_jwt()}"})
    assert status == 202
    assert body["status"] == "pending_verification"

@patch("api.candidate_store.get_candidate")
@patch("api.acceptance_store.get_acceptance")
def test_get_result_delivered_with_acceptance(mock_acc, mock_res, api_server):
    mock_res.return_value = {"result_id": "test-123", "status": "ACCEPTED"}
    mock_acc.return_value = {"outcome": "accepted"}
    status, body = _get(f"{api_server}/v1/results/test-123", {"Authorization": f"Bearer {make_jwt()}"})
    assert status == 200
    assert body["result_id"] == "test-123"

@patch("api.candidate_store.get_candidate")
@patch("api.acceptance_store.get_acceptance")
def test_get_result_blocked_with_rejection(mock_acc, mock_res, api_server):
    mock_res.return_value = {"result_id": "test-123", "status": "ACCEPTED"}
    mock_acc.return_value = {"outcome": "rejected", "reasons": ["bad data"]}
    status, body = _get(f"{api_server}/v1/results/test-123", {"Authorization": f"Bearer {make_jwt()}"})
    assert status == 403
    assert body["error"] == "result_not_accepted"

@patch("api.candidate_store.get_candidate")
@patch("api.candidate_store.compute_evidence_digest")
def test_post_acceptance_persists_record(mock_digest, mock_get_candidate, api_server, tmp_path):
    mock_get_candidate.return_value = {"candidate_id": "test-123", "evidence": []}
    mock_digest.return_value = "h"
    
    with patch("api.acceptance_store._acceptance_file", return_value=tmp_path / "acc.jsonl"):
        payload = {"decision_id": "dec-1", "candidate_id": "test-123", "outcome": "accepted",
                   "verifier_id": "tester", "evidence_digest": "h", "trace_id": "t"}
        payload["signature"] = acceptance_store.generate_signature(payload)
        status, body = _post(f"{api_server}/v1/acceptance", payload, {"Authorization": f"Bearer {make_jwt()}"})
        assert status == 201
        assert (tmp_path / "acc.jsonl").exists()

# === TESTS NUEVOS HMAC (3) ===
def test_acceptance_without_signature_fails(api_server):
    payload = {"decision_id": "dec-1", "candidate_id": "test-123", "outcome": "accepted"}
    status, body = _post(f"{api_server}/v1/acceptance", payload, {"Authorization": f"Bearer {make_jwt()}"})
    assert status == 400
    assert "firma" in body["error"].lower() or "signature" in body["error"].lower()

def test_acceptance_with_invalid_signature_fails(api_server):
    payload = {"decision_id": "dec-1", "candidate_id": "test-123", "outcome": "accepted", "signature": "bad-hash"}
    status, body = _post(f"{api_server}/v1/acceptance", payload, {"Authorization": f"Bearer {make_jwt()}"})
    assert status == 400
    assert "firma" in body["error"].lower() or "signature" in body["error"].lower()

def test_acceptance_with_tampered_field_fails(api_server):
    payload = {"decision_id": "dec-1", "candidate_id": "test-123", "outcome": "accepted",
               "verifier_id": "tester", "evidence_digest": "h", "trace_id": "t",
               "reasons": ["ok"]}
    payload["signature"] = acceptance_store.generate_signature(payload)
    # Alterar un campo DESPUÉS de firmar
    payload["reasons"] = ["tampered"]
    status, body = _post(f"{api_server}/v1/acceptance", payload, {"Authorization": f"Bearer {make_jwt()}"})
    assert status == 400
    assert "firma" in body["error"].lower() or "signature" in body["error"].lower()
