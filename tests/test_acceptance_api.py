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
    # Inyectamos el token para que el servidor lo acepte
    monkeypatch.setenv("CHALAMANDRA_API_TOKEN", "test-token")
    
    # Levantamos el servidor en un puerto aleatorio
    server = create_server(host="127.0.0.1", port=0)
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

@patch("api.job_manager.get_result")
@patch("api.acceptance_store.get_acceptance")
def test_get_result_blocked_without_acceptance(mock_get_acc, mock_get_result, api_server):
    mock_get_result.return_value = {"result_id": "test-123", "status": "ACCEPTED"}
    mock_get_acc.return_value = None
    
    headers = {"Authorization": "Bearer test-token"}
    status, body = _get(f"{api_server}/v1/results/test-123", headers)
    
    assert status == 202
    assert body["status"] == "pending_verification"

@patch("api.job_manager.get_result")
@patch("api.acceptance_store.get_acceptance")
def test_get_result_delivered_with_acceptance(mock_get_acc, mock_get_result, api_server):
    mock_get_result.return_value = {"result_id": "test-123", "status": "ACCEPTED"}
    mock_get_acc.return_value = {"outcome": "accepted"}
    
    headers = {"Authorization": "Bearer test-token"}
    status, body = _get(f"{api_server}/v1/results/test-123", headers)
    
    assert status == 200
    assert body["result_id"] == "test-123"

@patch("api.job_manager.get_result")
@patch("api.acceptance_store.get_acceptance")
def test_get_result_blocked_with_rejection(mock_get_acc, mock_get_result, api_server):
    mock_get_result.return_value = {"result_id": "test-123", "status": "ACCEPTED"}
    mock_get_acc.return_value = {"outcome": "rejected", "reasons": ["bad data"]}
    
    headers = {"Authorization": "Bearer test-token"}
    status, body = _get(f"{api_server}/v1/results/test-123", headers)
    
    assert status == 403
    assert body["error"] == "result_not_accepted"

def test_post_acceptance_creates_record(api_server, tmp_path):
    with patch("api.acceptance_store._acceptance_file", return_value=tmp_path / "acc.jsonl"):
        headers = {"Authorization": "Bearer test-token"}
        payload = {
            "decision_id": "dec-1",
            "candidate_id": "test-123",
            "outcome": "accepted",
            "verifier_id": "tester"
        }
        status, body = _post(f"{api_server}/v1/acceptance", payload, headers)
        assert status == 201
        assert body["decision_id"] == "dec-1"
        assert (tmp_path / "acc.jsonl").exists()
