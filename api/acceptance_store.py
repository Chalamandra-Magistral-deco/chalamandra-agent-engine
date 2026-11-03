"""Append-only Ed25519 acceptance store. Private signing keys never enter the API."""
from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from api.crypto_signer import verify_decision
from api.jsonl_index import JsonlIndex

_CANONICAL_FIELDS = (
    "decision_id", "candidate_id", "outcome", "criteria_checked", "reasons",
    "missing_evidence", "verifier_id", "evidence_digest", "decided_at", "trace_id",
)
_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _state_dir() -> Path:
    return Path.home() / ".local" / "state" / "chalamandra" / "agents"


def _acceptance_file() -> Path:
    # Deliberately separate from legacy HMAC acceptance.jsonl; old records are not trusted.
    return _state_dir() / "acceptance_store.jsonl"


@contextmanager
def _locked_store(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a", encoding="utf-8") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)


def _append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ) + "\n"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _read_all(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"acceptance_store.jsonl inválido en línea {line_number}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"Registro de aceptación no-objeto en línea {line_number}")
            records.append(record)
    return records


def _validate_decision_shape(decision: dict[str, Any]) -> None:
    missing = [field for field in _CANONICAL_FIELDS if field not in decision]
    if missing:
        raise ValueError("Campos canónicos de aceptación faltantes: " + ", ".join(missing))
    for field in ("decision_id", "candidate_id", "verifier_id", "evidence_digest", "trace_id", "decided_at"):
        if not isinstance(decision[field], str) or not decision[field].strip():
            raise ValueError(f"{field} debe ser texto no vacío")
    if not _TRACE_ID_RE.fullmatch(decision["trace_id"]) or decision["trace_id"] == "0" * 32:
        raise ValueError("trace_id debe ser un W3C trace ID de 32 caracteres hexadecimales no-cero")
    if not re.fullmatch(r"[0-9a-f]{64}", decision["evidence_digest"]):
        raise ValueError("evidence_digest debe ser SHA-256 hexadecimal de 64 caracteres")
    if decision["outcome"] not in {"accepted", "rejected"}:
        raise ValueError("outcome debe ser accepted o rejected")
    for field in ("criteria_checked", "reasons", "missing_evidence"):
        value = decision[field]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"{field} debe ser una lista de textos")
    try:
        timestamp = decision["decided_at"]
        parsed = datetime.fromisoformat(timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp)
    except (TypeError, ValueError) as exc:
        raise ValueError("decided_at debe ser una fecha ISO-8601 con zona horaria") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("decided_at debe incluir zona horaria")


def _verify_persisted_record(record: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    _validate_decision_shape(record)
    if record.get("candidate_id") != candidate_id:
        raise ValueError("candidate_id no coincide con el registro solicitado")
    expected_verifier_id = os.getenv("CHALAMANDRA_VERIFIER_ID", "").strip()
    if not expected_verifier_id or record.get("verifier_id") != expected_verifier_id:
        raise ValueError("La identidad del verificador persistido no coincide con la configurada")
    signature = record.get("signature")
    if not isinstance(signature, str) or not verify_decision(record, signature):
        raise ValueError("Firma Ed25519 inválida en acceptance_store.jsonl")
    from api import candidate_store
    candidate = candidate_store.get_candidate(candidate_id)
    if not candidate:
        raise ValueError("Candidato no encontrado para verificar la aceptación persistida")
    if record.get("evidence_digest") != candidate_store.compute_evidence_digest(candidate):
        raise ValueError("El evidence_digest persistido ya no coincide con la evidencia del candidato")
    return record


def record_acceptance(decision: dict[str, Any]) -> str:
    if not isinstance(decision, dict):
        raise ValueError("La decisión de aceptación debe ser un objeto")
    _validate_decision_shape(decision)
    signature = decision.get("signature")
    if not isinstance(signature, str) or not signature:
        raise ValueError("Firma Ed25519 (signature) requerida para registrar una decisión")
    expected_verifier_id = os.getenv("CHALAMANDRA_VERIFIER_ID", "").strip()
    if not expected_verifier_id:
        raise ValueError("CHALAMANDRA_VERIFIER_ID no está configurado")
    if decision["verifier_id"] != expected_verifier_id:
        raise ValueError("La identidad del verificador no coincide con la identidad configurada")
    if not verify_decision(decision, signature):
        raise ValueError("Firma Ed25519 inválida. Verificador no autorizado o decisión alterada.")

    from api import candidate_store
    candidate_id = decision["candidate_id"]
    candidate = candidate_store.get_candidate(candidate_id)
    if not candidate:
        raise ValueError("Candidato no encontrado para verificar la evidencia")
    if decision["outcome"] == "accepted" and candidate.get("status") != "candidate":
        raise ValueError(
            "Solo un candidato ejecutado y elegible puede recibir aceptación; "
            "resultados bloqueados o fallidos no son publicables."
        )
    expected_digest = candidate_store.compute_evidence_digest(candidate)
    if decision["evidence_digest"] != expected_digest:
        raise ValueError("El evidence_digest no coincide con la evidencia del candidato")

    record = {key: decision[key] for key in _CANONICAL_FIELDS}
    record["signature"] = signature
    target = _acceptance_file()
    store = JsonlIndex(target, ("candidate_id", "decision_id"))
    with store.locked() as db:
        decision_matches = store.find(db, "decision_id", str(decision["decision_id"]))
        for existing in decision_matches:
            if existing == record:
                return str(decision["decision_id"])
            raise ValueError("decision_id ya existe con contenido diferente")
        candidate_matches = store.find(db, "candidate_id", candidate_id)
        if candidate_matches:
            raise ValueError("El candidato ya tiene una decisión de aceptación registrada; use un candidato nuevo para re-revisión")
        store.append(db, record)
    return str(decision["decision_id"])


def get_acceptance(candidate_id: str) -> dict[str, Any] | None:
    target = _acceptance_file()
    store = JsonlIndex(target, ("candidate_id", "decision_id"))
    with store.locked() as db:
        matches = store.find(db, "candidate_id", candidate_id)
        if not matches:
            return None
        # Reconcile only cryptographically valid duplicate records whose decision
        # semantics are identical. Keep the append-only history untouched and choose
        # the first persisted record as the stable canonical view. Conflicting outcomes,
        # criteria, reasons, verifier, evidence digest, or trace still fail closed.
        validated = [
            _verify_persisted_record(record, candidate_id)
            for record in matches
        ]
        if len(validated) == 1:
            return validated[0]

        metadata_fields = {"decision_id", "decided_at"}

        def semantic_payload(record: dict[str, Any]) -> dict[str, Any]:
            return {
                field: record[field]
                for field in _CANONICAL_FIELDS
                if field not in metadata_fields
            }

        baseline = semantic_payload(validated[0])
        if any(semantic_payload(record) != baseline for record in validated[1:]):
            raise ValueError(
                "Decisiones de aceptación duplicadas con contenido semántico conflictivo"
            )
        return validated[0]
