"""Pruebas del servicio HTTP local, sin llamadas a modelos ni red externa."""

from __future__ import annotations

import http.client
import json
import jwt
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from api.server import create_server
from api import task_bus
from core.master import Master
from api.safe_http import ResolvedWebhookTarget

TEST_TOKEN = "test-api-token"
TEST_JWT_SECRET = "jwt-secret-para-produccion-de-32-bytes-minimo"
TEST_MAGIS_JWT_SECRET = "magis-service-test-secret-distinto-y-largo-32-bytes"

def make_jwt(scopes="jobs:write jobs:read results:read orchestration:run orchestration:read") -> str:
    import time
    payload = {"scope": scopes, "exp": int(time.time()) + 3600}
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


@pytest.fixture
def local_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[int, Any]]:
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.delenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", raising=False)
    monkeypatch.delenv("CHALAMANDRA_MAGIS_EXECUTION_ENABLED", raising=False)
    master = Master()

    async def fake_tool_call(agent: str, tool_call: dict[str, Any]) -> dict[str, Any]:
        return {"agent": agent, "tool_call": tool_call, "status": "test-result"}

    monkeypatch.setattr(master, "execute_agent_tool_call", fake_tool_call)
    server = create_server("127.0.0.1", 0, master=master)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], master
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
) -> tuple[int, dict[str, Any]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    request_headers = (
        dict(headers)
        if headers is not None
        else {"Authorization": f"Bearer {make_jwt()}"}
    )
    body = None
    if payload is not None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    status = response.status
    text = response.read().decode("utf-8")
    connection.close()
    return status, json.loads(text)



def test_v1_request_authenticates_enqueues_and_persists_w3c_trace(local_api, tmp_path):
    port, _ = local_api
    trace_id = "1234567890abcdef1234567890abcdef"
    parent_id = "1234567890abcdef"
    traceparent = f"00-{trace_id}-{parent_id}-01"
    payload = {
        "objective": "traceable_test_request",
        "payload": {"source": "synthetic_test"},
        "permissions": {"allowed_tools": [], "side_effects": "none"},
        "success_criteria": ["must be traceable"],
    }
    headers = {
        "Authorization": f"Bearer {make_jwt(scopes='jobs:write')}" ,
        "Idempotency-Key": "request-flow-test-001",
        "traceparent": traceparent,
    }
    status, first = request(
        port, "/v1/request?do-not-log-this=query-secret", method="POST",
        payload=payload, headers=headers,
    )
    assert status == 202
    assert first["status"] == "queued"
    assert first["trace_id"] == trace_id
    assert first["task_id"]
    assert first["run_id"]

    repeated_headers = dict(headers)
    repeated_headers["traceparent"] = "00-abcdefabcdefabcdefabcdefabcdefab-abcdefabcdefabcd-01"
    status, replay = request(
        port, "/v1/request", method="POST", payload=payload, headers=repeated_headers,
    )
    assert status == 202
    assert replay["task_id"] == first["task_id"]
    assert replay["run_id"] == first["run_id"]
    assert replay["idempotent_replay"] is True

    from api import task_bus
    stored = task_bus.get_task_status(first["task_id"])
    assert stored is not None
    assert stored["trace_id"] == trace_id

    trace_file = tmp_path / "traces.jsonl"
    records = [json.loads(line) for line in trace_file.read_text().splitlines() if line.strip()]
    traced = [row for row in records if row.get("path") == "/v1/request" and row.get("trace_id") == trace_id]
    assert traced
    assert traced[0]["traceparent"] == traceparent
    assert traced[0]["status"] == 202
    assert "query-secret" not in trace_file.read_text()


def test_v1_request_requires_auth_and_idempotency_key(local_api):
    port, _ = local_api
    payload = {"objective": "test", "payload": {}, "permissions": {"allowed_tools": [], "side_effects": "none"}}
    status, body = request(
        port, "/v1/request", method="POST", payload=payload,
        headers={"Content-Type": "application/json", "Idempotency-Key": "missing-auth-123"},
    )
    assert status == 401
    assert body["error"] == "unauthorized"

    status, body = request(
        port, "/v1/request", method="POST", payload=payload,
        headers={"Authorization": f"Bearer {make_jwt(scopes='jobs:write')}"},
    )
    assert status == 400
    assert body["error"] == "idempotency_key_required"

def test_health_inspect_and_tools_are_readable_on_loopback(local_api):
    port, master = local_api

    status, health = request(port, "/healthz")
    assert status == 200
    assert health["status"] == "ok"
    assert health["execution_enabled"] is False

    status, inspect = request(port, "/v1/inspect")
    assert status == 200
    assert "catedralx_reasoning" in inspect["tools"]
    assert {agent["name"] for agent in inspect["agents"]} == {
        "coordinator", "engineer", "auditor", "sentinel", "analyst"
    }

    status, tools = request(port, "/v1/tools")
    assert status == 200
    assert {tool["name"] for tool in tools["tools"]} == set(master.tools.available())


def test_structured_tool_call_uses_master_permission_boundary(local_api):
    port, _ = local_api
    status, result = request(
        port,
        "/v1/tool-calls",
        method="POST",
        payload={
            "agent": "analyst",
            "tool_call": {
                "name": "search_term_miner",
                "arguments": {
                    "csv_path": "sample.csv",
                    "profile": "prospecting",
                    "cost_floor": 10,
                },
            },
        },
    )
    assert status == 200
    assert result["status"] == "ok"
    assert result["result"]["status"] == "test-result"


def test_server_rejects_unknown_and_malformed_routes(local_api):
    port, _ = local_api

    status, result = request(port, "/not-a-route")
    assert status == 404
    assert result["error"] == "not_found"

    status, result = request(port, "/v1/tool-calls", method="POST", payload={"agent": "analyst"})
    assert status == 400
    assert result["error"] == "expected_agent_and_tool_call"


def test_model_backed_routes_are_disabled_by_default(local_api):
    port, _ = local_api
    status, result = request(
        port,
        "/v1/plan",
        method="POST",
        payload={"task": "solo plan de prueba"},
    )
    assert status == 403
    assert result["error"] == "model_calls_disabled"


def test_loopback_without_token_allows_only_read_only_get(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CHALAMANDRA_API_TOKEN", raising=False)
    monkeypatch.delenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", raising=False)
    server = create_server("127.0.0.1", 0, master=Master())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, result = request(server.server_address[1], "/healthz", headers={})
        assert status == 200
        assert result["status"] == "ok"

        status, result = request(
            server.server_address[1],
            "/v1/tool-calls",
            method="POST",
            payload={"agent": "analyst", "tool_call": {}},
            headers={},
        )
        assert status == 401
        assert result["error"] == "unauthorized"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_payload_size_and_content_type_are_limited(local_api):
    port, _ = local_api

    status, result = request(
        port,
        "/v1/tool-calls",
        method="POST",
        payload=b"x" * (64 * 1024 + 1),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {make_jwt()}"},
    )
    assert status == 413
    assert result["error"] == "request_too_large"

    status, result = request(
        port,
        "/v1/tool-calls",
        method="POST",
        payload={"agent": "analyst", "tool_call": {}},
        headers={"Content-Type": "text/plain", "Authorization": f"Bearer {make_jwt()}"},
    )
    assert status == 415
    assert result["error"] == "content_type_must_be_json"


def test_token_protects_loopback_server(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.delenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", raising=False)
    server = create_server("127.0.0.1", 0, master=Master())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, result = request(server.server_address[1], "/v1/inspect", headers={})
        assert status == 401
        assert result["error"] == "unauthorized"

        status, result = request(
            server.server_address[1],
            "/v1/inspect",
            headers={"Authorization": f"Bearer {make_jwt()}"},
        )
        assert status == 200
        assert "models" in result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_non_loopback_requires_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ALLOW_CONTAINER_BIND", raising=False)
    monkeypatch.delenv("CHALAMANDRA_API_TOKEN", raising=False)
    monkeypatch.delenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", raising=False)

    with pytest.raises(ValueError, match="loopback"):
        create_server("0.0.0.0", 0)

    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", TEST_JWT_SECRET)
    with pytest.raises(ValueError, match="loopback"):
        create_server("0.0.0.0", 0)


def test_model_calls_require_token_even_on_loopback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CHALAMANDRA_API_TOKEN", raising=False)
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "true")

    with pytest.raises(ValueError, match="CHALAMANDRA_API_TOKEN"):
        create_server("127.0.0.1", 0)


def test_internal_run_status_requires_orchestration_read_scope(local_api, monkeypatch):
    port, _ = local_api
    task = {
        "task_id": "task-internal-1",
        "run_id": "run-internal-1",
        "status": "candidate",
        "trace_id": "trace-internal-1",
        "updated_at": "2026-10-25T00:00:00+00:00",
    }
    monkeypatch.setattr(task_bus, "get_tasks_by_run", lambda run_id: [task] if run_id == task["run_id"] else [])

    status, result = request(
        port,
        "/internal/v1/orchestration/runs/run-internal-1",
        headers={},
    )
    assert status == 401
    assert result["error"] == "unauthorized"

    status, result = request(
        port,
        "/internal/v1/orchestration/runs/run-internal-1",
        headers={"Authorization": f"Bearer {make_jwt(scopes='jobs:write')}"},
    )
    assert status == 401

    status, result = request(
        port,
        "/internal/v1/orchestration/runs/run-internal-1",
        headers={"Authorization": f"Bearer {make_jwt(scopes='orchestration:read')}"},
    )
    assert status == 200
    assert result["status"] == "awaiting_acceptance"
    assert result["task_ids"] == ["task-internal-1"]
    assert result["trace_id"] == "trace-internal-1"

    status, result = request(
        port,
        "/internal/v1/orchestration/runs/missing-run",
        headers={"Authorization": f"Bearer {make_jwt(scopes='orchestration:read')}"},
    )
    assert status == 404
    assert result["error"] == "run_not_found"


def test_internal_task_result_requires_tasks_complete_scope(local_api, monkeypatch):
    port, _ = local_api
    payload = {
        "candidate_id": "candidate-internal-1",
        "result_id": "candidate-internal-1",
        "worker_id": "worker-test-1",
        "status": "candidate",
        "summary": "synthetic candidate",
        "evidence": [],
        "assumptions": [],
        "contradictions": [],
        "trace_id": "trace-internal-1",
        "lease_token": "lease-test-token",
    }
    calls = []

    def fake_complete(task_id, body, result_id=None, lease_token=None):
        calls.append((task_id, body, result_id, lease_token))
        return True

    monkeypatch.setattr(task_bus, "mark_task_completed", fake_complete)

    status, result = request(
        port,
        "/internal/v1/tasks/task-internal-1/results",
        method="POST",
        payload=payload,
        headers={},
    )
    assert status == 401
    assert result["error"] == "unauthorized"
    assert calls == []

    status, result = request(
        port,
        "/internal/v1/tasks/task-internal-1/results",
        method="POST",
        payload=payload,
        headers={"Authorization": f"Bearer {make_jwt(scopes='jobs:write')}"},
    )
    assert status == 401
    assert calls == []

    without_lease = {key: value for key, value in payload.items() if key != "lease_token"}
    status, result = request(
        port,
        "/internal/v1/tasks/task-internal-1/results",
        method="POST",
        payload=without_lease,
        headers={"Authorization": f"Bearer {make_jwt(scopes='tasks:complete')}"},
    )
    assert status == 400
    assert result["error"] == "lease_token_required"
    assert calls == []

    invalid_state = {**payload, "status": "accepted"}
    status, result = request(
        port,
        "/internal/v1/tasks/task-internal-1/results",
        method="POST",
        payload=invalid_state,
        headers={"Authorization": f"Bearer {make_jwt(scopes='tasks:complete')}"},
    )
    assert status == 400
    assert result["error"] == "invalid_candidate_status"
    assert calls == []

    incomplete = {key: value for key, value in payload.items() if key != "summary"}
    status, result = request(
        port, "/internal/v1/tasks/task-internal-1/results", method="POST",
        payload=incomplete,
        headers={"Authorization": f"Bearer {make_jwt(scopes='tasks:complete')}"},
    )
    assert status == 400
    assert result["error"] == "candidate_result_missing_fields"
    assert calls == []

    unexpected = {**payload, "uncontracted_field": "not-allowed"}
    status, result = request(
        port, "/internal/v1/tasks/task-internal-1/results", method="POST",
        payload=unexpected,
        headers={"Authorization": f"Bearer {make_jwt(scopes='tasks:complete')}"},
    )
    assert status == 400
    assert result["error"] == "candidate_result_unknown_fields"
    assert calls == []

    mismatched = {**payload, "result_id": "different-result"}
    status, result = request(
        port, "/internal/v1/tasks/task-internal-1/results", method="POST",
        payload=mismatched,
        headers={"Authorization": f"Bearer {make_jwt(scopes='tasks:complete')}"},
    )
    assert status == 400
    assert result["error"] == "candidate_result_identity_mismatch"
    assert calls == []

    status, result = request(
        port,
        "/internal/v1/tasks/task-internal-1/results",
        method="POST",
        payload=payload,
        headers={"Authorization": f"Bearer {make_jwt(scopes='tasks:complete')}"},
    )
    assert status == 202
    assert result["candidate_id"] == "candidate-internal-1"
    assert calls == [(
        "task-internal-1",
        {key: value for key, value in payload.items() if key != "lease_token"},
        "candidate-internal-1",
        "lease-test-token",
    )]

    monkeypatch.setattr(task_bus, "mark_task_completed", lambda *args, **kwargs: False)
    status, result = request(
        port,
        "/internal/v1/tasks/task-internal-1/results",
        method="POST",
        payload=payload,
        headers={"Authorization": f"Bearer {make_jwt(scopes='tasks:complete')}"},
    )
    assert status == 409
    assert result["error"] == "task_lease_invalid_or_expired"


def test_create_subscription_requires_write_scope_and_persists_schema(local_api, monkeypatch, tmp_path):
    from api import subscription_store

    port, _ = local_api
    # This test isolates authorization and persistence; SSRF behavior is tested separately.
    monkeypatch.setattr(
        subscription_store,
        "resolve_safe_url",
        lambda _url: ResolvedWebhookTarget(
            scheme="http",
            hostname="webhook-sink",
            port=8000,
            request_target="/callback",
            addresses=("172.23.0.5",),
        ),
    )
    target = tmp_path / "subscriptions.jsonl"
    monkeypatch.setattr(subscription_store, "_subs_file", lambda: target)
    payload = {
        "endpoint_url": "http://webhook-sink:8000/callback",
        "event_types": ["result.accepted"],
    }

    status, denied = request(
        port, "/v1/subscriptions", method="POST", payload=payload,
        headers={"Authorization": f"Bearer {make_jwt(scopes='subscriptions:read')}"},
    )
    assert status == 401
    assert denied["error"] == "unauthorized"

    status, created = request(
        port, "/v1/subscriptions", method="POST", payload=payload,
        headers={"Authorization": f"Bearer {make_jwt(scopes='subscriptions:write')}"},
    )
    assert status == 201
    assert created["endpoint_url"] == payload["endpoint_url"]
    assert created["event_types"] == ["result.accepted"]
    assert created["status"] == "enabled"
    assert target.exists()


@pytest.mark.parametrize("scopes", ["subscriptions:read", "jobs:read"])
def test_delete_subscription_requires_write_scope(local_api, monkeypatch, tmp_path, scopes):
    from api import subscription_store

    port, _ = local_api
    monkeypatch.setattr(
        subscription_store,
        "resolve_safe_url",
        lambda _url: ResolvedWebhookTarget(
            scheme="https",
            hostname="webhook.example.test",
            port=443,
            request_target="/callback",
            addresses=("93.184.216.34",),
        ),
    )
    target = tmp_path / "subscriptions.jsonl"
    monkeypatch.setattr(subscription_store, "_subs_file", lambda: target)
    payload = {
        "endpoint_url": "https://webhook.example.test/callback",
        "event_types": ["result.accepted"],
    }

    status, created = request(
        port,
        "/v1/subscriptions",
        method="POST",
        payload=payload,
        headers={"Authorization": f"Bearer {make_jwt(scopes='subscriptions:write')}"},
    )
    assert status == 201
    subscription_id = created["subscription_id"]

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    connection.request(
        "DELETE",
        f"/v1/subscriptions/{subscription_id}",
        headers={"Authorization": f"Bearer {make_jwt(scopes=scopes)}"},
    )
    denied_response = connection.getresponse()
    denied_body = denied_response.read()
    connection.close()
    status = denied_response.status
    denied = json.loads(denied_body) if denied_body else {}
    assert status == 401
    assert denied["error"] == "unauthorized"
    current = next(
        item for item in subscription_store.list_subscriptions()
        if item["subscription_id"] == subscription_id
    )
    assert current["status"] == "enabled"

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    connection.request(
        "DELETE",
        f"/v1/subscriptions/{subscription_id}",
        headers={"Authorization": f"Bearer {make_jwt(scopes='subscriptions:write')}"},
    )
    response = connection.getresponse()
    response_body = response.read()
    connection.close()

    assert response.status == 204
    assert response_body == b""
    current = next(
        item for item in subscription_store.list_subscriptions()
        if item["subscription_id"] == subscription_id
    )
    assert current["status"] == "disabled"


def test_distribution_requires_accepted_candidate_and_service_identity(local_api, monkeypatch):
    from unittest.mock import patch
    from api import candidate_store, acceptance_store, webhook_dispatcher
    import time
    import jwt

    port, _ = local_api
    service_secret = "test-only-catedralx-service-secret-32-bytes"
    monkeypatch.setenv("CHALAMANDRA_CATEDRALX_JWT_SECRET", service_secret)
    token = jwt.encode(
        {"scope": "results:publish", "sub": "catedralx-service", "exp": int(time.time()) + 300},
        service_secret,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"candidate_id": "candidate-pending-1"}

    with patch.object(candidate_store, "get_candidate", return_value={"candidate_id": payload["candidate_id"], "status": "candidate"}), \
         patch.object(acceptance_store, "get_acceptance", return_value=None), \
         patch.object(webhook_dispatcher, "dispatch_event") as dispatch:
        status, body = request(
            port, "/internal/v1/distributions", method="POST", payload=payload, headers=headers,
        )
    assert status == 409
    assert body["error"] == "result_not_independently_accepted"
    dispatch.assert_not_called()


def test_distribution_dispatches_only_verified_acceptance_with_w3c_parent(local_api, monkeypatch):
    from unittest.mock import patch
    from api import candidate_store, acceptance_store, webhook_dispatcher
    import time
    import jwt

    port, _ = local_api
    trace_id = "6c0a78fc04a34d9b8e8f1fbced8a0041"
    traceparent = f"00-{trace_id}-00f067aa0ba902b7-01"
    service_secret = "test-only-catedralx-service-secret-32-bytes"
    monkeypatch.setenv("CHALAMANDRA_CATEDRALX_JWT_SECRET", service_secret)
    token = jwt.encode(
        {"scope": "results:publish", "sub": "catedralx-service", "exp": int(time.time()) + 300},
        service_secret,
        algorithm="HS256",
    )
    candidate_id = "candidate-accepted-1"
    payload = {"candidate_id": candidate_id, "trace_id": trace_id, "event_id": "distribution-e2e-001"}
    candidate = {"candidate_id": candidate_id, "status": "candidate", "trace_id": trace_id}
    acceptance = {"candidate_id": candidate_id, "outcome": "accepted", "trace_id": trace_id}

    with patch.object(candidate_store, "get_candidate", return_value=candidate), \
         patch.object(acceptance_store, "get_acceptance", return_value=acceptance), \
         patch("api.task_bus.get_result_task_statuses", return_value=[{"task_id": "task-1", "status": "accepted"}]), \
         patch.object(webhook_dispatcher, "dispatch_event", return_value=1) as dispatch:
        status, body = request(
            port, "/internal/v1/distributions", method="POST", payload=payload,
            headers={"Authorization": f"Bearer {token}", "traceparent": traceparent},
        )

    assert status == 202
    assert body["status"] == "dispatched"
    assert body["delivered_count"] == 1
    assert body["event_id"] == payload["event_id"]
    assert body["trace_id"] == trace_id
    dispatch.assert_called_once_with(
        event_type="result.accepted",
        result_id=candidate_id,
        trace_id=trace_id,
        traceparent=traceparent,
        event_id=payload["event_id"],
    )


def test_accepted_distribution_posts_to_local_sink_with_w3c_child_span(
    local_api, monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """Run the real subscription -> acceptance gate -> dispatcher -> HTTP sink locally."""
    import hashlib
    import hmac
    import time
    import uuid
    from datetime import datetime, timezone
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    import jwt
    from nacl.signing import SigningKey
    from api import acceptance_store, candidate_store
    from api.crypto_signer import create_canonical_payload

    port, _ = local_api
    trace_id = "5e5e59d88d1c4268aa9c4b3ad52db540"
    incoming_parent_id = "3c12cbae65701234"
    incoming_traceparent = f"00-{trace_id}-{incoming_parent_id}-01"
    verifier_id = "local-sink-e2e-verifier"
    signing_key = SigningKey(bytes.fromhex("22" * 32))
    service_secret = "local-sink-service-secret-at-least-32-bytes"
    webhook_secret = "local-sink-webhook-secret-at-least-32-bytes"

    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("CHALAMANDRA_CATEDRALX_JWT_SECRET", service_secret)
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_PUBLIC_KEY", signing_key.verify_key.encode().hex())
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_ID", verifier_id)
    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_SECRET", webhook_secret)
    # Permit only the exact loopback sink used by this isolated integration test.
    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_PRIVATE_HOST_ALLOWLIST", "127.0.0.1")

    candidate_id = str(uuid.uuid4())
    candidate = {
        "candidate_id": candidate_id,
        "status": "candidate",
        "trace_id": trace_id,
        "engine": "CatedralX",
        "analysis_only": True,
        "orders_created": False,
        "orders_executed": False,
        "evidence": [{
            "evidence_id": str(uuid.uuid4()),
            "kind": "calculation",
            "source": "local_synthetic_fixture",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "digest": hashlib.sha256(b"synthetic local distribution evidence").hexdigest(),
        }],
    }
    candidate_store.save_candidate(candidate)
    monkeypatch.setattr(
        "api.task_bus.get_result_task_statuses",
        lambda _: [{"task_id": "task-accepted", "status": "accepted"}],
    )
    decision = {
        "decision_id": str(uuid.uuid4()),
        "candidate_id": candidate_id,
        "outcome": "accepted",
        "criteria_checked": ["synthetic candidate shape", "evidence digest", "W3C trace continuity"],
        "reasons": [],
        "missing_evidence": [],
        "verifier_id": verifier_id,
        "evidence_digest": candidate_store.compute_evidence_digest(candidate),
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "trace_id": trace_id,
    }
    decision["signature"] = signing_key.sign(create_canonical_payload(decision)).signature.hex()
    acceptance_store.record_acceptance(decision)

    received: list[dict[str, object]] = []

    class LocalSink(BaseHTTPRequestHandler):
        def log_message(self, format_string, *args):
            return

        def do_POST(self):
            if self.path != "/callback":
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
            received.append({
                "body": body,
                "traceparent": self.headers.get("traceparent"),
                "trace_id_header": self.headers.get("X-Trace-Id"),
                "signature": self.headers.get("X-Chalamandra-Signature"),
            })
            raw = b'{"accepted":true}'
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    sink = ThreadingHTTPServer(("127.0.0.1", 0), LocalSink)
    sink_thread = Thread(target=sink.serve_forever, daemon=True)
    sink_thread.start()
    try:
        subscription_payload = {
            "endpoint_url": f"http://127.0.0.1:{sink.server_address[1]}/callback",
            "event_types": ["result.accepted"],
        }
        status, subscription = request(
            port, "/v1/subscriptions", method="POST", payload=subscription_payload,
            headers={"Authorization": f"Bearer {make_jwt(scopes='subscriptions:write')}"},
        )
        assert status == 201
        assert subscription["status"] == "enabled"

        service_token = jwt.encode(
            {"scope": "results:publish", "sub": "catedralx-service", "exp": int(time.time()) + 300},
            service_secret,
            algorithm="HS256",
        )
        event_id = "local-sink-distribution-e2e-001"
        status, response = request(
            port, "/internal/v1/distributions", method="POST",
            payload={
                "candidate_id": candidate_id,
                "event_type": "result.accepted",
                "event_id": event_id,
                "trace_id": trace_id,
            },
            headers={"Authorization": f"Bearer {service_token}", "traceparent": incoming_traceparent},
        )
        assert status == 202
        assert response["status"] == "dispatched"
        assert response["delivered_count"] == 1
        assert response["event_id"] == event_id

        assert len(received) == 1
        message = received[0]
        assert message["body"]["event_id"] == event_id  # type: ignore[index]
        assert message["body"]["event_type"] == "result.accepted"  # type: ignore[index]
        assert message["body"]["result_id"] == candidate_id  # type: ignore[index]
        assert message["body"]["trace_id"] == trace_id  # type: ignore[index]
        assert message["trace_id_header"] == trace_id
        assert message["signature"]
        child = message["traceparent"]
        assert isinstance(child, str) and child.startswith(f"00-{trace_id}-")
        assert child.split("-")[2] != incoming_parent_id
        expected_signature = hmac.new(
            webhook_secret.encode(),
            json.dumps(message["body"], sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()
        assert hmac.compare_digest(message["signature"], expected_signature)  # type: ignore[call-overload]
    finally:
        sink.shutdown()
        sink.server_close()
        sink_thread.join(timeout=2)



def make_magis_jwt(scopes="magis:execute magis:read") -> str:
    import time
    return jwt.encode(
        {"scope": scopes, "sub": "magis-service", "exp": int(time.time()) + 300},
        TEST_MAGIS_JWT_SECRET,
        algorithm="HS256",
    )


def test_magis_product_route_fails_closed_when_model_execution_is_disabled(local_api, monkeypatch):
    port, _ = local_api
    monkeypatch.setenv("CHALAMANDRA_MAGIS_JWT_SECRET", TEST_MAGIS_JWT_SECRET)
    status, body = request(
        port,
        "/internal/v1/products/magis/requests",
        method="POST",
        payload={
            "operation_id": "f7e2cf4f-0bf2-4f56-a0da-62c7fc95e16f",
            "prompt": "synthetic test input",
        },
        headers={
            "Authorization": f"Bearer {make_magis_jwt('magis:execute')}",
            "Idempotency-Key": "magis-f7e2cf4f-0bf2-4f56-a0da-62c7fc95e16f",
        },
    )
    assert status == 403
    assert body == {"error": "magis_execution_disabled"}


def test_magis_product_token_cannot_use_general_api_identity(local_api, monkeypatch):
    port, _ = local_api
    monkeypatch.setenv("CHALAMANDRA_MAGIS_JWT_SECRET", TEST_MAGIS_JWT_SECRET)
    # A general JWT with a product-looking subject is not valid under the
    # dedicated MAGIS key; the identity is bound to its own signing secret.
    token = jwt.encode(
        {"scope": "magis:execute", "sub": "magis-service", "exp": 9999999999},
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    status, body = request(
        port,
        "/internal/v1/products/magis/requests",
        method="POST",
        payload={
            "operation_id": "f7e2cf4f-0bf2-4f56-a0da-62c7fc95e16f",
            "prompt": "synthetic test input",
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "magis-f7e2cf4f-0bf2-4f56-a0da-62c7fc95e16f",
        },
    )
    assert status == 401
    assert body == {"error": "unauthorized"}


def test_magis_product_job_status_is_scoped_to_magis_tasks(local_api, monkeypatch):
    from api import task_bus

    port, _ = local_api
    monkeypatch.setenv("CHALAMANDRA_MAGIS_JWT_SECRET", TEST_MAGIS_JWT_SECRET)
    monkeypatch.setattr(task_bus, "get_task_status", lambda _task_id: {
        "task_id": "task-magis-test",
        "status": "candidate",
        "trace_id": "1234567890abcdef1234567890abcdef",
        "result_id": "candidate-magis-test",
        "updated_at": "2026-11-03T00:00:00Z",
        "product_code": "magis",
        "payload": json.dumps({"operation_id": "f7e2cf4f-0bf2-4f56-a0da-62c7fc95e16f"}),
    })
    status, body = request(
        port,
        "/internal/v1/products/magis/jobs/task-magis-test",
        headers={"Authorization": f"Bearer {make_magis_jwt('magis:read')}"},
    )
    assert status == 200
    assert body["status"] == "candidate"
    assert body["result_id"] == "candidate-magis-test"


def test_magis_product_result_is_never_delivered_before_independent_acceptance(local_api, monkeypatch):
    from api import acceptance_store, candidate_store, task_bus

    port, _ = local_api
    monkeypatch.setenv("CHALAMANDRA_MAGIS_JWT_SECRET", TEST_MAGIS_JWT_SECRET)
    monkeypatch.setattr(task_bus, "is_magis_result", lambda _result_id: True)
    monkeypatch.setattr(candidate_store, "get_candidate", lambda result_id: {
        "candidate_id": result_id,
        "status": "candidate",
        "summary": "synthetic result",
    })
    monkeypatch.setattr(acceptance_store, "get_acceptance", lambda _result_id: None)
    headers = {"Authorization": f"Bearer {make_magis_jwt('magis:read')}"}
    status, body = request(
        port,
        "/internal/v1/products/magis/results/candidate-magis-test",
        headers=headers,
    )
    assert status == 202
    assert body["status"] == "pending_verification"

    monkeypatch.setattr(acceptance_store, "get_acceptance", lambda _result_id: {
        "outcome": "accepted",
        "decision_id": "decision-independent-1",
        "verifier_id": "test-verifier",
    })
    status, body = request(
        port,
        "/internal/v1/products/magis/results/candidate-magis-test",
        headers=headers,
    )
    assert status == 200
    assert body["delivery_status"] == "accepted"
    assert body["acceptance"]["outcome"] == "accepted"


def test_magis_service_can_enqueue_only_the_server_defined_product_contract(monkeypatch):
    from api import task_bus
    import time
    import jwt

    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("CHALAMANDRA_MAGIS_JWT_SECRET", TEST_MAGIS_JWT_SECRET)
    monkeypatch.setenv("CHALAMANDRA_MAGIS_EXECUTION_ENABLED", "true")
    monkeypatch.delenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", raising=False)
    master = Master()
    calls = []
    task = {
        "task_id": "d7a4f7a2-997f-4d4f-ae91-2d6192c4d09b",
        "run_id": "3c3e642e-7b10-4e8e-a0e5-692e8609ff21",
        "status": "queued",
        "trace_id": "1234567890abcdef1234567890abcdef",
        "product_code": "magis",
        "payload": json.dumps({"operation_id": "f7e2cf4f-0bf2-4f56-a0da-62c7fc95e16f"}),
    }
    monkeypatch.setattr(task_bus, "enqueue_task", lambda contract, idempotency_key=None: calls.append((contract, idempotency_key)) or task["task_id"])
    monkeypatch.setattr(task_bus, "get_task_status", lambda _task_id: task)

    server = create_server("127.0.0.1", 0, master=master)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    operation_id = "f7e2cf4f-0bf2-4f56-a0da-62c7fc95e16f"
    token = jwt.encode(
        {"scope": "magis:execute", "sub": "magis-service", "exp": int(time.time()) + 300},
        TEST_MAGIS_JWT_SECRET,
        algorithm="HS256",
    )
    try:
        status, body = request(
            server.server_address[1],
            "/internal/v1/products/magis/requests",
            method="POST",
            payload={"operation_id": operation_id, "prompt": "synthetic task", "context": "test"},
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": f"magis-{operation_id}",
            },
        )
        assert status == 202
        assert body["task_id"] == task["task_id"]
        assert len(calls) == 1
        contract, idem = calls[0]
        assert idem == f"magis-{operation_id}"
        assert contract["objective"] == "magis_text_analysis"
        assert contract["product_code"] == "magis"
        assert contract["required_tools"] == []
        assert contract["payload"]["operation_id"] == operation_id
        # Product-scoped enablement must not open the general model endpoint.
        general_status, general_body = request(
            server.server_address[1],
            "/v1/tasks",
            method="POST",
            payload={"task": "must remain disabled"},
            headers={"Authorization": f"Bearer {make_jwt(scopes='jobs:write')}"},
        )
        assert general_status == 403
        assert general_body["error"] == "model_calls_disabled"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _valid_market_task_payload():
    from datetime import datetime, timezone
    from conftest import sign_market_payload
    prices = [100.0 + index * 0.01 for index in range(30)]
    return sign_market_payload({
        "historical_prices": prices,
        "current_price": prices[-1],
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "controlled_api_market_fixture",
    })


def test_v1_jobs_requires_explicit_task_type_and_contract(local_api):
    port, _ = local_api
    headers = {
        "Authorization": f"Bearer {make_jwt(scopes='jobs:write')}",
        "Idempotency-Key": "jobs-contract-test-01",
    }

    status, body = request(
        port, "/v1/jobs", method="POST", payload={"payload": _valid_market_task_payload()}, headers=headers,
    )
    assert status == 400
    assert body["error"] == "task_type_required"

    status, body = request(
        port, "/v1/jobs", method="POST",
        payload={"type": "financial_analysis", "payload": _valid_market_task_payload()},
        headers={**headers, "Idempotency-Key": "jobs-contract-test-02"},
    )
    assert status == 400
    assert body["error"] == "invalid_task_contract"
    assert "ambiguous_legacy_task_type" in body["detail"]


def test_v1_jobs_enqueues_explicit_market_analysis_without_implicit_catedralx(local_api):
    port, _ = local_api
    status, body = request(
        port, "/v1/jobs", method="POST",
        payload={"type": "financial_market_analysis", "payload": _valid_market_task_payload()},
        headers={
            "Authorization": f"Bearer {make_jwt(scopes='jobs:write')}",
            "Idempotency-Key": "jobs-market-route-001",
        },
    )
    assert status == 202
    stored = task_bus.get_task_status(body["job_id"])
    assert stored is not None
    assert stored["objective"] == "financial_market_analysis"
    assert json.loads(stored["payload"])["source"] == "controlled_api_market_fixture"


def test_v1_jobs_preserves_incomplete_governance_for_catedralx_decision_result(local_api):
    port, _ = local_api
    payload = _valid_market_task_payload()
    payload["dynamic_constraints"] = {
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
    }
    payload["central_dimensions_bp"] = {
        "viability_bp": 9000,
        "human_value_bp": 9000,
        "distribution_bp": 9000,
        "resilience_bp": 9000,
    }
    status, body = request(
        port, "/v1/jobs", method="POST",
        payload={"type": "catedralx_economic_evaluation", "payload": payload},
        headers={
            "Authorization": f"Bearer {make_jwt(scopes='jobs:write')}",
            "Idempotency-Key": "jobs-economic-incomplete-1",
        },
    )
    assert status == 202
    stored = task_bus.get_task_status(body["job_id"])
    assert stored is not None
    assert stored["objective"] == "catedralx_economic_evaluation"
    assert "evidence_bp" not in json.loads(stored["payload"])["central_dimensions_bp"]


def test_general_request_cannot_claim_catedralx_human_authorization(local_api):
    port, _ = local_api
    payload = {
        "origin": "CatedralX.FEX.GOVERNANCE.economic_cycle",
        "cycle_id": "forged-cycle",
        "measurement": {}, "assessment": {}, "proposal": {},
        "authorization": {
            "approved": True, "approver": "forged-owner", "rationale": "fake",
            "identity_verified": True,
            "identity_permissions": ["economic_cycle_authorize"],
            "identity_verification": "APPROVER_IDENTITY_VERIFIER",
        },
        "execution_boundary": {},
        "_agents_ingress": {
            "subject": "catedralx-service", "scope": "orchestration:run", "verified": True,
        },
    }
    status, body = request(
        port, "/v1/request", method="POST",
        payload={
            "objective": "catedralx_economic_proposal_review",
            "payload": payload,
            "permissions": {"allowed_tools": [], "side_effects": "none"},
            "success_criteria": ["test"],
        },
        headers={
            "Authorization": f"Bearer {make_jwt(scopes='jobs:write')}",
            "Idempotency-Key": "forged-economic-proposal-001",
        },
    )
    assert status == 403
    assert body["error"] == "economic_proposal_requires_authenticated_catedralx_service"
