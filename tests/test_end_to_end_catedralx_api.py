"""Local end-to-end test: CatedralX dispatch -> Agents API -> worker -> acceptance gate."""
import base64
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from nacl.signing import SigningKey

import pytest

_default_catedralx_root = Path(__file__).resolve().parents[2] / "CatedralX"
CATEDRALX_ROOT = Path(os.environ.get("CATEDRALX_PATH", _default_catedralx_root)).resolve()
if not (CATEDRALX_ROOT / "BIN" / "catedral").is_file():
    pytest.skip(
        "CatedralX checkout is not available; set CATEDRALX_PATH to run this cross-repo test.",
        allow_module_level=True,
    )


def _http_json(url, *, method="GET", token, payload=None, extra_headers=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    headers.update(extra_headers or {})
    request = Request(url, data=data, method=method, headers=headers)
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


TEST_VERIFIER_SEED_HEX = "22" * 32
TEST_VERIFIER_KEY = SigningKey(bytes.fromhex(TEST_VERIFIER_SEED_HEX))
VERIFIER_CLI = Path(__file__).resolve().parents[1] / "verifier" / "independent_acceptance_verifier.py"


def _sign_decision(decision):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "CHALAMANDRA_ED25519_SEED": TEST_VERIFIER_SEED_HEX,
        "CHALAMANDRA_VERIFIER_ID": decision["verifier_id"],
    }
    action = "ACCEPT" if decision["outcome"] == "accepted" else "REJECT"
    confirmation = f"{action}:{decision['candidate_id']}"
    result = subprocess.run(
        [sys.executable, str(VERIFIER_CLI), "--confirm", confirmation],
        input=json.dumps(decision),
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"Isolated verifier failed: {result.stderr.strip()}")
    return json.loads(result.stdout)["signature"]


def test_catedralx_to_agents_api_worker_and_independent_acceptance(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    from api import acceptance_store, candidate_store, subscription_store, task_bus, worker_loop
    from api.server import create_server

    jwt_secret = "integration-test-jwt-secret-32-bytes-minimum"
    service_secret = "integration-test-catedralx-service-jwt-secret-2026"
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", jwt_secret)
    monkeypatch.setenv("CHALAMANDRA_CATEDRALX_JWT_SECRET", service_secret)
    monkeypatch.setenv(
        "CHALAMANDRA_VERIFIER_PUBLIC_KEY", TEST_VERIFIER_KEY.verify_key.encode().hex()
    )
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_ID", "isolated-test-verifier")
    monkeypatch.delenv("CHALAMANDRA_ED25519_SEED", raising=False)
    monkeypatch.delenv("CHALAMANDRA_VERIFIER_SECRET", raising=False)
    monkeypatch.delenv("CHALAMANDRA_HMAC_SECRET", raising=False)
    monkeypatch.setenv("CHALAMANDRA_API_TOKEN", "integration-test-api-token")
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "false")
    monkeypatch.setenv("CATEDRALX_PATH", str(CATEDRALX_ROOT))
    monkeypatch.setenv("CATEDRALX_STATE_PATH", str(tmp_path / "catedralx-state" / "hormonas.json"))
    monkeypatch.setenv("CATEDRALX_FEEDBACK_PATH", str(tmp_path / "catedralx-state" / "feedback.jsonl"))
    monkeypatch.setenv("CATEDRALX_RESULT_STORE_PATH", str(tmp_path / "catedralx-state" / "results.jsonl"))

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

        # Normal inbound product path: /v1/request -> TaskBus -> real CatedralX CLI.
        trace_id = "1234567890abcdef1234567890abcdef"
        traceparent = f"00-{trace_id}-1234567890abcdef-01"
        request_contract = {
            "objective": "financial_analysis",
            "payload": payload,
            "permissions": {"allowed_tools": []},
            "success_criteria": ["Produce a traced, analysis-only CatedralX DecisionResult."],
        }
        status, queued = _http_json(
            f"{base_url}/v1/request",
            method="POST",
            token=_make_jwt(jwt_secret, "jobs:write"),
            payload=request_contract,
            extra_headers={"Idempotency-Key": "normal-flow-e2e-001", "traceparent": traceparent},
        )
        assert status == 202
        assert queued["status"] == "queued"
        assert queued["trace_id"] == trace_id
        run_id = queued["run_id"]

        task = task_bus.get_next_task()
        assert task is not None
        assert task["run_id"] == run_id
        assert json.loads(task["payload"]) == payload

        # Run the actual canonical CLI in a subprocess, with state redirected to tmp_path.
        candidate_result = worker_loop.process_task(task)
        assert candidate_result["status"] == "candidate"
        result_id = candidate_result["candidate_id"]
        assert candidate_result["catedralx_trace_id"] == trace_id
        assert candidate_result["decision_result"]["evaluation_hash"]
        assert len(candidate_result["decision_result"]["evaluation_hash"]) == 64
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
        decision["signature"] = _sign_decision(decision)
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

        acceptance_file = acceptance_store._acceptance_file()
        assert acceptance_file.name == "acceptance_store.jsonl"
        acceptance_record = json.loads(acceptance_file.read_text().splitlines()[-1])
        assert len(acceptance_record["signature"]) == 128
        assert acceptance_record["trace_id"] == trace_id
        trace_rows = [json.loads(line) for line in (tmp_path / "traces.jsonl").read_text().splitlines()]
        inbound_rows = [row for row in trace_rows if row["path"] == "/v1/request" and row["trace_id"] == trace_id]
        assert inbound_rows
        assert inbound_rows[0]["traceparent"] == traceparent
        assert inbound_rows[0]["status"] == 202
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
