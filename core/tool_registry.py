"""Registro central de herramientas invocables por Master."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    """
    Contrato inmutable de una herramienta registrada.

    Attributes:
        name (str): Identificador único usado en las llamadas.
        description (str): Descripción que se expone a routers y modelos.
        skills (tuple[str, ...]): Skills para las que se propone la herramienta.
        handler (Callable[..., Any]): Función síncrona o asíncrona que ejecuta la operación.

    Security:
        El objeto describe una capacidad; no contiene por sí mismo una regla de
        autorización por agente. La autorización debe aplicarse en `Master`.

    Example:
        spec = ToolSpec("echo", "Devuelve un valor", ("analysis",), lambda value: value)
    """
    name: str
    description: str
    skills: tuple[str, ...]
    handler: Callable[..., Any]


class ToolRegistry:
    """
    Registro en memoria de herramientas invocables por el host.

    El registro resuelve nombres, publica el inventario por skill y ejecuta handlers
    síncronos o asíncronos. No implementa autorización por agente ni limita los
    efectos del handler; esos controles pertenecen al límite de confianza superior.

    Example:
        registry = ToolRegistry()
        registry.register("echo", "Devuelve un valor", ("analysis",), lambda value: value)
        assert registry.available() == ["echo"]
    """

    def __init__(self) -> None:
        """
        Crea un registro vacío de herramientas.

        Returns:
            None.

        Side effects:
            Inicializa únicamente un diccionario en memoria; no carga plugins ni accede
            a la red.

        Example:
            registry = ToolRegistry()
            assert registry.available() == []
        """
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        description: str,
        skills: tuple[str, ...],
        handler: Callable[..., Any],
    ) -> None:
        """
        Registra un handler con su nombre, descripción y skills compatibles.

        Args:
            name (str): Nombre único de la herramienta.
            description (str): Descripción visible en el inventario y esquemas de llamada.
            skills (tuple[str, ...]): Skills que pueden proponer esta herramienta.
            handler (Callable[..., Any]): Función síncrona o asíncrona que implementa la operación.

        Returns:
            None.

        Raises:
            ValueError: Si el nombre ya está registrado.

        Side effects:
            Añade una entrada al diccionario del registro en memoria; no ejecuta el handler.

        Example:
            registry.register("echo", "Devuelve el valor", ("analysis",), lambda value: value)
        """
        if name in self._tools:
            raise ValueError(f"Herramienta ya registrada: {name}")

        self._tools[name] = ToolSpec(
            name=name,
            description=description,
            skills=skills,
            handler=handler,
        )

    def available(self) -> list[str]:
        """
        Devuelve los nombres registrados en orden alfabético.

        Returns:
            list[str]: Inventario ordenado; la lista es nueva y no expone el diccionario interno.

        Raises:
            No genera excepciones deliberadas.

        Side effects:
            Ninguno.

        Example:
            names = registry.available()
        """
        return sorted(self._tools)

    def get(self, name: str) -> ToolSpec:
        """
        Obtiene la especificación de una herramienta por su nombre exacto.

        Args:
            name (str): Identificador registrado.

        Returns:
            ToolSpec: Definición inmutable de la herramienta.

        Raises:
            ValueError: Si el nombre no existe en el registro.

        Side effects:
            Ninguno.

        Example:
            spec = registry.get("dynamics")
        """
        tool = self._tools.get(name)

        if tool is None:
            raise ValueError(
                f"Herramienta no registrada: {name}"
            )

        return tool

    def tools_for_skill(self, skill: str) -> list[str]:
        """
        Filtra el inventario por coincidencia exacta de skill.

        Args:
            skill (str): Skill que debe aparecer en `ToolSpec.skills`.

        Returns:
            list[str]: Nombres compatibles en orden alfabético.

        Raises:
            No genera excepciones deliberadas para nombres de skill ordinarios.

        Side effects:
            Ninguno; no ejecuta las herramientas.

        Example:
            candidates = registry.tools_for_skill("analysis")
        """
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
        """
        Ejecuta un handler registrado y espera su resultado.

        Args:
            name (str): Nombre de la herramienta a ejecutar.
            **kwargs (Any): Argumentos reenviados sin transformación al handler.

        Returns:
            Any: Resultado del handler. Los handlers async se esperan directamente;
            los síncronos se ejecutan en un hilo para no bloquear el event loop, y si
            devuelven un awaitable también se espera.

        Raises:
            ValueError: Si no existe el nombre registrado.
            Exception: Propaga errores del handler.

        Side effects:
            Los determina el handler. El registro no valida permisos ni neutraliza
            operaciones de red, persistencia o coste.

        Example:
            registry.register("echo", "Devuelve el valor", ("analysis",), lambda value: value)
            result = await registry.execute("echo", value="hola")
        """
        tool = self.get(name)

        if inspect.iscoroutinefunction(tool.handler):
            return await tool.handler(**kwargs)

        # Network and CPU-bound synchronous handlers must not block the host's
        # event loop, particularly when multiple tasks are executed concurrently.
        result = await asyncio.to_thread(tool.handler, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
