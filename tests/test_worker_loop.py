import json
from pathlib import Path

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

    def fake_mark_task_completed(task_id, candidate_result, result_id=None):
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
    assert "sin result_id" in result["summary"]
    assert len(saved_candidates) == 1
    assert len(completion_calls) == 1
    assert completion_calls[0]["result_id"] is None


def test_catedralx_subprocess_uses_worker_python(monkeypatch, tmp_path):
    import json
    import subprocess
    import sys

    captured = {}

    def fake_run(args, *, cwd, env, capture_output, text, timeout):
        captured.update(env=env.copy(), cwd=cwd, timeout=timeout)
        snapshot_path = Path(env["CATEDRALX_SNAPSHOT_PATH"])
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        assert snapshot["PERCEIVE"]["source"] == "worker_request"

        output = {
            "status": "ANALYSIS_COMPLETE",
            "decision_result": {
                "result_id": "canonical-result",
                "status": "ACCEPTED",
                "orders_created": False,
                "orders_executed": False,
            },
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(output), "")

    monkeypatch.setattr(worker_loop, "_catedral_repo", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = worker_loop._run_catedralx({
        "historical_prices": [1.1, 1.2, 1.3],
        "current_price": 1.4,
    })

    assert captured["env"]["CATEDRALX_PYTHON"] == sys.executable
    assert captured["cwd"] == tmp_path
    assert captured["timeout"] == 60
    assert result["status"] == "ACCEPTED"
    assert result["result_id"] == "canonical-result"
