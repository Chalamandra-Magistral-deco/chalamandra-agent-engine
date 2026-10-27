"""Task Bus atómico usando SQLite (Fase 1 corregida)."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

def _state_dir() -> Path:
    return Path.home() / ".local" / "state" / "chalamandra" / "agents"

def _db_path() -> Path:
    path = _state_dir() / "tasks.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

def _get_conn():
    conn = sqlite3.connect(str(_db_path()), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    with _get_conn() as conn:
        # 1. Crear tabla base primero (P0-1 corregido)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
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
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS idempotency (
                idempotency_key TEXT PRIMARY KEY,
                payload_hash TEXT NOT NULL,
                task_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        # 2. Migración de columna payload (después de crear tabla)
        cursor = conn.execute("PRAGMA table_info(tasks)")
        columns = [row[1] for row in cursor.fetchall()]
        if "payload" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN payload TEXT")
        if "result_id" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN result_id TEXT")

def enqueue_task(contract: dict, idempotency_key: str | None = None) -> str:
    init_db()
    task_id = contract.get("task_id") or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    
    # P0-2: Idempotencia atómica
    if idempotency_key:
        import hashlib
        payload_hash = hashlib.sha256(json.dumps(contract.get("payload", {}), sort_keys=True).encode()).hexdigest()
        with _get_conn() as conn:
            # Intentar insertar en idempotency; si ya existe, devolver el task_id existente
            try:
                conn.execute(
                    "INSERT INTO idempotency (idempotency_key, payload_hash, task_id, created_at) VALUES (?, ?, ?, ?)",
                    (idempotency_key, payload_hash, task_id, now)
                )
            except sqlite3.IntegrityError:
                # La clave ya existe. Verificar que el payload sea el mismo.
                row = conn.execute(
                    "SELECT task_id, payload_hash FROM idempotency WHERE idempotency_key = ?",
                    (idempotency_key,)
                ).fetchone()
                if row:
                    if row["payload_hash"] != payload_hash:
                        raise ValueError("Idempotency-Key reutilizada con payload distinto")
                    return row["task_id"]
                raise
    
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO tasks (task_id, run_id, objective, required_tools, success_criteria, status, trace_id, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                contract.get("run_id"),
                contract.get("objective"),
                json.dumps(contract.get("required_tools", [])),
                json.dumps(contract.get("success_criteria", [])),
                "queued",
                contract.get("trace_id"),
                json.dumps(contract.get("payload", {})),
                now,
                now
            )
        )
    return task_id

def get_next_task() -> dict | None:
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM tasks WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1").fetchone()
        if not row:
            conn.execute("COMMIT")
            return None
        conn.execute("UPDATE tasks SET status = 'running', updated_at = ? WHERE task_id = ? AND status = 'queued'", (now, row["task_id"]))
        conn.execute("COMMIT")
        updated = conn.execute("SELECT * FROM tasks WHERE task_id = ? AND status = 'running'", (row["task_id"],)).fetchone()
        if not updated:
            return None
        return dict(updated)

def mark_task_completed(task_id: str, candidate_result: dict, result_id: str | None = None):
    """Marca la tarea como terminada propagando el estado real del candidato (P0-4)."""
    now = datetime.now(timezone.utc).isoformat()
    # Extraer el estado del candidato (candidate, failed, blocked)
    candidate_status = candidate_result.get("status", "failed")
    
    with _get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, candidate_result = ?, result_id = ?, updated_at = ? WHERE task_id = ?",
            (candidate_status, json.dumps(candidate_result), result_id, now, task_id)
        )

def get_task_status(task_id: str) -> dict | None:
    init_db()
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return dict(row)

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
