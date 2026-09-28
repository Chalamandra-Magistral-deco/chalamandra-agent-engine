"""Ejecución en paralelo de un prompt sobre múltiples modelos.

Envuelve Orchestrator.run_parallel y normaliza el resultado.
No hace routing: recibe la lista de modelos ya decidida.
"""

from __future__ import annotations

from adapters.base import ModelRequest, ModelResponse
from core.orchestrator import Orchestrator


async def run_on_models(
    orchestrator: Orchestrator,
    prompt: str,
    models: list[str],
    *,
    system_prompt: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 512,
    timeout_seconds: float = 60.0,
) -> dict[str, ModelResponse]:
    """Ejecuta el mismo prompt en N modelos en paralelo.

    Devuelve {modelo: ModelResponse}. Cada respuesta mantiene su telemetría.
    """
    request = ModelRequest(
        prompt=prompt,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
    return await orchestrator.run_parallel(models, request)
