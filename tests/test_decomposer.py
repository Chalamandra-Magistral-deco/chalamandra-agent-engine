"""Tests deterministas del Task Decomposer."""

import json
from types import SimpleNamespace

import pytest

from core.decomposer import Decomposer


class FakeOrchestrator:
    async def run(self, model, request):
        return SimpleNamespace(
            status=SimpleNamespace(value="ok"),
            output="""{
              "subtasks": [
                {
                  "description": "Analizar meta description",
                  "skill": "analysis",
                  "depends_on": []
                },
                {
                  "description": "Analizar canonical",
                  "skill": "analysis",
                  "depends_on": ["0"]
                }
              ]
            }""",
        )


@pytest.mark.asyncio
async def test_decompose():
    dec = Decomposer(FakeOrchestrator())

    task = (
        "Analiza el index.html de un sitio y dime "
        "si tiene meta description y canonical."
    )

    subtasks = await dec.decompose(task)

    assert len(subtasks) == 2
    assert subtasks[0].skill == "analysis"
    assert subtasks[1].skill == "analysis"
    assert subtasks[1].depends_on == [subtasks[0].id]


def test_infer_exact_validation_rule():
    from core.decomposer import _infer_validation_rules

    assert _infer_validation_rules("Di solo: OK") == ["exact:OK"]


def test_infer_exact_validation_rule_case_insensitive():
    from core.decomposer import _infer_validation_rules

    assert _infer_validation_rules("Responde solo: FUNCIONA") == [
        "exact:FUNCIONA"
    ]


def test_infer_exact_validation_rule_quoted():
    from core.decomposer import _infer_validation_rules

    assert _infer_validation_rules('Di solo: "OK"') == ["exact:OK"]


def test_no_inference_when_instruction_is_not_explicit():
    from core.decomposer import _infer_validation_rules

    assert _infer_validation_rules("Analiza el sistema") == []


@pytest.mark.asyncio
async def test_single_subtask_preserves_original_task_details():
    class SingleSubtaskOrchestrator:
        async def run(self, model, request):
            return SimpleNamespace(
                status=SimpleNamespace(value="ok"),
                output=json.dumps({
                    "subtasks": [{
                        "description": "Usa dynamics con los valores indicados.",
                        "skill": "analysis",
                        "depends_on": [],
                    }]
                }),
            )

    task = (
        "Usa dynamics con a=0.2, b=0.1, alpha=0.5, gamma=0.8, "
        "tau=1.0, duration=5 y dt=0.1. Omite K; no lo envíes como null."
    )
    decomposer = Decomposer(SingleSubtaskOrchestrator())

    subtasks = await decomposer.decompose(task)

    assert len(subtasks) == 1
    assert task in subtasks[0].description
    assert "alpha=0.5" in subtasks[0].description
    assert "no lo envíes como null" in subtasks[0].description


@pytest.mark.asyncio
async def test_decompose_drops_self_dependency():
    class SelfDependencyOrchestrator:
        async def run(self, model, request):
            return SimpleNamespace(
                status=SimpleNamespace(value="ok"),
                output=json.dumps({
                    "subtasks": [{
                        "description": "Analizar un sistema",
                        "skill": "analysis",
                        "depends_on": ["0"],
                    }]
                }),
            )

    decomposer = Decomposer(SelfDependencyOrchestrator())
    subtasks = await decomposer.decompose("Analizar un sistema")

    assert len(subtasks) == 1
    assert subtasks[0].depends_on == []
