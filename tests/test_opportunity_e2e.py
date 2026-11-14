"""HTTP-to-worker end-to-end coverage for the CatedralX opportunity contract.

Uses the canonical CatedralX checkout supplied by CI, synthetic opportunity data,
isolated stores, loopback HTTP only, and a signed independent rejection decision.
No model calls, external tools, financial orders, or production services are used.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from nacl.signing import SigningKey

from api import acceptance_store, candidate_store, task_bus
from api.server import create_server
from api.worker_loop import process_task


TEST_VERIFIER_SEED_HEX = "44" * 32
TEST_VERIFIER_KEY = SigningKey(bytes.fromhex(TEST_VERIFIER_SEED_HEX))
TRACE_ID = "abcdef0123456789abcdef0123456789"


def _opportunity_payload() -> dict:
    return {
        "opportunity_id": "e2e-synthetic-opportunity",
        "name": "Servicio digital de prueba",
        "category": "digital_service",
        "income_model": "hybrid",
        "currency": "MXN",
        "horizon_months": 12,
        "capital_required": 5000,
        "available_capital": 3000,
        "revenue": [
            {"amount": 2000, "frequency": "monthly", "evidence_id": "e2e-ev-1"}
        ],
        "costs": [
            {"amount": 500, "frequency": "monthly", "evidence_id": "e2e-ev-1"}
        ],
        "evidence": [{
            "evidence_id": "e2e-ev-1",
            "source": "synthetic_e2e_fixture",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "digest": hashlib.sha256(b"synthetic-e2e-evidence").hexdigest(),
        }],
        "assumptions": ["La demanda mensual permanece estable durante el horizonte."],
        "scenarios": [
            {"name": "conservative", "revenue_multiplier": 0.7, "cost_multiplier": 1.2},
            {"name": "base", "revenue_multiplier": 1.0, "cost_multiplier": 1.0},
            {"name": "optimistic", "revenue_multiplier": 1.2, "cost_multiplier": 0.9},
        ],
        "trace_id": TRACE_ID,
    }


def _http_json(port: int, method: str, path: str, token: str, payload=None, headers=None):
    request_headers = {"Authorization": f"Bearer {token}"}
    request_headers.update(headers or {})
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request(method, path, body=body, headers={
        **request_headers,
        **({"Content-Type": "application/json"} if body is not None else {}),
    })
    response = connection.getresponse()
    status = response.status
    raw = response.read().decode("utf-8")
    connection.close()
    return status, json.loads(raw) if raw else {}


def _signed_rejection(candidate: dict) -> dict:
    decision = {
        "decision_id": str(uuid.uuid4()),
        "candidate_id": candidate["candidate_id"],
        "outcome": "rejected",
        "criteria_checked": [
            "synthetic end-to-end trace continuity",
            "evidence digest integrity",
            "analysis-only execution boundary",
        ],
        "reasons": ["Synthetic test verifies the independent rejection gate."],
        "missing_evidence": [],
        "verifier_id": "isolated-opportunity-e2e-verifier",
        "evidence_digest": candidate_store.compute_evidence_digest(candidate),
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "trace_id": candidate["trace_id"],
    }
    verifier_cli = (
        Path(__file__).resolve().parents[1]
        / "verifier"
        / "independent_acceptance_verifier.py"
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "CHALAMANDRA_ED25519_SEED": TEST_VERIFIER_SEED_HEX,
        "CHALAMANDRA_VERIFIER_ID": decision["verifier_id"],
    }
    confirmation = f"REJECT:{decision['candidate_id']}"
    signed = subprocess.run(
        [sys.executable, str(verifier_cli), "--confirm", confirmation],
        input=json.dumps(decision),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
        check=False,
    )
    assert signed.returncode == 0, signed.stderr
    decision["signature"] = json.loads(signed.stdout)["signature"]
    return decision


def test_opportunity_http_worker_catedralx_and_independent_acceptance(monkeypatch):
    catedralx_path = os.getenv("CATEDRALX_PATH")
    if not catedralx_path or not (Path(catedralx_path) / "BIN/catedral").is_file():
        pytest.skip("Canonical CatedralX checkout is supplied by the integration CI job.")

    monkeypatch.setenv("CATEDRALX_PATH", catedralx_path)
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "false")
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_ID", "isolated-opportunity-e2e-verifier")
    monkeypatch.setenv(
        "CHALAMANDRA_VERIFIER_PUBLIC_KEY",
        TEST_VERIFIER_KEY.verify_key.encode().hex(),
    )
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", "synthetic-opportunity-e2e-jwt-secret")
    monkeypatch.setenv(
        "CHALAMANDRA_CATEDRALX_JWT_SECRET",
        "synthetic-opportunity-e2e-service-jwt-secret",
    )
    monkeypatch.setenv("CHALAMANDRA_API_TOKEN", "synthetic-opportunity-e2e-api-token")
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_ID", "isolated-opportunity-e2e-verifier")

    service_secret = os.environ["CHALAMANDRA_CATEDRALX_JWT_SECRET"]
    service_jwt = jwt.encode(
        {
            "scope": "orchestration:run",
            "sub": "catedralx-service",
            "exp": int(time.time()) + 180,
        },
        service_secret,
        algorithm="HS256",
    )
    read_jwt = jwt.encode(
        {
            "scope": "orchestration:read results:read jobs:read",
            "sub": "isolated-e2e-reader",
            "exp": int(time.time()) + 180,
        },
        os.environ["CHALAMANDRA_JWT_SECRET"],
        algorithm="HS256",
    )
    acceptance_jwt = jwt.encode(
        {
            "scope": "acceptance:write",
            "sub": "isolated-opportunity-e2e-verifier",
            "exp": int(time.time()) + 180,
        },
        os.environ["CHALAMANDRA_JWT_SECRET"],
        algorithm="HS256",
    )

    # The route only needs the registered-tool inventory; the worker's typed
    # opportunity path must never instantiate or call Master.
    master = SimpleNamespace(tools=SimpleNamespace(available=lambda: []))
    server = create_server("127.0.0.1", 0, master=master)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    try:
        status, queued = _http_json(
            port,
            "POST",
            "/internal/v1/orchestration/runs",
            service_jwt,
            {
                "objective": "catedralx_opportunity_evaluation",
                "payload": _opportunity_payload(),
                "permissions": {"allowed_tools": [], "side_effects": "none"},
                "success_criteria": [
                    "Return reproducible economic scenarios.",
                    "Preserve evidence and trace identity.",
                    "Produce an analysis-only candidate requiring independent acceptance.",
                ],
            },
            headers={
                "Idempotency-Key": "synthetic-opportunity-e2e-001",
                "traceparent": f"00-{TRACE_ID}-0123456789abcdef-01",
            },
        )
        assert status == 202, queued
        assert queued["status"] == "queued"
        assert queued["trace_id"] == TRACE_ID

        # Exercise the same persisted queue and worker path used by the service.
        task = task_bus.get_next_task()
        assert task is not None
        assert task["task_id"] == queued["task_id"]
        candidate = process_task(task)

        assert candidate["status"] == "candidate"
        assert candidate["engine"] == "CatedralX"
        assert candidate["decision_result"] is None
        assert candidate["economic_assessment"]["evaluation_domain"] == "business_opportunity"
        assert candidate["economic_assessment"]["status"] == "ANALYSIS_COMPLETE"
        assert candidate["economic_assessment"]["trace_id"] == TRACE_ID
        assert candidate["catedralx_trace_id"] == TRACE_ID
        assert candidate["economic_assessment"]["scenarios"]["base"]["revenue_total"] == 24000.0
        assert candidate["economic_assessment"]["scenarios"]["base"]["cost_total"] == 6000.0
        assert candidate["economic_assessment"]["financials"]["funding_gap"] == 2000.0
        assert candidate["economic_assessment"]["authorization"] == "NOT_GRANTED"
        assert candidate["economic_assessment"]["independent_acceptance_required"] is True
        assert candidate["analysis_only"] is True
        assert candidate["orders_created"] is False
        assert candidate["orders_executed"] is False
        assert candidate["execution_boundary"]["independent_acceptance_required"] is True

        status, run = _http_json(
            port, "GET",
            f"/internal/v1/orchestration/runs/{queued['run_id']}",
            read_jwt,
        )
        assert status == 200
        assert run["status"] == "awaiting_acceptance"
        assert run["trace_id"] == TRACE_ID

        status, pending = _http_json(
            port, "GET", f"/v1/results/{candidate['candidate_id']}", read_jwt
        )
        assert status == 202
        assert pending["status"] == "pending_verification"

        decision = _signed_rejection(candidate_store.get_candidate(candidate["candidate_id"]))
        status, rejected = _http_json(
            port, "POST", "/v1/acceptance", acceptance_jwt, decision
        )
        assert status == 201, rejected
        assert rejected["outcome"] == "rejected"

        status, blocked_delivery = _http_json(
            port, "GET", f"/v1/results/{candidate['candidate_id']}", read_jwt
        )
        assert status == 403
        assert blocked_delivery["error"] == "result_not_accepted"

        persisted = acceptance_store.get_acceptance(candidate["candidate_id"])
        assert persisted["outcome"] == "rejected"
        assert persisted["trace_id"] == TRACE_ID
        assert task_bus.get_task_status(queued["task_id"])["status"] == "rejected"
        assert candidate_store.get_candidate(candidate["candidate_id"])["orders_created"] is False
        assert candidate_store.get_candidate(candidate["candidate_id"])["orders_executed"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
