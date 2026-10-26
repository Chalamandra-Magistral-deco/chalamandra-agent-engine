
import os
from api.crypto_signer import sign_decision, verify_decision
from api.trace_parser import parse_traceparent

# Setup temporal para pruebas (en producción usar variables reales)
os.environ["CHALAMANDRA_VERIFIER_SECRET"] = "a" * 64  # 32 bytes en hex
from nacl.signing import SigningKey
sk = SigningKey(bytes.fromhex(os.environ["CHALAMANDRA_VERIFIER_SECRET"]))
os.environ["CHALAMANDRA_VERIFIER_PUBLIC_KEY"] = sk.verify_key.encode().hex()

def get_base_record():
    return {
        "candidate_id": "cand-1", "outcome": "accepted", "evidence_digest": "abc", 
        "trace_id": "tid-1", "verifier_id": "v1", "decision_id": "d1", 
        "criteria_checked": [], "reasons": ["ok"], "missing_evidence": [], 
        "decided_at": "2026-10-25T12:00:00Z"
    }

def test_signature_fails_on_mutated_reasons():
    record = get_base_record()
    sig = sign_decision(record)
    record["reasons"] = ["tampered"]
    assert verify_decision(record, sig) is False

def test_signature_fails_on_mutated_decided_at():
    record = get_base_record()
    sig = sign_decision(record)
    record["decided_at"] = "2026-10-25T12:05:00Z"
    assert verify_decision(record, sig) is False

def test_w3c_rejects_uppercase_hex():
    assert parse_traceparent("00-12345678901234567890123456789012-1234567890123456-00") is not None
    assert parse_traceparent("00-1234567890123456789012345678901A-1234567890123456-00") is None

def test_w3c_rejects_all_zero_trace_id():
    assert parse_traceparent("00-00000000000000000000000000000000-1234567890123456-00") is None

def test_w3c_rejects_all_zero_parent_id():
    assert parse_traceparent("00-12345678901234567890123456789012-0000000000000000-00") is None

def test_w3c_rejects_version_ff():
    assert parse_traceparent("ff-12345678901234567890123456789012-1234567890123456-00") is None
