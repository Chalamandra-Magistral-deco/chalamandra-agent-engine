"""Test de humo para Orchestrator. Requiere Ollama corriendo."""

import asyncio

from adapters.base import ModelRequest, ModelRole
from core.orchestrator import Orchestrator


async def main() -> None:
    o = Orchestrator()

    # 1. Modelos disponibles
    models = o.available_models()
    print(f"MODELOS DISPONIBLES: {models}")
    assert "ollama" in models

    # 2. Ejecución simple
    req = ModelRequest(
        prompt="Responde solo: CHALAMANDRA",
        temperature=0.0,
        max_tokens=10,
        role=ModelRole.AGENT,
    )
    r = await o.run("ollama", req)
    print(f"\nRUN simple → status={r.status.value} output={r.output!r}")
    assert r.status.value == "ok"

    # 3. Ejecución paralela (mismo modelo, dos llamadas)
    req2 = ModelRequest(
        prompt="Responde solo: OK",
        temperature=0.0,
        max_tokens=10,
        role=ModelRole.AGENT,
    )
    results = await o.run_parallel(["ollama", "ollama"], req2)
    print(f"\nRUN paralelo → {len(results)} respuestas")
    for model, resp in results.items():
        print(f"  {model}: {resp.status.value} → {resp.output!r}")
        assert resp.status.value == "ok"

    print("\nTEST ORCHESTRATOR OK")


if __name__ == "__main__":
    asyncio.run(main())
