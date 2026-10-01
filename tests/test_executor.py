"""Tests unitarios para el módulo Executor."""

import pytest
from unittest.mock import AsyncMock

from adapters.base import ModelResponse, ModelStatus, ModelRole
from core.decomposer import Subtask
from core.executor import Executor, SubtaskResult


class FakeMaster:
    def __init__(self):
        self.orchestrator = AsyncMock()

    def route_skill(self, skill: str):
        return {"models": ["ollama"], "tools": []}

    def select_model(self, skill: str, *, allow_fallback: bool = True):
        return "ollama"


def test_build_levels_dag():
    executor = Executor(master=FakeMaster())
    
    subtasks = [
        Subtask(id="0", description="Paso 1", skill="analysis", depends_on=[]),
        Subtask(id="1", description="Paso 2A", skill="reasoning", depends_on=["0"]),
        Subtask(id="2", description="Paso 2B", skill="reasoning", depends_on=["0"]),
        Subtask(id="3", description="Paso 3", skill="analysis", depends_on=["1", "2"]),
    ]

    levels = executor.build_levels(subtasks)

    assert len(levels) == 3
    assert [s.id for s in levels[0]] == ["0"]
    assert sorted([s.id for s in levels[1]]) == ["1", "2"]
    assert [s.id for s in levels[2]] == ["3"]


def test_build_levels_cycle_detection():
    executor = Executor(master=FakeMaster())
    
    subtasks = [
        Subtask(id="0", description="A", skill="reasoning", depends_on=["1"]),
        Subtask(id="1", description="B", skill="reasoning", depends_on=["0"]),
    ]

    with pytest.raises(ValueError, match="ciclo"):
        executor.build_levels(subtasks)


@pytest.mark.asyncio
async def test_execute_plan_mocked():
    fake_master = FakeMaster()
    fake_master.orchestrator.run.return_value = ModelResponse(
        call_id="test-123",
        model="ollama",
        role=ModelRole.AGENT,
        output="Análisis completado",
        status=ModelStatus.OK,
    )

    executor = Executor(master=fake_master)
    subtasks = [
        Subtask(id="0", description="Subtarea inicial", skill="analysis", depends_on=[]),
    ]

    summary = await executor.execute_plan("Tarea test", subtasks)

    assert summary.status == "completed"
    assert "0" in summary.subtask_results
    assert summary.subtask_results["0"].output == "Análisis completado"
    assert summary.subtask_results["0"].model_used == "ollama"


@pytest.mark.asyncio
async def test_execute_plan_passes_dependency_context():
    fake_master = FakeMaster()
    fake_master.orchestrator.run.side_effect = [
        ModelResponse(
            call_id="a",
            model="ollama",
            role=ModelRole.AGENT,
            output="resultado A",
            status=ModelStatus.OK,
        ),
        ModelResponse(
            call_id="b",
            model="ollama",
            role=ModelRole.AGENT,
            output="resultado B",
            status=ModelStatus.OK,
        ),
    ]

    executor = Executor(master=fake_master)

    subtasks = [
        Subtask(
            id="0",
            description="Paso A",
            skill="analysis",
            depends_on=[],
        ),
        Subtask(
            id="1",
            description="Paso B",
            skill="analysis",
            depends_on=["0"],
        ),
    ]

    summary = await executor.execute_plan("Tarea", subtasks)

    assert summary.status == "completed"
    assert len(fake_master.orchestrator.run.await_args_list) == 2
    assert "resultado A" in fake_master.orchestrator.run.await_args_list[1].args[1].prompt


def test_build_levels_missing_dependency():
    executor = Executor(master=FakeMaster())

    subtasks = [
        Subtask(
            id="0",
            description="Paso A",
            skill="analysis",
            depends_on=["99"],
        ),
    ]

    with pytest.raises(ValueError, match="dependencias inexistentes"):
        executor.build_levels(subtasks)
