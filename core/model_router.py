"""Model Router: selecciona modelos para ejecutar una skill."""

from __future__ import annotations

from adapters.base import Skill
from core.orchestrator import Orchestrator


class ModelRouter:
    """Selecciona modelos disponibles para una skill.

    Regla principal:
    1. Preferir modelos con skill verificada.
    2. Usar modelos reclamados como fallback.
    3. Nunca seleccionar un adapter inactivo.
    """

    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orchestrator = orchestrator

    @staticmethod
    def _skill_name(skill: str | Skill) -> str:
        return skill.value if isinstance(skill, Skill) else skill

    def verified(self, skill: str | Skill) -> list[str]:
        """Modelos activos con evidencia local de la skill."""
        return self.orchestrator.models_for_skill(
            skill,
            only_verified=True,
        )

    def active(self, skill: str | Skill) -> list[str]:
        """Modelos activos compatibles, verificados o reclamados."""
        return self.orchestrator.models_for_skill(
            skill,
            only_verified=False,
        )

    def fallback(self, skill: str | Skill) -> list[str]:
        """Modelos activos compatibles pero no verificados."""
        verified = set(self.verified(skill))
        return [
            model
            for model in self.active(skill)
            if model not in verified
        ]

    def route(
        self,
        skill: str | Skill,
        *,
        allow_fallback: bool = True,
    ) -> list[str]:
        """Devuelve modelos ordenados por confianza operacional."""

        verified = self.verified(skill)

        if verified:
            if not allow_fallback:
                return verified

            return verified + [
                model
                for model in self.fallback(skill)
                if model not in verified
            ]

        if allow_fallback:
            return self.active(skill)

        return []

    def select(
        self,
        skill: str | Skill,
        *,
        allow_fallback: bool = True,
    ) -> str | None:
        """Selecciona el primer modelo de la ruta."""
        models = self.route(
            skill,
            allow_fallback=allow_fallback,
        )
        return models[0] if models else None

    def inspect(self, skill: str | Skill) -> dict[str, object]:
        """Expone la decisión del router para trazabilidad."""
        return {
            "skill": self._skill_name(skill),
            "verified": self.verified(skill),
            "fallback": self.fallback(skill),
            "active": self.active(skill),
            "selected": self.select(skill),
        }
