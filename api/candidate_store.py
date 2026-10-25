"""Store de CandidateResult para validación de evidencia (P1)."""
import json
from pathlib import Path

def _state_dir() -> Path:
    return Path.home() / ".local" / "state" / "chalamandra" / "agents"

def _candidates_file() -> Path:
    return _state_dir() / "candidates.jsonl"

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

def save_candidate(candidate: dict) -> str:
    candidate_id = candidate.get("candidate_id")
    if not candidate_id:
        raise ValueError("candidate_id requerido")
    _append(_candidates_file(), candidate)
    return candidate_id

def get_candidate(candidate_id: str) -> dict | None:
    latest = None
    for rec in _read_all(_candidates_file()):
        if rec.get("candidate_id") == candidate_id:
            latest = rec
    return latest

def compute_evidence_digest(candidate: dict) -> str:
    """Calcula un digest estable de la evidencia del candidato."""
    import hashlib
    evidence_str = json.dumps(candidate.get("evidence", []), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(evidence_str.encode("utf-8")).hexdigest()
