"""Task Bus (Orchestration Flow - Fase 3)."""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def _state_dir() -> Path:
    return Path.home() / ".local" / "state" / "chalamandra" / "agents"

def _tasks_file() -> Path:
    return _state_dir() / "tasks.jsonl"

def _append(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def _read_all(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records

def enqueue_task(contract: dict[str, Any]) -> str:
    """Registra un nuevo TaskContract en el bus."""
    task_id = contract.get("task_id") or str(uuid.uuid4())
    record = {
        "task_id": task_id,
        "run_id": contract.get("run_id"),
        "objective": contract.get("objective"),
        "required_tools": contract.get("required_tools", []),
        "success_criteria": contract.get("success_criteria", []),
        "status": "queued",
        "trace_id": contract.get("trace_id"),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    _append(_tasks_file(), record)
    return task_id

def get_next_task() -> dict | None:
    """Obtiene la siguiente tarea en cola (simulación de worker)."""
    records = _read_all(_tasks_file())
    # Buscar la primera tarea que siga en 'queued'
    # (Asumimos que si hay un 'running' o 'completed' para el mismo task_id, ya se procesó)
    processed_ids = {r["task_id"] for r in records if r["status"] in ("running", "completed", "failed")}
    
    for r in records:
        if r["status"] == "queued" and r["task_id"] not in processed_ids:
            return r
    return None

def mark_task_running(task_id: str):
    _append(_tasks_file(), {"task_id": task_id, "status": "running", "updated_at": datetime.now(timezone.utc).isoformat()})

def mark_task_completed(task_id: str, candidate_result: dict):
    _append(_tasks_file(), {
        "task_id": task_id,
        "status": "completed",
        "candidate_result": candidate_result,
        "updated_at": datetime.now(timezone.utc).isoformat()
    })
