import json
import threading
import urllib.request
import urllib.error
from unittest.mock import patch
import pytest
from api.server import create_server
from api import acceptance_store

@pytest.fixture
def api_server(monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_API_TOKEN", "test-token")
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_SECRET", "test-verifier-secret")
    server = create_server(host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    thread.join()

def _post(url, data, headers=None):
    headers = headers or {}
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

def test_acceptance_without_signature_fails(api_server):
    headers = {"Authorization": "Bearer test-token"}
    payload = {"decision_id": "dec-1", "candidate_id": "test-123", "outcome": "accepted", "verifier_id": "tester"}
    status, body = _post(f"{api_server}/v1/acceptance", payload, headers)
    assert status == 400
    assert "firma" in body["error"].lower() or "signature" in body["error"].lower()

def test_acceptance_with_invalid_signature_fails(api_server):
    headers = {"Authorization": "Bearer test-token"}
    payload = {"decision_id": "dec-1", "candidate_id": "test-123", "outcome": "accepted", "signature": "bad-hash"}
    status, body = _post(f"{api_server}/v1/acceptance", payload, headers)
    assert status == 400
    assert "firma" in body["error"].lower() or "signature" in body["error"].lower()

def test_acceptance_with_valid_signature_passes(api_server):
    headers = {"Authorization": "Bearer test-token"}
    payload = {
        "decision_id": "dec-1", "candidate_id": "test-123", "outcome": "accepted", 
        "evidence_digest": "hash123", "trace_id": "trace-1", "verifier_id": "tester"
    }
    # Generar firma correcta usando la función interna
    from api.acceptance_store import generate_signature
    payload["signature"] = generate_signature(payload)
    
    status, body = _post(f"{api_server}/v1/acceptance", payload, headers)
    assert status == 201
    assert body["decision_id"] == "dec-1"
