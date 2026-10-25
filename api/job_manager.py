"""Job Manager para Request Flow (Fase 1b)."""
import json
import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Rutas de persistencia (fuera del repo)
def _state_dir() -> Path:
    return Path.home() / ".local" / "state" / "chalamandra" / "agents"

def _jobs_file() -> Path:
    return _state_dir() / "jobs.jsonl"

def _idempotency_file() -> Path:
    return _state_dir() / "idempotency.jsonl"

def _catedralx_results_file() -> Path:
    return Path.home() / ".local" / "state" / "chalamandra" / "catedralx" / "results.jsonl"

def _append(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def _read_all(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records

def check_idempotency(key: str, payload: dict) -> dict | None:
    """Si la key existe y el payload es igual, retorna el job_id previo."""
    for rec in _read_all(_idempotency_file()):
        if rec.get("key") == key and rec.get("payload") == payload:
            return rec
    return None

def submit_job(payload: dict, idempotency_key: str) -> str:
    """Crea el job y lanza el hilo de ejecución."""
    # Validar idempotencia
    existing = check_idempotency(idempotency_key, payload)
    if existing:
        return existing["job_id"]

    job_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    
    job_record = {
        "job_id": job_id,
        "status": "queued",
        "trace_id": trace_id,
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload
    }
    _append(_jobs_file(), job_record)
    _append(_idempotency_file(), {"key": idempotency_key, "payload": payload, "job_id": job_id})
    
    # Hilo de ejecución
    threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()
    return job_id

def _run_job(job_id: str):
    """Ejecuta CatedralX y captura el resultado."""
    _append(_jobs_file(), {"job_id": job_id, "status": "running", "updated_at": datetime.now(timezone.utc).isoformat()})
    
    catedral_repo = Path("/home/azureuser/workspace/repos/CatedralX")
    
    try:
        # Ejecutamos el CLI de CatedralX en un subproceso aislado
        result = subprocess.run(
            ["bash", "BIN/catedral", "motor"],
            cwd=catedral_repo,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        # Parsear stdout buscando el JSON
        stdout = result.stdout.strip()
        decision_result = None
        
        # Buscar la primera llave de apertura para intentar parsear
        start_idx = stdout.find('{')
        if start_idx != -1:
            try:
                # Intenta parsear el JSON completo
                parsed = json.loads(stdout[start_idx:])
                if "decision_result" in parsed:
                    decision_result = parsed["decision_result"]
            except json.JSONDecodeError:
                pass
                
        if decision_result:
            _append(_jobs_file(), {
                "job_id": job_id,
                "status": "completed",
                "result_id": decision_result.get("result_id"),
                "updated_at": datetime.now(timezone.utc).isoformat()
            })
        else:
            _append(_jobs_file(), {
                "job_id": job_id,
                "status": "failed",
                "reason": "Could not parse DecisionResult from CatedralX output",
                "updated_at": datetime.now(timezone.utc).isoformat()
            })
            
    except Exception as e:
        _append(_jobs_file(), {
            "job_id": job_id,
            "status": "failed",
            "reason": str(e),
            "updated_at": datetime.now(timezone.utc).isoformat()
        })

def get_job_status(job_id: str) -> dict | None:
    """Obtiene el estado más reciente del job."""
    latest = None
    for rec in _read_all(_jobs_file()):
        if rec.get("job_id") == job_id:
            if latest is None:
                latest = rec
            else:
                latest.update(rec) # Sobrescribe el estado, conserva los demás campos
    return latest

def get_result(result_id: str) -> dict | None:
    """Lee el result store de CatedralX."""
    for rec in _read_all(_catedralx_results_file()):
        if rec.get("result_id") == result_id:
            return rec
    return None
