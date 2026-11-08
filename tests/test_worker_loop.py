import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from api import worker_loop


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
        "objective": "financial_analysis",
        "payload": json.dumps({
            "historical_prices": [1.10, 1.11, 1.12],
            "current_price": 1.13,
        }),
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
        "objective": "financial_analysis",
        "payload": '{"historical_prices":[1.1,1.2],"current_price":1.3}',
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
                "trace_id": "trace-canonical",
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
    result = worker_loop._run_catedralx({
        "historical_prices": prices,
        "current_price": prices[-1],
        "observed_at": observed_at,
        "source": "test_verified_feed",
        "trace_id": "1234567890abcdef1234567890abcdef",
    })

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
        "human_value_bp": 8100,
        "distribution_bp": 7600,
        "resilience_bp": 9000,
        "evidence_bp": 9200,
        "reinvestment_surplus": "125.50",
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
    result = worker_loop._run_catedralx({
        "historical_prices": prices,
        "current_price": prices[-1],
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "test_verified_feed",
        "central_dimensions_bp": dimensions,
        "dynamic_constraints": constraints,
    })
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
        "objective": "catedralx_economic_proposal_review",
        "payload": json.dumps({
            "origin": "CatedralX.FEX.GOVERNANCE.economic_cycle",
            "cycle_id": "cycle-test-99",
            "proposal": {"proposal": "Analyze product demand"},
            "execution_boundary": {"prohibited": ["ad_spend", "publications"]},
        }),
        "success_criteria": json.dumps(["Produce candidate with sources"]),
        "required_tools": "[]",
        "trace_id": "trace-economic-proposal-test",
    }

    result = worker_loop.process_task(task)

    assert result["status"] == "candidate"
    assert "cycle-test-99" in captured["objective"]
    assert "Produce candidate with sources" in captured["objective"]
    assert "ad_spend" in captured["objective"]
    assert "No publiques" in captured["objective"]
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
    return {
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
    assert result["execution_boundary"]["independent_acceptance_required"] is True
    assert result["candidate_id"] == result["result_id"]
    assert result["evidence"][0]["observed_at"] == payload["measurement"]["observed_at"]
    assert result["evidence"][0]["data_source"] == payload["measurement"]["source"]
    assert result["orders_created"] is False
    assert result["orders_executed"] is False
    assert saved[0]["candidate_id"] == result["candidate_id"]
    assert completed[0]["result_id"] == result["candidate_id"]


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
            "objective": "financial_analysis",
            "payload": json.dumps({
                "historical_prices": [1.10, 1.11, 1.12],
                "current_price": 1.13,
            }),
            "trace_id": "trace-stale-lease-worker-test",
            "lease_token": "expired-worker-lease",
        })

    assert saved_candidates == []
