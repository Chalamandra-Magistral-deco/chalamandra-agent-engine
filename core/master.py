"""Master: punto de entrada del sistema de agentes."""

from __future__ import annotations

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

    def inspect(self) -> dict:
        return {
            "models": self.orchestrator.available_models(),
            "tools": self.tools.available(),
            "agents": self.agent_registry.available(self.tools.available()),
            "capabilities": self.orchestrator.capabilities(),
            # Only source/status metadata is exposed, never credential values.
            "credential_status": dict(
                getattr(self.orchestrator, "credential_status", {})
            ),
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

            steps.append({
                "subtask": st.to_dict(),
                "agent": agent_data,
                "models": models,
                "verified_models": self.model_router.verified(st.skill),
                "fallback_models": self.model_router.fallback(st.skill),
                "active_models": self.model_router.active(st.skill),
                "tools": routed_tools,
            })

        return {
            "task": task,
            "subtask_count": len(subtasks),
            "steps": steps,
        }

    async def execute_tool(self, tool: str, **kwargs):
        """Invoca una herramienta desde el límite de confianza de la aplicación."""
        return await self.tools.execute(tool, **kwargs)

    async def execute_agent_tool(self, agent: str, tool: str, **kwargs):
        """Invoca una herramienta únicamente si el perfil asignado la permite."""
        profile = self.agent_registry.get(agent)
        if not self.agent_registry.tool_is_allowed(profile.name, tool):
            raise PermissionError(
                f"El agente {profile.name!r} no tiene permiso para usar {tool!r}."
            )
        return await self.execute_tool(tool, **kwargs)

    async def execute_task(self, task: str):
        """Descompone y ejecuta una tarea completa mediante el Executor DAG."""
        subtasks = await self.decomposer.decompose(task)
        return await self.executor.execute_plan(task, subtasks)
