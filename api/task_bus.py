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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidate_publications (
                    task_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    candidate_result TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'published')),
                    created_at TEXT NOT NULL,
                    published_at TEXT,
                    last_error TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
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
                "execution_policy": (
                    "ALTER TABLE tasks ADD COLUMN execution_policy TEXT "
                    "NOT NULL DEFAULT '{\"allowed_tools\":[],\"side_effects\":\"none\"}'"
                ),
            }
            for column, statement in migrations.items():
                if column not in columns:
                    conn.execute(statement)

            publication_columns = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(candidate_publications)"
                ).fetchall()
            }
            if "last_error" not in publication_columns:
                conn.execute(
                    "ALTER TABLE candidate_publications ADD COLUMN last_error TEXT"
                )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidate_publications_pending "
                "ON candidate_publications(created_at, task_id) "
                "WHERE status='pending'"
            )

            # One-time backfill: recover terminal task results written before the
            # transactional outbox existed. Results without candidate IDs are not
            # candidate artifacts and remain only in the task history.
            migration_version = "candidate_publication_outbox_v1"
            migration_done = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?",
                (migration_version,),
            ).fetchone()
            if not migration_done:
                legacy_rows = conn.execute(
                    "SELECT task_id, candidate_result, created_at, updated_at "
                    "FROM tasks WHERE candidate_result IS NOT NULL "
                    "AND status NOT IN ('queued', 'running')"
                ).fetchall()
                migration_now = datetime.now(timezone.utc).isoformat()
                for legacy_row in legacy_rows:
                    try:
                        legacy_candidate = json.loads(legacy_row[1])
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise ValueError(
                            "legacy task candidate_result contains invalid JSON"
                        ) from exc
                    if not isinstance(legacy_candidate, dict):
                        raise ValueError("legacy task candidate_result must be an object")
                    legacy_candidate_id = legacy_candidate.get("candidate_id")
                    if isinstance(legacy_candidate_id, str) and legacy_candidate_id.strip():
                        conn.execute(
                            "INSERT OR IGNORE INTO candidate_publications "
                            "(task_id, candidate_id, candidate_result, status, created_at) "
                            "VALUES (?, ?, ?, 'pending', ?)",
                            (
                                legacy_row[0],
                                legacy_candidate_id,
                                legacy_row[1],
                                legacy_row[3] or legacy_row[2] or migration_now,
                            ),
                        )
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (migration_version, migration_now),
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
        "execution_policy": contract.get(
            "execution_policy", {"allowed_tools": [], "side_effects": "none"}
        ),
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
            "(task_id, run_id, objective, product_code, required_tools, success_criteria, status, trace_id, payload, execution_policy, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                json.dumps(
                    contract.get("execution_policy", {"allowed_tools": [], "side_effects": "none"}),
                    ensure_ascii=False, sort_keys=True, allow_nan=False,
                ),
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
    """Persist a candidate only when the current worker presents its lease."""
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id is required")
    if not isinstance(candidate_result, dict):
        raise ValueError("candidate_result must be an object")
    if not isinstance(lease_token, str) or not lease_token.strip():
        return False

    candidate_status = candidate_result.get("status")
    if candidate_status not in {"candidate", "blocked", "failed"}:
        raise ValueError("candidate status must be candidate, blocked, or failed")

    serialized_candidate = json.dumps(
        candidate_result, ensure_ascii=False, allow_nan=False
    )
    candidate_id = candidate_result.get("candidate_id")

    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        cursor = conn.execute(
            """
            UPDATE tasks
            SET status = ?, candidate_result = ?, result_id = ?, updated_at = ?,
                lease_until = NULL, lease_token = NULL
            WHERE task_id = ? AND status = 'running' AND lease_token = ?
              AND lease_until IS NOT NULL AND lease_until > ?
            """,
            (
                candidate_status,
                serialized_candidate,
                result_id,
                now,
                task_id,
                lease_token,
                now,
            ),
        )
        if cursor.rowcount != 1:
            return False

        # The task result and its publication intent commit in the same SQLite
        # transaction. A worker crash after this point is repaired from this outbox.
        if isinstance(candidate_id, str) and candidate_id.strip():
            conn.execute(
                "INSERT INTO candidate_publications "
                "(task_id, candidate_id, candidate_result, status, created_at) "
                "VALUES (?, ?, ?, 'pending', ?)",
                (task_id, candidate_id, serialized_candidate, now),
            )
        return True


