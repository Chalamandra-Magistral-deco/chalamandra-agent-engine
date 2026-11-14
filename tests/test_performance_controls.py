from __future__ import annotations

import json
import sqlite3
import stat

import pytest

from api import candidate_store, subscription_store, task_bus, trace_store, worker_loop
from api.server import EngineHTTPServer, EngineRequestHandler


def test_task_queue_queries_use_partial_indexes(tmp_path, monkeypatch):
    db_path = tmp_path / "tasks.db"
    monkeypatch.setattr(task_bus, "_db_path", lambda: db_path)
    task_bus.init_db()

    with sqlite3.connect(db_path) as conn:
        queued = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM tasks "
            "WHERE status='queued' ORDER BY created_at ASC LIMIT 1"
        ).fetchall()
        leased = conn.execute(
            "EXPLAIN QUERY PLAN SELECT task_id FROM tasks "
            "WHERE status='running' AND lease_until IS NOT NULL AND lease_until <= ?",
            ("2099-01-01T00:00:00+00:00",),
        ).fetchall()
        by_run = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM tasks "
            "WHERE run_id=? ORDER BY created_at",
            ("benchmark-run",),
        ).fetchall()

    assert "idx_tasks_queued_created" in " ".join(str(row) for row in queued)
    assert "idx_tasks_running_lease" in " ".join(str(row) for row in leased)
    assert "idx_tasks_run_created" in " ".join(str(row) for row in by_run)


def _without_digest(record):
    """Stored records carry integrity fields; compare the candidate payload."""
    return {k: v for k, v in record.items() if k not in {"record_digest", "record_digest_alg"}}


def test_candidate_store_index_is_idempotent_and_rebuilds(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_store, "_state_dir", lambda: tmp_path)
    candidate = {"candidate_id": "candidate-1", "status": "candidate", "revision": 1}
    candidate_store.save_candidate(candidate)
    candidate_store.save_candidate(candidate)

    found = candidate_store.get_candidate("candidate-1")
    assert _without_digest(found) == candidate
    path = candidate_store._candidates_file()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1

    with pytest.raises(ValueError, match="candidate_id conflict"):
        candidate_store.save_candidate(
            {"candidate_id": "candidate-1", "status": "candidate", "revision": 2}
        )
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1

    # Simulate a legacy writer that appended JSONL without updating the side index.
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"candidate_id": "candidate-legacy", "status": "blocked"}) + "\n")

    with pytest.raises(ValueError, match="record_digest"):
        candidate_store.get_candidate("candidate-legacy")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_trace_fsync_is_batched_per_interval(tmp_path, monkeypatch):
    target = tmp_path / "traces.jsonl"
    calls = []
    monkeypatch.setattr(trace_store, "_trace_file", lambda: target)
    monkeypatch.setattr(trace_store.os, "fsync", lambda fd: calls.append(fd))
    # The monotonic clock may start below 60 seconds in a fresh CI container.
    monkeypatch.setattr(trace_store, "_LAST_SYNC", -61.0)
    monkeypatch.setattr(trace_store, "_SYNC_INTERVAL_SECONDS", 60.0)
    monkeypatch.setattr(trace_store, "_TRACE_TARGET_READY", None)

    for number in range(5):
        trace_store.record_request(
            trace_id=f"{number + 1:032x}",
            traceparent=None,
            method="GET",
            path="/healthz",
            status=200,
            client_ip="127.0.0.1",
        )

    assert len(calls) == 1
    assert len(target.read_text(encoding="utf-8").splitlines()) == 5
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_task_bus_initializes_indexed_schema_once(tmp_path, monkeypatch):
    db_path = tmp_path / "tasks.db"
    monkeypatch.setattr(task_bus, "_db_path", lambda: db_path)
    task_bus.init_db()
    task_bus.enqueue_task({"objective": "index smoke test"})
    task_id = task_bus.get_next_task()["task_id"]
    assert task_bus.get_task_status(task_id)["status"] == "running"
    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    with task_bus._get_conn() as connection:
        connection.execute("SELECT COUNT(*) FROM tasks").fetchone()
        for sidecar in (
            db_path.with_name(db_path.name + "-wal"),
            db_path.with_name(db_path.name + "-shm"),
        ):
            if sidecar.exists():
                assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600


def test_worker_rejects_unsafe_process_count(monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_WORKER_PROCESSES", "17")
    with pytest.raises(ValueError, match="entre 1 y 16"):
        worker_loop.main()


def test_api_http_threads_are_bounded(monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_API_MAX_CONCURRENCY", "8")
    server = EngineHTTPServer(
        ("127.0.0.1", 0),
        EngineRequestHandler,
        master=object(),
    )
    try:
        assert server.max_concurrency == 8
        assert server.request_queue_size == 128
        assert server._request_slots is not None
    finally:
        server.server_close()


def test_subscription_ledger_uses_latest_indexed_state(tmp_path, monkeypatch):
    target = tmp_path / "subscriptions.jsonl"
    monkeypatch.setattr(subscription_store, "_subs_file", lambda: target)

    # Seed two historical states as an old writer would have done.
    subscription_store._append(
        target,
        {"subscription_id": "subscription-1", "status": "enabled"},
    )
    subscription_store._append(
        target,
        {"subscription_id": "subscription-2", "status": "enabled"},
    )
    subscription_store._append(
        target,
        {"subscription_id": "subscription-1", "status": "paused"},
    )

    before = {
        item["subscription_id"]: item["status"]
        for item in subscription_store.list_subscriptions()
    }
    assert before == {
        "subscription-1": "paused",
        "subscription-2": "enabled",
    }

    assert subscription_store.disable_subscription("subscription-2") is True
    after = {
        item["subscription_id"]: item["status"]
        for item in subscription_store.list_subscriptions()
    }
    assert after == {
        "subscription-1": "paused",
        "subscription-2": "disabled",
    }
    assert target.with_suffix(target.suffix + ".index.sqlite3").exists()
