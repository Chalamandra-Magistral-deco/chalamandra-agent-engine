"""Núcleo de ejecución de modelos del sistema Chalamandra."""

from __future__ import annotations

import asyncio
import os

from adapters.base import ModelRequest, ModelResponse, Skill
from adapters.ollama import OllamaAdapter
from adapters.gemini import GeminiAdapter
from adapters.deepseek import DeepSeekAdapter
from adapters.gpt import GPTAdapter
from dotenv import load_dotenv
from telemetry.trace import Tracer


load_dotenv("config/.env")


class Orchestrator:
    """Núcleo de ejecución."""

    def __init__(self, tracer: Tracer | None = None) -> None:
        self.adapters: dict[str, object] = {
            "ollama": OllamaAdapter(),
        }

        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key:
            self.adapters["gemini"] = GeminiAdapter(api_key=gemini_key)

        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        if deepseek_key:
            self.adapters["deepseek"] = DeepSeekAdapter(
                api_key=deepseek_key
            )

        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            self.adapters["gpt"] = GPTAdapter(
                api_key=openai_key
            )

        self.tracer = tracer or Tracer()

    def available_models(self) -> list[str]:
        return sorted(self.adapters.keys())

    def register(self, name: str, adapter: object) -> None:
        self.adapters[name] = adapter

    def capabilities(self) -> dict[str, dict[str, object]]:
        """Expone las capacidades verificadas de los adaptadores registrados."""
        result: dict[str, dict[str, object]] = {}

        for name, adapter in self.adapters.items():
            raw_skills = getattr(adapter, "verified_skills", ())

            skills_verified = [
                skill.value if isinstance(skill, Skill) else str(skill)
                for skill in raw_skills
            ]

            result[name] = {
                "registered": True,
                "skills_verified": sorted(skills_verified),
            }

        return result

    def models_for_skill(
        self,
        skill: str | Skill,
        *,
        only_verified: bool = True,
    ) -> list[str]:
        """Devuelve modelos registrados compatibles con una skill."""
        skill_name = (
            skill.value if isinstance(skill, Skill) else skill
        )

        if not only_verified:
            return sorted(self.adapters.keys())

        capabilities = self.capabilities()

        return sorted(
            name
            for name, info in capabilities.items()
            if skill_name in info["skills_verified"]
        )

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
        """Ejecuta modelos en paralelo sin mutar el registro de adaptadores.

        Las claves de `results` representan instancias de ejecución:
        ollama#0, ollama#1, etc.

        El registro canónico sigue conteniendo únicamente:
        ollama, gemini, claude, etc.
        """
        tasks: list[tuple[str, str, asyncio.Task[ModelResponse]]] = []

        for index, model in enumerate(models):
            result_key = f"{model}#{index}"
            task = asyncio.create_task(self.run(model, request))
            tasks.append((result_key, model, task))

        results: dict[str, ModelResponse] = {}

        for result_key, model, task in tasks:
            try:
                results[result_key] = await task
            except Exception as e:
                results[result_key] = ModelResponse(
                    call_id=request.call_id,
                    model=model,
                    role=request.role,
                    output="",
                    errors=[f"orchestrator error: {e!r}"],
                )
                self.tracer.record(results[result_key])

        return results
