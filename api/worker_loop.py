"""Worker Loop real: procesa tareas del TaskBus (Fase 2)."""
import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from api import task_bus
from core.master import Master

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
    
    try:
        # Detectar si es una tarea financiera
        if "financial" in objective.lower() or "historical_prices" in payload:
            result = _run_catedralx(payload)
            if result.get("status") == "ACCEPTED":
                summary = f"Análisis financiero completado: {result.get('decision')}"
                status = "candidate"
                evidence.append({
                    "evidence_id": str(uuid.uuid4()),
                    "kind": "calculation",
                    "source": "catedralx",
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "description": "DecisionResult generado por CatedralX",
                    "digest": str(hash(json.dumps(result, sort_keys=True)))
                })
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
                "digest": str(hash(str(result)))
            })
            status = "candidate"
    except Exception as e:
        summary = f"Error en ejecución: {str(e)}"
        status = "failed"

    candidate_result = {
        "candidate_id": str(uuid.uuid4()),
        "worker_id": "worker-agent-01",
        "status": status,
        "summary": summary,
        "evidence": evidence,
        "assumptions": [],
        "contradictions": [],
        "trace_id": task.get("trace_id")
    }
    task_bus.mark_task_completed(task_id, candidate_result)
    return candidate_result

def main():
    print("Worker persistente iniciado. Procesando tareas reales...")
    while True:
        task = task_bus.get_next_task()
        if task:
            process_task(task)
        time.sleep(2)

if __name__ == "__main__":
    main()
