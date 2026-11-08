"""Append-only CandidateResult projection with an indexed JSONL ledger."""
import hashlib
import json
from pathlib import Path

from api.jsonl_index import JsonlIndex


def _state_dir() -> Path:
    return Path.home() / ".local" / "state" / "chalamandra" / "agents"


def _candidates_file() -> Path:
    return _state_dir() / "candidates.jsonl"


def _serialize_candidate(candidate: dict) -> str:
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be an object")
    return json.dumps(
        candidate,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _save_candidate(candidate: dict) -> tuple[str, bool]:
    serialized = _serialize_candidate(candidate)
    candidate_id = candidate.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("candidate_id requerido")

    store = JsonlIndex(_candidates_file(), ("candidate_id",))
    with store.locked() as db:
        existing = store.find(db, "candidate_id", candidate_id)
        if existing:
            existing_payloads = {_serialize_candidate(record) for record in existing}
            if len(existing_payloads) != 1 or serialized not in existing_payloads:
                raise ValueError(
                    "candidate_id conflict: existing candidate has different content"
                )
            return candidate_id, False
        store.append(db, candidate)
    return candidate_id, True


def save_candidate(candidate: dict) -> str:
    """Persist a candidate once; identical retries are harmless, conflicts fail closed."""
    candidate_id, _inserted = _save_candidate(candidate)
    return candidate_id


def reconcile_pending_publications(limit: int = 100) -> dict[str, int]:
    """Project valid outbox entries and quarantine conflicts without blocking later entries."""
    from api import task_bus

    pending = task_bus.get_pending_candidate_publications(limit=limit)
    recovered = 0
    already_present = 0
    conflicted = 0

    for publication in pending:
        candidate = publication["candidate_result"]
        candidate_id = publication["candidate_id"]
        try:
            if candidate.get("candidate_id") != candidate_id:
                raise ValueError(
                    "candidate publication outbox conflict: candidate_id mismatch"
                )
            saved_id, inserted = _save_candidate(candidate)
            if saved_id != candidate_id:
                raise ValueError(
                    "candidate publication outbox conflict: invalid candidate id"
                )
        except ValueError as exc:
            if not task_bus.mark_candidate_publication_blocked(
                publication["task_id"], candidate_id, str(exc)
            ):
                raise RuntimeError(
                    "candidate publication conflict could not be quarantined"
                ) from exc
            conflicted += 1
            continue

        if not task_bus.mark_candidate_publication_published(
            publication["task_id"], candidate_id
        ):
            raise RuntimeError("candidate publication could not be acknowledged in SQLite")

        if inserted:
            recovered += 1
        else:
            already_present += 1

    return {
        "examined": len(pending),
        "recovered": recovered,
        "already_present": already_present,
        "conflicted": conflicted,
        "published": recovered + already_present,
    }


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
