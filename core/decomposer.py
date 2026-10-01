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
    validation_rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


_PROMPT_TEMPLATE = """Eres un descomponedor de tareas. Recibes una tarea y devuelves
una lista de subtasks en JSON.

Skills disponibles (SOLO estas 2): reasoning, analysis

NO uses otras skills. Si crees que la tarea requiere otra cosa,
úsala igual pero con skill=reasoning.

Formato EXACTO (solo JSON, sin texto alrededor):

{"subtasks": [{"description": "...", "skill": "...", "depends_on": []}, {"description": "...", "skill": "...", "depends_on": ["0"]}]}

Reglas:
- 1 a 5 subtasks.
- Cada subtask tiene UNA skill de la lista.
- depends_on referencia indices anteriores como strings ("0", "1").
- Tarea simple = 1 sola subtask.
- PRESERVA EXACTAMENTE LA INTENCION DE LA TAREA.
- NO reemplaces una instruccion por un analisis de la instruccion.
- NO interpretes ni critiques la tarea cuando esta pida una respuesta concreta.
- Si la tarea es una instruccion simple, la subtask debe conservar esa instruccion casi literalmente.
- Ejemplo: "Di solo: OK" -> description="Di solo: OK", skill="reasoning".

TAREA: __TASK__
"""


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Sin JSON en respuesta")
    return json.loads(match.group(0))


def _infer_validation_rules(text: str) -> list[str]:
    """Infiere reglas deterministas solo desde instrucciones explícitas."""

    patterns = (
        r"^\s*di\s+solo\s*:\s*(?P<value>.+?)\s*$",
        r"^\s*responde\s+solo\s*:\s*(?P<value>.+?)\s*$",
        r"^\s*devuelve\s+solo\s*:\s*(?P<value>.+?)\s*$",
        r"^\s*responde\s+exactamente\s*:\s*(?P<value>.+?)\s*$",
    )

    for pattern in patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if not match:
            continue

        expected = match.group("value").strip()

        if (
            len(expected) >= 2
            and expected[0] == expected[-1]
            and expected[0] in {"'", '"'}
        ):
            expected = expected[1:-1].strip()

        if expected:
            return [f"exact:{expected}"]

    return []


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
            max_tokens=200,
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

            description = str(
                item.get("description", "")
            ).strip()

            subtasks.append(Subtask(
                id=sid,
                description=description,
                skill=str(item.get("skill", "reasoning")).strip(),
                depends_on=deps,
                validation_rules=_infer_validation_rules(description),
            ))

        # Si la tarea es simple y el modelo no preservó el patrón explícito,
        # recuperamos la regla desde la intención original.
        if len(subtasks) == 1 and not subtasks[0].validation_rules:
            subtasks[0].validation_rules = _infer_validation_rules(task)

        return subtasks

    def _fallback(self, task: str) -> list[Subtask]:
        return [Subtask(
            id=str(uuid.uuid4())[:8],
            description=task,
            skill="reasoning",
            depends_on=[],
            validation_rules=_infer_validation_rules(task),
        )]
