"""Append-only CandidateResult projection with an indexed JSONL ledger."""
import hashlib
import hmac
import json
import os
from pathlib import Path

from api.jsonl_index import JsonlIndex
from api.state_dir import agents_state_dir


def _state_dir() -> Path:
    return agents_state_dir()


def _digest_key() -> bytes:
    raw = os.getenv("CHALAMANDRA_CANDIDATE_HMAC_KEY", "").strip()
    if len(raw) != 64 or not re.fullmatch(r"[0-9a-fA-F]{64}", raw):
        raise RuntimeError(
            "CHALAMANDRA_CANDIDATE_HMAC_KEY es obligatorio y debe contener "
            "exactamente 64 caracteres hexadecimales (32 bytes)"
        )
    return bytes.fromhex(raw)


def require_integrity_key() -> None:
    """Fail startup when candidate-record integrity cannot be authenticated."""
    _digest_key()


def _record_digest_fields(record: dict) -> tuple[str, str]:
    """Authenticate the complete candidate record with HMAC-SHA256."""
    body = {
        key: value
        for key, value in record.items()
        if key not in {"record_digest", "record_digest_alg"}
    }
    serialized = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hmac.new(
        _digest_key(),
        serialized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return "hmac-sha256", digest


def _verify_record_digest(record: dict) -> None:
    digest = record.get("record_digest")
    algorithm = record.get("record_digest_alg")
    if not isinstance(digest, str) or not isinstance(algorithm, str):
        raise ValueError("candidate record_digest missing or invalid")
    if algorithm != "hmac-sha256":
        raise ValueError("candidate record_digest algorithm is not trusted")
    expected_algorithm, expected = _record_digest_fields(record)
    if algorithm != expected_algorithm or not hmac.compare_digest(digest, expected):
        raise ValueError("candidate record integrity check failed: record_digest mismatch")


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
    stored = dict(candidate)
    alg, digest = _record_digest_fields(stored)
    stored["record_digest"] = digest
    stored["record_digest_alg"] = alg
    serialized = _serialize_candidate(stored)
    candidate_id = stored.get("candidate_id")
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
        store.append(db, stored)
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
        if not matches:
            return None
        record = matches[-1]
        # Fail-closed: a tampered record never reaches acceptance or delivery.
        # record_acceptance() and get_acceptance() read through here, so the
        # integrity check also covers the acceptance re-verification path.
        _verify_record_digest(record)
        return record


def compute_evidence_digest(candidate: dict) -> str:
    """Calcula un digest estable de la evidencia del candidato."""
    evidence_str = json.dumps(
        candidate.get("evidence", []),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(evidence_str.encode("utf-8")).hexdigest()
