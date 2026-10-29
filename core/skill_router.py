"""Router de skills para modelos y herramientas."""

from __future__ import annotations

from adapters.base import Skill
from core.orchestrator import Orchestrator
from core.tool_registry import ToolRegistry


class SkillRouter:
    """
    Relaciona una skill con modelos activos y herramientas registradas.

    El router consulta `Orchestrator` para capacidades del modelo y `ToolRegistry`
    para herramientas. No ejecuta llamadas a modelos ni handlers.

    Example:
        route = router.route("analysis")
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        tools: ToolRegistry,
    ) -> None:
        """
        Asocia un catálogo de capacidades de modelo y un registro de herramientas.

        Args:
            orchestrator (Orchestrator): Fuente de modelos activos y skills verificadas.
            tools (ToolRegistry): Registro de herramientas disponibles por skill.

        Returns:
            None.

        Raises:
            No genera excepciones deliberadas; los objetos incompatibles fallarán al consultar.

        Side effects:
            Conserva referencias a ambos componentes; no ejecuta modelos ni herramientas.

        Example:
            router = SkillRouter(orchestrator, tool_registry)
        """

        self.orchestrator = orchestrator
        self.tools = tools

    def models_for(
        self,
        skill: str | Skill,
        *,
        only_verified: bool = True,
    ) -> list[str]:
        """
        Obtiene los modelos que el orquestador asocia con una skill.

        Args:
            skill (str | Skill): Skill requerida.
            only_verified (bool): Si es `True`, exige evidencia local de la capacidad;
                si es `False`, admite skills declaradas por el registro de modelos.

        Returns:
            list[str]: Nombres de adapters activos compatibles.

        Raises:
            Propaga errores de consulta del orquestador.

        Side effects:
            No llama al proveedor.

        Example:
            models = router.models_for("reasoning", only_verified=True)
        """
        return self.orchestrator.models_for_skill(
            skill,
            only_verified=only_verified,
        )

    def tools_for(
        self,
        skill: str | Skill,
    ) -> list[str]:
        """
        Obtiene las herramientas registradas para una skill.

        Args:
            skill (str | Skill): Nombre o enumeración de la skill.

        Returns:
            list[str]: Nombres de herramientas compatibles en orden alfabético.

        Raises:
            No genera excepciones deliberadas para skills ordinarias.

        Side effects:
            Ninguno; no ejecuta los handlers.

        Example:
            tools = router.tools_for("analysis")
        """
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
        """
        Construye una vista conjunta de modelos y herramientas para una skill.

        Args:
            skill (str | Skill): Skill que se quiere resolver.

        Returns:
            dict[str, list[str]]: `models` verificados, `fallback_models` activos sin
            verificación local, `active_models` compatibles y `tools` registradas.

        Raises:
            Propaga errores inesperados de `models_for` o `tools_for`.

        Side effects:
            No ejecuta modelos ni herramientas.

        Example:
            route = router.route("analysis")
            print(route["models"], route["tools"])
        """
        verified = self.models_for(
            skill,
            only_verified=True,
        )

        active = self.models_for(
            skill,
            only_verified=False,
        )

        fallback = [
            model
            for model in active
            if model not in verified
        ]

        return {
            "models": verified,
            "fallback_models": fallback,
            "active_models": active,
            "tools": self.tools_for(skill),
        }
