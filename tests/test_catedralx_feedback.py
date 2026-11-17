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


def test_fex_full_lifecycle_through_api_worker_and_feedback(
    monkeypatch, tmp_path
):
    """API intake -> leased worker -> independent API acceptance -> FEX feedback."""
    import http.client
    import threading
    import time
    import uuid
    from types import SimpleNamespace

    import jwt
    from api import task_bus, worker_loop
    from api.server import create_server

    state_root = tmp_path / "state"
    monkeypatch.setenv("CHALAMANDRA_STATE_DIR", str(state_root))
    monkeypatch.setenv("CHALAMANDRA_CANDIDATE_HMAC_KEY", "ab" * 32)
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", "synthetic-acceptance-api-secret")
    monkeypatch.setenv(
        "CHALAMANDRA_CATEDRALX_JWT_SECRET", "synthetic-catedralx-service-secret"
    )
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "false")
    monkeypatch.setenv("CATEDRALX_PATH", str(CATEDRALX_ROOT))
    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_SECRET", "synthetic-webhook-secret-" + "x" * 16)

    # Exercise the real durable dispatcher without making a network request.
    # The safe target is a synthetic HTTPS receiver and post_json records a
    # successful HTTP response; no production endpoint or side effect is used.
    from api import subscription_store, webhook_dispatcher

    monkeypatch.setattr(
        subscription_store,
        "resolve_safe_url",
        lambda _url: SimpleNamespace(
            scheme="https",
            hostname="receiver.test",
            port=443,
            request_target="/accept",
            addresses=("203.0.113.10",),
        ),
    )
    monkeypatch.setattr(
        webhook_dispatcher,
        "post_json",
        lambda _target, *, data, headers, timeout: 204,
    )
    subscription_store.create_subscription(
        "https://receiver.test/accept",
        ["result.accepted"],
    )

    # The existing helper installs a test-only identity verifier. A separate
    # test in this module verifies real Ed25519 approver assertions and nonce use.
    _inject_test_approver_identity_verifier(monkeypatch)
    cycle_ledger = tmp_path / "fex-cycle.jsonl"
    cycle_id = _authorized_cycle(cycle_ledger, monkeypatch)
    measurement = {
        "metric": "qualified_leads",
        "source": "synthetic_full_lifecycle_fixture",
        "period": "2026-11",
        "unit": "leads",
        "formula": "count_unique",
        "value": 12,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    proposal = {
        "proposal": "Analyze demand evidence for a product page.",
        "expected_outcome": "Produce a candidate for independent review.",
        "verification_plan": "Compare qualified leads in the next approved period.",
    }
    # Model CatedralX's canonical upstream attestation: the service JWT is
    # authenticated by the API, which injects trusted ingress metadata. The
    # worker then accepts only these canonical verified-identity claims.
    # Cryptographic human-assertion verification is covered by a separate test.
    authorization = {
        "approved": True,
        "approver": "test-owner",
        "rationale": "Synthetic authorization for analysis only.",
        "identity_verified": True,
        "identity_verification": "APPROVER_IDENTITY_VERIFIER",
        "identity_permissions": ["economic_cycle_authorize"],
    }
    payload = {
        "origin": "CatedralX.FEX.GOVERNANCE.economic_cycle",
        "cycle_id": cycle_id,
        "measurement": measurement,
        "assessment": {
            "assessment": "Synthetic measurement supports analysis only.",
            "evidence": "Generated test fixture; not live company evidence.",
        },
        "proposal": proposal,
        "authorization": authorization,
        "execution_boundary": {
            "allowed": ["analysis", "research", "candidate_generation"],
            "prohibited": [
                "publications", "external_contact", "ad_spend",
                "purchases", "financial_orders", "automatic_reinvestment",
            ],
            "independent_acceptance_required": True,
        },
    }

    service_token = jwt.encode(
        {
            "scope": "orchestration:run",
            "sub": "catedralx-service",
            "exp": int(time.time()) + 300,
        },
        os.environ["CHALAMANDRA_CATEDRALX_JWT_SECRET"],
        algorithm="HS256",
    )
    master = SimpleNamespace(tools=SimpleNamespace(available=lambda: []))
    server = create_server("127.0.0.1", 0, master=master)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    trace_id = "a1b2c3d4e5f60718293a4b5c6d7e8f90"

    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_address[1], timeout=10
        )
        request_body = json.dumps(
            {
                "objective": "catedralx_economic_proposal_review",
                "payload": payload,
                "permissions": {"allowed_tools": [], "side_effects": "none"},
                "success_criteria": ["Preserve authorization and evidence."],
            }
        )
        connection.request(
            "POST",
            "/internal/v1/orchestration/runs",
            body=request_body,
            headers={
                "Authorization": f"Bearer {service_token}",
                "Content-Type": "application/json",
                "traceparent": f"00-{trace_id}-0123456789abcdef-01",
            },
        )
        response = connection.getresponse()
        queued = json.loads(response.read().decode("utf-8"))
        connection.close()
        assert response.status == 202, queued
        assert queued["trace_id"] == trace_id

        leased = task_bus.get_next_task()
        assert leased is not None
        assert leased["task_id"] == queued["task_id"]
        candidate_result = worker_loop.process_task(leased)
        assert candidate_result["status"] == "candidate"
        assert candidate_result["business_cycle_id"] == cycle_id
        assert candidate_result["trace_id"] == trace_id
        assert candidate_result["catedralx_trace_id"] == trace_id
        assert candidate_result["business_proposal"]["authorization"]["identity_verified"] is True
        assert candidate_result["orders_created"] is False
        assert candidate_result["orders_executed"] is False

        candidate = candidate_store.get_candidate(candidate_result["candidate_id"])
        assert candidate is not None
        verifier_key = SigningKey(bytes(range(32, 64)))
        verifier_id = "synthetic-independent-fex-verifier"
        monkeypatch.setenv(
            "CHALAMANDRA_VERIFIER_PUBLIC_KEY",
            verifier_key.verify_key.encode().hex(),
        )
        monkeypatch.setenv("CHALAMANDRA_VERIFIER_ID", verifier_id)
        decision = _signed_acceptance(candidate, verifier_key, verifier_id)
        decision["decision_id"] = str(uuid.uuid4())
        decision["criteria_checked"] = [
            "candidate evidence digest",
            "trace continuity",
            "analysis-only boundary",
        ]
        decision["signature"] = verifier_key.sign(
            crypto_signer.create_canonical_payload(decision)
        ).signature.hex()
        acceptance_token = jwt.encode(
            {
                "scope": "acceptance:write",
                "sub": verifier_id,
                "exp": int(time.time()) + 300,
            },
            os.environ["CHALAMANDRA_JWT_SECRET"],
            algorithm="HS256",
        )
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_address[1], timeout=10
        )
        connection.request(
            "POST",
            "/v1/acceptance",
            body=json.dumps(decision),
            headers={
                "Authorization": f"Bearer {acceptance_token}",
                "Content-Type": "application/json",
            },
        )
        accepted_response = connection.getresponse()
        accepted = json.loads(accepted_response.read().decode("utf-8"))
        connection.close()
        assert accepted_response.status == 201, accepted
        assert accepted["outcome"] == "accepted"
        assert task_bus.get_result_task_statuses(candidate["candidate_id"])
        assert all(
            row["status"] == "accepted"
            for row in task_bus.get_result_task_statuses(candidate["candidate_id"])
        )

        reconciled = reconcile_feedback_once(ledger_path=cycle_ledger)
        assert reconciled["processed_cycles"] == [cycle_id]
        assert reconciled["processed_count"] == 1
        assert reconciled["errors"] == []
        cycle = next(
            row for row in resumir_ciclos(cycle_ledger)["cycles"]
            if row["cycle_id"] == cycle_id
        )
        assert cycle["events"] == [
            "measure", "evaluate", "propose", "authorize", "verify", "feedback"
        ]
        assert cycle["complete"] is True

        repeated = reconcile_feedback_once(ledger_path=cycle_ledger)
        assert repeated["processed_count"] == 0
        assert repeated["skipped"] == 1
        assert candidate["orders_created"] is False
        assert candidate["orders_executed"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
