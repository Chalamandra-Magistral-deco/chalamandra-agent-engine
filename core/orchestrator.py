"""Núcleo de ejecución de modelos del sistema Chalamandra."""

from __future__ import annotations

import asyncio
import importlib.util
import os

from adapters.base import ModelRequest, ModelResponse, Skill
from adapters.ollama import OllamaAdapter
from adapters.gemini import GeminiAdapter
from adapters.deepseek import DeepSeekAdapter
from adapters.gpt import GPTAdapter
from adapters.claude import ClaudeAdapter
from dotenv import load_dotenv
from telemetry.trace import Tracer
from registry.models import MODELS


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

        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        if anthropic_key:
            self.adapters["claude"] = ClaudeAdapter(
                api_key=anthropic_key
            )

        self.tracer = tracer or Tracer()

    def available_models(self) -> list[str]:
        return sorted(self.adapters.keys())

    def register(self, name: str, adapter: object) -> None:
        self.adapters[name] = adapter

    def adapter_states(self) -> dict[str, dict[str, object]]:
        """Clasifica el estado operativo de todos los adapters conocidos.

        Estados:
          VERIFIED     -> activo y con skills verificadas.
          ACTIVE       -> activo pero sin skills verificadas.
          REGISTERED   -> adapter implementado pero no activo.
          UNAVAILABLE  -> sin adapter implementado.
        """
        known = sorted(set(MODELS) | set(self.adapters))
        result: dict[str, dict[str, object]] = {}

        for name in known:
            module_available = (
                importlib.util.find_spec(f"adapters.{name}") is not None
            )

            active = name in self.adapters
            adapter = self.adapters.get(name)
            raw_skills = getattr(adapter, "verified_skills", ()) if adapter else ()

            verified_skills = [
                skill.value if isinstance(skill, Skill) else str(skill)
                for skill in raw_skills
            ]

            if active and verified_skills:
                state = "VERIFIED"
            elif active:
                state = "ACTIVE"
            elif module_available:
                state = "REGISTERED"
            else:
                state = "UNAVAILABLE"

            result[name] = {
                "state": state,
                "registered": module_available or active,
                "active": active,
                "verified": bool(verified_skills),
                "skills_verified": sorted(verified_skills),
            }

        return result

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
        """Devuelve adapters activos compatibles con una skill.

        only_verified=True:
            solo adapters activos con evidencia local de la skill.

        only_verified=False:
            adapters activos cuya skill esté verificada o reclamada
            en el registro de modelos.
        """
        skill_name = (
            skill.value if isinstance(skill, Skill) else skill
        )

        capabilities = self.capabilities()

        if only_verified:
            return sorted(
                name
                for name, info in capabilities.items()
                if skill_name in info["skills_verified"]
            )

        active = set(self.adapters.keys())

        compatible: set[str] = set()

        for name in active:
            info = MODELS.get(name)

            if info is None:
                # Adapter registrado dinámicamente: solo contamos
                # las skills verificadas que declara directamente.
                verified = capabilities.get(
                    name,
                    {},
                ).get("skills_verified", [])

                if skill_name in verified:
                    compatible.add(name)

                continue

            if (
                skill_name in info.skills_verified
                or skill_name in info.skills_claimed
            ):
                compatible.add(name)

        return sorted(compatible)

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
