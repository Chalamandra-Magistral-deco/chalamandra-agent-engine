"""Test de humo para OllamaAdapter. Requiere Ollama corriendo en local."""

import asyncio

from adapters.base import ModelRequest, ModelRole
from adapters.ollama import OllamaAdapter


async def main() -> None:
    adapter = OllamaAdapter()
    request = ModelRequest(
        prompt="Responde solamente: CHALAMANDRA OK",
        system_prompt="Eres un asistente conciso.",
        temperature=0.0,
        max_tokens=20,
        role=ModelRole.AGENT,
    )

    response = await adapter.generate(request)

    print("--- ModelResponse ---")
    for k, v in response.model_dump().items():
        if k != "raw":
            print(f"{k}: {v}")

    assert response.status.value == "ok"
    assert "CHALAMANDRA OK" in response.output.upper()
    assert response.latency_ms > 0
    assert response.tokens_in > 0
    assert response.tokens_out > 0
    assert response.cost_usd == 0.0

    print("\nTEST OK")


if __name__ == "__main__":
    asyncio.run(main())
