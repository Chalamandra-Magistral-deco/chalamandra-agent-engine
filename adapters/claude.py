"""
Adaptador para Anthropic Claude.

Usa la Messages API de Anthropic.
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


class ClaudeAdapter(ModelAdapter):
    """Adaptador concreto para Anthropic Claude."""

    name = "claude"
    default_version = "claude-sonnet-5"
    BASE_URL = "https://api.anthropic.com/v1/messages"
    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.model = model or os.getenv(
            "CLAUDE_MODEL",
            self.default_version,
        )

        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY no configurada.")

    async def generate(self, request: ModelRequest) -> ModelResponse:
        start_ms = self._now_ms()

        messages = [
            {
                "role": "user",
                "content": request.prompt,
            }
        ]

        payload = {
            "model": self.model,
            "max_tokens": max(request.max_tokens, 256),
            "messages": messages,
        }

        if request.system_prompt:
            payload["system"] = request.system_prompt

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
            "content-type": "application/json",
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

                    if response.status_code in (
                        429,
                        500,
                        502,
                        503,
                        529,
                    ):
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
                "Claude agotó los reintentos.",
                start_ms,
            )

        try:
            output_parts: list[str] = []

            for block in data.get("content", []):
                if block.get("type") == "text":
                    text = block.get("text", "")
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
