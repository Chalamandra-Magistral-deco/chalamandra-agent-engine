"""Test formal de routing + ejecución de adapters."""

import asyncio

from core.decomposer import Subtask
from core.master import Master


async def check(
    master: Master,
    skill: str,
    expected_model: str,
    description: str,
) -> None:
    route = master.route_skill(skill)

    candidates = (
        route["models"]
        if route["models"]
        else route["fallback_models"]
    )

    print(f"\n===== {skill.upper()} =====")
    print("VERIFIED:", route["models"])
    print("FALLBACK:", route["fallback_models"])
    print("ACTIVE:", route["active_models"])
    print("CANDIDATES:", candidates)

    assert expected_model in candidates, (
        f"{skill}: {expected_model} no está en {candidates}"
    )

    subtask = Subtask(
        id=f"routing-{skill}",
        description=description,
        skill=skill,
        depends_on=[],
    )

    result = await master.executor.execute_subtask(
        subtask,
        {},
    )

    print("MODEL USED:", result.model_used)
    print("STATUS:", result.status)
    print("ERRORS:", result.errors)

    assert result.status == "ok"
    assert result.model_used == expected_model
    assert result.output
    assert not result.errors


async def main() -> None:
    master = Master()

    await check(
        master,
        "reasoning",
        "ollama",
        "Explica brevemente qué es razonamiento.",
    )

    await check(
        master,
        "research",
        "gemini",
        "Explica brevemente qué es una meta description.",
    )

    await check(
        master,
        "analysis",
        "ollama",
        "Analiza brevemente una etiqueta HTML.",
    )

    print("\nTEST ROUTING EXECUTION OK")


if __name__ == "__main__":
    asyncio.run(main())
