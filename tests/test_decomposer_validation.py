import pytest

from adapters.base import ModelResponse, ModelRole, ModelStatus
from core.decomposer import Decomposer


class FakeOrchestrator:
    async def run(self, model, request):
        return ModelResponse(
            call_id="decomposer-validation",
            model=model,
            role=ModelRole.PLANNER,
            output='{"subtasks": [{"description": "Di solo: OK", "skill": "reasoning", "depends_on": []}]}',
            status=ModelStatus.OK,
        )


class FailingOrchestrator:
    async def run(self, model, request):
        return ModelResponse(
            call_id="decomposer-fallback",
            model=model,
            role=ModelRole.PLANNER,
            output="",
            status=ModelStatus.ERROR,
            errors=["test failure"],
        )


@pytest.mark.asyncio
async def test_decomposer_attaches_validation_rule():
    decomposer = Decomposer(FakeOrchestrator())

    subtasks = await decomposer.decompose("Di solo: OK")

    assert len(subtasks) == 1
    assert subtasks[0].description == "Di solo: OK"
    assert subtasks[0].skill == "reasoning"
    assert subtasks[0].validation_rules == ["exact:OK"]


@pytest.mark.asyncio
async def test_decomposer_fallback_preserves_validation_rule():
    decomposer = Decomposer(FailingOrchestrator())

    subtasks = await decomposer.decompose("Responde solo: FUNCIONA")

    assert len(subtasks) == 1
    assert subtasks[0].description == "Responde solo: FUNCIONA"
    assert subtasks[0].validation_rules == ["exact:FUNCIONA"]
