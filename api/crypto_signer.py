import json
import os
from typing import Dict, Any
try:
    from nacl.signing import SigningKey, VerifyKey
    from nacl.exceptions import BadSignatureError
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False

def create_canonical_payload(decision_record: Dict[str, Any]) -> bytes:
    """Crea una representación canónica (ordenada, sin espacios) de TODA la decisión."""
    canonical_dict = {
        "candidate_id": decision_record.get("candidate_id"),
        "outcome": decision_record.get("outcome"),
        "evidence_digest": decision_record.get("evidence_digest"),
        "trace_id": decision_record.get("trace_id"),
        "verifier_id": decision_record.get("verifier_id"),
        "decision_id": decision_record.get("decision_id"),
        "criteria_checked": decision_record.get("criteria_checked", []),
        "reasons": decision_record.get("reasons", []),
        "missing_evidence": decision_record.get("missing_evidence", []),
        "decided_at": decision_record.get("decided_at")
    }
    return json.dumps(canonical_dict, sort_keys=True, separators=(',', ':')).encode('utf-8')

def sign_decision(decision_record: Dict[str, Any]) -> str:
    if not NACL_AVAILABLE:
        raise RuntimeError("PyNaCl no está instalado. Ejecuta: pip install pynacl")
    
    secret_hex = os.getenv("CHALAMANDRA_ED25519_SEED") or os.getenv("CHALAMANDRA_VERIFIER_SECRET")
    if not secret_hex or len(secret_hex) < 64:
        raise ValueError("CHALAMANDRA_VERIFIER_SECRET debe ser una clave hex de 64 caracteres (32 bytes)")
        
    private_key = SigningKey(bytes.fromhex(secret_hex))
    payload = create_canonical_payload(decision_record)
    signed = private_key.sign(payload)
    return signed.signature.hex()

def verify_decision(decision_record: Dict[str, Any], signature_hex: str) -> bool:
    if not NACL_AVAILABLE:
        raise RuntimeError("PyNaCl no está instalado.")
    
    public_key_hex = os.getenv("CHALAMANDRA_VERIFIER_PUBLIC_KEY")
    if not public_key_hex:
        raise ValueError("CHALAMANDRA_VERIFIER_PUBLIC_KEY no está configurada")
        
    try:
        public_key = VerifyKey(bytes.fromhex(public_key_hex))
        payload = create_canonical_payload(decision_record)
        signature = bytes.fromhex(signature_hex)
        public_key.verify(payload, signature)
        return True
    except (BadSignatureError, ValueError):
        return False
