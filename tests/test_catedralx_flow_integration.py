"""Isolated integration test for CatedralX economic proposals -> Agents worker -> acceptance gate."""
from __future__ import annotations

import hmac
import http.client
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt
import pytest
from nacl.signing import SigningKey

from api import (
    acceptance_store,
    candidate_store,
    distribution_store,
    subscription_store,
    task_bus,
    webhook_dispatcher,
    worker_loop,
)
from api.server import create_server

_default_catedralx_root = Path(__file__).resolve().parents[2] / "CatedralX"
CATEDRALX_ROOT = Path(os.environ.get("CATEDRALX_PATH", _default_catedralx_root)).resolve()
if not (CATEDRALX_ROOT / "elite" / "FEX" / "GOVERNANCE" / "economic_cycle.py").is_file() or not (
    CATEDRALX_ROOT / "elite" / "bin" / "dispatch_agents.py"
).is_file():
    import pytest

    pytest.skip(
        "Canonical CatedralX integration source is not available; set CATEDRALX_PATH to run cross-repo tests.",
        allow_module_level=True,
    )


TEST_VERIFIER_SEED_HEX = "33" * 32
TEST_VERIFIER_KEY = SigningKey(bytes.fromhex(TEST_VERIFIER_SEED_HEX))
VERIFIER_CLI = Path(__file__).resolve().parents[1] / "verifier" / "independent_acceptance_verifier.py"


def _sign_decision(decision: dict) -> str:
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


def _inject_test_approver_identity_verifier(monkeypatch) -> None:
    sys.path.insert(0, str(CATEDRALX_ROOT / "elite"))
    from AEX.DECISION import ordenes

    monkeypatch.setattr(
        ordenes,
        "APPROVER_IDENTITY_VERIFIER",
        lambda assertion, context, amount: {
            "verified": isinstance(assertion, dict) and assertion.get("signed") == "test-only-assertion",
            "subject": assertion.get("subject") if isinstance(assertion, dict) else None,
            "permissions": ["economic_cycle_authorize"],
        },
    )


