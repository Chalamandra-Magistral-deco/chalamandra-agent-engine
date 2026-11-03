"""Indexed append-only CandidateResult store."""
import hashlib
import json
from pathlib import Path

from api.jsonl_index import JsonlIndex


def _state_dir() -> Path:
    return Path.home() / ".local" / "state" / "chalamandra" / "agents"


def _candidates_file() -> Path:
    return _state_dir() / "candidates.jsonl"


def save_candidate(candidate: dict) -> str:
    candidate_id = candidate.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("candidate_id requerido")
    store = JsonlIndex(_candidates_file(), ("candidate_id",))
    with store.locked() as db:
        store.append(db, candidate)
    return candidate_id


def get_candidate(candidate_id: str) -> dict | None:
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("candidate_id requerido")
    store = JsonlIndex(_candidates_file(), ("candidate_id",))
    with store.locked() as db:
        matches = store.find(db, "candidate_id", candidate_id)
        return matches[-1] if matches else None


def compute_evidence_digest(candidate: dict) -> str:
    """Calcula un digest estable de la evidencia del candidato."""
    evidence_str = json.dumps(
        candidate.get("evidence", []),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(evidence_str.encode("utf-8")).hexdigest()
