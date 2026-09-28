"""Test end-to-end del pipeline: parallel + comparison."""

import asyncio

from adapters.base import ModelRequest, ModelRole
from core.orchestrator import Orchestrator
from pipeline.comparison import summarize_comparison
from pipeline.parallel import run_on_models


async def main() -> None:
    o = Orchestrator()

    # Dos instancias del mismo modelo para probar paralelismo real.
    models = ["ollama", "ollama"]

    request = ModelRequest(
        call_id="pipeline-test",
        prompt="Responde solo con la palabra: CHALAMANDRA",
        role=ModelRole.AGENT,
    )

    print(f"Ejecutando en {len(models)} modelos/instancias...")
    responses = await run_on_models(
        o,
        models,
        request,
    )

    print(f"\nRespuestas: {len(responses)}")
    for name, r in responses.items():
        print(
            f"  {name}: "
            f"status={r.status.value} "
            f"latency={r.latency_ms}ms "
            f"output={r.output!r}"
        )

    summary = summarize_comparison(responses)

    print(f"\nConsenso: {summary['consensus_score']}")
    print(f"Outliers: {summary['outliers']}")
    print(f"Estados:  {summary['statuses']}")

    assert len(responses) == 2
    assert set(responses.keys()) == {"ollama#0", "ollama#1"}
    assert all(r.status.value == "ok" for r in responses.values())
    assert all("CHALAMANDRA" in r.output for r in responses.values())
    assert 0.0 <= summary["consensus_score"] <= 1.0

    print("\nTEST PIPELINE OK")


if __name__ == "__main__":
    asyncio.run(main())
