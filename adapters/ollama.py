"""
Adaptador para Ollama (modelos locales).

Usa la API REST local de Ollama en http://127.0.0.1:11434.
Soporta system prompt nativo vía /api/chat.
No requiere API key. Coste = 0 USD (modelo local).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from adapters.base import (
    ModelAdapter,
    ModelRequest,
    ModelResponse,
    ModelStatus,
    Skill,
)


class OllamaAdapter(ModelAdapter):
    """Adaptador concreto para modelos servidos por Ollama."""

    name = "ollama"
    default_version = "qwen2.5:3b"

    # Capacidad verificada por la suite actual.
    # Se amplía únicamente cuando exista una prueba correspondiente.
    verified_skills = (Skill.REASONING, Skill.ANALYSIS)

    DEFAULT_BASE_URL = "http://127.0.0.1:11434"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or self.DEFAULT_BASE_URL).rstrip(
            "/"
        )
        self.model = model or os.getenv("OLLAMA_MODEL", self.default_version)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """
        Envía una conversación al servidor Ollama configurado.

        Args:
            request (ModelRequest): Prompt, mensajes, opciones y herramientas nativas opcionales.

        Returns:
            ModelResponse: Texto y llamadas a herramientas normalizadas, junto con tokens,
            latencia y estado. Conexiones fallidas, timeout y respuestas vacías producen un
            estado de error/rechazo en vez de un resultado exitoso falso.

        Raises:
            asyncio.CancelledError: Puede propagarse si se cancela la coroutine.

        Side effects:
            Realiza una llamada HTTP al endpoint local configurado (por defecto
            `http://127.0.0.1:11434/api/chat`). En el uso predeterminado no usa una API de
            nube ni reporta coste de proveedor; los recursos locales siguen teniendo coste.
            Las llamadas nativas se devuelven como propuestas, no se ejecutan aquí.

        Example:
            response = await adapter.generate(ModelRequest(prompt="Di solo: OK"))
        """
        start_ms = self._now_ms()

        messages: list[dict[str, Any]] = request.messages or []
        if request.messages is None:
            if request.system_prompt:
                messages.append({"role": "system", "content": request.system_prompt})
            messages.append({"role": "user", "content": request.prompt})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }
        if request.tools:
            payload["tools"] = request.tools

        url = f"{self.base_url}/api/chat"

        try:
            async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
                r = await client.post(url, json=payload)
                r.raise_for_status()
                data = r.json()
        except httpx.TimeoutException as e:
            return self._error_response(request, ModelStatus.TIMEOUT, str(e), start_ms)
        except httpx.ConnectError as e:
            return self._error_response(
                request,
                ModelStatus.ERROR,
                f"No se pudo conectar a Ollama en {self.base_url}: {e}",
                start_ms,
            )
        except httpx.HTTPStatusError as e:
            return self._error_response(
                request,
                ModelStatus.ERROR,
                f"HTTP {e.response.status_code}: {e.response.text[:300]}",
                start_ms,
            )
        except Exception as e:
            return self._error_response(request, ModelStatus.ERROR, repr(e), start_ms)

        try:
            message = data.get("message", {})
            output = message.get("content", "")
            raw_tool_calls = message.get("tool_calls", []) or []
            tool_calls: list[dict[str, Any]] = []
            for call in raw_tool_calls:
                function = call.get("function", {}) if isinstance(call, dict) else {}
                name = function.get("name")
                arguments = function.get("arguments", {})
                if isinstance(name, str) and isinstance(arguments, dict):
                    tool_calls.append({"name": name, "arguments": arguments})

            if not output and not tool_calls and data.get("done_reason") == "load":
                return self._error_response(
                    request, ModelStatus.REFUSED, "Modelo sin salida (load)", start_ms
                )

            if not output and not tool_calls:
                return self._error_response(
                    request,
                    ModelStatus.REFUSED,
                    "Respuesta sin texto ni llamadas a herramientas.",
                    start_ms,
                )

            return ModelResponse(
                call_id=request.call_id,
                model=self.name,
                model_version=data.get("model", self.model),
                role=request.role,
                output=output,
                tool_calls=tool_calls,
                tokens_in=data.get("prompt_eval_count", 0),
                tokens_out=data.get("eval_count", 0),
                cost_usd=0.0,  # modelo local, sin coste de API
                latency_ms=self._elapsed_ms(start_ms),
                status=ModelStatus.OK,
                raw=data,
            )
        except Exception as e:
            return self._error_response(request, ModelStatus.ERROR, f"Parse error: {e}", start_ms)
