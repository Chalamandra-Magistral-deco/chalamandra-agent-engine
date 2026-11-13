"""Integration tests for MAGIS worker execution with the real SQLite TaskBus."""
import json

import pytest

from api import task_bus, worker_loop


@pytest.fixture
def isolated_task_bus(tmp_path, monkeypatch):
    db = tmp_path / "magis-worker-tasks.db"
    monkeypatch.setattr(task_bus, "_db_path", lambda: db)
    task_bus.init_db()
    return db


class FakeMaster:
    async def execute_task(self, objective, *, allowed_tools=None, trace_id=None):
        return {"summary": "MAGIS integration candidate", "trace_id": trace_id}


def _enqueue_magis_task():
    return task_bus.enqueue_task({
        "task_id": "magis-real-lease-test",
        "run_id": "magis-real-lease-run",
        "product_code": "magis",
        "objective": "analyze supplied MAGIS context",
        "payload": {"topic": "lease integration"},
        "required_tools": [],
        "success_criteria": ["produce a reviewable candidate"],
        "trace_id": "trace-magis-real-lease",
    })


def _configure_worker(monkeypatch, saved):
    monkeypatch.setenv("CHALAMANDRA_MAGIS_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "false")
    monkeypatch.setattr(worker_loop, "Master", FakeMaster)
    monkeypatch.setattr(
        worker_loop.candidate_store,
        "save_candidate",
        lambda candidate: saved.append(candidate),
    )


def test_magis_worker_completes_and_publishes_with_real_unexpired_lease(
    isolated_task_bus, monkeypatch
):
    saved = []
    _configure_worker(monkeypatch, saved)
    task_id = _enqueue_magis_task()

    claimed = task_bus.get_next_task(lease_seconds=120)
    assert claimed is not None
    assert claimed["task_id"] == task_id
    assert claimed["product_code"] == "magis"
    assert claimed["lease_token"]

    result = worker_loop.process_task(claimed)

    assert result["status"] == "candidate"
    assert result["trace_id"] == "trace-magis-real-lease"
    assert len(saved) == 1
    assert saved[0]["candidate_id"] == result["candidate_id"]

    completed = task_bus.get_task_status(task_id)
    assert completed["status"] == "candidate"
    assert completed["lease_token"] is None
    assert task_bus.get_pending_candidate_publications() == [{
        "task_id": task_id,
        "candidate_id": result["candidate_id"],
        "candidate_result": result,
    }]


@pytest.mark.parametrize("lease_state", ["missing", "expired"])
def test_magis_worker_does_not_publish_without_current_lease(
    isolated_task_bus, monkeypatch, lease_state
):
    saved = []
    _configure_worker(monkeypatch, saved)
    task_id = _enqueue_magis_task()
    claimed = task_bus.get_next_task(lease_seconds=120)
    assert claimed is not None and claimed["task_id"] == task_id

    if lease_state == "missing":
        claimed.pop("lease_token")
    else:
        with task_bus._get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET lease_until = '2000-01-01T00:00:00+00:00' "
                "WHERE task_id = ?",
                (task_id,),
            )

    with pytest.raises(
        RuntimeError,
        match="worker lease is missing, invalid, or expired",
    ):
        worker_loop.process_task(claimed)

    assert saved == []
    assert task_bus.get_pending_candidate_publications() == []
    assert task_bus.get_task_status(task_id)["status"] == "running"
