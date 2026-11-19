import hashlib
import json

from api.decision_result_integrity import verify_decision_result_integrity


def _record():
    record = {
        "result_id": "integrity-test-result",
        "status": "UNCERTAIN",
        "engine": "CatedralX",
        "decision": "hold",
        "evaluation_hash": "a" * 64,
        "trace_id": "1234567890abcdef1234567890abcdef",
        "analysis_only": True,
        "orders_created": False,
        "orders_executed": False,
        "evidence": [],
        "assumptions": [],
        "risks": [],
        "provenance": [],
    }
    canonical = lambda value: json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    record["contract_hash"] = hashlib.sha256(canonical(record)).hexdigest()
    chain = {
        "sequence": 1,
        "previous_record_hash": "0" * 64,
        "contract_hash": record["contract_hash"],
        "result_id": record["result_id"],
    }
    record["store_integrity"] = {
        "sequence": 1,
        "previous_record_hash": "0" * 64,
        "record_hash": hashlib.sha256(canonical(chain)).hexdigest(),
    }
    return record


def test_agents_recomputes_full_decision_result_hash_and_chain_node():
    record = _record()
    assert verify_decision_result_integrity(record) == (True, "ok")


def test_agents_rejects_tampered_decision_result_field():
    record = _record()
    record["decision"] = "buy"
    assert verify_decision_result_integrity(record) == (
        False, "decision_result_contract_hash_mismatch"
    )


def test_agents_rejects_tampered_store_chain_node():
    record = _record()
    record["store_integrity"]["record_hash"] = "f" * 64
    assert verify_decision_result_integrity(record) == (
        False, "decision_result_store_record_hash_mismatch"
    )
