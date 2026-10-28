"""Canonical payload and Ed25519 verification for the Agents API.

This module deliberately contains no signing function. The private seed and
signing operation live only in verifier/independent_acceptance_verifier.py,
which is not copied into the production API image.
"""
import json
import os
import re
from typing import Any

try:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False


_CANONICAL_FIELDS = (
    "decision_id",
    "candidate_id",
    "outcome",
    "criteria_checked",
    "reasons",
    "missing_evidence",
    "verifier_id",
    "evidence_digest",
    "decided_at",
    "trace_id",
)


def create_canonical_payload(decision_record: dict[str, Any]) -> bytes:
    """Serializa de forma estable los campos cubiertos por la firma."""
    canonical = {key: decision_record.get(key) for key in _CANONICAL_FIELDS}
    return json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _require_nacl() -> None:
    if not NACL_AVAILABLE:
        raise RuntimeError("PyNaCl no está instalado; no se puede validar una firma Ed25519.")


def verify_decision(decision_record: dict[str, Any], signature_hex: str) -> bool:
    """Verifica exclusivamente con clave pública; no puede firmar decisiones."""
    _require_nacl()
    public_hex = os.getenv("CHALAMANDRA_VERIFIER_PUBLIC_KEY", "")
    if len(public_hex) != 64 or not re.fullmatch(r"[0-9a-fA-F]{64}", public_hex):
        raise ValueError(
            "CHALAMANDRA_VERIFIER_PUBLIC_KEY debe contener exactamente 64 caracteres hexadecimales"
        )
    try:
        public_key = VerifyKey(bytes.fromhex(public_hex))
        signature = bytes.fromhex(signature_hex)
        if len(signature) != 64:
            return False
        public_key.verify(create_canonical_payload(decision_record), signature)
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False
