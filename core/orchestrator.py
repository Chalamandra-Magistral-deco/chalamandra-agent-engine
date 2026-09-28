"""Núcleo de ejecución de modelos del sistema Chalamandra."""

from __future__ import annotations

import asyncio

from adapters.base import ModelRequest, ModelResponse
from adapters.ollama import OllamaAdapter


class Orchestrator:
    """Núcleo de ejecución de modelos del sistema Chalamandra.

    Responsabilidades:
      - Registrar adaptadores disponibles.
      - Despachar una solicitud a un modelo concreto.
      - Ejecutar en paralelo la misma solicitud sobre varios modelos.
    """

    def __init__(self) -> None:
        self.adapters: dict[str, object] = {
            "ollama": OllamaAdapter(),
        }

    def available_models(self) -> list[str]:
        """Devuelve los modelos actualmente conectados al orquestador."""
        return sorted(self.adapters.keys())

    def register(self, name: str, adapter: object) -> None:
        """Registra un nuevo adaptador en runtime."""
        self.adapters[name] = adapter

    async def run(
        self,
        model: str,
        request: ModelRequest,
    ) -> ModelResponse:
        """Ejecuta una solicitud mediante el adaptador indicado."""
        adapter = self.adapters.get(model)
        if adapter is None:
            raise ValueError(f"Modelo no conectado al orquestador: {model}")
        return await adapter.generate(request)  # type: ignore[attr-defined]

    async def run_parallel(
        self,
        models: list[str],
        request: ModelRequest,
    ) -> dict[str, ModelResponse]:
        """Ejecuta la misma solicitud en varios modelos en paralelo.

        Devuelve un diccionario {modelo: ModelResponse}.
        Cada ModelResponse lleva su propio call_id, latencia, tokens, etc.
        """
        tasks = {
            model: asyncio.create_task(self.run(model, request))
            for model in models
        }
        results: dict[str, ModelResponse] = {}
        for model, task in tasks.items():
            try:
                results[model] = await task
            except Exception as e:
                results[model] = ModelResponse(
                    call_id=request.call_id,
                    model=model,
                    role=request.role,
                    output="",
                    errors=[f"orchestrator error: {e!r}"],
                )
        return results
