"""
Adaptador para Google Gemini.
Usa la API REST Generative Language v1beta.
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


class GeminiAdapter(ModelAdapter):
    """Adaptador concreto para modelos Gemini de Google."""

    name = "gemini"
    default_version = "gemini-3.8-flash"
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model = model or self.default_version
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY no configurada. "
                "Cópiala en config/.env y expórtala como variable de entorno."
            )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        start_ms = self._now_ms()

        contents = []
        if request.system_prompt:
            contents.append({
                "role": "user",
                "parts": [{"text": request.system_prompt}],
            })
        contents.append({
            "role": "user",
            "parts": [{"text": request.prompt}],
        })

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": max(request.max_tokens, 512),
                "thinkingConfig": {
                    "thinkingLevel": "low",
                },
            },
        }

        url = f"{self.BASE_URL}/{self.model}:generateContent?key={self.api_key}"

        max_retries = 3

        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
                    r = await client.post(url, json=payload)

                    if r.status_code == 503 and attempt < max_retries:
                        await asyncio.sleep(2 ** attempt)
                        continue

                    r.raise_for_status()
                    data = r.json()
                    break

            except httpx.TimeoutException as e:
                return self._error_response(
                    request, ModelStatus.TIMEOUT, str(e), start_ms
                )

            except httpx.HTTPStatusError as e:
                return self._error_response(
                    request,
                    ModelStatus.ERROR,
                    f"HTTP {e.response.status_code}: {e.response.text[:300]}",
                    start_ms,
                )

            except Exception as e:
                return self._error_response(
                    request, ModelStatus.ERROR, repr(e), start_ms
                )
        else:
            return self._error_response(
                request,
                ModelStatus.ERROR,
                "Gemini agotó los reintentos por HTTP 503",
                start_ms,
            )

        try:
            candidates = data.get("candidates", [])
            if not candidates:
                return self._error_response(
                    request, ModelStatus.REFUSED, "Sin candidatos en la respuesta", start_ms
                )

            parts = candidates[0].get("content", {}).get("parts", [])
            output = "".join(p.get("text", "") for p in parts)

            usage = data.get("usageMetadata", {})
            return ModelResponse(
                call_id=request.call_id,
                model=self.name,
                model_version=self.model,
                role=request.role,
                output=output,
                tokens_in=usage.get("promptTokenCount", 0),
                tokens_out=usage.get("candidatesTokenCount", 0),
                latency_ms=self._elapsed_ms(start_ms),
                status=ModelStatus.OK,
                raw=data,
            )
        except Exception as e:
            return self._error_response(
                request, ModelStatus.ERROR, f"Parse error: {e}", start_ms
            )
