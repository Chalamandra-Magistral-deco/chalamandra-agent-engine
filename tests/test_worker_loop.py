import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from api import worker_loop
from conftest import sign_market_payload


def _economic_evaluation_payload():
    prices = [100.0 + index * 0.01 for index in range(30)]
    return sign_market_payload({
        "historical_prices": prices,
        "current_price": prices[-1],
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "synthetic_verified_test_feed",
        "dynamic_constraints": {
            "reinvestment_surplus": 1.0,
            "entropy_rate": 0.1,
            "entropy_time_scale": 1.0,
            "loop_gain": 0.5,
            "signal_count": 2,
            "optimal_signal_count": 3,
            "exposure": 0.1,
            "max_exposure": 0.5,
            "resilience": 0.9,
            "minimum_resilience": 0.8,
        },
        "central_dimensions_bp": {
            "viability_bp": 9000,
            "human_value_bp": 9000,
            "distribution_bp": 9000,
            "resilience_bp": 9000,
            "evidence_bp": 9000,
        },
    })


def test_financial_task_preserves_catedralx_result_id(monkeypatch):
    saved_candidates = []
    completion_calls = []

    decision = {
        "result_id": "canonical-result-123",
        "status": "ACCEPTED",
        "engine": "CatedralX",
        "decision": "buy",
        "orders_created": False,
        "orders_executed": False,
    }

    monkeypatch.setattr(
        worker_loop, "_run_catedralx", lambda payload: decision
    )
    monkeypatch.setattr(
        worker_loop.candidate_store,
        "save_candidate",
        lambda candidate: saved_candidates.append(candidate),
    )

    def fake_mark_task_completed(task_id, candidate_result, result_id=None, lease_token=None):
        completion_calls.append({
            "task_id": task_id,
            "candidate_result": candidate_result,
            "result_id": result_id,
        })

    monkeypatch.setattr(
        worker_loop.task_bus, "mark_task_completed",
        fake_mark_task_completed,
    )

    task = {
        "task_id": "test-task-123",
        "objective": "catedralx_economic_evaluation",
        "payload": json.dumps(_economic_evaluation_payload()),
        "trace_id": "trace-test-123",
    }

    result = worker_loop.process_task(task)

    assert result["status"] == "candidate"
    assert result["candidate_id"] == "canonical-result-123"
    assert len(saved_candidates) == 1
    assert saved_candidates[0]["candidate_id"] == "canonical-result-123"
    assert len(completion_calls) == 1
    assert completion_calls[0]["result_id"] == "canonical-result-123"
    assert completion_calls[0]["candidate_result"]["trace_id"] == "trace-test-123"


def test_accepted_without_result_id_is_blocked(monkeypatch):
    saved_candidates = []
    completion_calls = []

    monkeypatch.setattr(
        worker_loop, "_run_catedralx",
        lambda payload: {"status": "ACCEPTED", "decision": "hold"},
    )
    monkeypatch.setattr(
        worker_loop.candidate_store, "save_candidate",
        lambda candidate: saved_candidates.append(candidate),
    )
    monkeypatch.setattr(
        worker_loop.task_bus, "mark_task_completed",
        lambda task_id, candidate_result, result_id=None: completion_calls.append(
            {"task_id": task_id, "result_id": result_id,
             "candidate_result": candidate_result}
        ),
    )

    result = worker_loop.process_task({
        "task_id": "missing-result-id",
        "objective": "catedralx_economic_evaluation",
        "payload": json.dumps(_economic_evaluation_payload()),
        "trace_id": "trace-missing-id",
    })

    assert result["status"] == "blocked"
    assert "without" not in result["summary"].lower()
    assert "sin DecisionResult canónico válido" in result["summary"]
    assert len(saved_candidates) == 1
    assert len(completion_calls) == 1
    assert completion_calls[0]["result_id"] is None


