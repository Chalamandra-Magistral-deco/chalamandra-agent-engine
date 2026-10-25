"""Tests unitarios para el módulo Executor."""

import pytest
from unittest.mock import AsyncMock

from adapters.base import ModelResponse, ModelStatus, ModelRole
from core.decomposer import Subtask
from core.executor import Executor


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

    assert summary.status == "partial_error"
    assert "0" in summary.subtask_results
    assert summary.subtask_results["0"].output == "Análisis completado"
    assert summary.subtask_results["0"].model_used == "ollama"
    assert summary.subtask_results["0"].agent == "analyst"
    request = fake_master.orchestrator.run.await_args.args[1]
    assert request.metadata["agent"] == "analyst"
    assert request.metadata["allowed_tools"] == []


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

    assert summary.status == "partial_error"
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


@pytest.mark.asyncio
async def test_execute_plan_validation_failure():
    fake_master = FakeMaster()

    fake_master.orchestrator.run.return_value = ModelResponse(
        call_id="validation-1",
        model="ollama",
        role=ModelRole.AGENT,
        output="NO",
        status=ModelStatus.OK,
    )

    executor = Executor(master=fake_master)

    subtasks = [
        Subtask(
            id="0",
            description="Di solo: OK",
            skill="reasoning",
            depends_on=[],
            validation_rules=["exact:OK"],
        ),
    ]

    summary = await executor.execute_plan(
        "Tarea validada",
        subtasks,
    )

    result = summary.subtask_results["0"]

    assert result.output == "NO"
    assert result.status == "validation_failed"
    assert result.validation_status == "FAIL"
    assert result.validation_errors
    assert summary.status == "partial_error"


@pytest.mark.asyncio
async def test_execute_plan_validation_success():
    fake_master = FakeMaster()

    fake_master.orchestrator.run.return_value = ModelResponse(
        call_id="validation-2",
        model="ollama",
        role=ModelRole.AGENT,
        output="OK",
        status=ModelStatus.OK,
    )

    executor = Executor(master=fake_master)

    subtasks = [
        Subtask(
            id="0",
            description="Di solo: OK",
            skill="reasoning",
            depends_on=[],
            validation_rules=["exact:OK"],
        ),
    ]

    summary = await executor.execute_plan(
        "Tarea validada",
        subtasks,
    )

    result = summary.subtask_results["0"]

    assert result.output == "OK"
    assert result.status == "ok"
    assert result.validation_status == "PASS"
    assert not result.validation_errors
    assert summary.status == "completed"


@pytest.mark.asyncio
async def test_execute_plan_propagates_cancellation() -> None:
    import asyncio

    fake_master = FakeMaster()
    fake_master.orchestrator.run.side_effect = asyncio.CancelledError()
    executor = Executor(master=fake_master)
    subtasks = [
        Subtask(
            id="cancel",
            description="Analizar datos",
            skill="analysis",
            depends_on=[],
        )
    ]

    with pytest.raises(asyncio.CancelledError):
        await executor.execute_plan("Cancelación", subtasks)


@pytest.mark.asyncio
async def test_native_tool_call_is_validated_executed_and_returned_to_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.master import Master

    master = Master()
    executed: list[tuple[str, dict]] = []

    async def fake_execute_tool(tool: str, **kwargs):
        executed.append((tool, kwargs))
        return {"status": "ok", "tool": tool}

    monkeypatch.setattr(master, "execute_tool", fake_execute_tool)
    tool_arguments = {"a": 1.0, "b": 0.5, "alpha": 0.2, "gamma": 0.1, "tau": 2.0}
    tool_message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "dynamics", "arguments": tool_arguments}}],
    }
    master.orchestrator.run = AsyncMock(
        side_effect=[
            ModelResponse(
                call_id="tool-call",
                model="ollama",
                role=ModelRole.AGENT,
                output="",
                tool_calls=[{"name": "dynamics", "arguments": tool_arguments}],
                raw={"message": tool_message},
            ),
            ModelResponse(
                call_id="final",
                model="ollama",
                role=ModelRole.AGENT,
                output="Simulación completada con resultado verificado.",
            ),
        ]
    )
    executor = Executor(master=master)
    subtask = Subtask(
        id="native-tools",
        description="Analizar EUR/USD y volatilidad",
        skill="analysis",
        depends_on=[],
    )

    result = await executor.execute_subtask(subtask, {})

    assert result.status == "validation_failed"
    assert result.output == "Simulación completada con resultado verificado."
    assert executed == [("dynamics", tool_arguments)]
    first_request = master.orchestrator.run.await_args_list[0].args[1]
    assert {tool["function"]["name"] for tool in first_request.tools} == {
        "dynamics",
        "catedralx_reasoning",
        "search_term_miner",
    }
    assert first_request.max_tokens == 256
    followup_request = master.orchestrator.run.await_args_list[1].args[1]
    assert followup_request.messages is not None
    assert followup_request.messages[0]["role"] == "system"
    assert "fuente de verdad" in followup_request.messages[0]["content"]
    assert followup_request.messages[-1]["role"] == "tool"
    assert '"ok": true' in followup_request.messages[-1]["content"]


@pytest.mark.asyncio
async def test_native_tool_call_with_invalid_arguments_is_not_executed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.master import Master

    master = Master()
    executed: list[str] = []

    async def fake_execute_tool(tool: str, **kwargs):
        executed.append(tool)
        return "should not execute"

    monkeypatch.setattr(master, "execute_tool", fake_execute_tool)
    bad_arguments = {"unknown": True}
    master.orchestrator.run = AsyncMock(
        side_effect=[
            ModelResponse(
                call_id="tool-call",
                model="ollama",
                role=ModelRole.AGENT,
                output="",
                tool_calls=[{"name": "dynamics", "arguments": bad_arguments}],
                raw={"message": {"role": "assistant", "content": "", "tool_calls": []}},
            ),
            ModelResponse(
                call_id="final",
                model="ollama",
                role=ModelRole.AGENT,
                output="La llamada fue rechazada.",
            ),
        ]
    )
    executor = Executor(master=master)
    subtask = Subtask(
        id="invalid-native-tools",
        description="Analizar EUR/USD y volatilidad",
        skill="analysis",
        depends_on=[],
    )

    await executor.execute_subtask(subtask, {})

    assert executed == []
    followup_request = master.orchestrator.run.await_args_list[1].args[1]
    assert '"ok": false' in followup_request.messages[-1]["content"]
    assert "Argumentos inválidos" in followup_request.messages[-1]["content"]
