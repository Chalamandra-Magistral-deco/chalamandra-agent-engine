"""Test end-to-end del pipeline: parallel + comparison."""

import asyncio

from core.orchestrator import Orchestrator
from pipeline.comparison import summarize_comparison
from pipeline.parallel import run_on_models


async def main() -> None:
    o = Orchestrator()

    # Con un solo modelo conectado, simulamos multiplicidad
    # (cuando haya más, basta con cambiar esta lista)
    models = ["ollama", "ollama"]

    prompt = "Responde solo con la palabra: CHALAMANDRA"

    print(f"Ejecutando en {len(models)} modelos...")
    responses = await run_on_models(o, prompt, models)

    print(f"\nRespuestas: {len(responses)}")
    for name, r in responses.items():
        print(f"  {name}: status={r.status.value} latency={r.latency_ms}ms output={r.output!r}")

    summary = summarize_comparison(responses)
    print(f"\nConsenso: {summary['consensus_score']}")
    print(f"Outliers: {summary['outliers']}")
    print(f"Estados:  {summary['statuses']}")

    assert len(responses) == 2
    assert all(r.status.value == "ok" for r in responses.values())
    assert 0.0 <= summary["consensus_score"] <= 1.0

    print("\nTEST PIPELINE OK")


if __name__ == "__main__":
    asyncio.run(main())
