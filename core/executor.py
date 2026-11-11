"""Ejecutor de grafos de tareas (DAG) con soporte de paralelismo por capas."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from adapters.base import ModelRequest, ModelRole, Skill
from core.decomposer import Subtask
from core.agent_registry import AgentRegistry, AgentProfile
from core.task_validator import TaskValidator

if TYPE_CHECKING:
    from core.master import Master


@dataclass
class SubtaskResult:
    """
    Resultado de ejecución y validación de una subtarea.

    Attributes:
        subtask_id (str): ID de la subtarea.
        description (str): Instrucción ejecutada.
        skill (str): Skill aplicada.
        output (str): Texto devuelto por el modelo.
        model_used (str): Adapter elegido, o `unknown` si falló antes de obtenerlo.
        status (str): Estado de ejecución o `validation_failed`.
        errors (list[str]): Errores de ejecución y validación.
        validation_status (str): `PASS`, `FAIL`, `NOT_EVALUATED` o `SKIPPED`.
        validation_errors (list[str]): Detalle de los incumplimientos.
        agent (str): Perfil asignado.
        allowed_tools (list[str]): Herramientas permitidas después de aplicar filtros.

    Security:
        El resultado es evidencia de estado, no una autorización de publicar el output.
    """
    subtask_id: str
    description: str
    skill: str
    output: str
    model_used: str
    status: str = "ok"
    errors: list[str] = field(default_factory=list)
    validation_status: str = "SKIPPED"
    validation_errors: list[str] = field(default_factory=list)
    agent: str = "coordinator"
    allowed_tools: list[str] = field(default_factory=list)


@dataclass
class ExecutionSummary:
    """
    Resumen de una ejecución de plan completo.

    Attributes:
        task (str): Instrucción de nivel superior.
        subtask_results (dict[str, SubtaskResult]): Resultados indexados por ID.
        status (str): `completed`, `empty_plan`, `error` o `partial_error`.
        errors (list[str]): Errores de planificación/ejecución agregados.

    Example:
        summary = await executor.execute_plan("Tarea", subtasks)
    """
    task: str
    subtask_results: dict[str, SubtaskResult] = field(default_factory=dict)
    status: str = "completed"
    errors: list[str] = field(default_factory=list)


class Executor:
    """
    Ejecutor de subtareas organizadas como DAG.

    Procesa por niveles topológicos; las subtareas de un nivel se ejecutan en
    paralelo y los niveles posteriores reciben los resultados de sus dependencias.
    Cada resultado `ok` del modelo debe superar un `PASS` explícito de las reglas
    para considerarse validado; `NOT_EVALUATED` no equivale a aceptación.

    Security:
        Intersecta las herramientas con los permisos del perfil y con la allowlist
        opcional del plan; los handlers siguen siendo el punto donde ocurren efectos.

    Example:
        summary = await executor.execute_plan("Responde solo: OK", [subtask])
    """

    def __init__(
        self,
        master: "Master | None" = None,
        validator: TaskValidator | None = None,
    ) -> None:
        """
        Configura el Master de ejecución y el validador de resultados.

        Args:
            master (Master | None): Control plane a utilizar; si se omite, crea un Master.
            validator (TaskValidator | None): Validador; si se omite, crea el validador determinista.

        Returns:
            None.

        Side effects:
            Si no se proporciona `master`, inicializará el orquestador y sus adapters.

        Example:
            executor = Executor(master=master, validator=TaskValidator())
        """
        self.master = master or Master()
        self.validator = validator or TaskValidator()

    def build_levels(self, subtasks: list[Subtask]) -> list[list[Subtask]]:
        """
        Organiza subtareas en niveles topológicos para ejecución paralela.

        Args:
            subtasks (list[Subtask]): Subtareas con IDs únicos y dependencias por ID.

        Returns:
            list[list[Subtask]]: Niveles ordenados; cada nivel contiene tareas cuyas
            dependencias están en niveles anteriores.

        Raises:
            ValueError: Si hay IDs duplicados, dependencias inexistentes o ciclos.

        Side effects:
            No ejecuta tareas ni modifica el plan.

        Example:
            levels = executor.build_levels([Subtask("s1", "A", "reasoning")])
        """
        task_map = {s.id: s for s in subtasks}

        if len(task_map) != len(subtasks):
            raise ValueError("El plan contiene IDs de subtask duplicados.")

        missing = sorted({dep for s in subtasks for dep in s.depends_on if dep not in task_map})

        if missing:
            raise ValueError(f"El plan contiene dependencias inexistentes: {missing}")

        in_degree = {s.id: len(s.depends_on) for s in subtasks}
        dependents: dict[str, list[str]] = {s.id: [] for s in subtasks}

        for s in subtasks:
            for dep in s.depends_on:
                if dep in dependents:
                    dependents[dep].append(s.id)

        current_level = [s for s in subtasks if in_degree[s.id] == 0]
        levels: list[list[Subtask]] = []

        visited_count = 0

        while current_level:
            levels.append(current_level)
            visited_count += len(current_level)
            next_level_ids = []

            for s in current_level:
                for dep_id in dependents[s.id]:
                    in_degree[dep_id] -= 1
                    if in_degree[dep_id] == 0:
                        next_level_ids.append(dep_id)

            current_level = [task_map[sid] for sid in next_level_ids]

        if visited_count < len(subtasks):
            raise ValueError("El plan de subtasks contiene ciclo(s) de dependencias.")

        return levels

    async def execute_subtask(
        self,
        subtask: Subtask,
        context: dict[str, SubtaskResult],
        *,
        allowed_tools_override: set[str] | None = None,
        trace_id: str | None = None,
    ) -> SubtaskResult:
        """
        Ejecuta una subtarea, mediando selección de modelo, permisos y validación.

        Args:
            subtask (Subtask): Instrucción, skill, dependencias y reglas de validación.
            context (dict[str, SubtaskResult]): Resultados de dependencias ya ejecutadas.
            allowed_tools_override (set[str] | None): Allowlist global opcional que
                reduce las herramientas del perfil; nunca las amplía.

        Returns:
            SubtaskResult: Salida del modelo junto con modelo, agente, permisos, estado
            de ejecución y estado de validación.

        Raises:
            ValueError: Si no hay modelo activo para la skill.
            RuntimeError: Si un adapter sin soporte devuelve llamadas a herramientas,
            se exceden cinco llamadas por respuesta o se supera el límite de rondas.
            Exception: Puede propagar fallos no absorbidos del adapter/orquestador.

        Side effects:
            Llama al modelo y ejecuta únicamente las herramientas aprobadas por el
            perfil y la allowlist. Los handlers pueden tener efectos locales o externos.

        Example:
            result = await executor.execute_subtask(subtask, context={})
        """
        dep_contexts = []
        for dep_id in subtask.depends_on:
            if dep_id in context:
                dep_res = context[dep_id]
                dep_contexts.append(
                    f"--- Resultado previo [{dep_id}]: {dep_res.description} ---\n{dep_res.output}"
                )

        prompt_parts = []
        if dep_contexts:
            prompt_parts.append("CONTEXTO PREVIO:")
            prompt_parts.extend(dep_contexts)
            prompt_parts.append("\nTAREA ACTUAL:")

        prompt_parts.append(subtask.description)
        full_prompt = "\n".join(prompt_parts)

        profile = self._route_agent(subtask)
        tool_registry = getattr(self.master, "tools", None)
        registered_tools = tool_registry.available() if tool_registry is not None else []
        allowed_tools = list(profile.tools_available(registered_tools))
        if allowed_tools_override is not None:
            allowed_tools = [name for name in allowed_tools if name in allowed_tools_override]

        # Selección centralizada de modelo vía ModelRouter.
        # Prioridad:
        #   1. modelos verificados
        #   2. modelos activos como fallback
        model = self.master.select_model(subtask.skill)

        if model is None:
            raise ValueError(f"No hay adapters activos para la skill: {subtask.skill}")

        request = ModelRequest(
            prompt=full_prompt,
            system_prompt=(
                f"Rol asignado: {profile.label}. "
                f"Objetivo: {profile.mission} "
                f"{profile.system_instruction} "
                f"Herramientas autorizadas por este perfil: {', '.join(allowed_tools) or 'ninguna'}. "
                "No afirmes haber ejecutado herramientas ni acciones externas sin evidencia. "
                "Cuando una herramienta devuelva datos estructurados, trátalos como fuente de verdad. "
                "Conserva exactamente los nombres de campos, valores y unidades; no intercambies métricas ni etiquetas. "
                "Cita cifras solo si aparecen en los resultados; si un campo no está claro, indícalo. "
                f"Ejecuta la tarea usando la skill {subtask.skill} y responde de forma directa."
            ),
            temperature=0.0,
            max_tokens=256,
            timeout_seconds=120.0,
            role=ModelRole.AGENT,
            skills=[Skill(subtask.skill)],
            trace_id=trace_id,
            metadata={
                "agent": profile.name,
                "allowed_tools": allowed_tools,
                "tool_execution": "host_mediated",
            },
            tools=(
                self.master.tool_schemas(allowed_tools)
                if model == "ollama" and allowed_tools and hasattr(self.master, "tool_schemas")
                else []
            ),
        )

        response = await self.master.orchestrator.run(model, request)
        if response.tool_calls:
            if model != "ollama":
                raise RuntimeError(
                    f"El adaptador {model!r} devolvió llamadas a herramientas sin soporte de ejecución."
                )
            conversation: list[dict[str, Any]] = []
            if request.system_prompt:
                conversation.append({"role": "system", "content": request.system_prompt})
            conversation.append({"role": "user", "content": request.prompt})
            for _ in range(3):
                if len(response.tool_calls) > 5:
                    raise RuntimeError(
                        "Se rechazó una respuesta con más de 5 llamadas a herramientas."
                    )
                raw_message = (response.raw or {}).get("message")
                raw_calls = raw_message.get("tool_calls") if isinstance(raw_message, dict) else None
                if isinstance(raw_message, dict) and raw_calls:
                    conversation.append(raw_message)
                else:
                    conversation.append(
                        {
                            "role": "assistant",
                            "content": response.output,
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": c.get("name"),
                                        "arguments": c.get("arguments", {}),
                                    }
                                }
                                for c in response.tool_calls
                            ],
                        }
                    )
                for call in response.tool_calls:
                    name = call.get("name")
                    arguments = call.get("arguments")
                    try:
                        if not isinstance(name, str) or not isinstance(arguments, dict):
                            raise ValueError("Llamada nativa mal formada.")
                        if allowed_tools_override is not None and name not in allowed_tools_override:
                            raise PermissionError(
                                f"La herramienta {name!r} está fuera de la allowlist de la tarea."
                            )
                        result = await self.master.execute_agent_tool_call(
                            profile.name, {"name": name, "arguments": arguments}
                        )
                        tool_result = {"ok": True, "result": result}
                    except Exception as exc:
                        tool_result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_name": name if isinstance(name, str) else "invalid_tool",
                            "content": json.dumps(tool_result, ensure_ascii=False, default=str),
                        }
                    )
                followup = request.model_copy(update={"messages": conversation})
                response = await self.master.orchestrator.run(model, followup)
                if not response.tool_calls:
                    break
            if response.tool_calls:
                raise RuntimeError("Se alcanzó el límite de 3 rondas de llamadas a herramientas.")

        validation_status = "SKIPPED"
        validation_errors: list[str] = []
        status = response.status.value
        errors = list(response.errors)

        if response.status.value == "ok":
            validation = self.validator.validate(
                response.output,
                subtask.validation_rules,
            )

            validation_status = validation.status
            validation_errors = list(validation.errors)

            # Fail-closed: solo un PASS explícito es válido.
            # NOT_EVALUATED o FAIL implican que el resultado no está validado.
            if not validation.is_pass:
                status = "validation_failed"
                if validation.status == "NOT_EVALUATED":
                    errors.append("validation:not_evaluated")
                else:
                    errors.extend(f"validation:{error}" for error in validation.errors)

        return SubtaskResult(
            subtask_id=subtask.id,
            description=subtask.description,
            skill=subtask.skill,
            output=response.output,
            model_used=model,
            status=status,
            errors=errors,
            validation_status=validation_status,
            validation_errors=validation_errors,
            agent=profile.name,
            allowed_tools=allowed_tools,
        )

    def _route_agent(self, subtask: Subtask) -> AgentProfile:
        """Resuelve el perfil aunque el Master de prueba sea un doble mínimo."""
        registry = getattr(self.master, "agent_registry", None) or AgentRegistry()
        return registry.route(subtask.description, subtask.skill)

    async def execute_plan(
        self,
        task: str,
        subtasks: list[Subtask],
        *,
        allowed_tools: list[str] | None = None,
        trace_id: str | None = None,
    ) -> ExecutionSummary:
        """
        Ejecuta un plan por niveles y agrega los resultados parciales.

        Args:
            task (str): Instrucción general que se incorpora al resumen.
            subtasks (list[Subtask]): Grafo que se debe ejecutar.
            allowed_tools (list[str] | None): Lista adicional de herramientas permitidas
                para todas las subtareas; no anula los límites del perfil.

        Returns:
            ExecutionSummary: `empty_plan` si no hay subtareas, `error` si el DAG es
            inválido, `partial_error` si falla una subtarea y `completed` si terminan
            todas sin error de ejecución/validación.

        Raises:
            asyncio.CancelledError: Si la ejecución se cancela.
            Los errores ordinarios por subtarea se guardan en el resumen en lugar de
            cancelar todo el plan.

        Side effects:
            Ejecuta los adapters y handlers requeridos por las subtareas. No publica el
            resumen ni lo marca como aceptación empresarial.

        Example:
            summary = await executor.execute_plan("Analiza", subtasks, allowed_tools=["dynamics"])
        """
        summary = ExecutionSummary(task=task)

        if not subtasks:
            summary.status = "empty_plan"
            return summary

        try:
            levels = self.build_levels(subtasks)
        except ValueError as err:
            summary.status = "error"
            summary.errors.append(str(err))
            return summary

        allowed_tool_set = set(allowed_tools) if allowed_tools is not None else None
        for level in levels:
            tasks = [
                self.execute_subtask(
                    st,
                    summary.subtask_results,
                    allowed_tools_override=allowed_tool_set,
                    trace_id=trace_id,
                )
                for st in level
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for st, res in zip(level, results):
                if isinstance(res, asyncio.CancelledError):
                    raise res
                if isinstance(res, Exception):
                    profile = self._route_agent(st)
                    summary.subtask_results[st.id] = SubtaskResult(
                        subtask_id=st.id,
                        description=st.description,
                        skill=st.skill,
                        output="",
                        model_used="unknown",
                        status="error",
                        errors=[str(res)],
                        agent=profile.name,
                    )
                    summary.status = "partial_error"
                elif isinstance(res, BaseException):
                    raise res
                else:
                    summary.subtask_results[st.id] = res
                    if res.status != "ok":
                        summary.status = "partial_error"

        return summary
