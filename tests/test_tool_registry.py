"""Tests for safe execution of registered tools."""

import asyncio
import threading

from core.tool_registry import ToolRegistry


def test_sync_handler_runs_off_the_event_loop_thread() -> None:
    registry = ToolRegistry()
    handler_threads: list[int] = []

    def sync_handler(*, value: int) -> dict[str, int]:
        handler_threads.append(threading.get_ident())
        return {"value": value}

    registry.register(
        name="sync_test",
        description="Test synchronous dispatch",
        skills=("analysis",),
        handler=sync_handler,
    )

    async def run() -> tuple[int, dict[str, int]]:
        loop_thread = threading.get_ident()
        result = await registry.execute("sync_test", value=7)
        return loop_thread, result

    loop_thread, result = asyncio.run(run())
    assert result == {"value": 7}
    assert handler_threads
    assert handler_threads[0] != loop_thread
