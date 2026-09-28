"""Router de skills para modelos y herramientas."""

from __future__ import annotations

from adapters.base import Skill
from core.orchestrator import Orchestrator
from core.tool_registry import ToolRegistry


class SkillRouter:
    """Resuelve qué modelos y herramientas pueden cubrir una skill."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        tools: ToolRegistry,
    ) -> None:
        self.orchestrator = orchestrator
        self.tools = tools

    def models_for(
        self,
        skill: str | Skill,
        *,
        only_verified: bool = True,
    ) -> list[str]:
        return self.orchestrator.models_for_skill(
            skill,
            only_verified=only_verified,
        )

    def tools_for(
        self,
        skill: str | Skill,
    ) -> list[str]:
        skill_name = (
            skill.value
            if isinstance(skill, Skill)
            else skill
        )

        return self.tools.tools_for_skill(skill_name)

    def route(
        self,
        skill: str | Skill,
    ) -> dict[str, list[str]]:
        return {
            "models": self.models_for(skill),
            "tools": self.tools_for(skill),
        }
