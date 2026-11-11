"""Contrato mínimo: demuestra los 3 criterios de éxito del principio rector.

Criterio 3 (no elusión de controles de aceptación):
  - POST /internal/v1/tasks/{id}/results sin token → 401
  - GET  /internal/v1/orchestration/runs/{id} sin token → 401
  - POST /v1/plan y /v1/tasks con model_calls_enabled=true, sin token → 401
  - GET  /v1/subscriptions solo requiere subscriptions:read

Los flujos end-to-end reales (invocan CatedralX) van en Fase I tras Fase C.
"""
from __future__ import annotations

import http.client
import json
import threading
import time
from collections.abc import Iterator
from typing import Any

import jwt
import pytest

from api.server import create_server
from core.master import Master

TEST_JWT_SECRET = "test-general-jwt-secret-minimum-32-bytes"
TEST_CATEDRALX_JWT_SECRET = "test-catedralx-service-jwt-secret-2026"
TEST_TOKEN = "test-api-token-contract-32-bytes-min"


def make_jwt(
    scopes: str = "jobs:write jobs:read results:read results:write subscriptions:read",
    *,
    subject: str | None = None,
) -> str:
    payload = {"scope": scopes, "exp": int(time.time()) + 3600}
    if subject:
        payload["sub"] = subject
    secret = TEST_CATEDRALX_JWT_SECRET if subject == "catedralx-service" else TEST_JWT_SECRET
    return jwt.encode(payload, secret, algorithm="HS256")


