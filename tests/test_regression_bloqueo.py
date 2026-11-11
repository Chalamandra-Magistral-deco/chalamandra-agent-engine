"""Regresión: un contrato económico incompleto se bloquea antes de CatedralX."""
import json
from datetime import datetime, timezone

from api import worker_loop
from conftest import sign_market_payload


def test_worker_blocks_incomplete_economic_contract_without_accepting_or_publishing(monkeypatch):
    trace_id = "1234567890abcdef1234567890abcdef"
    observed_at = datetime.now(timezone.utc).isoformat()

    decision = {
        "result_id": "blocked-missing-governance-input",
        "status": "BLOCKED_MISSING_INPUT",
        "engine": "CatedralX",
        "decision": "HOLD",
        "evidence": [],
        "assumptions": [],
        "risks": [],
        "provenance": [],
        "trace_id": trace_id,
        "evaluation_hash": "d" * 64,
        "analysis_only": True,
        "orders_created": False,
        "orders_executed": False,
        "missing_inputs": ["dynamic_constraints", "central_dimensions_bp"],
    }
    engine_calls = []
    monkeypatch.setattr(
        worker_loop, "_run_catedralx",
        lambda payload: engine_calls.append(payload) or decision,
    )

    saved_candidates = []
    completions = []
    monkeypatch.setattr(
        worker_loop.candidate_store,
        "save_candidate",
        lambda candidate: saved_candidates.append(candidate),
    )
    monkeypatch.setattr(
        worker_loop.task_bus,
        "mark_task_completed",
        lambda task_id, candidate_result, result_id=None: completions.append(
            {
                "task_id": task_id,
                "candidate_result": candidate_result,
                "result_id": result_id,
            }
        ),
    )

    task = {
        "task_id": "task-blocked-missing-input",
        "objective": "catedralx_economic_evaluation",
        "payload": json.dumps(
            sign_market_payload({
                "historical_prices": [100.0 + index * 0.01 for index in range(30)],
                "current_price": 100.29,
                "observed_at": observed_at,
                "source": "synthetic_regression_fixture",
            })
        ),
        "trace_id": trace_id,
    }

    result = worker_loop.process_task(task)

    assert result["status"] == "blocked"
    assert "BLOCKED_MISSING_INPUT" in result["summary"]
    assert len(engine_calls) == 1
    assert result["decision_result"] == decision
    assert result["candidate_id"] == decision["result_id"]
    assert len(saved_candidates) == 1
    assert saved_candidates[0]["status"] == "blocked"
    assert saved_candidates[0]["decision_result"] == decision
    assert len(completions) == 1
    assert completions[0]["task_id"] == task["task_id"]
    assert completions[0]["result_id"] == decision["result_id"]
    assert completions[0]["candidate_result"]["status"] == "blocked"
