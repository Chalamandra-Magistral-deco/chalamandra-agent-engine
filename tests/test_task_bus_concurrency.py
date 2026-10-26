import threading
from api import task_bus

def test_concurrent_task_reservation():
    task_bus.init_db()
    # Limpiar base de datos de prueba
    with task_bus._get_conn() as conn:
        conn.execute("DELETE FROM tasks")
    
    # Crear una sola tarea
    task_id = task_bus.enqueue_task({"objective": "prueba concurrencia", "trace_id": "t1"})
    
    resultados = []
    
    def worker():
        task = task_bus.get_next_task()
        if task and task["task_id"] == task_id:
            resultados.append(task_id)
            
    # Lanzar 5 hilos simultáneos
    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    # Solo uno debe haber ganado
    assert len(resultados) == 1
