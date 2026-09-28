"""Master: punto de entrada del sistema de agentes."""

from __future__ import annotations

from adapters.base import Skill
from core.orchestrator import Orchestrator
from core.skill_router import SkillRouter
from core.tool_registry import ToolRegistry
from pipeline.dynamics_tool import analyze_dynamics


class Master:
    """Control plane central de Chalamandra Agent Engine."""

    def __init__(self) -> None:
        self.orchestrator = Orchestrator()
        self.tools = ToolRegistry()
        self.router = SkillRouter(
            self.orchestrator,
            self.tools,
        )

        self._register_core_tools()

    def _register_core_tools(self) -> None:
        self.tools.register(
            name="dynamics",
            description=(
                "Analiza y simula sistemas dinámicos "
                "con retardo."
            ),
            skills=("analysis", "reasoning"),
            handler=analyze_dynamics,
        )

    def inspect(self) -> dict:
        """Estado del sistema de control."""
        return {
            "models": self.orchestrator.available_models(),
            "tools": self.tools.available(),
            "capabilities": self.orchestrator.capabilities(),
        }

    def route_skill(
        self,
        skill: str | Skill,
    ) -> dict[str, list[str]]:
        return self.router.route(skill)

    async def execute_tool(
        self,
        tool: str,
        **kwargs,
    ):
        return await self.tools.execute(
            tool,
            **kwargs,
        )
