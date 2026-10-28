"""Local end-to-end test: CatedralX dispatch -> Agents API -> worker -> acceptance gate."""
import base64
import importlib.util
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

CATEDRALX_ROOT = Path(__file__).resolve().parents[2] / "CatedralX"
DISPATCH_SCRIPT = CATEDRALX_ROOT / "elite" / "bin" / "dispatch_agents.py"
SPEC = importlib.util.spec_from_file_location("dispatch_agents_e2e", DISPATCH_SCRIPT)
dispatch_agents = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(dispatch_agents)


def _http_json(url, *, method="GET", token, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body) if body else {}


def _make_jwt(secret, scope):
    def enc(data):
        return base64.urlsafe_b64encode(
            json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
        ).rstrip(b"=").decode()

    header = enc({"alg": "HS256", "typ": "JWT"})
    claims = enc({"scope": scope, "exp": int(time.time()) + 120, "sub": "test-verifier"})
    import hashlib
    import hmac

    signing = f"{header}.{claims}"
    signature = hmac.new(secret.encode(), signing.encode(), hashlib.sha256).digest()
    return signing + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()


def test_catedralx_to_agents_api_worker_and_independent_acceptance(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    from api import acceptance_store, candidate_store, subscription_store, task_bus, worker_loop
    from api.server import create_server

    jwt_secret = "integration-test-jwt-secret-32-bytes-minimum"
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", jwt_secret)
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_SECRET", "integration-test-verifier-secret")
    monkeypatch.setenv("CHALAMANDRA_API_TOKEN", "integration-test-api-token")
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "false")
    monkeypatch.setenv("CATEDRALX_PATH", str(CATEDRALX_ROOT))
    monkeypatch.setenv("CATEDRALX_STATE_PATH", str(tmp_path / "catedralx-state" / "hormonas.json"))
    monkeypatch.setenv("CATEDRALX_FEEDBACK_PATH", str(tmp_path / "catedralx-state" / "feedback.jsonl"))

    agents_state = tmp_path / "agents-state"
    monkeypatch.setattr(task_bus, "_state_dir", lambda: agents_state)
    monkeypatch.setattr(candidate_store, "_state_dir", lambda: agents_state)
    monkeypatch.setattr(acceptance_store, "_state_dir", lambda: agents_state)
    monkeypatch.setattr(subscription_store, "_state_dir", lambda: agents_state)

    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        observed_at = datetime.now(timezone.utc).isoformat()
        prices = [100.0 + index * 0.01 for index in range(59)]
        payload = {
            "historical_prices": prices,
            "current_price": prices[-1],
            "observed_at": observed_at,
            "source": "isolated_integration_test_fixture",
        }

        # This is a real HTTP POST to a temporary loopback API, not a mock.
        queued = dispatch_agents.dispatch_task(
            "financial_analysis",
            payload,
            base_url,
            jwt_secret,
            timeout=5,
        )
        assert queued["status"] == "queued"
        run_id = queued["run_id"]

        task = task_bus.get_next_task()
        assert task is not None
        assert task["run_id"] == run_id
        assert json.loads(task["payload"]) == payload

        # Run the actual canonical CLI in a subprocess, with state redirected to tmp_path.
        candidate_result = worker_loop.process_task(task)
        assert candidate_result["status"] == "candidate"
        result_id = candidate_result["candidate_id"]
        assert candidate_result["catedralx_trace_id"]
        assert candidate_result["evidence"][0]["observed_at"] == observed_at
        assert candidate_result["evidence"][0]["market_source"] == payload["source"]

        read_token = _make_jwt(jwt_secret, "orchestration:read results:read")
        status, run = _http_json(
            f"{base_url}/internal/v1/orchestration/runs/{run_id}",
            token=read_token,
        )
        assert status == 200
        assert run["status"] == "awaiting_acceptance"

        status, pending = _http_json(
            f"{base_url}/v1/results/{result_id}",
            token=read_token,
        )
        assert status == 202
        assert pending["status"] == "pending_verification"

        # A separate, signed acceptance decision is required to publish the result.
        decision = {
            "decision_id": str(uuid.uuid4()),
            "candidate_id": result_id,
            "outcome": "accepted",
            "criteria_checked": ["DecisionResult schema", "evidence digest", "traceability"],
            "reasons": [],
            "missing_evidence": [],
            "verifier_id": "isolated-test-verifier",
            "evidence_digest": candidate_store.compute_evidence_digest(
                candidate_store.get_candidate(result_id)
            ),
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "trace_id": candidate_result["trace_id"],
        }
        decision["signature"] = acceptance_store.generate_signature(decision)
        write_token = _make_jwt(jwt_secret, "acceptance:write")
        status, accepted = _http_json(
            f"{base_url}/v1/acceptance",
            method="POST",
            token=write_token,
            payload=decision,
        )
        assert status == 201
        assert accepted["outcome"] == "accepted"

        status, final_job = _http_json(
            f"{base_url}/v1/jobs/{task['task_id']}",
            token=_make_jwt(jwt_secret, "jobs:read"),
        )
        assert status == 200
        assert final_job["status"] == "accepted"
        assert final_job["result_id"] == result_id

        status, final_run = _http_json(
            f"{base_url}/internal/v1/orchestration/runs/{run_id}",
            token=read_token,
        )
        assert status == 200
        assert final_run["status"] == "accepted"
        assert final_run["task_statuses"] == ["accepted"]

        status, delivered = _http_json(
            f"{base_url}/v1/results/{result_id}",
            token=read_token,
        )
        assert status == 200
        assert delivered["candidate_id"] == result_id
        assert delivered["delivery_status"] == "accepted"
        assert delivered["acceptance"]["decision_id"] == decision["decision_id"]
        assert delivered["trace_id"] == candidate_result["trace_id"]
        assert delivered["evidence"][0]["market_source"] == payload["source"]
        assert delivered["evidence"][0]["observed_at"] == observed_at
        assert delivered["orders_created"] is False
        assert delivered["orders_executed"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