def _make_server(monkeypatch, tmp_path, model_calls_enabled: bool):
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("CHALAMANDRA_CATEDRALX_JWT_SECRET", TEST_CATEDRALX_JWT_SECRET)
    monkeypatch.setenv("CHALAMANDRA_API_TOKEN", TEST_TOKEN)
    monkeypatch.setenv(
        "CHALAMANDRA_API_ENABLE_MODEL_CALLS",
        "true" if model_calls_enabled else "false",
    )
    from api import candidate_store, task_bus, subscription_store
    monkeypatch.setattr(candidate_store, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(task_bus, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(subscription_store, "_state_dir", lambda: tmp_path)
    master = Master()
    return create_server("127.0.0.1", 0, master=master)


@pytest.fixture
def local_api(monkeypatch, tmp_path) -> Iterator[int]:
    server = _make_server(monkeypatch, tmp_path, model_calls_enabled=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def local_api_model_enabled(monkeypatch, tmp_path) -> Iterator[int]:
    server = _make_server(monkeypatch, tmp_path, model_calls_enabled=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(
    port: int,
    path: str,
    *,
    method: str = "GET",
    payload: Any = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    request_headers = (
        dict(headers)
        if headers is not None
        else {"Authorization": f"Bearer {make_jwt()}"}
    )
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    status = response.status
    text = response.read().decode("utf-8")
    connection.close()
    try:
        return status, json.loads(text) if text else None
    except json.JSONDecodeError:
        return status, text


def test_internal_tasks_results_requires_auth(local_api):
    """P0-1: el worker no puede inyectar resultados sin token."""
    port = local_api
    from api import task_bus
    task_id = task_bus.enqueue_task(
        {"objective": "test", "trace_id": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"}
    )

    status, _ = request(
        port, f"/internal/v1/tasks/{task_id}/results",
        method="POST",
        payload={"candidate_id": "cand-1", "status": "accepted"},
        headers={},
    )
    assert status == 401, f"sin token esperaba 401, obtuve {status}"

    status, result = request(
        port, f"/internal/v1/tasks/{task_id}/results",
        method="POST",
        payload={"candidate_id": "cand-1", "status": "accepted", "lease_token": "not-valid"},
        headers={"Authorization": f"Bearer {make_jwt('tasks:complete')}"},
    )
    assert status == 400
    assert result["error"] == "invalid_candidate_status"

    claim = task_bus.get_next_task()
    assert claim is not None and claim["task_id"] == task_id
    status, _ = request(
        port, f"/internal/v1/tasks/{task_id}/results",
        method="POST",
        payload={
            "candidate_id": "cand-1",
            "result_id": "cand-1",
            "worker_id": "worker-contract-test",
            "status": "candidate",
            "summary": "synthetic candidate",
            "evidence": [],
            "assumptions": [],
            "contradictions": [],
            "trace_id": "00-" + "a" * 32 + "-" + "b" * 16 + "-01",
            "lease_token": claim["lease_token"],
        },
        headers={"Authorization": f"Bearer {make_jwt('tasks:complete')}"},
    )
    assert status == 202


def test_internal_orchestration_runs_get_requires_auth(local_api):
    """P0-2: el run interno no se puede leer sin token."""
    port = local_api
    import uuid
    run_id = str(uuid.uuid4())

    status, _ = request(port, f"/internal/v1/orchestration/runs/{run_id}", headers={})
    assert status == 401, f"sin token esperaba 401, obtuve {status}"

    status, _ = request(
        port, f"/internal/v1/orchestration/runs/{run_id}",
        headers={"Authorization": f"Bearer {make_jwt('orchestration:read')}"},
    )
    assert status in (200, 404), f"con token esperaba 200/404, obtuve {status}"


def test_plan_requires_auth_when_model_calls_enabled(local_api_model_enabled):
    """P0-13: /v1/plan no ejecuta Master sin token."""
    port = local_api_model_enabled
    status, _ = request(port, "/v1/plan", method="POST", payload={"task": "dummy"}, headers={})
    assert status == 401, f"sin token esperaba 401, obtuve {status}"


def test_tasks_requires_auth_when_model_calls_enabled(local_api_model_enabled):
    """P0-13: /v1/tasks no ejecuta Master sin token."""
    port = local_api_model_enabled
    status, _ = request(port, "/v1/tasks", method="POST", payload={"task": "dummy"}, headers={})
    assert status == 401, f"sin token esperaba 401, obtuve {status}"


def test_subscriptions_get_only_requires_read_scope(local_api):
    """P0-15: GET /v1/subscriptions exige read, no write+read."""
    port = local_api
    status, _ = request(
        port, "/v1/subscriptions",
        headers={"Authorization": f"Bearer {make_jwt('subscriptions:read')}"},
    )
    assert status == 200, f"con scope read esperaba 200, obtuve {status}"



def test_orchestration_run_preserves_payload_and_reports_terminal_candidate(local_api, monkeypatch):
    from api import task_bus

    port = local_api
    from conftest import sign_market_payload
    payload = sign_market_payload({
        "historical_prices": [100.0 + index * 0.01 for index in range(30)],
        "current_price": 100.29,
        "observed_at": "2026-10-27T20:00:00+00:00",
        "source": "test_fixture",
    })
    status, created = request(
        port,
        "/internal/v1/orchestration/runs",
        method="POST",
        payload={"objective": "financial_market_analysis", "payload": payload, "permissions": {"allowed_tools": [], "side_effects": "none"}},
        headers={"Authorization": f"Bearer {make_jwt('orchestration:run', subject='catedralx-service')}"},
    )
    assert status == 202
    run_id = created["run_id"]
    tasks = task_bus.get_tasks_by_run(run_id)
    assert len(tasks) == 1
    assert json.loads(tasks[0]["payload"]) == payload
    assert json.loads(tasks[0]["execution_policy"]) == {
        "allowed_tools": [],
        "side_effects": "none",
    }

    claimed = task_bus.get_next_task()
    assert claimed is not None
    assert claimed["task_id"] == tasks[0]["task_id"]
    task_bus.mark_task_completed(
        tasks[0]["task_id"],
        {"status": "candidate", "candidate_id": "candidate-test"},
        result_id="candidate-test",
        lease_token=claimed["lease_token"],
    )
    status, run = request(
        port,
        f"/internal/v1/orchestration/runs/{run_id}",
        headers={"Authorization": f"Bearer {make_jwt('orchestration:read')}"},
    )
    assert status == 200
    assert run["status"] == "awaiting_acceptance"
    assert run["task_statuses"] == ["candidate"]



def test_orchestration_run_idempotency_preserves_original_run(local_api):
    from api import task_bus

    port = local_api
    headers = {
        "Authorization": f"Bearer {make_jwt('orchestration:run', subject='catedralx-service')}",
        "Idempotency-Key": "catedralx-economic-cycle-test-1",
    }
    payload = {
        "objective": "catedralx_economic_proposal_review",
        "payload": {
            "origin": "CatedralX.FEX.GOVERNANCE.economic_cycle",
            "cycle_id": "cycle-idem-test",
            "measurement": {},
            "assessment": {},
            "proposal": {},
            "authorization": {
                "approved": True,
                "approver": "test-owner",
                "rationale": "test only",
                "identity_verified": True,
                "identity_permissions": ["economic_cycle_authorize"],
                "identity_verification": "APPROVER_IDENTITY_VERIFIER",
            },
            "execution_boundary": {},
        },
        "permissions": {"allowed_tools": [], "side_effects": "none"},
        "success_criteria": ["Create a traceable candidate"],
    }

    status_first, first = request(
        port, "/internal/v1/orchestration/runs",
        method="POST", payload=payload, headers=headers,
    )
    status_replay, replay = request(
        port, "/internal/v1/orchestration/runs",
        method="POST", payload=payload, headers=headers,
    )

    assert status_first == status_replay == 202
    assert first["run_id"] == replay["run_id"]
    assert first["task_id"] == replay["task_id"]
    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert len(task_bus.get_tasks_by_run(first["run_id"])) == 1

    changed_payload = dict(payload["payload"])
    changed_payload["cycle_id"] = "cycle-idem-test-changed"
    changed = {**payload, "payload": changed_payload}
    status_conflict, conflict = request(
        port, "/internal/v1/orchestration/runs",
        method="POST", payload=changed, headers=headers,
    )
    assert status_conflict == 409
    assert conflict["error"] == "idempotency_conflict"


def test_orchestration_run_validates_objective_and_contract_shapes(local_api):
    port = local_api
    auth = {"Authorization": f"Bearer {make_jwt('orchestration:run', subject='catedralx-service')}"}
    status, body = request(
        port, "/internal/v1/orchestration/runs",
        method="POST", payload={"objective": " "}, headers=auth,
    )
    assert status == 400
    assert body["error"] == "invalid_objective"

    status, body = request(
        port, "/internal/v1/orchestration/runs",
        method="POST",
        payload={"objective": "valid", "permissions": []},
        headers=auth,
    )
    assert status == 400
    assert body["error"] == "permissions_must_be_object"



def test_orchestration_run_requires_catedralx_service_principal(local_api):
    status, body = request(
        local_api,
        "/internal/v1/orchestration/runs",
        method="POST",
        payload={"objective": "test"},
        headers={"Authorization": f"Bearer {make_jwt('orchestration:run')}"},
    )
    assert status == 401
    assert body["error"] == "unauthorized"


def test_orchestration_run_blocks_tools_without_effect_classification(local_api):
    auth = {
        "Authorization": f"Bearer {make_jwt('orchestration:run', subject='catedralx-service')}"
    }
    status, body = request(
        local_api, "/internal/v1/orchestration/runs", method="POST",
        payload={
            "objective": "read-only analysis",
            "permissions": {"allowed_tools": ["dynamics"], "side_effects": "read_only"},
        }, headers=auth,
    )
    assert status == 403
    assert body["error"] == "tool_effect_classification_missing"
    assert body["execution_status"] == "blocked"


def test_orchestration_run_blocks_unverified_approved_scoped_effects(local_api):
    auth = {
        "Authorization": f"Bearer {make_jwt('orchestration:run', subject='catedralx-service')}"
    }
    status, body = request(
        local_api, "/internal/v1/orchestration/runs", method="POST",
        payload={
            "objective": "scoped external action",
            "permissions": {"allowed_tools": [], "side_effects": "approved_scoped"},
        }, headers=auth,
    )
    assert status == 403
    assert body["error"] == "approved_scoped_requires_verifiable_approval"
    assert body["execution_status"] == "blocked"


def test_orchestration_run_rejects_unregistered_allowed_tools(local_api):
    port = local_api
    auth = {
        "Authorization": f"Bearer {make_jwt('orchestration:run', subject='catedralx-service')}"
    }
    status, body = request(
        port, "/internal/v1/orchestration/runs",
        method="POST",
        payload={
            "objective": "valid",
            "permissions": {"allowed_tools": ["shell_arbitrary"], "side_effects": "none"},
        },
        headers=auth,
    )
    assert status == 400
    assert body["error"] == "unknown_allowed_tools"
