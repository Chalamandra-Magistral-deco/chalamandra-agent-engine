"""Worker Loop real: procesa tareas del TaskBus (Fase 2)."""
import asyncio
import signal
import sys
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from api import task_bus
from api import candidate_store
from core.master import Master


class TaskTimeout(Exception):
    pass


def _task_timeout_handler(signum, frame):
    raise TaskTimeout("task exceeded 60s timeout")

def _catedral_repo() -> Path:
    return Path("/home/azureuser/workspace/repos/CatedralX")

def _run_catedralx(payload: dict) -> dict:
    """Ejecuta CatedralX con el payload y devuelve el DecisionResult."""
    import subprocess
    import tempfile
    import os
    
    snapshot = {
        "PERCEIVE": {
            "historial_precios": payload.get("historical_prices", []),
            "precio": payload.get("current_price", 0.0),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "source": "worker_request"
        }
    }
    fd, temp_path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, 'w') as f:
        json.dump(snapshot, f)
        
    env = os.environ.copy()
    env["CATEDRALX_SNAPSHOT_PATH"] = temp_path
    # P0-36: el worker corre bajo systemd-user sin PATH del venv.
    # BIN/catedral resuelve "python3" al sistema (sin numpy) si no
    # se le pasa CATEDRALX_PYTHON. Usar el python del venv del worker.
    env["CATEDRALX_PYTHON"] = sys.executable
    
    try:
        result = subprocess.run(
            ["bash", "BIN/catedral", "motor"],
            cwd=_catedral_repo(),
            env=env,
            capture_output=True,
            text=True,
            timeout=60
        )
        stdout = result.stdout.strip()
        start_idx = stdout.find('{')
        if start_idx != -1:
            parsed = json.loads(stdout[start_idx:])
            if "decision_result" in parsed:
                return parsed["decision_result"]
        return {"status": "failed", "reason": "Could not parse DecisionResult", "raw": stdout}
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

def process_task(task: dict) -> dict:
    task_id = task["task_id"]
    payload = {}
    if task.get("payload"):
        try:
            payload = json.loads(task["payload"])
        except json.JSONDecodeError:
            pass

    objective = task.get("objective", "")
    evidence = []
    summary = "No ejecutado"
    status = "failed"
    catedral_result_id: str | None = None
    catedralx_trace_id: str = ""
    
    try:
        # Detectar si es una tarea financiera
        if "financial" in objective.lower() or "historical_prices" in payload:
            result = _run_catedralx(payload)
            if result.get("status") == "ACCEPTED" and result.get("result_id"):
                summary = f"Análisis financiero completado: {result.get('decision')}"
                status = "candidate"
                # Mantener la identidad del resultado canónico de CatedralX.
                catedral_result_id = str(result["result_id"])
                catedralx_trace_id = str(result.get("trace_id") or "")
                
                evidence.append({
                    "evidence_id": str(uuid.uuid4()),
                    "kind": "calculation",
                    "source": "catedralx",
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "description": "DecisionResult generado por CatedralX",
                    "digest": __import__("hashlib").sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
                })
            elif result.get("status") == "ACCEPTED":
                summary = "CatedralX devolvió ACCEPTED sin result_id"
                status = "blocked"
            else:
                summary = f"CatedralX rechazó el análisis: {result.get('reason', 'unknown')}"
                status = "blocked"
        else:
            # Tarea general: usar el Master
            master = Master()
            result = asyncio.run(master.execute_task(objective))
            summary = result.get("summary", "Ejecución completada") if isinstance(result, dict) else str(result)
            evidence.append({
                "evidence_id": str(uuid.uuid4()),
                "kind": "calculation",
                "source": "master_execution",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "description": f"Ejecución del Master para: {objective}",
                "digest": __import__("hashlib").sha256(str(result).encode()).hexdigest()
            })
            status = "candidate"
    except Exception as e:
        summary = f"Error en ejecución: {str(e)}"
        status = "failed"

    candidate_result = {
        "candidate_id": catedral_result_id or str(uuid.uuid4()),
        "worker_id": "worker-agent-01",
        "status": status,
        "summary": summary,
        "evidence": evidence,
        "assumptions": [],
        "contradictions": [],
        "trace_id": task.get("trace_id"),
        "catedralx_trace_id": catedralx_trace_id or None,
    }
    # FASE 3: Guardar el candidato para que el Gate de Aceptación pueda verificar la evidencia
    candidate_store.save_candidate(candidate_result)
    task_bus.mark_task_completed(task_id, candidate_result, result_id=catedral_result_id)
    return candidate_result

def main():
    print("Worker persistente iniciado. Procesando tareas reales...")
    while True:
        task = task_bus.get_next_task()
        if task:
            signal.signal(signal.SIGALRM, _task_timeout_handler)
            signal.alarm(60)
            try:
                process_task(task)
            except TaskTimeout:
                task_bus.mark_task_completed(
                    task["task_id"],
                    {"status": "failed", "reason": "timeout_60s"},
                    None,
                )
                print(f"[worker] task {task['task_id']} timeout 60s -> failed")
            except Exception as e:
                task_bus.mark_task_completed(
                    task["task_id"],
                    {"status": "failed", "reason": f"worker_exception: {e}"},
                    None,
                )
                print(f"[worker] task {task['task_id']} exception -> failed: {e}")
            finally:
                signal.alarm(0)
        time.sleep(2)

if __name__ == "__main__":
    main()
