"""F-2 (liberación): los reintentos por expiración de lease tienen tope y dead-letter.

Sin tope, una tarea cuyo worker muera siempre se reencolaba cada 120 s
indefinidamente. Con el tope (MAX_TASK_ATTEMPTS = 5) la tarea pasa a estado
terminal 'dead_letter' y queda visible para inspección manual.
"""
import pytest

from api import task_bus

PAST_LEASE = "2000-01-01T00:00:00+00:00"


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "tasks.db"
    monkeypatch.setattr(task_bus, "_db_path", lambda: db)
    task_bus.init_db()
    return db


def _expire_lease(task_id):
    with task_bus._get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET lease_until = ? WHERE task_id = ?",
            (PAST_LEASE, task_id),
        )


def _crash_worker_once(task_id):
    """Simula la muerte del worker: expira el lease y deja que get_next_task reclame."""
    _expire_lease(task_id)
    return task_bus.get_next_task(lease_seconds=120)


def test_task_is_requeued_below_max_attempts(isolated_db):
    task_id = task_bus.enqueue_task({"objective": "tarea transitoria", "trace_id": "t1"})
    claimed = task_bus.get_next_task(lease_seconds=120)
    assert claimed["task_id"] == task_id
    assert claimed["attempt_count"] == 1

    # El worker muere 3 veces: la tarea se reencola cada vez.
    for expected_attempt in (2, 3, 4):
        reclaimed = _crash_worker_once(task_id)
        assert reclaimed is not None
        assert reclaimed["task_id"] == task_id
        assert reclaimed["attempt_count"] == expected_attempt

    assert task_bus.get_task_status(task_id)["status"] == "running"


def test_task_goes_dead_letter_after_max_attempts(isolated_db):
    task_id = task_bus.enqueue_task({"objective": "tarea que siempre mata al worker"})
    claimed = task_bus.get_next_task(lease_seconds=120)
    assert claimed["attempt_count"] == 1

    # Intentos 2..5: la tarea se sigue reencolando.
    for _ in range(task_bus.MAX_TASK_ATTEMPTS - 1):
        assert _crash_worker_once(task_id) is not None

    assert task_bus.get_task_status(task_id)["attempt_count"] == task_bus.MAX_TASK_ATTEMPTS

    # Quinta muerte: ya no se reencola; queda en cuarentena terminal.
    assert _crash_worker_once(task_id) is None
    dead = task_bus.get_task_status(task_id)
    assert dead["status"] == "dead_letter"
    assert dead["attempt_count"] == task_bus.MAX_TASK_ATTEMPTS
    assert dead["lease_token"] is None

    # Y sigue en cuarentena en llamadas posteriores.
    assert task_bus.get_next_task(lease_seconds=120) is None
    assert task_bus.get_task_status(task_id)["status"] == "dead_letter"

    listed = task_bus.get_dead_letter_tasks()
    assert [row["task_id"] for row in listed] == [task_id]
    assert listed[0]["attempt_count"] == task_bus.MAX_TASK_ATTEMPTS


def test_dead_letter_does_not_block_other_tasks(isolated_db):
    doomed = task_bus.enqueue_task({"objective": "condenada"})
    healthy = task_bus.enqueue_task({"objective": "sana"})
    assert task_bus.get_next_task(lease_seconds=120)["task_id"] == doomed
    for _ in range(task_bus.MAX_TASK_ATTEMPTS - 1):
        assert _crash_worker_once(doomed) is not None
    # Quinta muerte de la condenada: se cuarentena y la cola sigue viva.
    _expire_lease(doomed)
    assert task_bus.get_next_task(lease_seconds=120)["task_id"] == healthy
    assert task_bus.get_task_status(doomed)["status"] == "dead_letter"


def test_completed_task_never_goes_dead_letter(isolated_db):
    task_id = task_bus.enqueue_task({"objective": "tarea que termina bien"})
    claimed = task_bus.get_next_task(lease_seconds=120)
    assert task_bus.mark_task_completed(
        task_id,
        {"status": "candidate", "candidate_id": "cand-1"},
        result_id="cand-1",
        lease_token=claimed["lease_token"],
    )
    assert task_bus.get_task_status(task_id)["status"] == "candidate"
    assert task_bus.get_dead_letter_tasks() == []
