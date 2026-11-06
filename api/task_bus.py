"""Task Bus atómico usando SQLite (Fase 1 corregida)."""
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

def _state_dir() -> Path:
    return Path.home() / ".local" / "state" / "chalamandra" / "agents"

def _db_path() -> Path:
    path = _state_dir() / "tasks.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

_DB_INIT_LOCK = threading.RLock()
_DB_READY_PATH: str | None = None
_DB_FILES_SECURED_PATHS: set[str] = set()


def _get_conn():
    path = _db_path()
    conn = sqlite3.connect(str(path), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=NORMAL")
    cache_key = str(path.resolve())
    with _DB_INIT_LOCK:
        if cache_key not in _DB_FILES_SECURED_PATHS:
            # Open the WAL shared-memory files once, then restrict their modes.
            conn.execute("PRAGMA schema_version").fetchone()
            os.chmod(path, 0o600)
            for sidecar in (Path(str(path) + "-wal"), Path(str(path) + "-shm")):
                if sidecar.exists():
                    os.chmod(sidecar, 0o600)
            _DB_FILES_SECURED_PATHS.add(cache_key)
    return conn


def init_db():
    """Initialize schema once per process/path; serialize first-start migrations."""
    global _DB_READY_PATH
    path = _db_path()
    cache_key = str(path.resolve())
    with _DB_INIT_LOCK:
        if _DB_READY_PATH == cache_key:
            return
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        path.touch(mode=0o600, exist_ok=True)
        os.chmod(path, 0o600)
        conn = sqlite3.connect(str(path), timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            for sidecar in (Path(str(path) + "-wal"), Path(str(path) + "-shm")):
                if sidecar.exists():
                    os.chmod(sidecar, 0o600)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    objective TEXT,
                    product_code TEXT,
                    required_tools TEXT,
                    success_criteria TEXT,
                    status TEXT NOT NULL,
                    trace_id TEXT,
                    candidate_result TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
            }
            migrations = {
                "payload": "ALTER TABLE tasks ADD COLUMN payload TEXT",
                "product_code": "ALTER TABLE tasks ADD COLUMN product_code TEXT",
                "result_id": "ALTER TABLE tasks ADD COLUMN result_id TEXT",
                "lease_until": "ALTER TABLE tasks ADD COLUMN lease_until TEXT",
                "lease_token": "ALTER TABLE tasks ADD COLUMN lease_token TEXT",
                "attempt_count": (
                    "ALTER TABLE tasks ADD COLUMN attempt_count "
                    "INTEGER NOT NULL DEFAULT 0"
                ),
            }
            for column, statement in migrations.items():
                if column not in columns:
                    conn.execute(statement)

            # Partial indexes make queue and lease scans proportional to active work.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_queued_created "
                "ON tasks(created_at, task_id) WHERE status='queued'"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_running_lease "
                "ON tasks(lease_until) "
                "WHERE status='running' AND lease_until IS NOT NULL"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_running_updated "
                "ON tasks(updated_at) "
                "WHERE status='running' AND lease_until IS NULL"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_run_created "
                "ON tasks(run_id, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_result_status "
                "ON tasks(result_id, status)"
            )
            conn.commit()
            # SQLite may create WAL/SHM during schema migration; lock them down
            # after creation, before other workers begin using this path.
            for sidecar in (Path(str(path) + "-wal"), Path(str(path) + "-shm")):
                if sidecar.exists():
                    os.chmod(sidecar, 0o600)
            _DB_READY_PATH = cache_key
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