def test_catedralx_subprocess_uses_worker_python(monkeypatch, tmp_path):
    import json
    import subprocess
    import sys

    captured = {}

    def fake_run(args, *, cwd, env, capture_output, text, timeout, check=False):
        captured.update(env=env.copy(), cwd=cwd, timeout=timeout)
        snapshot_path = Path(env["CATEDRALX_SNAPSHOT_PATH"])
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        assert snapshot["PERCEIVE"]["source"] == "test_verified_feed"
        assert snapshot["PERCEIVE"]["observed_at"] == observed_at
        assert snapshot["PERCEIVE"]["historial_precios"] == prices

        output = {
            "status": "ANALYSIS_COMPLETE",
            "decision_result": {
                "result_id": "canonical-result",
                "status": "ACCEPTED",
                "engine": "CatedralX",
                "decision": "hold",
                "evaluation_hash": "b" * 64,
                "evaluation_gate": "ACCEPTED",
                "evidence": [],
                "assumptions": [],
                "risks": [],
                "provenance": [],
                "trace_id": "1234567890abcdef1234567890abcdef",
                "analysis_only": True,
                "orders_created": False,
                "orders_executed": False,
            },
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(output), "")

    monkeypatch.setattr(worker_loop, "_catedral_repo", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_run)

    monkeypatch.setenv("CATEDRALX_STATE_PATH", "/tmp/state-test.json")
    monkeypatch.setenv("CATEDRALX_RESULT_STORE_PATH", "/tmp/results-test.jsonl")
    monkeypatch.setenv("CATEDRALX_FEEDBACK_PATH", "/tmp/feedback-test.jsonl")
    monkeypatch.setenv("CATEDRALX_PANIC_PATH", "/tmp/panic-test")
    monkeypatch.setenv("UNRELATED_SECRET_SENTINEL", "must-not-propagate")
    monkeypatch.setenv("CATEDRALX_DSX_AUTH_SECRET", "must-not-propagate")
    monkeypatch.setenv("CATEDRALX_FEX_EVIDENCE_SECRET", "must-not-propagate")
    monkeypatch.setenv("CHALAMANDRA_CATEDRALX_JWT_SECRET", "must-not-propagate")

    prices = [1.10 + index * 0.001 for index in range(30)]
    observed_at = datetime.now(timezone.utc).isoformat()
    market_payload = sign_market_payload({
        "historical_prices": prices,
        "current_price": prices[-1],
        "observed_at": observed_at,
        "source": "test_verified_feed",
    })
    market_payload["trace_id"] = "1234567890abcdef1234567890abcdef"
    result = worker_loop._run_catedralx(market_payload)

    assert captured["env"]["CATEDRALX_PYTHON"] == sys.executable
    assert captured["env"]["CATEDRALX_TRACE_ID"] == "1234567890abcdef1234567890abcdef"
    assert captured["env"]["CATEDRALX_STATE_PATH"] == "/tmp/state-test.json"
    assert captured["env"]["CATEDRALX_RESULT_STORE_PATH"] == "/tmp/results-test.jsonl"
    assert captured["env"]["CATEDRALX_FEEDBACK_PATH"] == "/tmp/feedback-test.jsonl"
    assert captured["env"]["CATEDRALX_PANIC_PATH"] == "/tmp/panic-test"

    for name in (
        "UNRELATED_SECRET_SENTINEL",
        "CATEDRALX_DSX_AUTH_SECRET",
        "CATEDRALX_FEX_EVIDENCE_SECRET",
        "CHALAMANDRA_CATEDRALX_JWT_SECRET",
    ):
        assert name not in captured["env"]

    assert captured["cwd"] == tmp_path
    assert captured["timeout"] == 60
    assert result["status"] == "ACCEPTED"
    assert result["result_id"] == "canonical-result"


