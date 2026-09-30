"""
Adaptador para DeepSeek API.

Usa Chat Completions compatible con OpenAI.
Modelo actual por defecto: deepseek-flash.
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


class DeepSeekAdapter(ModelAdapter):
    """Adaptador concreto para DeepSeek."""

    name = "deepseek"
    default_version = "deepseek-flash"
    BASE_URL = "https://api.deepseek.com/chat/completions"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self.model = model or os.getenv(
            "DEEPSEEK_MODEL",
            self.default_version,
        )

        if not self.api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY no configurada."
            )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        start_ms = self._now_ms()

        messages: list[dict[str, str]] = []

        if request.system_prompt:
            messages.append({
                "role": "system",
                "content": request.system_prompt,
            })

        messages.append({
            "role": "user",
            "content": request.prompt,
        })

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": False,
            "thinking": {
                "type": "enabled",
                "reasoning_effort": "low",
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

                    if response.status_code in (429, 503):
                        if attempt < max_retries:
                            retry_after = response.headers.get(
                                "Retry-After"
                            )

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
                "DeepSeek agotó los reintentos.",
                start_ms,
            )

        try:
            choices = data.get("choices", [])

            if not choices:
                return self._error_response(
                    request,
                    ModelStatus.REFUSED,
                    "Sin choices en la respuesta",
                    start_ms,
                )

            message = choices[0].get("message", {})
            output = message.get("content") or ""

            usage = data.get("usage", {})

            return ModelResponse(
                call_id=request.call_id,
                model=self.name,
                model_version=data.get(
                    "model",
                    self.model,
                ),
                role=request.role,
                output=output,
                tokens_in=usage.get(
                    "prompt_tokens",
                    0,
                ),
                tokens_out=usage.get(
                    "completion_tokens",
                    0,
                ),
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
