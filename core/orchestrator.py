"""Núcleo de ejecución de modelos del sistema Chalamandra."""

from __future__ import annotations

import asyncio

from adapters.base import ModelRequest, ModelResponse, ModelStatus
from adapters.ollama import OllamaAdapter
from registry import models as registry


class Orchestrator:
    """Núcleo de ejecución de modelos del sistema Chalamandra.

    Responsabilidades:
      - Registrar adaptadores disponibles.
      - Despachar una solicitud a un modelo concreto.
      - Ejecutar en paralelo la misma solicitud sobre varios modelos.
      - Exponer capacidades (registry) sin inventar evidencia.
    """

    def __init__(self) -> None:
        self.adapters: dict[str, object] = {
            "ollama": OllamaAdapter(),
        }

    # -----------------------------------------------------------------------
    # Registro de adaptadores
    # -----------------------------------------------------------------------

    def available_models(self) -> list[str]:
        """Adaptadores conectados al orquestador (listos para ejecutar)."""
        return sorted(self.adapters.keys())

    def register(self, name: str, adapter: object) -> None:
        """Registra un nuevo adaptador en runtime."""
        self.adapters[name] = adapter

    # -----------------------------------------------------------------------
    # Capacidades (desde el registry, no desde el orquestador)
    # -----------------------------------------------------------------------

    def capabilities(self) -> dict[str, dict]:
        """Capacidades declaradas por el registry. Sin inventar."""
        out: dict[str, dict] = {}
        for name in self.available_models():
            info = registry.get_model(name)
            if info is None:
                out[name] = {"registered": False}
                continue
            out[name] = {
                "registered": True,
                "provider": info.provider,
                "local": info.local,
                "versions": info.versions,
                "skills_verified": sorted(info.skills_verified),
                "skills_claimed": sorted(info.skills_claimed),
                "evidence": info.evidence,
            }
        return out

    def models_for_skill(self, skill: str, only_verified: bool = True) -> list[str]:
        """Modelos disponibles para una skill, filtrando por evidencia."""
        registered = set(self.available_models())
        if only_verified:
            candidates = registry.models_with_verified_skill(skill)
        else:
            candidates = registry.models_with_claimed_skill(skill)
        return sorted(registered & set(candidates))

    # -----------------------------------------------------------------------
    # Ejecución
    # -----------------------------------------------------------------------

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

        Soporta modelos duplicados: si pasas ["ollama", "ollama"],
        la clave del resultado incluirá un sufijo numérico para desambiguar.
        """
        if not models:
            return {}

        # Crear todas las tasks de una vez → ejecución paralela real
        tasks = [asyncio.create_task(self.run(m, request)) for m in models]

        # Esperar todas, capturando excepciones individualmente
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        # Construir resultado con clave única por invocación
        results: dict[str, ModelResponse] = {}
        for i, (model, resp) in enumerate(zip(models, responses)):
            key = model if models.count(model) == 1 else f"{model}#{i}"
            if isinstance(resp, BaseException):
                results[key] = ModelResponse(
                    call_id=request.call_id,
                    model=model,
                    role=request.role,
                    output="",
                    status=ModelStatus.ERROR,
                    errors=[f"orchestrator error: {resp!r}"],
                )
            else:
                results[key] = resp
        return results
