"""Integration test: plan + execute contra Ollama real.

No usa mocks. Requiere Ollama corriendo con qwen2.5:3b.
"""

import asyncio
import time

from core.master import Master


async def main() -> None:
    master = Master()
    task = "Dime si el HTML de un sitio tiene meta description."

    print("=== EXECUTE TASK (END-TO-END) ===", flush=True)

    t0 = time.time()
    result = await master.execute_task(task)
    elapsed = time.time() - t0

    print(f"\nEstado: {result.status} en {elapsed:.1f}s", flush=True)
    print(f"Task: {result.task}", flush=True)
    print(f"Subtasks: {len(result.subtask_results)}", flush=True)

    for sid, item in result.subtask_results.items():
        print(
            f"\n--- Subtask [{sid}] "
            f"(modelo: {item.model_used}, skill: {item.skill}) ---",
            flush=True,
        )
        print(f"Status: {item.status}", flush=True)
        print(item.output, flush=True)

        if item.errors:
            print(f"Errors: {item.errors}", flush=True)

    if result.errors:
        print(f"\nExecution errors: {result.errors}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
