from __future__ import annotations

import pytest

from core.agent_registry import AgentRegistry
from core.master import Master


def test_routes_security_analysis_to_sentinel() -> None:
    registry = AgentRegistry()

    profile = registry.route(
        "Detecta vulnerabilidades en un smart contract de Ethereum",
        "analysis",
    )

    assert profile.name == "sentinel"
    assert profile.allowed_tools == ()


def test_security_code_change_routes_to_engineer() -> None:
    profile = AgentRegistry().route(
        "Corregir la validación de un contrato Solidity",
        "reasoning",
    )

    assert profile.name == "engineer"


def test_routes_audit_implementation_and_market_tasks() -> None:
    registry = AgentRegistry()

    assert registry.route("Verificar las pruebas de regresión", "reasoning").name == "auditor"
    assert registry.route("Fix a failing test", "reasoning").name == "engineer"
    assert registry.route("Implementar un módulo Python", "reasoning").name == "engineer"
    assert registry.route("Analizar precios EUR/USD y volatilidad", "analysis").name == "analyst"
    assert registry.route("Responde solo: OK", "reasoning").name == "coordinator"


def test_profile_tools_are_filtered_to_registered_tools() -> None:
    profile = AgentRegistry().get("analyst")

    assert profile.tools_available(["dynamics", "catedralx_reasoning"]) == (
        "dynamics",
        "catedralx_reasoning",
    )


@pytest.mark.asyncio
async def test_master_exposes_agents_and_routes_tasks() -> None:
    master = Master()

    state = master.inspect()
    assert {agent["name"] for agent in state["agents"]} == {
        "coordinator",
        "engineer",
        "auditor",
        "sentinel",
        "analyst",
    }

    routed = master.route_agent("Analizar EUR/USD", "analysis")
    assert routed["name"] == "analyst"
    assert "catedralx_reasoning" in routed["allowed_tools"]


@pytest.mark.asyncio
async def test_agent_tool_permissions_are_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    master = Master()

    async def fake_execute_tool(tool: str, **kwargs):
        return {"tool": tool, "kwargs": kwargs}

    monkeypatch.setattr(master, "execute_tool", fake_execute_tool)

    with pytest.raises(PermissionError, match="no tiene permiso"):
        await master.execute_agent_tool("sentinel", "search_term_miner", csv_path="demo.csv")

    result = await master.execute_agent_tool(
        "analyst",
        "search_term_miner",
        csv_path="demo.csv",
        profile="prospecting",
        cost_floor=10,
    )
    assert result == {
        "tool": "search_term_miner",
        "kwargs": {
            "csv_path": "demo.csv",
            "profile": "prospecting",
            "cost_floor": 10,
        },
    }


@pytest.mark.asyncio
async def test_coordinator_cannot_execute_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    master = Master()
    calls: list[str] = []

    async def fake_execute_tool(tool: str, **kwargs):
        calls.append(tool)
        return {"tool": tool}

    monkeypatch.setattr(master, "execute_tool", fake_execute_tool)

    with pytest.raises(PermissionError, match="no tiene permiso"):
        await master.execute_agent_tool_call(
            "coordinator",
            {"name": "dynamics", "arguments": {}},
        )

    assert calls == []


@pytest.mark.asyncio
async def test_structured_tool_call_executes_only_registered_authorized_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master = Master()
    calls: list[tuple[str, dict]] = []

    async def fake_execute_tool(tool: str, **kwargs):
        calls.append((tool, kwargs))
        return {"tool": tool, "kwargs": kwargs}

    monkeypatch.setattr(master, "execute_tool", fake_execute_tool)
    result = await master.execute_agent_tool_call(
        "analyst",
        {
            "name": "search_term_miner",
            "arguments": {"csv_path": "demo.csv", "profile": "prospecting", "cost_floor": 10},
        },
    )
    assert result == {
        "tool": "search_term_miner",
        "kwargs": {"csv_path": "demo.csv", "profile": "prospecting", "cost_floor": 10},
    }
    assert calls == [
        ("search_term_miner", {"csv_path": "demo.csv", "profile": "prospecting", "cost_floor": 10})
    ]


@pytest.mark.asyncio
async def test_structured_tool_call_rejects_unauthorized_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master = Master()
    calls: list[str] = []

    async def fake_execute_tool(tool: str, **kwargs):
        calls.append(tool)
        return "should not execute"

    monkeypatch.setattr(master, "execute_tool", fake_execute_tool)
    with pytest.raises(PermissionError, match="no tiene permiso"):
        await master.execute_agent_tool_call(
            "sentinel",
            {"name": "search_term_miner", "arguments": {"csv_path": "demo.csv"}},
        )
    assert calls == []


@pytest.mark.asyncio
async def test_structured_tool_call_rejects_malformed_envelope_and_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master = Master()
    calls: list[str] = []

    async def fake_execute_tool(tool: str, **kwargs):
        calls.append(tool)
        return None

    monkeypatch.setattr(master, "execute_tool", fake_execute_tool)
    with pytest.raises(ValueError, match="únicamente name y arguments"):
        await master.execute_agent_tool_call(
            "analyst",
            {"name": "dynamics", "arguments": {}, "execute": True},
        )
    with pytest.raises(TypeError, match="arguments debe ser un objeto"):
        await master.execute_agent_tool_call("analyst", {"name": "dynamics", "arguments": "{}"})
    with pytest.raises(ValueError, match="Argumentos inválidos"):
        await master.execute_agent_tool_call(
            "analyst", {"name": "dynamics", "arguments": {"unknown_arg": True}}
        )
    assert calls == []


@pytest.mark.asyncio
async def test_structured_tool_call_rejects_unknown_tool_and_extra_envelope_fields() -> None:
    master = Master()
    with pytest.raises(ValueError, match="Herramienta no registrada"):
        await master.execute_agent_tool_call("analyst", {"name": "shell", "arguments": {}})
    with pytest.raises(ValueError, match="únicamente name y arguments"):
        await master.execute_agent_tool_call(
            "analyst", {"name": "dynamics", "arguments": {}, "agent": "sentinel"}
        )


@pytest.mark.parametrize(
    ("description", "skill", "expected"),
    [
        ("Review this code; do not modify it", "coding", "auditor"),
        ("Revisa este código, no lo modifiques", "coding", "auditor"),
        ("Audit this Solidity smart contract; do not modify it", "coding", "sentinel"),
        ("Review and modify this code", "reasoning", "engineer"),
        (
            "Review this code and implement a fix; do not modify unrelated tests",
            "coding",
            "engineer",
        ),
    ],
)
def test_read_only_audit_routes_to_auditor_before_implementation_keyword(
    description: str, skill: str, expected: str
) -> None:
    assert AgentRegistry().route(description, skill).name == expected
