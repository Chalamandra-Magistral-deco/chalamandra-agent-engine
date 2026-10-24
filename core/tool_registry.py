"""Registro central de herramientas invocables por Master."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    skills: tuple[str, ...]
    handler: Callable[..., Any]


class ToolRegistry:
    """Descubre e invoca herramientas registradas."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        description: str,
        skills: tuple[str, ...],
        handler: Callable[..., Any],
    ) -> None:
        if name in self._tools:
            raise ValueError(f"Herramienta ya registrada: {name}")

        self._tools[name] = ToolSpec(
            name=name,
            description=description,
            skills=skills,
            handler=handler,
        )

    def available(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> ToolSpec:
        tool = self._tools.get(name)

        if tool is None:
            raise ValueError(
                f"Herramienta no registrada: {name}"
            )

        return tool

    def tools_for_skill(self, skill: str) -> list[str]:
        return sorted(
            name
            for name, tool in self._tools.items()
            if skill in tool.skills
        )

    async def execute(
        self,
        name: str,
        **kwargs: Any,
    ) -> Any:
        tool = self.get(name)

        if inspect.iscoroutinefunction(tool.handler):
            return await tool.handler(**kwargs)

        # Network and CPU-bound synchronous handlers must not block the host's
        # event loop, particularly when multiple tasks are executed concurrently.
        result = await asyncio.to_thread(tool.handler, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
