"""Local end-to-end test: CatedralX dispatch -> Agents API -> worker -> acceptance gate."""
import base64
import json
import os
import subprocess
import sys
import threading
import time
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


def test_catedralx_to_agents_api_worker_preserves_blocked_missing_input(tmp_path, monkeypatch):
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
        from conftest import sign_market_payload
        payload = sign_market_payload({
            "historical_prices": prices,
            "current_price": prices[-1],
            "observed_at": observed_at,
            "source": "isolated_integration_test_fixture",
            "dynamic_constraints": {
                "reinvestment_surplus": 1.0,
                "entropy_rate": 0.1,
                "entropy_time_scale": 1.0,
                "loop_gain": 0.5,
                "signal_count": 2,
                "optimal_signal_count": 3,
                "exposure": 0.1,
                "max_exposure": 0.5,
                "resilience": 0.9,
                "minimum_resilience": 0.8,
            },
            "central_dimensions_bp": {
                "viability_bp": 9000,
                "human_value_bp": 9000,
                "distribution_bp": 9000,
                "resilience_bp": 9000,
                "evidence_bp": 9000,
            },
        })

        # Normal inbound product path: /v1/request -> TaskBus -> real CatedralX CLI.
        trace_id = "1234567890abcdef1234567890abcdef"
        traceparent = f"00-{trace_id}-1234567890abcdef-01"
        request_contract = {
            "objective": "catedralx_economic_evaluation",
            "payload": payload,
            "permissions": {"allowed_tools": [], "side_effects": "none"},
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

        # Execute the canonical CLI with a complete, synthetic governance contract.
        from unittest.mock import patch

        with patch("api.server.webhook_dispatcher.dispatch_event") as dispatch_webhook:
            candidate_result = worker_loop.process_task(task)

        assert candidate_result["status"] in {"candidate", "blocked"}
        result_id = candidate_result["candidate_id"]
        decision_result = candidate_result["decision_result"]
        assert result_id == decision_result["result_id"]
        assert decision_result["status"] in {"ACCEPTED", "UNCERTAIN", "REJECTED", "BLOCKED_MISSING_INPUT"}
        assert decision_result["evaluation_hash"]
        assert len(decision_result["evaluation_hash"]) == 64
        assert decision_result["trace_id"] == trace_id
        assert candidate_result["evidence"][0]["observed_at"] == observed_at
        assert candidate_result["evidence"][0]["market_source"] == payload["source"]
        assert dispatch_webhook.call_count == 0

        saved = candidate_store.get_candidate(result_id)
        assert saved is not None
        assert saved["status"] == candidate_result["status"]
        assert saved["decision_result"]["result_id"] == result_id
        assert acceptance_store.get_acceptance(result_id) is None

        status, job = _http_json(
            f"{base_url}/v1/jobs/{task['task_id']}",
            token=_make_jwt(jwt_secret, "jobs:read"),
        )
        assert status == 200
        assert job["status"] == candidate_result["status"]
        assert job["result_id"] == result_id

        read_token = _make_jwt(jwt_secret, "orchestration:read results:read")
        status, run = _http_json(
            f"{base_url}/internal/v1/orchestration/runs/{run_id}",
            token=read_token,
        )
        assert status == 200
        assert run["status"] in {"awaiting_acceptance", "completed_with_errors"}
        assert run["task_statuses"] == [candidate_result["status"]]

        trace_rows = [
            json.loads(line)
            for line in (tmp_path / "traces.jsonl").read_text().splitlines()
        ]
        inbound_rows = [
            row for row in trace_rows
            if row["path"] == "/v1/request" and row["trace_id"] == trace_id
        ]
        assert inbound_rows
        assert inbound_rows[0]["traceparent"] == traceparent
        assert inbound_rows[0]["status"] == 202
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)