def enqueue_task(contract: dict, idempotency_key: str | None = None) -> str:
    """Persist a task atomically; an idempotency key binds the complete task contract."""
    init_db()
    task_id = contract.get("task_id") or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    stable_contract = {
        "schema_version": 2,
        "objective": contract.get("objective"),
        "required_tools": contract.get("required_tools", []),
        "success_criteria": contract.get("success_criteria", []),
        "payload": contract.get("payload", {}),
    }
    # Preserve the pre-product idempotency digest for every existing caller.
    if contract.get("product_code") is not None:
        stable_contract["product_code"] = contract["product_code"]
    import hashlib
    payload_hash = hashlib.sha256(
        json.dumps(
            stable_contract,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    # One transaction prevents an idempotency record from being committed without
    # its corresponding task. Replays return the original task and run, not a new run.
    with _get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if idempotency_key:
            try:
                conn.execute(
                    "INSERT INTO idempotency "
                    "(idempotency_key, payload_hash, task_id, created_at) VALUES (?, ?, ?, ?)",
                    (idempotency_key, payload_hash, task_id, now),
                )
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT task_id, payload_hash FROM idempotency WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if not row:
                    raise
                if row["payload_hash"] != payload_hash:
                    raise ValueError("Idempotency-Key reutilizada con un contrato distinto")
                existing = conn.execute(
                    "SELECT task_id FROM tasks WHERE task_id = ?", (row["task_id"],)
                ).fetchone()
                if not existing:
                    raise RuntimeError("Registro de idempotencia huérfano; revisión requerida")
                return str(row["task_id"])

        conn.execute(
            "INSERT INTO tasks "
            "(task_id, run_id, objective, product_code, required_tools, success_criteria, status, trace_id, payload, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                contract.get("run_id"),
                contract.get("objective"),
                contract.get("product_code"),
                json.dumps(contract.get("required_tools", []), ensure_ascii=False),
                json.dumps(contract.get("success_criteria", []), ensure_ascii=False),
                "queued",
                contract.get("trace_id"),
                json.dumps(contract.get("payload", {}), ensure_ascii=False, allow_nan=False),
                now,
                now,
            ),
        )
    return task_id

def get_next_task(lease_seconds: int = 120) -> dict | None:
    """Atomically recover expired claims and reserve one queued task with a lease."""
    from datetime import timedelta

    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds < 1:
        raise ValueError("lease_seconds must be a positive integer")

    init_db()
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    stale_before = (now_dt - timedelta(seconds=lease_seconds)).isoformat()
    lease_until = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
    lease_token = str(uuid.uuid4())

    with _get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")

        # Reclaim current leases and legacy pre-lease claims separately so both
        # predicates can use their partial indexes. Timestamps are normalized UTC ISO-8601.
        conn.execute(
            """
            UPDATE tasks
            SET status = 'queued', lease_until = NULL, lease_token = NULL, updated_at = ?
            WHERE status = 'running'
              AND lease_until IS NOT NULL
              AND lease_until <= ?
            """,
            (now, now),
        )
        conn.execute(
            """
            UPDATE tasks
            SET status = 'queued', lease_token = NULL, updated_at = ?
            WHERE status = 'running'
              AND lease_until IS NULL
              AND updated_at <= ?
            """,
            (now, stale_before),
        )

        row = conn.execute(
            "SELECT * FROM tasks WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        if not row:
            conn.execute("COMMIT")
            return None

        cursor = conn.execute(
            """
            UPDATE tasks
            SET status = 'running',
                updated_at = ?,
                lease_until = ?,
                lease_token = ?,
                attempt_count = COALESCE(attempt_count, 0) + 1
            WHERE task_id = ? AND status = 'queued'
            """,
            (now, lease_until, lease_token, row["task_id"]),
        )
        if cursor.rowcount != 1:
            conn.execute("COMMIT")
            return None

        conn.execute("COMMIT")
        updated = conn.execute(
            "SELECT * FROM tasks WHERE task_id = ? AND status = 'running' AND lease_token = ?",
            (row["task_id"], lease_token),
        ).fetchone()
        return dict(updated) if updated else None


def mark_task_completed(
    task_id: str,
    candidate_result: dict,
    result_id: str | None = None,
    lease_token: str | None = None,
) -> bool:
    """Persist a result; worker claims must present their current lease token."""
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    candidate_status = candidate_result.get("status", "failed")

    with _get_conn() as conn:
        if lease_token is not None:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = ?, candidate_result = ?, result_id = ?, updated_at = ?,
                    lease_until = NULL, lease_token = NULL
                WHERE task_id = ? AND status = 'running' AND lease_token = ?
                """,
                (
                    candidate_status,
                    json.dumps(candidate_result),
                    result_id,
                    now,
                    task_id,
                    lease_token,
                ),
            )
        else:
            # Retain the authenticated API completion path for existing callers.
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = ?, candidate_result = ?, result_id = ?, updated_at = ?,
                    lease_until = NULL, lease_token = NULL
                WHERE task_id = ?
                """,
                (candidate_status, json.dumps(candidate_result), result_id, now, task_id),
            )
        return cursor.rowcount == 1

def mark_result_accepted(result_id: str) -> int:
    """Close all task states linked to an independently accepted result."""
    if not isinstance(result_id, str) or not result_id.strip():
        raise ValueError("result_id is required")
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE tasks SET status = 'accepted', updated_at = ? "
            "WHERE result_id = ? AND status = 'candidate'",
            (now, result_id),
        )
        return int(cursor.rowcount)


def get_task_status(task_id: str) -> dict | None:
    init_db()
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return dict(row)


def is_magis_task(task: dict | None) -> bool:
    """Return true only for a task carrying the server-defined MAGIS marker."""
    if not isinstance(task, dict):
        return False
    raw_payload = task.get("payload")
    try:
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    except (TypeError, ValueError):
        return False
    return task.get("product_code") == "magis" and isinstance(payload, dict)


def is_magis_result(result_id: str) -> bool:
    """Check that a candidate id belongs to a task created through MAGIS intake."""
    if not isinstance(result_id, str) or not result_id.strip():
        return False
    init_db()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT product_code FROM tasks WHERE result_id = ?",
            (result_id,),
        ).fetchall()
    for row in rows:
        if row["product_code"] == "magis":
            return True
    return False

def get_run_tasks(run_id: str) -> list[dict]:
    init_db()
    with _get_conn() as conn:
        rows = conn.execute("SELECT * FROM tasks WHERE run_id = ?", (run_id,)).fetchall()
        return [dict(r) for r in rows]


def get_tasks_by_run(run_id: str) -> list[dict]:
    """Devuelve todas las tareas de un run_id. Usado por GET /internal/v1/orchestration/runs/{id}."""
    init_db()
    with _get_conn() as conn:
        cur = conn.execute(
            "SELECT * FROM tasks WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        )
        return [dict(row) for row in cur.fetchall()]
