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


@pytest.mark.asyncio
async def test_master_initialization():
    master = Master()

    assert master is not None
    assert master.executor is not None


@pytest.mark.asyncio
async def test_master_execute_task():
    master = Master()

    class FakeDecomposer:
        async def decompose(self, task):
            from core.decomposer import Subtask

            return [
                Subtask(
                    id="0",
                    description="Tarea de prueba",
                    skill="analysis",
                    depends_on=[],
                )
            ]

    class FakeExecutor:
        async def execute_plan(self, task, subtasks):
            return {
                "task": task,
                "status": "completed",
                "step_count": len(subtasks),
            }

    master.decomposer = FakeDecomposer()
    master.executor = FakeExecutor()

    result = await master.execute_task("Ejecutar prueba")

    assert result["status"] == "completed"
    assert result["step_count"] == 1



@pytest.mark.asyncio
async def test_master_rejects_unregistered_task_tool_before_decomposition():
    master = Master()

    class MustNotDecompose:
        async def decompose(self, task):
            raise AssertionError("Invalid allowlist must fail before planning")

    master.decomposer = MustNotDecompose()

    with pytest.raises(ValueError, match="Herramientas no registradas"):
        await master.execute_task("Review economic proposal", allowed_tools=["shell_arbitrary"])
