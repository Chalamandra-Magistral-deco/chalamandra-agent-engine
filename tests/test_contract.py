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

TEST_JWT_SECRET = "jwt-secret-para-produccion-de-32-bytes-minimo"
TEST_TOKEN = "test-api-token-contract-32-bytes-min"


def make_jwt(scopes: str = "jobs:write jobs:read results:read results:write subscriptions:read") -> str:
    payload = {"scope": scopes, "exp": int(time.time()) + 3600}
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


def _make_server(monkeypatch, tmp_path, model_calls_enabled: bool):
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", TEST_JWT_SECRET)
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

    status, _ = request(
        port, f"/internal/v1/tasks/{task_id}/results",
        method="POST",
        payload={"candidate_id": "cand-1", "status": "accepted"},
        headers={"Authorization": f"Bearer {make_jwt('results:write')}"},
    )
    assert status == 202, f"con token esperaba 202, obtuve {status}"


def test_internal_orchestration_runs_get_requires_auth(local_api):
    """P0-2: el run interno no se puede leer sin token."""
    port = local_api
    import uuid
    run_id = str(uuid.uuid4())

    status, _ = request(port, f"/internal/v1/orchestration/runs/{run_id}", headers={})
    assert status == 401, f"sin token esperaba 401, obtuve {status}"

    status, _ = request(
        port, f"/internal/v1/orchestration/runs/{run_id}",
        headers={"Authorization": f"Bearer {make_jwt('results:read')}"},
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
