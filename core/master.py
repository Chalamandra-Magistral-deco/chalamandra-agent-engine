"""Master: punto de entrada del sistema de agentes."""

from __future__ import annotations

from adapters.base import Skill
from core.decomposer import Decomposer
from core.executor import Executor
from core.orchestrator import Orchestrator
from core.skill_router import SkillRouter
from core.model_router import ModelRouter
from core.tool_registry import ToolRegistry
from pipeline.dynamics_tool import analyze_dynamics


class Master:
    """Control plane central de Chalamandra Agent Engine."""

    def __init__(self) -> None:
        self.orchestrator = Orchestrator()
        self.tools = ToolRegistry()
        self.router = SkillRouter(self.orchestrator, self.tools)
        self.model_router = ModelRouter(self.orchestrator)
        self.decomposer = Decomposer(self.orchestrator)
        self.executor = Executor(self)

        self._register_core_tools()

    def _register_core_tools(self) -> None:
        self.tools.register(
            name="dynamics",
            description="Analiza y simula sistemas dinámicos con retardo.",
            skills=("analysis", "reasoning"),
            handler=analyze_dynamics,
        )

    def inspect(self) -> dict:
        return {
            "models": self.orchestrator.available_models(),
            "tools": self.tools.available(),
            "capabilities": self.orchestrator.capabilities(),
            # Only source/status metadata is exposed, never credential values.
            "credential_status": dict(
                getattr(self.orchestrator, "credential_status", {})
            ),
        }

    def route_skill(self, skill: str | Skill) -> dict[str, list[str]]:
        return self.router.route(skill)

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

            steps.append({
                "subtask": st.to_dict(),
                "models": models,
                "verified_models": self.model_router.verified(st.skill),
                "fallback_models": self.model_router.fallback(st.skill),
                "active_models": self.model_router.active(st.skill),
                "tools": route["tools"],
            })

        return {
            "task": task,
            "subtask_count": len(subtasks),
            "steps": steps,
        }

    async def execute_tool(self, tool: str, **kwargs):
        return await self.tools.execute(tool, **kwargs)

    async def execute_task(self, task: str):
        """Descompone y ejecuta una tarea completa mediante el Executor DAG."""
        subtasks = await self.decomposer.decompose(task)
        return await self.executor.execute_plan(task, subtasks)
