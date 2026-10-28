"""Isolated integration test for CatedralX economic proposals -> Agents worker -> acceptance gate."""
from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import jwt

from api import acceptance_store, candidate_store, subscription_store, task_bus, worker_loop
from api.server import create_server

CATEDRALX_ROOT = Path(__file__).resolve().parents[2] / "CatedralX"


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
    from FEX.GOVERNANCE.economic_cycle import registrar_evento

    dispatch_spec = importlib.util.spec_from_file_location(
        "catedralx_dispatch_agents", CATEDRALX_ROOT / "elite" / "bin" / "dispatch_agents.py"
    )
    assert dispatch_spec and dispatch_spec.loader
    dispatch_agents = importlib.util.module_from_spec(dispatch_spec)
    dispatch_spec.loader.exec_module(dispatch_agents)

    secret = "synthetic-integration-secret-for-tests-only-2026"
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", secret)
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
        first = dispatch_agents.dispatch_task(
            "catedralx_economic_proposal_review",
            proposal,
            api_url,
            secret,
            idempotency_key=idempotency_key,
        )
        replay = dispatch_agents.dispatch_task(
            "catedralx_economic_proposal_review",
            proposal,
            api_url,
            secret,
            idempotency_key=idempotency_key,
        )
        assert first["run_id"] == replay["run_id"]
        assert first["task_id"] == replay["task_id"]
        assert replay["idempotent_replay"] is True
        assert len(task_bus.get_tasks_by_run(first["run_id"])) == 1

        task = task_bus.get_next_task()
        assert task is not None
        assert task["task_id"] == first["task_id"]
        captured: dict = {}

        class FakeMaster:
            def __init__(self):
                pass

            async def execute_task(self, objective, *, allowed_tools=None):
                captured["prompt"] = objective
                captured["allowed_tools"] = allowed_tools
                return {"summary": "Synthetic candidate; no external action taken"}

        monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "true")
        monkeypatch.setattr(worker_loop, "Master", FakeMaster)
        candidate = worker_loop.process_task(task)
        assert candidate["status"] == "candidate"
        assert candidate["analysis_only"] is True
        assert candidate["orders_created"] is False
        assert candidate["orders_executed"] is False
        assert captured["allowed_tools"] == []
        assert cycle_id in captured["prompt"]
        assert "external_contact" in captured["prompt"]

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
    from FEX.GOVERNANCE.economic_cycle import registrar_evento

    secret = "deterministic-business-flow-test-jwt-secret-2026"
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", secret)
    monkeypatch.setenv(
        "CHALAMANDRA_VERIFIER_SECRET",
        "deterministic-business-flow-test-verifier-secret-2026",
    )
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "false")
    for store in (acceptance_store, candidate_store, subscription_store, task_bus):
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
        },
        path=ledger, cycle_id=cycle_id,
    )
    registrar_evento(
        "authorize",
        {
            "approved": True,
            "approver": "isolated-test-owner",
            "rationale": "Authorize analysis only; no external actions",
        },
        path=ledger, cycle_id=cycle_id,
    )
    proposal_payload = dispatch_agents.load_authorized_proposal(ledger, cycle_id)

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
        idempotency_key = "authorized-cycle:" + cycle_id
        queued = dispatch_agents.dispatch_task(
            "catedralx_economic_proposal_review",
            proposal_payload,
            f"http://127.0.0.1:{port}",
            secret,
            idempotency_key=idempotency_key,
        )
        assert queued["status"] == "queued"

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
        decision["signature"] = acceptance_store.generate_signature(decision)
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
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
