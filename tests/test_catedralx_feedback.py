from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from nacl.signing import SigningKey

ROOT = Path(__file__).resolve().parents[2]
_configured_root = os.environ.get("CATEDRALX_PATH", "").strip()
CATEDRALX_ROOT = (
    Path(_configured_root).expanduser().resolve()
    if _configured_root
    else (ROOT / "CatedralX").resolve()
)
ELITE_ROOT = CATEDRALX_ROOT / "elite"
if not (ELITE_ROOT / "FEX" / "GOVERNANCE" / "economic_cycle.py").is_file():
    raise RuntimeError(
        f"CatedralX no contiene FEX/GOVERNANCE/economic_cycle.py: {CATEDRALX_ROOT}"
    )
if str(ELITE_ROOT) not in sys.path:
    sys.path.insert(0, str(ELITE_ROOT))

from FEX.GOVERNANCE.economic_cycle import registrar_evento, resumir_ciclos  # noqa: E402
from api import acceptance_store, candidate_store, crypto_signer  # noqa: E402
from api.catedralx_feedback import reconcile_feedback_once  # noqa: E402


def _inject_test_approver_identity_verifier(monkeypatch) -> None:
    from AEX.DECISION import ordenes

    monkeypatch.setattr(
        ordenes,
        "APPROVER_IDENTITY_VERIFIER",
        lambda assertion, context, amount: {
            "verified": isinstance(assertion, dict) and assertion.get("signed") == "test-only-assertion",
            "subject": assertion.get("subject") if isinstance(assertion, dict) else None,
            "permissions": ["economic_cycle_authorize"],
        },
    )


def _authorized_cycle(path: Path, monkeypatch) -> str:
    _inject_test_approver_identity_verifier(monkeypatch)
    cycle_id = registrar_evento(
        "measure",
        {"metric": "qualified_leads", "source": "synthetic_test", "period": "2026-10",
         "unit": "leads", "formula": "count_unique", "value": 3,
         "observed_at": datetime.now(timezone.utc).isoformat()},
        path=path,
    )
    registrar_evento("evaluate", {"assessment": "synthetic assessment", "evidence": "synthetic only"}, path=path, cycle_id=cycle_id)
    registrar_evento("propose", {"proposal": "synthetic proposal", "expected_outcome": "reviewable candidate",
                                  "verification_plan": "measure observed outcome later",
                                  "source": "synthetic_test_fixture", "method": "fixed_fixture",
                                  "evidence_ref": "test-fixture://catedralx-feedback/qualified_leads"},
                     path=path, cycle_id=cycle_id)
    registrar_evento("authorize", {"approved": True, "approver": "test-owner", "rationale": "analysis only",
                                  "identity_assertion": {"signed": "test-only-assertion", "subject": "test-owner"}},
                     path=path, cycle_id=cycle_id)
    return cycle_id


def _signed_acceptance(candidate: dict, signing_key: SigningKey, verifier_id: str) -> dict:
    record = {
        "decision_id": "decision-feedback-test",
        "candidate_id": candidate["candidate_id"],
        "outcome": "accepted",
        "criteria_checked": ["evidence digest", "trace continuity"],
        "reasons": [],
        "missing_evidence": [],
        "verifier_id": verifier_id,
        "evidence_digest": candidate_store.compute_evidence_digest(candidate),
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "trace_id": candidate["trace_id"],
    }
    record["signature"] = signing_key.sign(crypto_signer.create_canonical_payload(record)).signature.hex()
    return record


def test_verified_business_acceptance_closes_economic_cycle_once(monkeypatch, tmp_path):
    ledger = tmp_path / "impact_cycle.jsonl"
    cycle_id = _authorized_cycle(ledger, monkeypatch)
    signing_key = SigningKey(bytes(range(32)))
    verifier_id = "feedback-test-verifier"
    monkeypatch.setenv("CATEDRALX_PATH", str(CATEDRALX_ROOT))
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_PUBLIC_KEY", signing_key.verify_key.encode().hex())
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_ID", verifier_id)

    candidate = {
        "candidate_id": "candidate-feedback-test",
        "status": "candidate",
        "business_cycle_id": cycle_id,
        "trace_id": "1234567890abcdef1234567890abcdef",
        "evidence": [{"kind": "synthetic_test", "source": "test fixture"}],
        "orders_created": False,
        "orders_executed": False,
    }
    candidate_store.save_candidate(candidate)
    acceptance_store.record_acceptance(_signed_acceptance(candidate, signing_key, verifier_id))

    result = reconcile_feedback_once(ledger_path=ledger)
    assert result["processed_cycles"] == [cycle_id]
    assert result["processed_count"] == 1
    assert result["errors"] == []
    summary = resumir_ciclos(ledger)
    cycle = next(row for row in summary["cycles"] if row["cycle_id"] == cycle_id)
    assert cycle["events"] == ["measure", "evaluate", "propose", "authorize", "verify", "feedback"]
    assert cycle["complete"] is True

    repeated = reconcile_feedback_once(ledger_path=ledger)
    assert repeated["processed_count"] == 0
    assert repeated["skipped"] == 1
    records = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert sum(row.get("event_type") == "verify" for row in records) == 1
    assert sum(row.get("event_type") == "feedback" for row in records) == 1
    feedback = next(row for row in records if row.get("event_type") == "feedback")
    assert "no demuestra por sí solo" in feedback["payload"]["lesson"]


def test_invalid_signature_does_not_advance_business_cycle(monkeypatch, tmp_path):
    ledger = tmp_path / "impact_cycle.jsonl"
    cycle_id = _authorized_cycle(ledger, monkeypatch)
    verifier_id = "feedback-test-verifier"
    signing_key = SigningKey(bytes(range(32)))
    monkeypatch.setenv("CATEDRALX_PATH", str(CATEDRALX_ROOT))
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_PUBLIC_KEY", signing_key.verify_key.encode().hex())
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_ID", verifier_id)
    candidate = {"candidate_id": "candidate-invalid-signature", "status": "candidate",
                 "business_cycle_id": cycle_id, "trace_id": "a" * 32,
                 "evidence": [{"kind": "synthetic"}], "orders_created": False, "orders_executed": False}
    candidate_store.save_candidate(candidate)
    acceptance = _signed_acceptance(candidate, signing_key, verifier_id)
    acceptance["signature"] = "00" * 64
    path = acceptance_store._acceptance_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(acceptance) + "\n")

    result = reconcile_feedback_once(ledger_path=ledger)
    assert result["processed_count"] == 0
    assert result["errors"] == [{"candidate_id": candidate["candidate_id"], "reason": "signature_invalid"}]
    cycle = next(row for row in resumir_ciclos(ledger)["cycles"] if row["cycle_id"] == cycle_id)
    assert cycle["stage"] == "authorize"
