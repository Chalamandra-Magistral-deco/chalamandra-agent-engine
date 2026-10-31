"""Regresión Agents: conservar DecisionResult BLOCKED_MISSING_INPUT de CatedralX."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from api import worker_loop


def test_worker_preserves_blocked_missing_input_without_accepting_or_publishing(
    monkeypatch, tmp_path
):
    trace_id = "1234567890abcdef1234567890abcdef"
    result_id = "blocked-result-001"
    observed_at = datetime.now(timezone.utc).isoformat()
    decision = {
        "result_id": result_id,
        "status": "BLOCKED_MISSING_INPUT",
        "engine": "CatedralX",
        "decision": "HOLD",
        "evaluation_hash": "a" * 64,
        "evaluation_gate": "BLOCKED_UNCERTAINTY",
        "missing_inputs": [
            "distribution",
            "evidence",
            "human_value",
            "reinvestment",
            "resilience",
        ],
        "blocking_reasons": ["METRICAS_NO_CALIBRADAS"],
        "evaluation_reason": "GOBERNANZA_NO_VERIFICADA",
        "evidence": [],
        "assumptions": [],
        "risks": [],
        "provenance": [],
        "trace_id": trace_id,
        "analysis_only": True,
        "orders_created": False,
        "orders_executed": False,
    }
    engine_output = {
        "status": "ANALYSIS_COMPLETE",
        "decision_result": decision,
    }

    def fake_run(args, *, cwd, env, capture_output, text, timeout, check=False):
        snapshot = json.loads(
            Path(env["CATEDRALX_SNAPSHOT_PATH"]).read_text(encoding="utf-8")
        )
        assert snapshot["PERCEIVE"]["observed_at"] == observed_at
        assert snapshot["PERCEIVE"]["source"] == "synthetic_regression_fixture"
        assert cwd == tmp_path
        assert timeout == 60
        return subprocess.CompletedProcess(
            args, 0, json.dumps(engine_output, ensure_ascii=False), ""
        )

    monkeypatch.setattr(worker_loop, "_catedral_repo", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_run)

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
        "objective": "financial_analysis",
        "payload": json.dumps(
            {
                "historical_prices": [100.0 + index * 0.01 for index in range(30)],
                "current_price": 100.29,
                "observed_at": observed_at,
                "source": "synthetic_regression_fixture",
            }
        ),
        "trace_id": trace_id,
    }

    result = worker_loop.process_task(task)

    assert result["status"] == "blocked"
    assert result["candidate_id"] == result_id
    assert result["result_id"] == result_id
    assert result["decision_result"] == decision
    assert result["decision_result"]["status"] == "BLOCKED_MISSING_INPUT"
    assert result["decision_result"]["evaluation_reason"] == "GOBERNANZA_NO_VERIFICADA"
    assert result["decision_result"]["missing_inputs"] == decision["missing_inputs"]

    assert len(saved_candidates) == 1
    assert saved_candidates[0]["status"] == "blocked"
    assert saved_candidates[0]["decision_result"] == decision

    assert len(completions) == 1
    assert completions[0]["task_id"] == task["task_id"]
    assert completions[0]["result_id"] == result_id
    assert completions[0]["candidate_result"]["status"] == "blocked"
