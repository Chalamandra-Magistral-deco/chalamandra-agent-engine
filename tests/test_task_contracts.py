"""Contracts and routing regressions for explicit financial task types."""
import json
from datetime import datetime, timezone

from api import worker_loop
from conftest import sign_market_payload
from api.task_contracts import (
    CATEDRALX_ECONOMIC_EVALUATION,
    FINANCIAL_MARKET_ANALYSIS,
    validate_task_contract,
)


def _market_payload():
    prices = [100.0 + index * 0.01 for index in range(30)]
    return sign_market_payload({
        "historical_prices": prices,
        "current_price": prices[-1],
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "controlled_verified_test_feed",
    })


def _economic_payload():
    return {
        **_market_payload(),
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
    }


def _patch_persistence(monkeypatch, saved, completed):
    monkeypatch.setattr(worker_loop.candidate_store, "save_candidate", lambda value: saved.append(value))
    monkeypatch.setattr(
        worker_loop.task_bus,
        "mark_task_completed",
        lambda task_id, candidate_result, result_id=None, **kwargs: completed.append(
            {"task_id": task_id, "candidate_result": candidate_result, "result_id": result_id}
        ) or True,
    )


def test_market_analysis_contract_is_valid_and_missing_governance_reaches_catedralx():
    assert validate_task_contract(FINANCIAL_MARKET_ANALYSIS, _market_payload()) is None
    assert validate_task_contract(CATEDRALX_ECONOMIC_EVALUATION, _market_payload()) is None


def test_market_source_attestation_rejects_tampering():
    payload = _market_payload()
    payload["historical_prices"][0] += 0.01
    assert validate_task_contract(FINANCIAL_MARKET_ANALYSIS, payload) == (
        "market_source_attestation_signature_invalid"
    )


def test_market_source_attestation_fails_closed_without_trust_anchor(monkeypatch):
    payload = _market_payload()
    monkeypatch.delenv("CHALAMANDRA_MARKET_SOURCE_PUBLIC_KEY", raising=False)
    assert validate_task_contract(FINANCIAL_MARKET_ANALYSIS, payload) == (
        "market_source_verification_not_configured"
    )


def test_market_source_attestation_is_required():
    payload = _market_payload()
    payload.pop("source_attestation")
    assert validate_task_contract(FINANCIAL_MARKET_ANALYSIS, payload) == (
        "market_source_attestation_required"
    )


def test_central_dimensions_rejects_noncanonical_reinvestment_field():
    payload = _economic_payload()
    payload["central_dimensions_bp"]["reinvestment_surplus"] = "1.0"
    assert validate_task_contract(CATEDRALX_ECONOMIC_EVALUATION, payload) == (
        "central_dimensions_bp_noncanonical_fields:reinvestment_surplus"
    )


def test_central_dimensions_validates_explicit_viability_bp():
    payload = _economic_payload()
    payload["central_dimensions_bp"]["viability_bp"] = 10001
    assert validate_task_contract(CATEDRALX_ECONOMIC_EVALUATION, payload) == (
        "central_dimensions_bp_invalid_fields:viability_bp"
    )


def test_extreme_numeric_governance_value_is_rejected_without_overflow():
    payload = _economic_payload()
    payload["dynamic_constraints"]["reinvestment_surplus"] = 10**10000
    assert validate_task_contract(CATEDRALX_ECONOMIC_EVALUATION, payload) == (
        "dynamic_constraints_invalid_fields:reinvestment_surplus"
    )


def test_legacy_financial_alias_fails_closed():
    assert validate_task_contract("financial_analysis", _market_payload()) == (
        "ambiguous_legacy_task_type_use_explicit_financial_task_type"
    )


def test_market_analysis_never_invokes_catedralx(monkeypatch):
    saved, completed = [], []
    _patch_persistence(monkeypatch, saved, completed)
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "true")

    def forbidden_catedralx(_payload):
        raise AssertionError("market analysis must not activate the economic cycle")

    class FakeMaster:
        async def execute_task(self, objective, *, allowed_tools=None):
            assert "financial_market_analysis" in objective
            assert allowed_tools == []
            return {"summary": "controlled market analysis"}

    monkeypatch.setattr(worker_loop, "_run_catedralx", forbidden_catedralx)
    monkeypatch.setattr(worker_loop, "Master", FakeMaster)
    trace_id = "1234567890abcdef1234567890abcdef"
    result = worker_loop.process_task({
        "task_id": "market-route-controlled",
        "objective": FINANCIAL_MARKET_ANALYSIS,
        "payload": json.dumps(_market_payload()),
        "trace_id": trace_id,
    })

    assert result["status"] == "candidate"
    assert result["engine"] == "ChalamandraAgentEngine"
    assert result["decision_result"] is None
    assert result["trace_id"] == trace_id
    assert len(saved) == len(completed) == 1


