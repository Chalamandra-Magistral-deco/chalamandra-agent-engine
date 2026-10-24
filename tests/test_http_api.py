"""Pruebas del servicio HTTP local, sin llamadas a modelos ni red externa."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from api.server import create_server
from core.master import Master

TEST_TOKEN = "test-api-token"


@pytest.fixture
def local_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[int, Any]]:
    monkeypatch.setenv("CHALAMANDRA_API_TOKEN", TEST_TOKEN)
    monkeypatch.delenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", raising=False)
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
        else {"Authorization": f"Bearer {TEST_TOKEN}"}
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
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TEST_TOKEN}"},
    )
    assert status == 413
    assert result["error"] == "request_too_large"

    status, result = request(
        port,
        "/v1/tool-calls",
        method="POST",
        payload={"agent": "analyst", "tool_call": {}},
        headers={"Content-Type": "text/plain", "Authorization": f"Bearer {TEST_TOKEN}"},
    )
    assert status == 415
    assert result["error"] == "content_type_must_be_json"


def test_token_protects_loopback_server(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHALAMANDRA_API_TOKEN", TEST_TOKEN)
    monkeypatch.delenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", raising=False)
    server = create_server("127.0.0.1", 0, master=Master())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, result = request(server.server_address[1], "/healthz", headers={})
        assert status == 401
        assert result["error"] == "unauthorized"

        status, result = request(
            server.server_address[1],
            "/healthz",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert status == 200
        assert result["status"] == "ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_non_loopback_requires_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CHALAMANDRA_API_TOKEN", raising=False)
    monkeypatch.delenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", raising=False)

    with pytest.raises(ValueError, match="loopback"):
        create_server("0.0.0.0", 0)

    monkeypatch.setenv("CHALAMANDRA_API_TOKEN", TEST_TOKEN)
    with pytest.raises(ValueError, match="loopback"):
        create_server("0.0.0.0", 0)


def test_model_calls_require_token_even_on_loopback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CHALAMANDRA_API_TOKEN", raising=False)
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "true")

    with pytest.raises(ValueError, match="CHALAMANDRA_API_TOKEN"):
        create_server("127.0.0.1", 0)
