"""Ejecución paralela sobre modelos realmente conectados al Orchestrator."""

from __future__ import annotations

from adapters.base import ModelRequest, ModelResponse
from core.orchestrator import Orchestrator


async def run_on_models(
    orchestrator: Orchestrator,
    models: list[str],
    request: ModelRequest,
) -> dict[str, ModelResponse]:
    """Ejecuta únicamente modelos conectados al Orchestrator."""

    connected = set(orchestrator.available_models())

    executable = [
        model for model in models
        if model in connected
    ]

    if not executable:
        raise ValueError(
            f"Ninguno de los modelos solicitados está conectado. "
            f"Solicitados={models!r}; conectados={sorted(connected)!r}"
        )

    return await orchestrator.run_parallel(
        executable,
        request,
    )