@pytest.mark.parametrize("outcome", ["accepted", "rejected"])
def test_catedralx_agents_verification_and_distribution_full_lifecycle(
    outcome, tmp_path, monkeypatch
):
    """Exercise request -> CatedralX -> candidate -> independent verifier -> delivery.

    All state is isolated under tmp_path. The only outbound delivery is to a local
    HTTP receiver explicitly allowlisted for this test; model calls remain disabled.
    """
    pytest.importorskip("numpy")
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from api import (
        acceptance_store,
        candidate_store,
        distribution_store,
        subscription_store,
        task_bus,
        webhook_dispatcher,
    )
    from api.server import create_server

    jwt_secret = "integration-test-jwt-secret-32-bytes-minimum"
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", jwt_secret)
    monkeypatch.setenv("CHALAMANDRA_CATEDRALX_JWT_SECRET", jwt_secret)
    monkeypatch.setenv(
        "CHALAMANDRA_VERIFIER_PUBLIC_KEY", TEST_VERIFIER_KEY.verify_key.encode().hex()
    )
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_ID", "isolated-test-verifier")
    monkeypatch.setenv(
        "CHALAMANDRA_WEBHOOK_SECRET", "integration-test-webhook-secret-32-bytes-minimum"
    )
    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_PRIVATE_HOST_ALLOWLIST", "127.0.0.1")
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "false")
    monkeypatch.setenv("CATEDRALX_PATH", str(CATEDRALX_ROOT))
    monkeypatch.setenv("CATEDRALX_STATE_PATH", str(tmp_path / "catedralx" / "hormonas.json"))
    monkeypatch.setenv("CATEDRALX_FEEDBACK_PATH", str(tmp_path / "catedralx" / "feedback.jsonl"))
    monkeypatch.setenv("CATEDRALX_RESULT_STORE_PATH", str(tmp_path / "catedralx" / "results.jsonl"))
    monkeypatch.setenv("CHALAMANDRA_TRACE_LOG_PATH", str(tmp_path / "agents-state" / "traces.jsonl"))
    for secret_name in (
        "CHALAMANDRA_ED25519_SEED",
        "CHALAMANDRA_VERIFIER_SECRET",
        "CHALAMANDRA_HMAC_SECRET",
    ):
        monkeypatch.delenv(secret_name, raising=False)

    # The API test process and the real worker subprocess must resolve the
    # same persistent state directory, just as the two Compose services do
    # through agents_runtime_state.
    worker_home = tmp_path / "worker-home"
    worker_home.mkdir()
    monkeypatch.setenv("HOME", str(worker_home))
    state_dir = worker_home / ".local" / "state" / "chalamandra" / "agents"
    for module in (
        task_bus,
        candidate_store,
        acceptance_store,
        subscription_store,
        distribution_store,
        webhook_dispatcher,
    ):
        monkeypatch.setattr(module, "_state_dir", lambda state_dir=state_dir: state_dir)

    received = []

    class LocalReceiver(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            received.append({"payload": payload, "headers": dict(self.headers.items())})
            self.send_response(204)
            self.end_headers()

        def log_message(self, _format, *_args):
            return

    receiver = ThreadingHTTPServer(("127.0.0.1", 0), LocalReceiver)
    receiver_thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    receiver_thread.start()
    receiver_url = f"http://127.0.0.1:{receiver.server_address[1]}/events"
    subscription = subscription_store.create_subscription(
        receiver_url, ["result.accepted"]
    )
    assert subscription["status"] == "enabled"

    api = create_server("127.0.0.1", 0)
    api_thread = threading.Thread(target=api.serve_forever, daemon=True)
    api_thread.start()
    base_url = f"http://127.0.0.1:{api.server_address[1]}"

    worker_process = None
    trace_id = "1234567890abcdef1234567890abcdef"
    traceparent = f"00-{trace_id}-1234567890abcdef-01"
    try:
        observed_at = datetime.now(timezone.utc).isoformat()
        prices = [100.0 + index * 0.01 for index in range(59)]
        from conftest import sign_market_payload

        payload = sign_market_payload({
            "historical_prices": prices,
            "current_price": prices[-1],
            "observed_at": observed_at,
            "source": "isolated_full_lifecycle_fixture",
            "dynamic_constraints": {
                "reinvestment_surplus": 1.0,
                "entropy_rate": 0.1,
                "entropy_time_scale": 1.0,
                "loop_gain": 0.5,
                "signal_count": 2,
                "optimal_signal_count": 3,
                "exposure": 0.1,
                "max_exposure": 0.5,
                "resilience": 0.9,
                "minimum_resilience": 0.8,
            },
            "central_dimensions_bp": {
                "viability_bp": 9000,
                "human_value_bp": 9000,
                "distribution_bp": 9000,
                "resilience_bp": 9000,
                "evidence_bp": 9000,
            },
        })
        request_contract = {
            "objective": "catedralx_economic_evaluation",
            "payload": payload,
            "permissions": {"allowed_tools": [], "side_effects": "none"},
            "success_criteria": [
                "Produce a traced, analysis-only CatedralX DecisionResult."
            ],
        }
        status, queued = _http_json(
            f"{base_url}/v1/request",
            method="POST",
            token=_make_jwt(jwt_secret, "jobs:write"),
            payload=request_contract,
            extra_headers={
                "Idempotency-Key": f"full-lifecycle-{outcome}-001",
                "traceparent": traceparent,
            },
        )
        assert status == 202
        assert queued["trace_id"] == trace_id

        # Run the production worker entrypoint as a separate process. It
        # must claim the API's SQLite task through the shared state directory.
        repo_root = Path(__file__).resolve().parents[1]
        worker_env = os.environ.copy()
        worker_env["PYTHONPATH"] = str(repo_root) + os.pathsep + worker_env.get("PYTHONPATH", "")
        worker_process = subprocess.Popen(
            [sys.executable, "-m", "api.worker_loop"],
            cwd=repo_root,
            env=worker_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        deadline = time.monotonic() + 60
        task = None
        while time.monotonic() < deadline:
            task = task_bus.get_task_status(queued["task_id"])
            if task and task["status"] in {"candidate", "blocked", "failed"}:
                break
            if worker_process.poll() is not None:
                worker_output, _ = worker_process.communicate(timeout=2)
                raise AssertionError(
                    "Persistent worker exited before completing the task: "
                    + worker_output[-4000:]
                )
            time.sleep(0.1)

        assert task is not None, "Persistent worker did not make the task visible"
        assert task["run_id"] == queued["run_id"]
        assert task["status"] == "candidate", task.get("candidate_result")
        assert worker_process.poll() is None, "Persistent worker exited after task completion"
        candidate_result = json.loads(task["candidate_result"])
        assert candidate_result["status"] == "candidate"
        assert candidate_result["engine"] == "CatedralX"
        assert candidate_result["decision_result"]["status"] == "ACCEPTED"
        assert candidate_result["trace_id"] == trace_id
        assert candidate_result["decision_result"]["trace_id"] == trace_id

        candidate_id = candidate_result["candidate_id"]
        persisted = candidate_store.get_candidate(candidate_id)
        assert persisted is not None
        assert persisted["evidence"]
        assert persisted["decision_result"]["evaluation_hash"]
        assert acceptance_store.get_acceptance(candidate_id) is None

        # The result is not readable as delivered before independent acceptance.
        status, pending = _http_json(
            f"{base_url}/v1/results/{candidate_id}",
            token=_make_jwt(jwt_secret, "results:read"),
        )
        assert status == 202
        assert pending["status"] == "pending_verification"
        assert not distribution_store._events_file().exists()
        assert received == []

        decision = {
            "decision_id": f"full-lifecycle-{outcome}-{candidate_id}",
            "candidate_id": candidate_id,
            "outcome": outcome,
            "criteria_checked": [
                "canonical_decision_result",
                "evidence_digest",
                "trace_ownership",
                "traceability",
            ],
            "reasons": [] if outcome == "accepted" else ["negative-path integration fixture"],
            "missing_evidence": [],
            "verifier_id": "isolated-test-verifier",
            "evidence_digest": candidate_store.compute_evidence_digest(persisted),
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace_id,
        }
        decision["signature"] = _sign_decision(decision)
        status, acceptance = _http_json(
            f"{base_url}/v1/acceptance",
            method="POST",
            token=_make_jwt(jwt_secret, "acceptance:write"),
            payload=decision,
        )
        assert status == 201
        assert acceptance["outcome"] == outcome

        task_status = task_bus.get_task_status(task["task_id"])
        assert task_status["status"] == ("accepted" if outcome == "accepted" else "rejected")
        stored_acceptance = acceptance_store.get_acceptance(candidate_id)
        assert stored_acceptance is not None
        assert stored_acceptance["outcome"] == outcome
        assert stored_acceptance["trace_id"] == trace_id

        if outcome == "accepted":
            assert len(received) == 1
            event = received[0]["payload"]
            headers = received[0]["headers"]
            assert event["event_type"] == "result.accepted"
            assert event["result_id"] == candidate_id
            assert event["trace_id"] == trace_id
            assert headers["X-Trace-Id"] == trace_id
            assert len(headers["X-Chalamandra-Signature"]) == 64
            child_parent = headers["traceparent"].split("-")
            assert child_parent[1] == trace_id
            dispatch_records = distribution_store._read_all(
                distribution_store._events_file()
            )
            assert dispatch_records[-1]["status"] == "dispatched"
            assert dispatch_records[-1]["delivered_count"] == 1

            status, delivered = _http_json(
                f"{base_url}/v1/results/{candidate_id}",
                token=_make_jwt(jwt_secret, "results:read"),
            )
            assert status == 200
            assert delivered["delivery_status"] == "accepted"
            assert delivered["acceptance"]["outcome"] == "accepted"

            status, run = _http_json(
                f"{base_url}/internal/v1/orchestration/runs/{queued['run_id']}",
                token=_make_jwt(jwt_secret, "orchestration:read"),
            )
            assert status == 200
            assert run["status"] == "accepted"
            assert run["trace_id"] == trace_id
        else:
            assert received == []
            status, blocked = _http_json(
                f"{base_url}/v1/results/{candidate_id}",
                token=_make_jwt(jwt_secret, "results:read"),
            )
            assert status == 403
            assert blocked["error"] == "result_not_accepted"
            assert not distribution_store._events_file().exists()
    finally:
        if worker_process is not None and worker_process.poll() is None:
            worker_process.terminate()
            try:
                worker_process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                worker_process.kill()
                worker_process.communicate(timeout=5)
        api.shutdown()
        api.server_close()
        api_thread.join(timeout=2)
        receiver.shutdown()
        receiver.server_close()
        receiver_thread.join(timeout=2)
