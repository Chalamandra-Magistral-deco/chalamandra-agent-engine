"""Master: punto de entrada del sistema de agentes."""

from __future__ import annotations

import inspect
import types
import typing
from collections.abc import Mapping, Sequence
from typing import Any, get_args, get_origin, get_type_hints

from pydantic import TypeAdapter

from adapters.base import Skill
from core.decomposer import Decomposer
from core.agent_registry import AgentRegistry
from core.executor import Executor
from core.orchestrator import Orchestrator
from core.skill_router import SkillRouter
from core.model_router import ModelRouter
from core.tool_registry import ToolRegistry
from pipeline.dynamics_tool import analyze_dynamics
from pipeline.catedralx_tool import catedralx_reasoning_tool
from pipeline.search_term_miner import mine_search_terms_tool
from pipeline.lead_classifier import classify_lead


class Master:
    """Control plane central de Chalamandra Agent Engine."""

    def __init__(self) -> None:
        self.orchestrator = Orchestrator()
        self.tools = ToolRegistry()
        self.router = SkillRouter(self.orchestrator, self.tools)
        self.model_router = ModelRouter(self.orchestrator)
        self.decomposer = Decomposer(self.orchestrator)
        self.agent_registry = AgentRegistry()
        self.executor = Executor(self)

        self._register_core_tools()

    def _register_core_tools(self) -> None:
        self.tools.register(
            name="dynamics",
            description="Analiza y simula sistemas dinámicos con retardo.",
            skills=("analysis", "reasoning"),
            handler=analyze_dynamics,
        )

        self.tools.register(
            name="search_term_miner",
            description=(
                "Analiza un CSV autorizado de términos de búsqueda de Ads; "
                "genera candidatos de keywords, negativas y n-grams sin "
                "modificar campañas."
            ),
            skills=("analysis", "research", "data"),
            handler=mine_search_terms_tool,
        )

        self.tools.register(
            name="catedralx_reasoning",
            description=(
                "Ejecuta el motor financiero de CatedralX con al menos 30 precios "
                "válidos, o usa data_source='frankfurter_daily' para obtener "
                "hasta 500 observaciones de referencia EUR/USD desde Frankfurter. "
                "Devuelve clasificación buy/sell/hold, impulso, volatilidad, drawdown "
                "y diagnósticos complementarios. signal_strength es una heurística, "
                "no una probabilidad. Solo análisis: trading permanece deshabilitado "
                "y no se crean ni ejecutan órdenes."
            ),
            skills=("analysis", "reasoning"),
            handler=catedralx_reasoning_tool,
        )

        self.tools.register(
            name="lead_classifier",
            description=(
                "Clasifica un lead del formulario de contacto: tipo, urgencia "
                "y propuesta sugerida. Determinista, sin LLM, sin red. "
                "Guarda en JSONL local. No envía respuesta al cliente."
            ),
            skills=("analysis", "research", "data"),
            handler=classify_lead,
        )

    def inspect(self) -> dict:
        return {
            "models": self.orchestrator.available_models(),
            "tools": self.tools.available(),
            "agents": self.agent_registry.available(self.tools.available()),
            "capabilities": self.orchestrator.capabilities(),
            # Only source/status metadata is exposed, never credential values.
            "credential_status": dict(getattr(self.orchestrator, "credential_status", {})),
        }

    def route_skill(self, skill: str | Skill) -> dict[str, list[str]]:
        return self.router.route(skill)

    def route_agent(self, description: str, skill: str = "reasoning") -> dict:
        """Asigna una subtask a un perfil y filtra sus herramientas autorizadas."""
        profile = self.agent_registry.route(description, skill)
        return profile.to_dict(self.tools.available())

    def route_models(
        self,
        skill: str | Skill,
        *,
        allow_fallback: bool = True,
    ) -> list[str]:
        return self.model_router.route(
            skill,
            allow_fallback=allow_fallback,
        )

    def select_model(
        self,
        skill: str | Skill,
        *,
        allow_fallback: bool = True,
    ) -> str | None:
        return self.model_router.select(
            skill,
            allow_fallback=allow_fallback,
        )

    async def plan(self, task: str) -> dict:
        """Descompone una tarea y enruta cada subtask a modelos/tools."""
        subtasks = await self.decomposer.decompose(task)

        steps = []
        for st in subtasks:
            route = self.router.route(st.skill)
            models = self.model_router.route(st.skill)
            profile = self.agent_registry.route(st.description, st.skill)
            agent_data = profile.to_dict(self.tools.available())
            allowed_tools = set(agent_data["allowed_tools"])
            routed_tools = [name for name in route["tools"] if name in allowed_tools]

            steps.append(
                {
                    "subtask": st.to_dict(),
                    "agent": agent_data,
                    "models": models,
                    "verified_models": self.model_router.verified(st.skill),
                    "fallback_models": self.model_router.fallback(st.skill),
                    "active_models": self.model_router.active(st.skill),
                    "tools": routed_tools,
                }
            )

        return {
            "task": task,
            "subtask_count": len(subtasks),
            "steps": steps,
        }

    @staticmethod
    def _json_schema_type(annotation: Any) -> dict[str, Any]:
        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin in (list, tuple, set, Sequence):
            item = args[0] if args else str
            return {"type": "array", "items": Master._json_schema_type(item)}
        if origin in (types.UnionType, typing.Union):
            non_none = [arg for arg in args if arg is not type(None)]
            return (
                Master._json_schema_type(non_none[0]) if len(non_none) == 1 else {"type": "string"}
            )
        if annotation is str:
            return {"type": "string"}
        if annotation is bool:
            return {"type": "boolean"}
        if annotation is int:
            return {"type": "integer"}
        if annotation is float:
            return {"type": "number"}
        if annotation is dict or origin is dict:
            return {"type": "object", "additionalProperties": True}
        return {"type": "string"}

    def tool_schemas(self, names: list[str]) -> list[dict[str, Any]]:
        """Construye esquemas nativos para el subconjunto autorizado."""
        schemas = []
        for name in names:
            spec = self.tools.get(name)
            signature = inspect.signature(spec.handler)
            try:
                hints = get_type_hints(spec.handler)
            except (NameError, TypeError):
                hints = {}
            properties: dict[str, Any] = {}
            required: list[str] = []
            for parameter_name, parameter in signature.parameters.items():
                if parameter.kind in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                ):
                    continue
                annotation = hints.get(parameter_name, parameter.annotation)
                properties[parameter_name] = self._json_schema_type(annotation)
                if parameter.default is inspect.Parameter.empty:
                    required.append(parameter_name)
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                }
            )
        return schemas

    async def execute_tool(self, tool: str, **kwargs):
        """Invoca una herramienta desde el límite de confianza de la aplicación."""
        return await self.tools.execute(tool, **kwargs)

    async def execute_agent_tool_call(self, agent: str, tool_call: Mapping[str, Any]) -> Any:
        """Valida y ejecuta una llamada estructurada en el límite de confianza.

        El modelo solo puede proponer ``{"name": ..., "arguments": {...}}``.
        El host comprueba registro, permisos del perfil y contrato de argumentos
        antes de ejecutar; texto libre del modelo nunca se interpreta como código.
        """
        if not isinstance(tool_call, Mapping):
            raise TypeError("tool_call debe ser un objeto con name y arguments.")
        if set(tool_call) != {"name", "arguments"}:
            raise ValueError("tool_call debe contener únicamente name y arguments.")

        tool_name = tool_call.get("name")
        arguments = tool_call.get("arguments")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_call.name debe ser un nombre no vacío.")
        if not isinstance(arguments, Mapping):
            raise TypeError("tool_call.arguments debe ser un objeto.")

        # Confirmar que existe antes de comprobar autorización evita ejecutar
        # nombres arbitrarios y produce un error controlado para herramientas ausentes.
        spec = self.tools.get(tool_name)
        profile = self.agent_registry.get(agent)
        registered_allowed = set(profile.tools_available(self.tools.available()))
        if tool_name not in registered_allowed:
            raise PermissionError(
                f"El agente {profile.name!r} no tiene permiso para usar {tool_name!r}."
            )

        try:
            signature = inspect.signature(spec.handler)
            signature.bind(**dict(arguments))
            try:
                hints = get_type_hints(spec.handler)
            except (NameError, TypeError):
                hints = {}
            for argument_name, argument_value in arguments.items():
                parameter = signature.parameters[argument_name]
                annotation = hints.get(argument_name, parameter.annotation)
                if annotation is not inspect.Parameter.empty:
                    TypeAdapter(annotation).validate_python(argument_value)
        except Exception as exc:
            if (
                isinstance(exc, (TypeError, ValueError, KeyError))
                or type(exc).__name__ == "ValidationError"
            ):
                raise ValueError(
                    f"Argumentos inválidos para la herramienta {tool_name!r}: {exc}"
                ) from exc
            raise

        return await self.execute_tool(tool_name, **dict(arguments))

    async def execute_agent_tool(self, agent: str, tool: str, **kwargs):
        """Compatibilidad: adapta la llamada antigua al contrato estructurado."""
        return await self.execute_agent_tool_call(
            agent,
            {"name": tool, "arguments": kwargs},
        )

    async def execute_task(self, task: str):
        """Descompone y ejecuta una tarea completa mediante el Executor DAG."""
        subtasks = await self.decomposer.decompose(task)
        return await self.executor.execute_plan(task, subtasks)
