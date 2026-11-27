"""Independent DecisionResult contract and ResultStore node verification."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def verify_decision_result_integrity(record: Any) -> tuple[bool, str]:
    """Recompute the complete contract hash and the returned history-node hash."""
    if not isinstance(record, dict):
        return False, "decision_result_not_object"
    contract_hash = record.get("contract_hash")
    if not isinstance(contract_hash, str) or not _HASH_RE.fullmatch(contract_hash):
        return False, "decision_result_contract_hash_missing_or_invalid"
    try:
        payload = {
            key: value for key, value in record.items()
            if key not in {"contract_hash", "store_integrity"}
        }
        computed = hashlib.sha256(_canonical_json(payload)).hexdigest()
    except (TypeError, ValueError, OverflowError):
        return False, "decision_result_contract_not_canonical_json"
    if computed != contract_hash:
        return False, "decision_result_contract_hash_mismatch"

    integrity = record.get("store_integrity")
    if not isinstance(integrity, dict):
        return False, "decision_result_store_integrity_missing"
    sequence = integrity.get("sequence")
    previous_hash = integrity.get("previous_record_hash")
    record_hash = integrity.get("record_hash")
    if (
        isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1
        or not isinstance(previous_hash, str) or not _HASH_RE.fullmatch(previous_hash)
        or not isinstance(record_hash, str) or not _HASH_RE.fullmatch(record_hash)
        or not isinstance(record.get("result_id"), str)
    ):
        return False, "decision_result_store_integrity_invalid"
    chain_payload = {
        "sequence": sequence,
        "previous_record_hash": previous_hash,
        "contract_hash": contract_hash,
        "result_id": record["result_id"],
    }
    expected = hashlib.sha256(_canonical_json(chain_payload)).hexdigest()
    if expected != record_hash:
        return False, "decision_result_store_record_hash_mismatch"
    return True, "ok"
