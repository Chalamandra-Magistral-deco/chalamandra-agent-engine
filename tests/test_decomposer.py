"""Unit test determinista del Task Decomposer."""

import asyncio
from types import SimpleNamespace

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


async def main() -> None:
    dec = Decomposer(FakeOrchestrator())

    task = (
        "Analiza el index.html de un sitio y dime "
        "si tiene meta description y canonical."
    )

    subtasks = await dec.decompose(task)

    print(f"SUBTASKS: {len(subtasks)}")

    for s in subtasks:
        print(
            f"  [{s.id}] "
            f"skill={s.skill:12} "
            f"deps={s.depends_on}"
        )
        print(f"       {s.description}")

    assert len(subtasks) == 2
    assert subtasks[0].skill == "analysis"
    assert subtasks[1].skill == "analysis"
    assert subtasks[1].depends_on == [subtasks[0].id]

    print("\nTEST DECOMPOSER OK")


if __name__ == "__main__":
    asyncio.run(main())
