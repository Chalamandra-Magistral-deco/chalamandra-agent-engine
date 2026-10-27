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