def _request(port: int, path: str, token: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", path, headers={"Authorization": f"Bearer {token}"})
    response = connection.getresponse()
    status = response.status
    payload = json.loads(response.read().decode("utf-8"))
    connection.close()
    return status, payload


def test_catedralx_authorized_proposal_flows_through_agents_to_acceptance_gate(
    monkeypatch, tmp_path
):
    sys.path.insert(0, str(CATEDRALX_ROOT / "elite"))
    _inject_test_approver_identity_verifier(monkeypatch)
    from FEX.GOVERNANCE.economic_cycle import registrar_evento

    dispatch_spec = importlib.util.spec_from_file_location(
        "catedralx_dispatch_agents", CATEDRALX_ROOT / "elite" / "bin" / "dispatch_agents.py"
    )
    assert dispatch_spec and dispatch_spec.loader
    dispatch_agents = importlib.util.module_from_spec(dispatch_spec)
    dispatch_spec.loader.exec_module(dispatch_agents)

    monkeypatch.setenv("CATEDRALX_PATH", str(CATEDRALX_ROOT))
    secret = "synthetic-integration-general-jwt-secret-2026"
    service_secret = "synthetic-catedralx-service-jwt-secret-2026"
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", secret)
    monkeypatch.setenv("CHALAMANDRA_CATEDRALX_JWT_SECRET", service_secret)
    monkeypatch.setenv(
        "CHALAMANDRA_VERIFIER_PUBLIC_KEY", TEST_VERIFIER_KEY.verify_key.encode().hex()
    )
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_ID", "isolated-test-verifier")
    monkeypatch.delenv("CHALAMANDRA_ED25519_SEED", raising=False)
    monkeypatch.delenv("CHALAMANDRA_VERIFIER_SECRET", raising=False)
    monkeypatch.delenv("CHALAMANDRA_HMAC_SECRET", raising=False)
    monkeypatch.setenv("CHALAMANDRA_API_TOKEN", "synthetic-api-token-for-tests-only")
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "false")
    monkeypatch.setattr(task_bus, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(candidate_store, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(acceptance_store, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(subscription_store, "_state_dir", lambda: tmp_path)

    ledger = tmp_path / "impact_cycle.jsonl"
    cycle_id = registrar_evento(
        "measure",
        {
            "metric": "qualified_leads",
            "source": "synthetic_fixture",
            "period": "2026-10",
            "unit": "leads",
            "formula": "count_unique",
            "value": 12,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        },
        path=ledger,
    )
    registrar_evento(
        "evaluate",
        {
            "assessment": "Synthetic input requires research",
            "evidence": "synthetic_fixture; no live business data used",
        },
        path=ledger,
        cycle_id=cycle_id,
    )
    registrar_evento(
        "propose",
        {
            "proposal": "Analyze demand evidence for a product page",
            "expected_outcome": "Create a reviewable, source-backed improvement candidate",
            "verification_plan": "Compare qualified lead counts before and after separately approved changes",
            "source": "synthetic_test_fixture",
            "method": "fixture_based_scenario",
            "evidence_ref": "test-fixture://catedralx-flow-integration/product-page",
        },
        path=ledger,
        cycle_id=cycle_id,
    )
    registrar_evento(
        "authorize",
        {
            "approved": True,
            "approver": "integration-test-owner",
            "rationale": "Test only; no external effects allowed",
            "identity_assertion": {"signed": "test-only-assertion", "subject": "integration-test-owner"},
        },
        path=ledger,
        cycle_id=cycle_id,
    )

    proposal = dispatch_agents.load_authorized_proposal(ledger, cycle_id)
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        api_url = f"http://127.0.0.1:{server.server_address[1]}"
        idempotency_key = "catedralx-economic-cycle:" + cycle_id
        # The general JWT key must not authenticate CatedralX service dispatch.
        with pytest.raises(RuntimeError, match="HTTP 401"):
            dispatch_agents.dispatch_task(
                "catedralx_economic_proposal_review",
                proposal,
                api_url,
                secret,
                idempotency_key=idempotency_key,
            )
        first = dispatch_agents.dispatch_task(
            "catedralx_economic_proposal_review",
            proposal,
            api_url,
            service_secret,
            idempotency_key=idempotency_key,
        )
        replay = dispatch_agents.dispatch_task(
            "catedralx_economic_proposal_review",
            proposal,
            api_url,
            service_secret,
            idempotency_key=idempotency_key,
        )
        assert first["run_id"] == replay["run_id"]
        assert first["task_id"] == replay["task_id"]
        assert replay["idempotent_replay"] is True
        assert len(task_bus.get_tasks_by_run(first["run_id"])) == 1

        task = task_bus.get_next_task()
        assert task is not None
        assert task["task_id"] == first["task_id"]
        class ForbiddenMaster:
            def __init__(self):
                raise AssertionError("proposal review is deterministic and must not call Master")

        monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "true")
        monkeypatch.setattr(worker_loop, "Master", ForbiddenMaster)
        candidate = worker_loop.process_task(task)
        assert candidate["status"] == "candidate", candidate["summary"]
        assert candidate["analysis_only"] is True
        assert candidate["orders_created"] is False
        assert candidate["orders_executed"] is False
        assert candidate["business_cycle_id"] == cycle_id
        assert candidate["business_proposal"]["authorization"]["approved"] is True
        assert candidate["execution_boundary"]["independent_acceptance_required"] is True
        assert candidate["catedralx_trace_id"] == candidate["trace_id"]

        read_token = jwt.encode(
            {"scope": "orchestration:read results:read", "sub": "independent-verifier", "exp": int(time.time()) + 60},
            secret,
            algorithm="HS256",
        )
        status, run = _request(
            server.server_address[1],
            f"/internal/v1/orchestration/runs/{first['run_id']}",
            read_token,
        )
        assert status == 200
        assert run["status"] == "awaiting_acceptance"

        status, result_view = _request(
            server.server_address[1],
            f"/v1/results/{candidate['candidate_id']}",
            read_token,
        )
        assert status == 202
        assert result_view["status"] == "pending_verification"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)



def test_approved_business_cycle_runs_with_models_off_and_returns_after_acceptance(
    monkeypatch, tmp_path
):
    import uuid
    from datetime import datetime, timezone
    from http.client import HTTPConnection
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from api import acceptance_store, candidate_store, subscription_store, task_bus

    sys.path.insert(0, str(CATEDRALX_ROOT / "elite"))
    _inject_test_approver_identity_verifier(monkeypatch)
    from FEX.GOVERNANCE.economic_cycle import registrar_evento

    secret = "deterministic-business-flow-test-jwt-secret-2026"
    service_secret = "deterministic-business-catedralx-service-secret-2026"
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", secret)
    monkeypatch.setenv("CHALAMANDRA_CATEDRALX_JWT_SECRET", service_secret)
    monkeypatch.setenv(
        "CHALAMANDRA_VERIFIER_PUBLIC_KEY", TEST_VERIFIER_KEY.verify_key.encode().hex()
    )
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_ID", "isolated-business-test-verifier")
    monkeypatch.delenv("CHALAMANDRA_ED25519_SEED", raising=False)
    monkeypatch.delenv("CHALAMANDRA_VERIFIER_SECRET", raising=False)
    monkeypatch.delenv("CHALAMANDRA_HMAC_SECRET", raising=False)
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "false")
    monkeypatch.setenv("CATEDRALX_PATH", str(CATEDRALX_ROOT))
    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_SECRET", "isolated-webhook-secret-at-least-32-bytes")
    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_PRIVATE_HOST_ALLOWLIST", "127.0.0.1")
    for store in (
        acceptance_store, candidate_store, distribution_store, subscription_store, task_bus
    ):
        monkeypatch.setattr(store, "_state_dir", lambda: tmp_path)

    dispatch_spec = importlib.util.spec_from_file_location(
        "catedralx_dispatch_business_e2e",
        CATEDRALX_ROOT / "elite" / "bin" / "dispatch_agents.py",
    )
    assert dispatch_spec and dispatch_spec.loader
    dispatch_agents = importlib.util.module_from_spec(dispatch_spec)
    dispatch_spec.loader.exec_module(dispatch_agents)

    ledger = tmp_path / "impact_cycle.jsonl"
    cycle_id = registrar_evento(
        "measure",
        {
            "metric": "qualified_leads",
            "source": "synthetic_fixture",
            "period": "2026-10",
            "unit": "leads",
            "formula": "count_unique",
            "value": 12,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        },
        path=ledger,
    )
    registrar_evento(
        "evaluate",
        {"assessment": "Insufficient evidence for an external campaign",
         "evidence": "Synthetic fixture; no production business data used"},
        path=ledger, cycle_id=cycle_id,
    )
    registrar_evento(
        "propose",
        {
            "proposal": "Analyze demand evidence for a product page",
            "expected_outcome": "Create a traceable candidate for independent review",
            "verification_plan": "Compare qualified lead counts across approved periods",
            "source": "synthetic_test_fixture",
            "method": "fixture_based_scenario",
            "evidence_ref": "test-fixture://catedralx-flow-integration/approved-cycle",
        },
        path=ledger, cycle_id=cycle_id,
    )
    registrar_evento(
        "authorize",
        {
            "approved": True,
            "approver": "isolated-test-owner",
            "rationale": "Authorize analysis only; no external actions",
            "identity_assertion": {"signed": "test-only-assertion", "subject": "isolated-test-owner"},
        },
        path=ledger, cycle_id=cycle_id,
    )
    scanner_spec = importlib.util.spec_from_file_location(
        "catedralx_authorized_cycle_scanner",
        CATEDRALX_ROOT / "elite" / "bin" / "dispatch_authorized_cycles.py",
    )
    assert scanner_spec and scanner_spec.loader
    cycle_scanner = importlib.util.module_from_spec(scanner_spec)
    scanner_spec.loader.exec_module(cycle_scanner)

    webhook_receipts: list[dict] = []

    class LocalWebhookReceiver(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            webhook_receipts.append({
                "payload": payload,
                "signature": self.headers.get("X-Chalamandra-Signature"),
                "trace_id": self.headers.get("X-Trace-Id"),
                "traceparent": self.headers.get("traceparent"),
            })
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            return

    receiver = ThreadingHTTPServer(("127.0.0.1", 0), LocalWebhookReceiver)
    receiver_thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    receiver_thread.start()
    subscription = subscription_store.create_subscription(
        f"http://127.0.0.1:{receiver.server_address[1]}/synthetic-webhook",
        ["result.accepted"],
    )

    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def get_json(path, jwt_token):
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", path, headers={"Authorization": f"Bearer {jwt_token}"})
        response = connection.getresponse()
        status = response.status
        raw = response.read().decode("utf-8")
        connection.close()
        return status, json.loads(raw) if raw else {}

    def post_json(path, jwt_token, payload):
        request = Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=5) as response:
                raw = response.read().decode("utf-8")
                return response.status, json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            return exc.code, json.loads(raw) if raw else {}

    try:
        dispatch_log = tmp_path / "dispatches.jsonl"
        scanned = cycle_scanner.scan_and_dispatch(
            ledger=ledger,
            dispatch_log=dispatch_log,
            api_url=f"http://127.0.0.1:{port}",
            secret=service_secret,
        )
        assert scanned["eligible"] == 1
        assert len(scanned["dispatched"]) == 1
        queued = {
            "status": scanned["dispatched"][0]["api_status"],
            "run_id": scanned["dispatched"][0]["run_id"],
            "task_id": scanned["dispatched"][0]["task_id"],
            "trace_id": scanned["dispatched"][0]["trace_id"],
        }
        assert queued["status"] == "queued"
        repeated = cycle_scanner.scan_and_dispatch(
            ledger=ledger,
            dispatch_log=dispatch_log,
            api_url=f"http://127.0.0.1:{port}",
            secret=service_secret,
        )
        assert repeated["dispatched"] == []
        assert repeated["already_dispatched"] == 1

        task = task_bus.get_next_task()
        assert task is not None
        candidate = worker_loop.process_task(task)
        assert candidate["status"] == "candidate"
        assert candidate["business_cycle_id"] == cycle_id
        assert candidate["result_id"] == candidate["candidate_id"]
        assert candidate["orders_created"] is False
        assert candidate["orders_executed"] is False

        read_jwt = jwt.encode(
            {
                "scope": "jobs:read orchestration:read results:read",
                "exp": int(time.time()) + 120,
                "sub": "isolated-test-reader",
            },
            secret,
            algorithm="HS256",
        )
        status, job = get_json(f"/v1/jobs/{task['task_id']}", read_jwt)
        assert status == 200
        assert job["result_id"] == candidate["candidate_id"]

        status, run = get_json(
            f"/internal/v1/orchestration/runs/{queued['run_id']}", read_jwt
        )
        assert status == 200
        assert run["status"] == "awaiting_acceptance"

        status, pending = get_json(
            f"/v1/results/{candidate['candidate_id']}", read_jwt
        )
        assert status == 202
        assert pending["status"] == "pending_verification"

        decision = {
            "decision_id": str(uuid.uuid4()),
            "candidate_id": candidate["candidate_id"],
            "outcome": "accepted",
            "criteria_checked": [
                "human authorization",
                "restricted execution boundary",
                "evidence digest",
                "trace continuity",
            ],
            "reasons": [],
            "missing_evidence": [],
            "verifier_id": "isolated-business-test-verifier",
            "evidence_digest": candidate_store.compute_evidence_digest(
                candidate_store.get_candidate(candidate["candidate_id"])
            ),
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "trace_id": candidate["trace_id"],
        }
        decision["signature"] = _sign_decision(decision)
        write_jwt = jwt.encode(
            {
                "scope": "acceptance:write",
                "exp": int(time.time()) + 120,
                "sub": "isolated-test-verifier",
            },
            secret,
            algorithm="HS256",
        )
        status, accepted = post_json("/v1/acceptance", write_jwt, decision)
        assert status == 201
        assert accepted["outcome"] == "accepted"

        # Exercise the real pinned-IP webhook transport against a loopback-only
        # receiver. The test allowlist is exact and never opens public egress.
        assert len(webhook_receipts) == 1
        receipt = webhook_receipts[0]
        assert receipt["payload"]["result_id"] == candidate["candidate_id"]
        assert receipt["payload"]["event_type"] == "result.accepted"
        assert receipt["payload"]["trace_id"] == candidate["trace_id"]
        assert receipt["trace_id"] == candidate["trace_id"]
        assert isinstance(receipt["traceparent"], str)
        assert receipt["traceparent"].startswith(f"00-{candidate['trace_id']}-")
        assert hmac.compare_digest(
            receipt["signature"], webhook_dispatcher._sign_payload(receipt["payload"])
        )

        # The internal distribution route is idempotent after the accepted
        # event was delivered; it must not notify the subscriber twice.
        publish_jwt = jwt.encode(
            {
                "scope": "results:publish",
                "exp": int(time.time()) + 120,
                "sub": "catedralx-service",
            },
            service_secret,
            algorithm="HS256",
        )
        status, replay_delivery = post_json(
            "/internal/v1/distributions",
            publish_jwt,
            {"result_id": candidate["candidate_id"], "event_type": "result.accepted"},
        )
        assert status == 202
        assert replay_delivery["status"] == "dispatched"
        assert replay_delivery["delivered_count"] == 1
        assert len(webhook_receipts) == 1

        event_rows = distribution_store._read_all(distribution_store._events_file())
        delivery_rows = distribution_store._read_all(distribution_store._deliveries_file())
        assert event_rows[-1]["status"] == "dispatched"
        assert event_rows[-1]["delivered_count"] == 1
        assert delivery_rows[-1]["status"] == "delivered"
        assert delivery_rows[-1]["subscription_id"] == subscription["subscription_id"]

        job_jwt = jwt.encode(
            {
                "scope": "jobs:read",
                "exp": int(time.time()) + 120,
                "sub": "isolated-test-reader",
            },
            secret,
            algorithm="HS256",
        )
        status, final_job = get_json(f"/v1/jobs/{task['task_id']}", job_jwt)
        assert status == 200
        assert final_job["status"] == "accepted"
        status, final_run = get_json(
            f"/internal/v1/orchestration/runs/{queued['run_id']}", read_jwt
        )
        assert status == 200
        assert final_run["status"] == "accepted"
        assert final_run["task_statuses"] == ["accepted"]

        status, delivered = get_json(
            f"/v1/results/{candidate['candidate_id']}", read_jwt
        )
        assert status == 200
        assert delivered["delivery_status"] == "accepted"
        assert delivered["acceptance"]["decision_id"] == decision["decision_id"]
        assert delivered["business_cycle_id"] == cycle_id
        assert delivered["business_proposal"]["authorization"]["approved"] is True
        assert delivered["execution_boundary"]["independent_acceptance_required"] is True
        assert delivered["execution_boundary"]["prohibited"]
        assert delivered["orders_created"] is False
        assert delivered["orders_executed"] is False

        from api.catedralx_feedback import reconcile_feedback_once
        feedback = reconcile_feedback_once(ledger_path=ledger)
        assert feedback["processed_cycles"] == [cycle_id]
        assert feedback["errors"] == []
        from FEX.GOVERNANCE.economic_cycle import resumir_ciclos
        completed_cycle = next(
            row for row in resumir_ciclos(ledger)["cycles"] if row["cycle_id"] == cycle_id
        )
        assert completed_cycle["events"] == ["measure", "evaluate", "propose", "authorize", "verify", "feedback"]
        assert completed_cycle["complete"] is True

        acceptance_file = acceptance_store._acceptance_file()
        assert acceptance_file.name == "acceptance_store.jsonl"
        acceptance_record = json.loads(acceptance_file.read_text().splitlines()[-1])
        assert len(acceptance_record["signature"]) == 128
        assert acceptance_record["trace_id"] == candidate["trace_id"]

        trace_file = tmp_path / "traces.jsonl"
        trace_rows = [json.loads(line) for line in trace_file.read_text().splitlines()]
        dispatched_trace = [
            row for row in trace_rows
            if row["path"] == "/internal/v1/orchestration/runs"
            and row["status"] == 202 and row["trace_id"] == queued["trace_id"]
        ]
        assert dispatched_trace
        assert dispatched_trace[0]["traceparent"].startswith("00-")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        receiver.shutdown()
        receiver.server_close()
        receiver_thread.join(timeout=2)
