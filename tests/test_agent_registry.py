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
        "coordinator", "engineer", "auditor", "sentinel", "analyst"
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