def get_pending_candidate_publications(limit: int = 100) -> list[dict]:
    """Return a bounded batch of durable candidate publications awaiting JSONL."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    init_db()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT task_id, candidate_id, candidate_result FROM candidate_publications "
            "WHERE status='pending' AND last_error IS NULL ORDER BY created_at, task_id LIMIT ?",
            (limit,),
        ).fetchall()

    publications = []
    for row in rows:
        try:
            candidate = json.loads(row["candidate_result"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("candidate publication outbox contains invalid JSON") from exc
        if not isinstance(candidate, dict):
            raise ValueError("candidate publication outbox entry must be an object")
        publications.append({
            "task_id": row["task_id"],
            "candidate_id": row["candidate_id"],
            "candidate_result": candidate,
        })
    return publications


def mark_candidate_publication_blocked(
    task_id: str, candidate_id: str, reason: str
) -> bool:
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id is required")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("candidate_id is required")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason is required")

    init_db()
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE candidate_publications SET last_error=? "
            "WHERE task_id=? AND candidate_id=? AND status='pending'",
            (reason[:1000], task_id, candidate_id),
        )
        return cursor.rowcount == 1


def get_blocked_candidate_publications(limit: int = 100) -> list[dict]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    init_db()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT task_id, candidate_id, candidate_result, last_error "
            "FROM candidate_publications WHERE status='pending' AND last_error IS NOT NULL "
            "ORDER BY created_at, task_id LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]



def retry_blocked_candidate_publication(task_id: str, candidate_id: str) -> bool:
    """Clear quarantine after an operator has resolved the underlying conflict."""
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id is required")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("candidate_id is required")

    init_db()
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE candidate_publications SET last_error=NULL "
            "WHERE task_id=? AND candidate_id=? AND status='pending' "
            "AND last_error IS NOT NULL",
            (task_id, candidate_id),
        )
        return cursor.rowcount == 1


def mark_candidate_publication_published(task_id: str, candidate_id: str) -> bool:
    """Acknowledge a JSONL projection; a repeated acknowledgement is idempotent."""
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id is required")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("candidate_id is required")

    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE candidate_publications SET status='published', published_at=? "
            "WHERE task_id=? AND candidate_id=? AND status='pending'",
            (now, task_id, candidate_id),
        )
        if cursor.rowcount == 1:
            return True
        row = conn.execute(
            "SELECT status FROM candidate_publications "
            "WHERE task_id=? AND candidate_id=?",
            (task_id, candidate_id),
        ).fetchone()
        return bool(row and row["status"] == "published")

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


def mark_result_rejected(result_id: str) -> int:
    """Close every non-accepted task linked to an independently rejected result."""
    if not isinstance(result_id, str) or not result_id.strip():
        raise ValueError("result_id is required")
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE tasks SET status = 'rejected', updated_at = ? "
            "WHERE result_id = ? AND status IN ('candidate', 'blocked', 'failed')",
            (now, result_id),
        )
        return int(cursor.rowcount)


def get_result_task_statuses(result_id: str) -> list[dict]:
    """Return every task linked to a result for acceptance reconciliation."""
    if not isinstance(result_id, str) or not result_id.strip():
        raise ValueError("result_id is required")
    init_db()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT task_id, status FROM tasks WHERE result_id = ? ORDER BY task_id",
            (result_id,),
        ).fetchall()
    return [dict(row) for row in rows]


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