def test_catedralx_snapshot_transports_governance_inputs_without_rewriting(monkeypatch, tmp_path):
    import json
    import subprocess

    captured = {}
    dimensions = {
        "viability_bp": 7800,
        "human_value_bp": 8100,
        "distribution_bp": 7600,
        "resilience_bp": 9000,
        "evidence_bp": 9200,
    }
    constraints = {
        "reinvestment_surplus": 125.5,
        "entropy_rate": 0.1,
        "entropy_time_scale": 2.0,
        "loop_gain": 0.7,
        "signal_count": 3,
        "optimal_signal_count": 5,
        "exposure": 0.2,
        "max_exposure": 0.5,
        "resilience": 0.9,
        "minimum_resilience": 0.7,
    }

    def fake_run(args, *, cwd, env, capture_output, text, timeout, check=False):
        captured.update(json.loads(Path(env["CATEDRALX_SNAPSHOT_PATH"]).read_text()))
        output = {"status": "NO_CYCLE", "reason": "test_transport_only"}
        return subprocess.CompletedProcess(args, 2, json.dumps(output), "")

    monkeypatch.setattr(worker_loop, "_catedral_repo", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_run)
    prices = [100.0 + index for index in range(30)]
    market_payload = sign_market_payload({
        "historical_prices": prices,
        "current_price": prices[-1],
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "test_verified_feed",
    })
    market_payload.update({
        "trace_id": "1234567890abcdef1234567890abcdef",
        "central_dimensions_bp": dimensions,
        "dynamic_constraints": constraints,
    })
    result = worker_loop._run_catedralx(market_payload)
    assert result["status"] == "BLOCKED"
    assert captured["CENTRAL_DIMENSIONS_BP"] == dimensions
    assert captured["DYNAMIC_CONSTRAINTS"] == constraints


def test_catedralx_blocks_non_object_governance_inputs(monkeypatch):
    import subprocess

    def should_not_run(*args, **kwargs):
        raise AssertionError("invalid governance payload must not invoke CatedralX")

    monkeypatch.setattr(subprocess, "run", should_not_run)
    result = worker_loop._run_catedralx({
        "historical_prices": [100.0 + index for index in range(30)],
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "test_verified_feed",
        "central_dimensions_bp": [8000],
    })
    assert result == {
        "status": "BLOCKED",
        "reason": "central_dimensions_bp_must_be_object",
    }


@pytest.mark.parametrize("trace_id", [None, "", "not-a-trace", "0" * 32, "A" * 32])
def test_catedralx_blocks_missing_or_invalid_trace_before_subprocess(
    trace_id, monkeypatch
):
    import subprocess

    def should_not_run(*args, **kwargs):
        raise AssertionError("CatedralX must not run without a valid W3C trace ID")

    monkeypatch.setattr(subprocess, "run", should_not_run)
    payload = sign_market_payload({
        "historical_prices": [100.0 + index for index in range(30)],
        "current_price": 129.0,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "test_verified_feed",
    })
    if trace_id is not None:
        payload["trace_id"] = trace_id
    result = worker_loop._run_catedralx(payload)
    assert result == {"status": "BLOCKED", "reason": "missing_or_invalid_trace_id"}


def test_catedralx_blocks_decision_result_with_mismatched_trace(monkeypatch, tmp_path):
    import json
    import subprocess

    def fake_run(args, *, cwd, env, capture_output, text, timeout, check=False):
        output = {
            "decision_result": {
                "result_id": "trace-mismatch",
                "status": "ACCEPTED",
                "engine": "CatedralX",
                "decision": "hold",
                "evaluation_hash": "a" * 64,
                "evaluation_gate": "ACCEPTED",
                "evidence": [], "assumptions": [], "risks": [], "provenance": [],
                "trace_id": "fedcfedcfedcfedcfedcfedcfedcfedc",
                "analysis_only": True, "orders_created": False, "orders_executed": False,
            }
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(output), "")

    monkeypatch.setattr(worker_loop, "_catedral_repo", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_run)
    payload = sign_market_payload({
        "historical_prices": [100.0 + index for index in range(30)],
        "current_price": 129.0,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "test_verified_feed",
    })
    payload["trace_id"] = "1234567890abcdef1234567890abcdef"
    result = worker_loop._run_catedralx(payload)
    assert result == {
        "status": "BLOCKED",
        "reason": "decision_result_contract_invalid_or_trace_mismatch",
    }


def test_catedral_repo_is_configurable_and_defaults_to_sibling_checkout(monkeypatch, tmp_path):
    monkeypatch.setenv("CATEDRALX_PATH", str(tmp_path / "configured-engine"))
    assert worker_loop._catedral_repo() == (tmp_path / "configured-engine").resolve()

    monkeypatch.delenv("CATEDRALX_PATH", raising=False)
    expected = Path(worker_loop.__file__).resolve().parents[2] / "CatedralX"
    assert worker_loop._catedral_repo() == expected.resolve()



