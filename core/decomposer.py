"""Task Decomposer: convierte una tarea en subtasks con skills asignadas."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field, asdict

from adapters.base import ModelRequest, ModelRole
from core.orchestrator import Orchestrator


@dataclass
class Subtask:
    id: str
    description: str
    skill: str
    depends_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


_PROMPT_TEMPLATE = """Eres un descomponedor de tareas. Recibes una tarea y devuelves
una lista de subtasks en JSON.

Skills disponibles: research, reasoning, coding, debugging, creative,
strategy, analysis, data, automation, vision, documents, web,
verification, red_team, synthesis, planning, meta_reasoning

Formato EXACTO (solo JSON, sin texto alrededor):

{"subtasks": [{"description": "...", "skill": "...", "depends_on": []}, {"description": "...", "skill": "...", "depends_on": ["0"]}]}

Reglas:
- 1 a 5 subtasks.
- Cada subtask tiene UNA skill de la lista.
- depends_on referencia indices anteriores como strings ("0", "1").
- Tarea simple = 1 sola subtask.

TAREA: __TASK__
"""


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Sin JSON en respuesta")
    return json.loads(match.group(0))


class Decomposer:
    def __init__(self, orchestrator: Orchestrator, model: str = "ollama") -> None:
        self.orchestrator = orchestrator
        self.model = model

    async def decompose(self, task: str) -> list[Subtask]:
        prompt = _PROMPT_TEMPLATE.replace("__TASK__", task)

        request = ModelRequest(
            prompt=prompt,
            system_prompt="Respondes solo con JSON valido.",
            temperature=0.0,
            max_tokens=800,
            role=ModelRole.PLANNER,
        )

        response = await self.orchestrator.run(self.model, request)

        if response.status.value != "ok":
            return self._fallback(task)

        try:
            data = _extract_json(response.output)
            raw = data.get("subtasks", [])
            if not raw:
                return self._fallback(task)
        except Exception:
            return self._fallback(task)

        subtasks: list[Subtask] = []
        idx_to_id: dict[int, str] = {}

        for i, item in enumerate(raw):
            sid = str(uuid.uuid4())[:8]
            idx_to_id[i] = sid

            deps: list[str] = []
            for d in item.get("depends_on", []) or []:
                if isinstance(d, int) and d in idx_to_id:
                    deps.append(idx_to_id[d])
                elif isinstance(d, str) and d.isdigit() and int(d) in idx_to_id:
                    deps.append(idx_to_id[int(d)])

            subtasks.append(Subtask(
                id=sid,
                description=str(item.get("description", "")).strip(),
                skill=str(item.get("skill", "reasoning")).strip(),
                depends_on=deps,
            ))

        return subtasks

    def _fallback(self, task: str) -> list[Subtask]:
        return [Subtask(
            id=str(uuid.uuid4())[:8],
            description=task,
            skill="reasoning",
            depends_on=[],
        )]
