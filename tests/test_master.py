"""Tests del Control Plane Master."""

import pytest

from core.master import Master


@pytest.mark.asyncio
async def test_master_inspect():
    master = Master()

    state = master.inspect()

    assert "ollama" in state["models"]
    assert "dynamics" in state["tools"]
    assert "analysis" in state["capabilities"]["ollama"]["skills_verified"]
    assert "reasoning" in state["capabilities"]["ollama"]["skills_verified"]


def test_master_route_reasoning():
    master = Master()

    route = master.route_skill("reasoning")

    assert "ollama" in route["models"]
    assert "dynamics" in route["tools"]


@pytest.mark.asyncio
async def test_master_execute_dynamics():
    master = Master()

    result = await master.execute_tool(
        "dynamics",
        a=1.0,
        b=1.0,
        alpha=2.0,
        gamma=-1.0,
        tau=0.82,
        duration=10.0,
        dt=0.001,
    )

    assert result["status"] == "ok"
    assert "equilibrium" in result
    assert "hopf" in result
    assert "analysis" in result
