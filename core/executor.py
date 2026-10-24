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
    task: str
    subtask_results: dict[str, SubtaskResult] = field(default_factory=dict)
    status: str = "completed"
    errors: list[str] = field(default_factory=list)


class Executor:
    """Ejecuta un plan de Subtasks organizadas como DAG."""

    def __init__(
        self,
        master: "Master | None" = None,
        validator: TaskValidator | None = None,
    ) -> None:
        self.master = master or Master()
        self.validator = validator or TaskValidator()

    def build_levels(self, subtasks: list[Subtask]) -> list[list[Subtask]]:
        """Organiza las subtasks en niveles independientes para ejecución paralela.

        Nivel 0: Subtasks sin dependencias.
        Nivel N: Subtasks cuyas dependencias están en niveles < N.
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
    ) -> SubtaskResult:
        """Ejecuta una subtask individual construyendo su prompt con el contexto previo."""
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

            if validation.is_fail:
                status = "validation_failed"
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
    ) -> ExecutionSummary:
        """Ejecuta el plan de subtasks respetando los niveles topológicos y resolviendo dependencias."""
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

        for level in levels:
            tasks = [self.execute_subtask(st, summary.subtask_results) for st in level]
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
