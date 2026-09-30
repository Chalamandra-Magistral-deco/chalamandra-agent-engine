"""Ejecutor de grafos de tareas (DAG) con soporte de paralelismo por capas."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from adapters.base import ModelRequest, ModelRole, Skill
from core.decomposer import Subtask
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


@dataclass
class ExecutionSummary:
    task: str
    subtask_results: dict[str, SubtaskResult] = field(default_factory=dict)
    status: str = "completed"
    errors: list[str] = field(default_factory=list)


class Executor:
    """Ejecuta un plan de Subtasks organizadas como DAG."""

    def __init__(self, master: "Master | None" = None) -> None:
        self.master = master or Master()

    def build_levels(self, subtasks: list[Subtask]) -> list[list[Subtask]]:
        """Organiza las subtasks en niveles independientes para ejecución paralela.
        
        Nivel 0: Subtasks sin dependencias.
        Nivel N: Subtasks cuyas dependencias están en niveles < N.
        """
        task_map = {s.id: s for s in subtasks}

        if len(task_map) != len(subtasks):
            raise ValueError("El plan contiene IDs de subtask duplicados.")

        missing = sorted({
            dep
            for s in subtasks
            for dep in s.depends_on
            if dep not in task_map
        })

        if missing:
            raise ValueError(
                f"El plan contiene dependencias inexistentes: {missing}"
            )

        in_degree = {s.id: len(s.depends_on) for s in subtasks}
        dependents = {s.id: [] for s in subtasks}

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
                dep_contexts.append(f"--- Resultado previo [{dep_id}]: {dep_res.description} ---\n{dep_res.output}")

        prompt_parts = []
        if dep_contexts:
            prompt_parts.append("CONTEXTO PREVIO:")
            prompt_parts.extend(dep_contexts)
            prompt_parts.append("\nTAREA ACTUAL:")

        prompt_parts.append(subtask.description)
        full_prompt = "\n".join(prompt_parts)

        # Ruteo vía Master según la skill solicitada.
        # Prioridad:
        #   1. modelos verificados
        #   2. modelos activos como fallback
        route = self.master.route_skill(subtask.skill)

        candidates = (
            route["models"]
            if route["models"]
            else route["fallback_models"]
        )

        if not candidates:
            raise ValueError(
                f"No hay adapters activos para la skill: {subtask.skill}"
            )

        model = candidates[0]

        request = ModelRequest(
            prompt=full_prompt,
            system_prompt=(
                f"Ejecuta la tarea asignada usando tu habilidad de {subtask.skill}. "
                "Responde de forma breve y directa."
            ),
            temperature=0.0,
            max_tokens=64,
            timeout_seconds=60.0,
            role=ModelRole.AGENT,
            skills=[Skill(subtask.skill)],
        )

        response = await self.master.orchestrator.run(model, request)

        return SubtaskResult(
            subtask_id=subtask.id,
            description=subtask.description,
            skill=subtask.skill,
            output=response.output,
            model_used=model,
            status=response.status.value,
            errors=response.errors,
        )

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
                if isinstance(res, Exception):
                    summary.subtask_results[st.id] = SubtaskResult(
                        subtask_id=st.id,
                        description=st.description,
                        skill=st.skill,
                        output="",
                        model_used="unknown",
                        status="error",
                        errors=[str(res)],
                    )
                    summary.status = "partial_error"
                else:
                    summary.subtask_results[st.id] = res
                    if res.status != "ok":
                        summary.status = "partial_error"

        return summary
