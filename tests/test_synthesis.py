"""Test end-to-end del pipeline + synthesis."""

import asyncio

from adapters.base import ModelRequest
from core.orchestrator import Orchestrator
from pipeline import (
    run_on_models,
    summarize_comparison,
    synthesize,
)


async def main() -> None:
    orchestrator = Orchestrator()

    print("Adaptadores registrados:")
    print(orchestrator.available_models())

    assert orchestrator.available_models() == ["ollama"]

    responses = await run_on_models(
        orchestrator,
        ["ollama", "ollama"],
        ModelRequest(
            prompt="Responde solo con la palabra: CHALAMANDRA"
        ),
    )

    print(f"\nRespuestas: {len(responses)}")

    for name, response in responses.items():
        print(
            f"  {name}: "
            f"status={response.status.value} "
            f"latency={response.latency_ms}ms "
            f"output={response.output!r}"
        )

    assert set(responses.keys()) == {
        "ollama#0",
        "ollama#1",
    }

    summary = summarize_comparison(responses)

    print(f"\nConsenso: {summary['consensus_score']}")
    print(f"Outliers: {summary['outliers']}")
    print(f"Estados: {summary['statuses']}")

    for strategy in (
        "vote",
        "longest",
        "highest_confidence",
    ):
        result = synthesize(
            responses,
            strategy=strategy,
        )

        print(f"\n[{strategy}]")
        print(f"output={result.output!r}")
        print(f"contributors={result.contributors}")
        print(f"confidence={result.confidence}")
        print(f"rationale={result.rationale}")
        print(f"errors={result.errors}")

        assert result.output, f"{strategy}: output vacío"
        assert result.contributors, (
            f"{strategy}: sin contributors"
        )

    print("\nTEST SYNTHESIS OK")


if __name__ == "__main__":
    asyncio.run(main())
