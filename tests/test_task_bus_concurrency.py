"""Test de concurrencia sobre el TaskBus.

Aísla la DB a tmp_path para no contaminar la DB de producción.
Bug previo: escribía en ~/.local/state/chalamandra/agents/tasks.db.
"""
import threading

import pytest

from api import task_bus


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "tasks.db"
    monkeypatch.setattr(task_bus, "_db_path", lambda: db)
    task_bus.init_db()
    yield db


def test_concurrent_task_reservation(isolated_db):
    with task_bus._get_conn() as conn:
        conn.execute("DELETE FROM tasks")

    task_id = task_bus.enqueue_task(
        {"objective": "prueba concurrencia", "trace_id": "t1"}
    )
    resultados = []

    def worker():
        task = task_bus.get_next_task()
        if task and task["task_id"] == task_id:
            resultados.append(task_id)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(resultados) == 1, f"esperaba 1 ganador, obtuve {len(resultados)}"



def test_idempotency_replays_same_task_and_binds_full_contract(isolated_db):
    contract = {
        "task_id": "first-task",
        "run_id": "first-run",
        "objective": "catedralx_economic_proposal_review",
        "required_tools": [],
        "success_criteria": ["candidate includes evidence"],
        "payload": {"cycle_id": "cycle-1"},
        "trace_id": "trace-first",
    }
    first = task_bus.enqueue_task(contract, idempotency_key="catedralx:cycle-1")
    replay = task_bus.enqueue_task(
        {
            **contract,
            "task_id": "second-task",
            "run_id": "second-run",
            "trace_id": "trace-second",
        },
        idempotency_key="catedralx:cycle-1",
    )

    assert first == replay == "first-task"
    stored = task_bus.get_task_status(first)
    assert stored is not None
    assert stored["run_id"] == "first-run"
    assert len(task_bus.get_tasks_by_run("first-run")) == 1
    assert task_bus.get_tasks_by_run("second-run") == []

    with pytest.raises(ValueError, match="contrato distinto"):
        task_bus.enqueue_task(
            {
                **contract,
                "task_id": "third-task",
                "objective": "different_objective",
            },
            idempotency_key="catedralx:cycle-1",
        )


def test_expired_worker_claim_is_recovered_with_new_fencing_token(isolated_db):
    task_id = task_bus.enqueue_task({"objective": "recover after worker crash"})
    first_claim = task_bus.get_next_task(lease_seconds=120)

    assert first_claim is not None
    assert first_claim["task_id"] == task_id
    assert first_claim["attempt_count"] == 1
    first_token = first_claim["lease_token"]

    with task_bus._get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET lease_until = '2000-01-01T00:00:00+00:00' WHERE task_id = ?",
            (task_id,),
        )

    second_claim = task_bus.get_next_task(lease_seconds=120)
    assert second_claim is not None
    assert second_claim["task_id"] == task_id
    assert second_claim["attempt_count"] == 2
    assert second_claim["lease_token"] != first_token

    stale_write = task_bus.mark_task_completed(
        task_id,
        {"status": "candidate", "summary": "stale worker"},
        lease_token=first_token,
    )
    assert stale_write is False
    still_running = task_bus.get_task_status(task_id)
    assert still_running["status"] == "running"
    assert still_running["lease_token"] == second_claim["lease_token"]

    current_write = task_bus.mark_task_completed(
        task_id,
        {"status": "candidate", "summary": "current worker"},
        lease_token=second_claim["lease_token"],
    )
    assert current_write is True
    completed = task_bus.get_task_status(task_id)
    assert completed["status"] == "candidate"
    assert completed["lease_token"] is None
    assert completed["lease_until"] is None


def test_legacy_running_task_without_lease_is_recovered_when_stale(isolated_db):
    task_id = task_bus.enqueue_task({"objective": "legacy running task"})
    with task_bus._get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'running', updated_at = '2000-01-01T00:00:00+00:00' WHERE task_id = ?",
            (task_id,),
        )

    recovered = task_bus.get_next_task(lease_seconds=120)
    assert recovered is not None
    assert recovered["task_id"] == task_id
    assert recovered["status"] == "running"
    assert recovered["attempt_count"] == 1
    assert recovered["lease_token"]


def test_task_completion_requires_current_unexpired_lease(isolated_db):
    task_id = task_bus.enqueue_task({"objective": "lease is mandatory"})
    claim = task_bus.get_next_task(lease_seconds=120)
    assert claim is not None and claim["task_id"] == task_id

    assert task_bus.mark_task_completed(
        task_id, {"status": "candidate"}
    ) is False
    assert task_bus.get_task_status(task_id)["status"] == "running"

    with pytest.raises(ValueError, match="candidate status"):
        task_bus.mark_task_completed(
            task_id, {"status": "accepted"}, lease_token=claim["lease_token"]
        )
    assert task_bus.get_task_status(task_id)["status"] == "running"

    with task_bus._get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET lease_until = '2000-01-01T00:00:00+00:00' WHERE task_id = ?",
            (task_id,),
        )
    assert task_bus.mark_task_completed(
        task_id, {"status": "candidate"}, lease_token=claim["lease_token"]
    ) is False
    assert task_bus.get_task_status(task_id)["status"] == "running"
