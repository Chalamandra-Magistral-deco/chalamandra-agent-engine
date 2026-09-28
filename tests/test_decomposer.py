"""Tests deterministas del Task Decomposer."""

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
