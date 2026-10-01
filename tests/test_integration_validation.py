import pytest

from adapters.base import ModelResponse, ModelRole, ModelStatus
from core.decomposer import Decomposer
from core.master import Master
from core.model_router import ModelRouter
from core.skill_router import SkillRouter


class FakeOrchestrator:
    def __init__(self):
        self.responses = [
            ModelResponse(
                call_id="plan-1",
                model="ollama",
                role=ModelRole.PLANNER,
                output=(
                    '{"subtasks": ['
                    '{"description": "Di solo: OK", '
                    '"skill": "reasoning", '
                    '"depends_on": []}'
                    ']}'
                ),
                status=ModelStatus.OK,
            ),
            ModelResponse(
                call_id="exec-1",
                model="ollama",
                role=ModelRole.AGENT,
                output="OK",
                status=ModelStatus.OK,
            ),
        ]

    async def run(self, model, request):
        return self.responses.pop(0)

    def models_for_skill(self, skill, *, only_verified=True):
        skill_name = (
            skill.value
            if hasattr(skill, "value")
            else skill
        )

        if skill_name in {"reasoning", "analysis"}:
            return ["ollama"]

        return []


@pytest.mark.asyncio
async def test_master_end_to_end_validation():
    fake = FakeOrchestrator()

    master = Master()

    master.orchestrator = fake
    master.decomposer = Decomposer(fake)
    master.model_router = ModelRouter(fake)
    master.router = SkillRouter(fake, master.tools)

    result = await master.execute_task("Di solo: OK")

    assert result.status == "completed"
    assert len(result.subtask_results) == 1

    subtask_result = next(
        iter(result.subtask_results.values())
    )

    assert subtask_result.output == "OK"
    assert subtask_result.model_used == "ollama"
    assert subtask_result.status == "ok"
    assert subtask_result.validation_status == "PASS"
    assert subtask_result.validation_errors == []
