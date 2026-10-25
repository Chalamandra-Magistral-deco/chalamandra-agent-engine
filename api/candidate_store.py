"""Store de CandidateResult para validación de evidencia (P1)."""
import json
from pathlib import Path

from api import jsonl_store

def _state_dir() -> Path:
    return jsonl_store.state_dir()

def _candidates_file() -> Path:
    return _state_dir() / "candidates.jsonl"

def _append(path: Path, record: dict):
    jsonl_store.append_jsonl(path, record)

def _read_all(path: Path) -> list[dict]:
    return jsonl_store.read_jsonl(path)

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
