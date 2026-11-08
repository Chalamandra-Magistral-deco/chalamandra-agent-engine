from __future__ import annotations

import json
import sqlite3

import pytest

from api import candidate_store, task_bus, worker_loop


def _complete_task(candidate: dict) -> str:
    task_id = task_bus.enqueue_task({"objective": "candidate publication recovery"})
    claim = task_bus.get_next_task()
    assert claim is not None
    assert claim["task_id"] == task_id
    assert task_bus.mark_task_completed(
        task_id,
        candidate,
        result_id=candidate.get("result_id"),
        lease_token=claim["lease_token"],
    ) is True
    return task_id


def test_task_completion_and_publication_outbox_commit_together():
    task_id = task_bus.enqueue_task({"objective": "atomic candidate completion"})
    claim = task_bus.get_next_task()
    assert claim is not None and claim["task_id"] == task_id

    with task_bus._get_conn() as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_candidate_publication
            BEFORE INSERT ON candidate_publications
            BEGIN
                SELECT RAISE(ABORT, 'simulated outbox failure');
            END
            """
        )

    candidate = {"candidate_id": "atomic-candidate", "status": "candidate"}
    with pytest.raises(sqlite3.IntegrityError, match="simulated outbox failure"):
        task_bus.mark_task_completed(
            task_id, candidate, lease_token=claim["lease_token"]
        )

    task = task_bus.get_task_status(task_id)
    assert task["status"] == "running"
    assert task["candidate_result"] is None
    assert task_bus.get_pending_candidate_publications() == []


def test_reconciliation_recovers_sqlite_commit_without_duplicate_jsonl():
    candidate = {
        "candidate_id": "recovered-candidate-1",
        "result_id": "recovered-candidate-1",
        "status": "candidate",
        "evidence": [{"source": "test", "value": 7}],
    }
    _complete_task(candidate)  # Simulates a crash before candidate_store.save_candidate().

    assert candidate_store.get_candidate(candidate["candidate_id"]) is None
    pending = task_bus.get_pending_candidate_publications()
    assert len(pending) == 1
    assert pending[0]["candidate_result"] == candidate

    first = candidate_store.reconcile_pending_publications()
    assert first == {
        "examined": 1,
        "recovered": 1,
        "already_present": 0,
        "conflicted": 0,
        "published": 1,
    }
    assert candidate_store.get_candidate(candidate["candidate_id"]) == candidate

    second = candidate_store.reconcile_pending_publications()
    assert second == {
        "examined": 0,
        "recovered": 0,
        "already_present": 0,
        "conflicted": 0,
        "published": 0,
    }
    path = candidate_store._candidates_file()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    assert task_bus.get_pending_candidate_publications() == []


def test_reconciliation_is_safe_when_jsonl_append_preceded_sqlite_ack():
    candidate = {"candidate_id": "already-written", "status": "blocked"}
    _complete_task(candidate)

    # Simulate termination after fsync of JSONL but before outbox acknowledgement.
    candidate_store.save_candidate(candidate)
    report = candidate_store.reconcile_pending_publications()

    assert report["examined"] == 1
    assert report["recovered"] == 0
    assert report["already_present"] == 1
    assert report["published"] == 1
    assert len(candidate_store._candidates_file().read_text(encoding="utf-8").splitlines()) == 1
    assert task_bus.get_pending_candidate_publications() == []


def test_reconciliation_rejects_outbox_candidate_id_mismatch():
    candidate = {"candidate_id": "expected-id", "status": "candidate"}
    task_id = _complete_task(candidate)

    with task_bus._get_conn() as conn:
        conn.execute(
            "UPDATE candidate_publications SET candidate_result=? WHERE task_id=?",
            (json.dumps({"candidate_id": "different-id", "status": "candidate"}), task_id),
        )

    report = candidate_store.reconcile_pending_publications()
    assert report["conflicted"] == 1
    assert candidate_store.get_candidate("different-id") is None
    assert task_bus.get_pending_candidate_publications() == []
    blocked = task_bus.get_blocked_candidate_publications()
    assert len(blocked) == 1
    assert "candidate_id mismatch" in blocked[0]["last_error"]


def test_init_db_backfills_legacy_sqlite_candidate_results_once(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-tasks.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                run_id TEXT,
                objective TEXT,
                required_tools TEXT,
                success_criteria TEXT,
                status TEXT NOT NULL,
                trace_id TEXT,
                candidate_result TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO tasks(task_id, status, candidate_result, created_at, updated_at) "
            "VALUES (?, 'candidate', ?, '2026-11-01T00:00:00+00:00', '2026-11-01T00:01:00+00:00')",
            ("legacy-candidate-task", json.dumps({"candidate_id": "legacy-candidate", "status": "candidate"})),
        )
        conn.execute(
            "INSERT INTO tasks(task_id, status, candidate_result, created_at, updated_at) "
            "VALUES (?, 'running', ?, '2026-11-01T00:00:00+00:00', '2026-11-01T00:01:00+00:00')",
            ("legacy-running-task", json.dumps({"candidate_id": "must-not-backfill", "status": "candidate"})),
        )

    monkeypatch.setattr(task_bus, "_db_path", lambda: db_path)
    monkeypatch.setattr(task_bus, "_DB_READY_PATH", None)
    task_bus.init_db()

    pending = task_bus.get_pending_candidate_publications()
    assert len(pending) == 1
    assert pending[0]["task_id"] == "legacy-candidate-task"
    assert pending[0]["candidate_id"] == "legacy-candidate"

    assert task_bus.mark_candidate_publication_published(
        "legacy-candidate-task", "legacy-candidate"
    ) is True
    task_bus._DB_READY_PATH = None  # Simulate another process initializing the same DB.
    task_bus.init_db()
    assert task_bus.get_pending_candidate_publications() == []


def test_worker_reconciles_outbox_before_claiming_more_tasks(monkeypatch):
    candidate = {"candidate_id": "worker-startup-recovery", "status": "failed"}
    _complete_task(candidate)
    monkeypatch.setattr(worker_loop, "write_worker_heartbeat", lambda *args, **kwargs: None)

    class StopAfterOneIteration:
        def __init__(self):
            self.checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks > 1

        def wait(self, _seconds):
            return True

    worker_loop._run_worker_loop(stop_event=StopAfterOneIteration())

    assert candidate_store.get_candidate(candidate["candidate_id"]) == candidate
    assert task_bus.get_pending_candidate_publications() == []


def test_duplicate_candidate_id_conflict_is_quarantined_and_other_entries_continue():
    first = {"candidate_id": "historical-duplicate", "status": "candidate", "value": "A"}
    second = {"candidate_id": "historical-duplicate", "status": "candidate", "value": "B"}
    independent = {"candidate_id": "independent-candidate", "status": "candidate"}

    _complete_task(first)
    _complete_task(second)
    _complete_task(independent)

    report = candidate_store.reconcile_pending_publications()

    assert report == {
        "examined": 3,
        "recovered": 2,
        "already_present": 0,
        "conflicted": 1,
        "published": 2,
    }
    assert candidate_store.get_candidate("historical-duplicate") == first
    assert candidate_store.get_candidate("independent-candidate") == independent
    assert task_bus.get_pending_candidate_publications() == []
    blocked = task_bus.get_blocked_candidate_publications()
    assert len(blocked) == 1
    assert blocked[0]["candidate_id"] == "historical-duplicate"
    assert "candidate_id conflict" in blocked[0]["last_error"]

    assert candidate_store.reconcile_pending_publications() == {
        "examined": 0,
        "recovered": 0,
        "already_present": 0,
        "conflicted": 0,
        "published": 0,
    }


def test_existing_outbox_schema_migrates_last_error_column(tmp_path, monkeypatch):
    db_path = tmp_path / "pre-quarantine.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE candidate_publications (
                task_id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                candidate_result TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'published')),
                created_at TEXT NOT NULL,
                published_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO candidate_publications "
            "(task_id, candidate_id, candidate_result, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            ("legacy-task", "legacy-id", json.dumps({"candidate_id": "legacy-id"}), "2026-11-01"),
        )

    monkeypatch.setattr(task_bus, "_db_path", lambda: db_path)
    monkeypatch.setattr(task_bus, "_DB_READY_PATH", None)
    task_bus.init_db()

    with task_bus._get_conn() as conn:
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(candidate_publications)"
        ).fetchall()}
    assert "last_error" in columns
    assert len(task_bus.get_pending_candidate_publications()) == 1


def test_blocked_publication_can_only_be_retried_explicitly():
    candidate = {"candidate_id": "retry-after-repair", "status": "candidate"}
    task_id = _complete_task(candidate)
    assert task_bus.mark_candidate_publication_blocked(
        task_id, candidate["candidate_id"], "test conflict"
    ) is True
    assert task_bus.get_pending_candidate_publications() == []
    assert task_bus.retry_blocked_candidate_publication(
        task_id, candidate["candidate_id"]
    ) is True
    assert len(task_bus.get_pending_candidate_publications()) == 1
    assert task_bus.retry_blocked_candidate_publication(
        task_id, candidate["candidate_id"]
    ) is False