def test_worker_blocks_financial_input_without_observation_metadata(monkeypatch):
    import subprocess

    def should_not_run(*args, **kwargs):
        raise AssertionError("CatedralX must not run without provenance metadata")

    monkeypatch.setattr(subprocess, "run", should_not_run)
    result = worker_loop._run_catedralx({
        "historical_prices": [100.0 + i * 0.01 for i in range(30)],
        "current_price": 100.29,
    })
    assert result == {"status": "BLOCKED", "reason": "missing_observed_at"}


def test_worker_blocks_stale_observation_before_calling_catedralx(monkeypatch):
    import subprocess
    from datetime import timedelta

    def should_not_run(*args, **kwargs):
        raise AssertionError("CatedralX must not run on stale inputs")

    monkeypatch.setattr(subprocess, "run", should_not_run)
    stale_at = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    result = worker_loop._run_catedralx({
        "historical_prices": [100.0 + i * 0.01 for i in range(30)],
        "current_price": 100.29,
        "observed_at": stale_at,
        "source": "test_verified_feed",
    })
    assert result == {"status": "BLOCKED", "reason": "stale_observation"}


def test_general_task_stays_blocked_when_only_magis_gate_is_enabled(monkeypatch):
    saved_candidates = []
    completion_calls = []

    monkeypatch.setenv("CHALAMANDRA_MAGIS_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "false")
    monkeypatch.setattr(
        worker_loop.candidate_store, "save_candidate",
        lambda candidate: saved_candidates.append(candidate),
    )
    monkeypatch.setattr(
        worker_loop.task_bus, "mark_task_completed",
        lambda task_id, candidate_result, result_id=None: completion_calls.append(
            {"task_id": task_id, "candidate_result": candidate_result, "result_id": result_id}
        ),
    )

    def should_not_execute(*args, **kwargs):
        raise AssertionError("Master must not execute while model calls are disabled")

    monkeypatch.setattr(worker_loop.Master, "execute_task", should_not_execute)
    result = worker_loop.process_task({
        "task_id": "model-calls-disabled",
        "objective": "research competitors",
        "payload": "{}",
        "trace_id": "trace-model-calls-disabled",
    })

    assert result["status"] == "blocked"
    assert "desactivadas" in result["summary"]
    assert saved_candidates[0]["status"] == "blocked"
    assert completion_calls[0]["candidate_result"]["status"] == "blocked"



def test_general_task_passes_payload_and_success_criteria_to_master(monkeypatch):
    saved_candidates = []
    completions = []
    captured = {}

    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "true")

    class FakeMaster:
        def __init__(self):
            pass

        async def execute_task(self, objective, *, allowed_tools=None):
            captured["objective"] = objective
            captured["allowed_tools"] = allowed_tools
            return {"summary": "Proposal review candidate"}

    monkeypatch.setattr(worker_loop, "Master", FakeMaster)
    monkeypatch.setattr(
        worker_loop.candidate_store, "save_candidate",
        lambda candidate: saved_candidates.append(candidate),
    )
    monkeypatch.setattr(
        worker_loop.task_bus, "mark_task_completed",
        lambda task_id, candidate_result, result_id=None: completions.append(
            {"task_id": task_id, "candidate_result": candidate_result, "result_id": result_id}
        ),
    )

    task = {
        "task_id": "economic-proposal-test",
        "objective": "general_analysis_test",
        "payload": json.dumps({"topic": "product demand evidence"}),
        "success_criteria": json.dumps(["Produce candidate with sources"]),
        "required_tools": "[]",
        "trace_id": "trace-economic-proposal-test",
    }

    result = worker_loop.process_task(task)

    assert result["status"] == "candidate"
    assert "general_analysis_test" in captured["objective"]
    assert "Produce candidate with sources" in captured["objective"]
    assert "product demand evidence" in captured["objective"]
    assert captured["allowed_tools"] == []
    assert len(saved_candidates) == 1
    assert len(completions) == 1
    assert completions[0]["candidate_result"]["trace_id"] == "trace-economic-proposal-test"



