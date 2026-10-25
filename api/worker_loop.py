"""Worker Loop real (P1): ejecuta tareas vía Master y genera evidencia real."""
import time
import uuid
import asyncio
from datetime import datetime, timezone
from api import task_bus
from core.master import Master

def process_task(task: dict) -> dict:
    task_id = task["task_id"]
    task_bus.mark_task_running(task_id)
    
    objective = task.get("objective", "")
    evidence = []
    summary = "No ejecutado"
    status = "failed"
    
    try:
        # Ejecutar la tarea real usando el Master
        master = Master()
        result = asyncio.run(master.execute_task(objective))
        
        # El resultado del Master contiene el plan y los resultados
        summary = result.get("summary", "Ejecución completada") if isinstance(result, dict) else str(result)
        evidence.append({
            "evidence_id": str(uuid.uuid4()),
            "kind": "calculation",
            "source": "master_execution",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "description": f"Ejecución real del Master para: {objective}",
            "digest": str(hash(str(result)))
        })
        status = "candidate"
    except Exception as e:
        summary = f"Error en ejecución: {str(e)}"
        evidence.append({
            "evidence_id": str(uuid.uuid4()),
            "kind": "error",
            "source": "master_execution",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "description": str(e)
        })
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
    print("Worker persistente iniciado. Ejecutando tareas reales...")
    while True:
        task = task_bus.get_next_task()
        if task:
            process_task(task)
        time.sleep(2)

if __name__ == "__main__":
    main()
