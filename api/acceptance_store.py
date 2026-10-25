"""Store for AcceptanceDecision (Fase 2)."""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def _state_dir() -> Path:
    return Path.home() / ".local" / "state" / "chalamandra" / "agents"

def _acceptance_file() -> Path:
    return _state_dir() / "acceptance.jsonl"

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

def record_acceptance(decision: dict[str, Any]) -> str:
    """Guarda una decisión de aceptación."""
    decision_id = decision.get("decision_id")
    if not decision_id:
        raise ValueError("decision_id es requerido")
    
    record = {
        "decision_id": decision_id,
        "candidate_id": decision.get("candidate_id"),
        "outcome": decision.get("outcome"),
        "criteria_checked": decision.get("criteria_checked", []),
        "reasons": decision.get("reasons", []),
        "missing_evidence": decision.get("missing_evidence", []),
        "verifier_id": decision.get("verifier_id"),
        "evidence_digest": decision.get("evidence_digest"),
        "decided_at": decision.get("decided_at", datetime.now(timezone.utc).isoformat()),
        "trace_id": decision.get("trace_id")
    }
    _append(_acceptance_file(), record)
    return decision_id

def get_acceptance(candidate_id: str) -> dict | None:
    """Obtiene la decisión más reciente para un candidate_id (result_id)."""
    latest = None
    for rec in _read_all(_acceptance_file()):
        if rec.get("candidate_id") == candidate_id:
            latest = rec
    return latest