def test_worker_blocks_tools_without_verified_effect_classification(monkeypatch):
    saved_candidates = []
    completions = []
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "true")

    class FakeMaster:
        def __init__(self):
            pass

        async def execute_task(self, objective, *, allowed_tools=None):
            raise AssertionError("unclassified tool must never reach Master")

    monkeypatch.setattr(worker_loop, "Master", FakeMaster)
    monkeypatch.setattr(
        worker_loop.candidate_store, "save_candidate",
        lambda candidate: saved_candidates.append(candidate),
    )
    monkeypatch.setattr(
        worker_loop.task_bus, "mark_task_completed",
        lambda task_id, candidate_result, result_id=None: completions.append(candidate_result),
    )
    task = {
        "task_id": "unclassified-tool-test",
        "objective": "read external data",
        "payload": "{}",
        "success_criteria": '["Produce a result"]',
        "required_tools": '["search"]',
        "execution_policy": '{"allowed_tools":["search"],"side_effects":"read_only"}',
        "trace_id": "trace-unclassified-tool-test",
    }
    result = worker_loop.process_task(task)
    assert result["status"] == "blocked"
    assert "clasificación de efectos verificada" in result["summary"]
    assert saved_candidates[0]["status"] == "blocked"
    assert completions[0]["status"] == "blocked"


def _approved_business_proposal_payload(cycle_id="cycle-deterministic-test"):
    payload = {
        "origin": "CatedralX.FEX.GOVERNANCE.economic_cycle",
        "cycle_id": cycle_id,
        "measurement": {
            "metric": "qualified_leads",
            "source": "verified_crm_export",
            "period": "2026-10",
            "unit": "leads",
            "formula": "count_unique",
            "value": 12,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        },
        "assessment": {
            "assessment": "The evidence is insufficient to justify an external campaign.",
            "evidence": "Verified CRM export is available for review.",
        },
        "proposal": {
            "proposal": "Analyze demand evidence for a product page.",
            "expected_outcome": "Generate a traceable candidate for independent review.",
            "verification_plan": "Compare qualified lead counts across approved periods.",
        },
        "authorization": {
            "approved": True,
            "approver": "business-owner-test",
            "rationale": "Authorize analysis only; no external actions.",
            "identity_verified": True,
            "identity_permissions": ["economic_cycle_authorize"],
            "identity_verification": "APPROVER_IDENTITY_VERIFIER",
            "identity_assertion": {"signed": "test-only-assertion", "subject": "business-owner-test"},
            "identity_context": {
                "purpose": "catedralx_economic_cycle_authorization",
                "cycle_id": cycle_id,
                "cycle_digest": "a" * 64,
                "proposal": {
                    "proposal": "Analyze demand evidence for a product page.",
                    "expected_outcome": "Generate a traceable candidate for independent review.",
                    "verification_plan": "Compare qualified lead counts across approved periods.",
                },
                "measurement": {
                    "metric": "qualified_leads", "source": "verified_crm_export",
                    "period": "2026-10", "unit": "leads", "formula": "count_unique",
                    "value": 12, "observed_at": "2026-11-08T00:00:00+00:00",
                },
                "verify_at": 1794115200,
            },
        },
        "_agents_ingress": {
            "subject": "catedralx-service", "scope": "orchestration:run", "verified": True,
        },
        "execution_boundary": {
            "allowed": ["analysis", "research", "candidate_generation"],
            "prohibited": [
                "publications", "external_contact", "ad_spend",
                "purchases", "financial_orders", "automatic_reinvestment",
            ],
            "independent_acceptance_required": True,
        },
    }
    payload["authorization"]["identity_context"]["measurement"] = dict(payload["measurement"])
    return payload


