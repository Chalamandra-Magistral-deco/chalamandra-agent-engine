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

    # 2. Capacidades desde registry
    caps = o.capabilities()
    print(f"\nCAPABILITIES: {list(caps.keys())}")
    assert "ollama" in caps
    assert caps["ollama"]["registered"] is True
    skills_verified = caps["ollama"]["skills_verified"]
    assert isinstance(skills_verified, list)
    assert "reasoning" in skills_verified

    # 3. Modelos por skill (solo verificados)
    reasoners = o.models_for_skill("reasoning", only_verified=True)
    print(f"\nMODELOS con reasoning verificado: {reasoners}")
    assert "ollama" in reasoners

    # 4. Ejecución simple
    req = ModelRequest(
        prompt="Responde solo: CHALAMANDRA",
        temperature=0.0,
        max_tokens=10,
        role=ModelRole.AGENT,
    )
    r = await o.run("ollama", req)
    print(f"\nRUN simple → status={r.status.value} output={r.output!r}")
    assert r.status.value == "ok"

    # 5. Ejecución paralela con modelo duplicado
    req2 = ModelRequest(
        prompt="Responde solo: OK",
        temperature=0.0,
        max_tokens=10,
        role=ModelRole.AGENT,
    )
    results = await o.run_parallel(["ollama", "ollama"], req2)
    print(f"\nRUN paralelo → {len(results)} respuestas")
    for key, resp in results.items():
        print(f"  {key}: {resp.status.value} → {resp.output!r}")
        assert resp.status.value == "ok"
    assert len(results) == 2

    print("\nTEST ORCHESTRATOR OK")


if __name__ == "__main__":
    asyncio.run(main())
