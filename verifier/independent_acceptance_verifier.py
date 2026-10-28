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
    SigningKey = None

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


def validate_decision(decision: Any, *, verifier_id: str) -> dict[str, Any]:
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
