"""Standalone acceptance signer; keep its private seed outside the Agents API process.

Input: one JSON decision on stdin. Output: same decision plus Ed25519 signature.
Signing requires an explicit confirmation argument bound to the candidate ID and
a verifier identity provisioned in this process environment.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from api.crypto_signer import create_canonical_payload  # noqa: E402

try:
    from nacl.signing import SigningKey
except ImportError:  # pragma: no cover - exercised by deployment startup checks
    SigningKey = None  # type: ignore[misc, assignment]

CANONICAL_FIELDS = (
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
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TRACE_ID_RE = re.compile(r"^(?!0{32}$)[0-9a-f]{32}$")
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def validate_decision(decision: Any, *, verifier_id: str) -> dict[str, Any]:
    """
    Valida y normaliza el contrato canónico de una decisión de aceptación.

    Args:
        decision (Any): Objeto decodificado de JSON que debe contener exactamente los
            campos incluidos en `CANONICAL_FIELDS`.
        verifier_id (str): Identidad provisionada para este proceso; debe coincidir
            con la identidad indicada dentro de la decisión.

    Returns:
        dict[str, Any]: Copia con los campos canónicos y en el orden del contrato.

    Raises:
        ValueError: Si el objeto no es un diccionario, faltan o sobran campos, hay
            tipos/valores inválidos, la identidad no coincide, el hash no es SHA-256
            hexadecimal, la fecha no tiene zona horaria, o la decisión no satisface
            las reglas para `accepted`/`rejected`.

    Side effects:
        Ninguno; no firma ni persiste la decisión.

    Example:
        validated = validate_decision(payload, verifier_id="verifier-staging")
    """
    if not isinstance(decision, dict):
        raise ValueError("decision_must_be_json_object")
    missing = [field for field in CANONICAL_FIELDS if field not in decision]
    if missing:
        raise ValueError("missing_fields:" + ",".join(missing))
    extras = sorted(set(decision) - set(CANONICAL_FIELDS))
    if extras:
        raise ValueError("unexpected_fields:" + ",".join(extras))

    for field in ("decision_id", "candidate_id", "verifier_id", "trace_id", "decided_at"):
        value = decision[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"invalid_{field}")

    if decision["verifier_id"] != verifier_id:
        raise ValueError("verifier_identity_mismatch")

    if not TRACE_ID_RE.fullmatch(decision["trace_id"]):
        raise ValueError("trace_id_must_be_nonzero_w3c_trace_id")

    if not isinstance(decision["evidence_digest"], str) or not SHA256_RE.fullmatch(
        decision["evidence_digest"]
    ):
        raise ValueError("evidence_digest_must_be_sha256_hex")

    if decision["outcome"] not in {"accepted", "rejected"}:
        raise ValueError("invalid_outcome")

    for field in ("criteria_checked", "reasons", "missing_evidence"):
        value = decision[field]
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError(f"invalid_{field}")

    try:
        timestamp = decision["decided_at"]
        if not RFC3339_RE.fullmatch(timestamp):
            raise ValueError("decided_at_must_be_rfc3339_date_time")
        parsed = datetime.fromisoformat(
            timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("decided_at_invalid_iso8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("decided_at_requires_timezone")

    if decision["outcome"] == "accepted":
        if not decision["criteria_checked"]:
            raise ValueError("accepted_decision_requires_checked_criteria")
        if decision["reasons"] or decision["missing_evidence"]:
            raise ValueError("accepted_decision_cannot_have_reasons_or_missing_evidence")
    elif not decision["reasons"] and not decision["missing_evidence"]:
        raise ValueError("rejected_decision_requires_reason_or_missing_evidence")

    return {field: decision[field] for field in CANONICAL_FIELDS}


def main() -> int:
    """
    Lee una decisión por stdin, verifica confirmación explícita y emite su firma Ed25519.

    Args:
        None. La confirmación se lee del argumento CLI `--confirm` con forma
        `ACCEPT:<candidate_id>` o `REJECT:<candidate_id>`.

    Returns:
        int: Código 0 si se validó y firmó; 2 si falta configuración, la entrada es
        inválida o no coincide la confirmación.

    Raises:
        Los errores de validación/JSON/lectura previstos se capturan y se reportan por
        stderr; errores inesperados fuera de esos casos podrían propagarse.

    Side effects:
        Lee stdin con límite de 128 KiB y requiere `CHALAMANDRA_VERIFIER_ID` y
        `CHALAMANDRA_ED25519_SEED` de 64 caracteres hexadecimales. Firma el payload
        canónico; nunca imprime la semilla y no envía la decisión a la API.

    Example:
        # Ejecutado como proceso CLI con la seed provisionada fuera de AGENTS:
        # printf '%s' "$DECISION_JSON" | python -m verifier.independent_acceptance_verifier --confirm "ACCEPT:<candidate_id>"
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm",
        required=True,
        help="Explicit operator confirmation: ACCEPT:<candidate_id> or REJECT:<candidate_id>",
    )
    args = parser.parse_args()

    verifier_id = os.getenv("CHALAMANDRA_VERIFIER_ID", "").strip()
    if not verifier_id:
        print("verifier_identity_not_configured", file=sys.stderr)
        return 2
    seed = os.getenv("CHALAMANDRA_ED25519_SEED", "")
    if SigningKey is None:
        print("pynacl_not_installed_in_verifier_environment", file=sys.stderr)
        return 2
    if len(seed) != 64 or not re.fullmatch(r"[0-9a-fA-F]{64}", seed):
        print("ed25519_seed_not_configured", file=sys.stderr)
        return 2

    try:
        raw = sys.stdin.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError("decision_input_too_large")
        decision_input = json.loads(raw)
        decision = validate_decision(decision_input, verifier_id=verifier_id)
        action = "ACCEPT" if decision["outcome"] == "accepted" else "REJECT"
        expected_confirmation = action + ":" + decision["candidate_id"]
        if args.confirm != expected_confirmation:
            raise ValueError("operator_confirmation_mismatch")
        signed = dict(decision)
        private_key = SigningKey(bytes.fromhex(seed))
        signed["signature"] = private_key.sign(
            create_canonical_payload(decision)
        ).signature.hex()
    except (json.JSONDecodeError, TypeError, ValueError, OSError) as exc:
        print(str(exc) or type(exc).__name__, file=sys.stderr)
        return 2

    print(json.dumps(signed, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