def test_authorized_business_proposal_runs_without_model_calls(monkeypatch):
    saved = []
    completed = []
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "false")
    monkeypatch.setattr(worker_loop.candidate_store, "save_candidate", lambda item: saved.append(item))
    monkeypatch.setattr(
        worker_loop.task_bus, "mark_task_completed",
        lambda task_id, candidate_result, result_id=None: completed.append(
            {"task_id": task_id, "candidate_result": candidate_result, "result_id": result_id}
        ),
    )

    def must_not_call_master(*args, **kwargs):
        raise AssertionError("Authorized economic validation must be deterministic when models are off")

    monkeypatch.setattr(worker_loop.Master, "execute_task", must_not_call_master)
    monkeypatch.setattr(
        worker_loop, "_verify_business_approver_identity",
        lambda payload, authorization, measurement, proposal: {
            "subject": "business-owner-test",
            "permissions": ["economic_cycle_authorize"],
            "identity_proof_digest": "c" * 64,
        },
    )
    payload = _approved_business_proposal_payload()
    task = {
        "task_id": "authorized-business-cycle-test",
        "objective": "catedralx_economic_proposal_review",
        "payload": json.dumps(payload),
        "trace_id": "trace-authorized-business-cycle",
        "success_criteria": json.dumps(["preserve proposal and approval"]),
        "required_tools": "[]",
    }

    result = worker_loop.process_task(task)

    assert result["status"] == "candidate"
    assert result["business_cycle_id"] == payload["cycle_id"]
    assert result["business_proposal"]["authorization"]["approved"] is True
    assert result["business_proposal"]["authorization"]["identity_verified"] is True
    assert result["business_proposal"]["authorization"]["identity_proof_digest"] == "c" * 64
    assert result["execution_boundary"]["independent_acceptance_required"] is True
    assert result["candidate_id"] == result["result_id"]
    assert result["evidence"][0]["observed_at"] == payload["measurement"]["observed_at"]
    assert result["evidence"][0]["data_source"] == payload["measurement"]["source"]
    assert result["orders_created"] is False
    assert result["orders_executed"] is False
    assert saved[0]["candidate_id"] == result["candidate_id"]
    assert saved[0]["catedralx_trace_id"] == "trace-authorized-business-cycle"
    assert completed[0]["result_id"] == result["candidate_id"]


def test_authorized_business_proposal_reverifies_real_ed25519_assertion_and_nonce(
    monkeypatch, tmp_path
):
    import time
    import uuid
    from nacl.signing import SigningKey

    catedralx_root = Path(
        os.getenv("CATEDRALX_PATH") or (Path(__file__).resolve().parents[2] / "CatedralX")
    ).resolve()
    monkeypatch.setenv("CATEDRALX_PATH", str(catedralx_root))
    monkeypatch.setenv("CATEDRALX_STATE_DIR", str(tmp_path / "catedralx-state"))
    signing_key = SigningKey.generate()
    monkeypatch.setenv(
        "CHALAMANDRA_APPROVER_PUBLIC_KEY",
        signing_key.verify_key.encode().hex(),
    )

    monkeypatch.syspath_prepend(str(catedralx_root / "elite"))
    from AEX.DECISION import ordenes

    payload = _approved_business_proposal_payload("cycle-real-ed25519-test")
    authorization = payload["authorization"]
    measurement = payload["measurement"]
    proposal = payload["proposal"]
    nonce = str(uuid.uuid4())
    assert ordenes._reclamar_nonce_atomico(
        nonce, "business-owner-test", payload["cycle_id"]
    ) is True

    now = int(time.time())
    identity_context = {
        "purpose": "catedralx_economic_cycle_authorization",
        "cycle_id": payload["cycle_id"],
        "cycle_digest": "f" * 64,
        "proposal": dict(proposal),
        "measurement": dict(measurement),
        "verify_at": now,
    }
    assertion = {
        "subject": "business-owner-test",
        "permissions": ["economic_cycle_authorize"],
        "purpose": identity_context["purpose"],
        "cycle_id": payload["cycle_id"],
        "cycle_digest": identity_context["cycle_digest"],
        "iat": now,
        "exp": now + 120,
        "nonce": nonce,
    }
    assertion["signature"] = signing_key.sign(
        ordenes._canonical_json_ed25519(assertion)
    ).signature.hex()
    authorization["identity_assertion"] = assertion
    authorization["identity_context"] = identity_context

    verified = worker_loop._verify_business_approver_identity(
        payload, authorization, measurement, proposal
    )
    assert verified["subject"] == "business-owner-test"
    assert verified["permissions"] == ["economic_cycle_authorize"]
    assert len(verified["identity_proof_digest"]) == 64

    tampered = dict(authorization)
    tampered["identity_assertion"] = {**assertion, "signature": "00" * 64}
    with pytest.raises(ValueError, match="human_identity_signature_or_nonce_invalid"):
        worker_loop._verify_business_approver_identity(
            payload, tampered, measurement, proposal
        )


