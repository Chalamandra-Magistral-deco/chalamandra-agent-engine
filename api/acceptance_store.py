"""Store y verificación criptográfica de AcceptanceDecision (Fase 2 corregida)."""
import hashlib
import hmac
import json
import os
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

def _verifier_secret() -> bytes:
    secret = os.environ.get("CHALAMANDRA_VERIFIER_SECRET")
    if not secret:
        raise ValueError("CHALAMANDRA_VERIFIER_SECRET no configurado. El verificador no puede operar.")
    return secret.encode("utf-8")

def _canonical_payload(decision: dict) -> str:
    """Serializa TODOS los campos canónicos de la decisión de forma estable."""
    fields = [
        "decision_id", "candidate_id", "outcome", "criteria_checked",
        "reasons", "missing_evidence", "verifier_id", "evidence_digest",
        "decided_at", "trace_id"
    ]
    payload = {k: decision.get(k) for k in fields}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)

def generate_signature(decision: dict) -> str:
    """Genera la firma HMAC-SHA256 sobre TODOS los campos canónicos."""
    return hmac.new(_verifier_secret(), _canonical_payload(decision).encode("utf-8"), hashlib.sha256).hexdigest()

def record_acceptance(decision: dict[str, Any]) -> str:
    decision_id = decision.get("decision_id")
    if not decision_id:
        raise ValueError("decision_id es requerido")
    
    provided_sig = decision.get("signature")
    if not provided_sig:
        raise ValueError("Firma criptográfica (signature) requerida para aceptar una decisión.")
    
    expected_sig = generate_signature(decision)
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise ValueError("Firma criptográfica inválida. Verificador no autorizado o decisión alterada.")
    
    # Validación de evidencia: el evidence_digest debe coincidir con el candidato almacenado
    from api import candidate_store
    cid = decision.get("candidate_id")
    if not cid:
        raise ValueError("candidate_id faltante")
    candidate = candidate_store.get_candidate(str(cid))
    if not candidate:
        raise ValueError("Candidato no encontrado para verificar la evidencia.")
    expected_evidence_digest = candidate_store.compute_evidence_digest(candidate)
    if decision.get("evidence_digest") != expected_evidence_digest:
        raise ValueError("El evidence_digest no coincide con la evidencia del candidato.")
    
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
        "trace_id": decision.get("trace_id"),
        "signature": provided_sig
    }
    _append(_acceptance_file(), record)
    return decision_id

def get_acceptance(candidate_id: str) -> dict | None:
    latest = None
    for rec in _read_all(_acceptance_file()):
        if rec.get("candidate_id") == candidate_id:
            latest = rec
    return latest
