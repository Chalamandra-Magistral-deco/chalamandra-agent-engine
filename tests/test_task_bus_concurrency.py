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
