"""Worker Loop para Orchestration Flow (Fase 3)."""
import time
import uuid
from datetime import datetime, timezone
from api import task_bus

def process_task(task: dict) -> dict:
    """Simula la ejecución de un agente y genera un CandidateResult."""
    task_id = task["task_id"]
    task_bus.mark_task_running(task_id)
    
    # Simulamos el trabajo del agente
    time.sleep(1) 
    
    candidate_result = {
        "candidate_id": str(uuid.uuid4()),
        "worker_id": "worker-agent-01",
        "status": "candidate",
        "summary": f"Processed objective: {task.get('objective', 'unknown')}",
        "evidence": [
            {
                "evidence_id": str(uuid.uuid4()),
                "kind": "calculation",
                "source": "simulated_agent",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "description": "Simulated evidence for proactive flow."
            }
        ],
        "assumptions": ["Simulated environment"],
        "contradictions": [],
        "trace_id": task.get("trace_id", str(uuid.uuid4()))
    }
    
    task_bus.mark_task_completed(task_id, candidate_result)
    return candidate_result

def run_once():
    """Busca y procesa una tarea si hay alguna disponible."""
    task = task_bus.get_next_task()
    if task:
        return process_task(task)
    return None

def run_loop(interval=2):
    """Loop infinito de escucha (usado en producción o background)."""
    print("Worker Loop iniciado. Esperando tareas...")
    while True:
        result = run_once()
        if result:
            print(f"Tarea procesada: {result['candidate_id']}")
        time.sleep(interval)
