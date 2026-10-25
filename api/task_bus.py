"""Task Bus atómico usando SQLite (Fase 1)."""
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
    # Habilitar WAL para mejor concurrencia
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    with _get_conn() as conn:
        # Migración: añadir columna payload si no existe
        cursor = conn.execute("PRAGMA table_info(tasks)")
        columns = [row[1] for row in cursor.fetchall()]
        if "payload" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN payload TEXT")
            
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
                payload TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)

def enqueue_task(contract: dict) -> str:
    init_db()
    task_id = contract.get("task_id") or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
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
    """Reserva atómicamente la siguiente tarea en cola."""
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        # BEGIN IMMEDIATE previene condiciones de carrera
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM tasks WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        
        if not row:
            conn.execute("COMMIT")
            return None
            
        # Actualización atómica a running
        conn.execute(
            "UPDATE tasks SET status = 'running', updated_at = ? WHERE task_id = ? AND status = 'queued'",
            (now, row["task_id"])
        )
        conn.execute("COMMIT")
        
        # Verificar que la actualización fue exitosa (por si otro proceso la tomó)
        updated = conn.execute(
            "SELECT * FROM tasks WHERE task_id = ? AND status = 'running'",
            (row["task_id"],)
        ).fetchone()
        
        if not updated:
            return None
            
        return dict(updated)

def mark_task_completed(task_id: str, candidate_result: dict):
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'completed', candidate_result = ?, updated_at = ? WHERE task_id = ?",
            (json.dumps(candidate_result), now, task_id)
        )

def get_run_tasks(run_id: str) -> list[dict]:
    init_db()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]
