"""
Adaptador para OpenAI GPT.

Usa la Responses API de OpenAI.
"""

from __future__ import annotations

import asyncio
import os

import httpx

from adapters.base import (
    ModelAdapter,
    ModelRequest,
    ModelResponse,
    ModelStatus,
)


class GPTAdapter(ModelAdapter):
    """Adaptador concreto para OpenAI GPT."""

    name = "gpt"
    default_version = "gpt-6-luna"
    BASE_URL = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv(
            "OPENAI_MODEL",
            self.default_version,
        )

        if not self.api_key:
            raise ValueError("OPENAI_API_KEY no configurada.")

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """
        Envía una solicitud a la API de OpenAI y normaliza el texto de salida.

        Args:
            request (ModelRequest): Solicitud común con prompt, rol y límite de tiempo.

        Returns:
            ModelResponse: Contenido, versión, uso de tokens, latencia y estado. Los errores
            de proveedor, timeout o parsing se registran en el estado y la lista `errors`.

        Raises:
            asyncio.CancelledError: Puede propagarse si se cancela la coroutine.

        Side effects:
            Realiza llamadas HTTPS y puede generar consumo facturable. No registra el valor
            de la clave en el objeto de respuesta ni ejecuta herramientas por sí misma.

        Example:
            response = await adapter.generate(ModelRequest(prompt="Explica el resultado"))
        """
        start_ms = self._now_ms()

        input_items: list[dict[str, str]] = []

        if request.system_prompt:
            input_items.append({
                "role": "system",
                "content": request.system_prompt,
            })

        input_items.append({
            "role": "user",
            "content": request.prompt,
        })

        payload = {
            "model": self.model,
            "input": input_items,
            "max_output_tokens": max(request.max_tokens, 256),
            "reasoning": {
                "effort": "low",
            },
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        max_retries = 3

        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=request.timeout_seconds
                ) as client:
                    response = await client.post(
                        self.BASE_URL,
                        headers=headers,
                        json=payload,
                    )

                    if response.status_code in (429, 500, 502, 503, 504):
                        if attempt < max_retries:
                            retry_after = response.headers.get("Retry-After")

                            if retry_after:
                                try:
                                    delay = float(retry_after)
                                except ValueError:
                                    delay = 2 ** attempt
                            else:
                                delay = 2 ** attempt

                            await asyncio.sleep(delay)
                            continue

                    response.raise_for_status()
                    data = response.json()
                    break

            except httpx.TimeoutException as e:
                return self._error_response(
                    request,
                    ModelStatus.TIMEOUT,
                    str(e),
                    start_ms,
                )

            except httpx.HTTPStatusError as e:
                return self._error_response(
                    request,
                    ModelStatus.ERROR,
                    f"HTTP {e.response.status_code}: "
                    f"{e.response.text[:300]}",
                    start_ms,
                )

            except Exception as e:
                return self._error_response(
                    request,
                    ModelStatus.ERROR,
                    repr(e),
                    start_ms,
                )

        else:
            return self._error_response(
                request,
                ModelStatus.ERROR,
                "OpenAI agotó los reintentos.",
                start_ms,
            )

        try:
            output_parts: list[str] = []

            for item in data.get("output", []):
                if item.get("type") != "message":
                    continue

                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        text = content.get("text", "")
                        if text:
                            output_parts.append(text)

            output = "".join(output_parts)

            usage = data.get("usage", {})

            if not output:
                return self._error_response(
                    request,
                    ModelStatus.REFUSED,
                    "Respuesta sin texto de salida.",
                    start_ms,
                )

            return ModelResponse(
                call_id=request.call_id,
                model=self.name,
                model_version=data.get("model", self.model),
                role=request.role,
                output=output,
                tokens_in=usage.get("input_tokens", 0),
                tokens_out=usage.get("output_tokens", 0),
                latency_ms=self._elapsed_ms(start_ms),
                status=ModelStatus.OK,
                raw=data,
            )

        except Exception as e:
            return self._error_response(
                request,
                ModelStatus.ERROR,
                f"Parse error: {e}",
                start_ms,
            )