def test_authorized_business_proposal_rejects_missing_signed_identity_proof():
    payload = _approved_business_proposal_payload()
    payload.pop("_agents_ingress", None)
    authorization = payload["authorization"]
    authorization.pop("identity_assertion")
    with pytest.raises(ValueError, match="human_identity_assertion_missing"):
        worker_loop._verify_business_approver_identity(
            payload, authorization, payload["measurement"], payload["proposal"]
        )


def test_authorized_business_proposal_fails_closed_without_approval(monkeypatch):
    saved = []
    completed = []
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "false")
    monkeypatch.setattr(worker_loop.candidate_store, "save_candidate", lambda item: saved.append(item))
    monkeypatch.setattr(
        worker_loop.task_bus, "mark_task_completed",
        lambda task_id, candidate_result, result_id=None: completed.append(candidate_result),
    )
    payload = _approved_business_proposal_payload()
    payload["authorization"]["approved"] = False

    result = worker_loop.process_task({
        "task_id": "unauthorized-business-cycle-test",
        "objective": "catedralx_economic_proposal_review",
        "payload": json.dumps(payload),
        "trace_id": "trace-unauthorized-business-cycle",
        "success_criteria": "[]",
        "required_tools": "[]",
    })

    assert result["status"] == "blocked"
    assert "human_authorization_missing" in result["summary"]
    assert result["evidence"] == []
    assert saved[0]["status"] == "blocked"
    assert completed[0]["status"] == "blocked"


def test_stale_lease_does_not_publish_candidate(monkeypatch):
    saved_candidates = []
    monkeypatch.setattr(
        worker_loop, "_run_catedralx",
        lambda payload: {
            "result_id": "stale-lease-result",
            "status": "ACCEPTED",
            "decision": "hold",
            "orders_created": False,
            "orders_executed": False,
        },
    )
    monkeypatch.setattr(
        worker_loop.candidate_store, "save_candidate",
        lambda candidate: saved_candidates.append(candidate),
    )
    monkeypatch.setattr(
        worker_loop.task_bus, "mark_task_completed",
        lambda *args, **kwargs: False,
    )

    with pytest.raises(RuntimeError, match="lease is missing, invalid, or expired"):
        worker_loop.process_task({
            "task_id": "stale-lease-worker-test",
            "objective": "catedralx_economic_evaluation",
            "payload": json.dumps(_economic_evaluation_payload()),
            "trace_id": "trace-stale-lease-worker-test",
            "lease_token": "expired-worker-lease",
        })

    assert saved_candidates == []


def _market_payload(**extra):
    prices = [1.10 + i * 0.001 for i in range(30)]
    payload = sign_market_payload({
        "historical_prices": prices,
        "current_price": prices[-1],
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "test_verified_feed",
        **extra,
    })
    payload["trace_id"] = "1234567890abcdef1234567890abcdef"
    return payload


def test_catedralx_unserializable_snapshot_leaves_no_temp_file(monkeypatch, tmp_path):
    import subprocess
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(worker_loop, "_catedral_repo", lambda: tmp_path)

    def must_not_run(*args, **kwargs):
        raise AssertionError("el motor no debe ejecutarse")

    monkeypatch.setattr(subprocess, "run", must_not_run)
    result = worker_loop._run_catedralx(
        _market_payload(dynamic_constraints={"x": float("nan")})
    )
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "snapshot_not_serializable"
    assert list(tmp_path.glob("*.json")) == []


def test_catedralx_timeout_returns_failed_and_cleans_up(monkeypatch, tmp_path):
    import subprocess
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(worker_loop, "_catedral_repo", lambda: tmp_path)

    def hung(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs.get("timeout", 60))

    monkeypatch.setattr(subprocess, "run", hung)
    result = worker_loop._run_catedralx(_market_payload())
    assert result == {"status": "failed", "reason": "catedralx_timeout"}
    assert list(tmp_path.glob("*.json")) == []
