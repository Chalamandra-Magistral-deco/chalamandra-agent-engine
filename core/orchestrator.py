"""Núcleo de ejecución de modelos del sistema Chalamandra."""

from __future__ import annotations

import asyncio

from adapters.base import ModelRequest, ModelResponse
from adapters.ollama import OllamaAdapter
from telemetry.trace import Tracer


class Orchestrator:
    """Núcleo de ejecución."""

    def __init__(self, tracer: Tracer | None = None) -> None:
        self.adapters: dict[str, object] = {
            "ollama": OllamaAdapter(),
        }
        self.tracer = tracer or Tracer()

    def available_models(self) -> list[str]:
        return sorted(self.adapters.keys())

    def register(self, name: str, adapter: object) -> None:
        self.adapters[name] = adapter

    async def run(self, model: str, request: ModelRequest) -> ModelResponse:
        adapter = self.adapters.get(model)
        if adapter is None:
            raise ValueError(f"Modelo no conectado al orquestador: {model}")
        response = await adapter.generate(request)  # type: ignore[attr-defined]
        self.tracer.record(response)
        return response

    async def run_parallel(
        self,
        models: list[str],
        request: ModelRequest,
    ) -> dict[str, ModelResponse]:
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
                self.tracer.record(results[model])
        return results