def test_economic_evaluation_uses_only_explicit_typed_route(monkeypatch):
    saved, completed = [], []
    _patch_persistence(monkeypatch, saved, completed)
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "false")
    trace_id = "abcdefabcdefabcdefabcdefabcdefab"
    observed_payload = {}
    decision = {
        "result_id": "controlled-catedralx-result",
        "status": "ACCEPTED",
        "engine": "CatedralX",
        "decision": "buy",
        "evidence": [],
        "assumptions": [],
        "risks": [],
        "provenance": [],
        "trace_id": trace_id,
        "evaluation_hash": "a" * 64,
        "analysis_only": True,
        "orders_created": False,
        "orders_executed": False,
    }

    def fake_catedralx(payload):
        observed_payload.update(payload)
        return decision

    class ForbiddenMaster:
        def __init__(self):
            raise AssertionError("economic evaluation must route to CatedralX, not Master")

    monkeypatch.setattr(worker_loop, "_run_catedralx", fake_catedralx)
    monkeypatch.setattr(worker_loop, "Master", ForbiddenMaster)
    result = worker_loop.process_task({
        "task_id": "economic-route-controlled",
        "objective": CATEDRALX_ECONOMIC_EVALUATION,
        "payload": json.dumps(_economic_payload()),
        "trace_id": trace_id,
    })

    assert result["status"] == "candidate"
    assert result["decision_result"] == decision
    assert result["catedralx_trace_id"] == trace_id
    assert observed_payload["dynamic_constraints"]["loop_gain"] == 0.5
    assert observed_payload["central_dimensions_bp"]["evidence_bp"] == 9000
    assert result["orders_created"] is False
    assert result["orders_executed"] is False
    # CatedralX ACCEPTED is not independent acceptance; the task remains a candidate.
    assert completed[0]["candidate_result"]["status"] == "candidate"


def test_economic_evaluation_forwards_missing_dimensions_to_catedralx(monkeypatch):
    payload = _economic_payload()
    del payload["central_dimensions_bp"]["evidence_bp"]
    assert validate_task_contract(CATEDRALX_ECONOMIC_EVALUATION, payload) is None
    observed = {}
    decision = {
        "result_id": "blocked-evidence-dimension",
        "status": "BLOCKED_MISSING_INPUT",
        "engine": "CatedralX",
        "decision": "HOLD",
        "evidence": [], "assumptions": [], "risks": [], "provenance": [],
        "trace_id": "fedcfedcfedcfedcfedcfedcfedcfedc",
        "evaluation_hash": "e" * 64, "analysis_only": True,
        "orders_created": False, "orders_executed": False,
        "missing_inputs": ["evidence_bp"],
    }
    monkeypatch.setattr(
        worker_loop, "_run_catedralx",
        lambda value: observed.update(value) or decision,
    )
    saved, completed = [], []
    _patch_persistence(monkeypatch, saved, completed)
    result = worker_loop.process_task({
        "task_id": "economic-missing-dimension",
        "objective": CATEDRALX_ECONOMIC_EVALUATION,
        "payload": json.dumps(payload),
        "trace_id": "fedcfedcfedcfedcfedcfedcfedcfedc",
    })
    assert result["status"] == "blocked"
    assert "BLOCKED_MISSING_INPUT" in result["summary"]
    assert result["decision_result"] == decision
    assert "evidence_bp" not in observed["central_dimensions_bp"]


def test_catedralx_blocked_decision_result_keeps_identity_and_never_accepts(monkeypatch):
    saved, completed = [], []
    _patch_persistence(monkeypatch, saved, completed)
    trace_id = "1234567890abcdef1234567890abcdef"
    decision = {
        "result_id": "blocked-dimension-result",
        "status": "BLOCKED_MISSING_INPUT",
        "engine": "CatedralX",
        "decision": "HOLD",
        "evidence": [],
        "assumptions": [],
        "risks": [],
        "provenance": [],
        "trace_id": trace_id,
        "evaluation_hash": "b" * 64,
        "analysis_only": True,
        "orders_created": False,
        "orders_executed": False,
        "missing_inputs": ["evidence"],
    }
    monkeypatch.setattr(worker_loop, "_run_catedralx", lambda _payload: decision)
    result = worker_loop.process_task({
        "task_id": "economic-blocked-result-controlled",
        "objective": CATEDRALX_ECONOMIC_EVALUATION,
        "payload": json.dumps(_economic_payload()),
        "trace_id": trace_id,
    })

    assert result["status"] == "blocked"
    assert result["candidate_id"] == decision["result_id"]
    assert result["result_id"] == decision["result_id"]
    assert result["decision_result"] == decision
    assert result["catedralx_trace_id"] == trace_id
    assert saved[0]["status"] == "blocked"
    assert completed[0]["result_id"] == decision["result_id"]
    assert completed[0]["candidate_result"]["status"] == "blocked"
