"""
Adaptador opcional para Ollama Cloud.

Usa https://ollama.com/api/chat con OLLAMA_API_KEY.
Se mantiene separado de OllamaAdapter, que continúa siendo exclusivamente local.
No reintenta solicitudes: evita duplicar consumo cuando una respuesta se pierde.
"""

from __future__ import annotations

import math
import os
from typing import Any

import httpx

from adapters.base import ModelAdapter, ModelRequest, ModelResponse, ModelStatus


class OllamaCloudAdapter(ModelAdapter):
    """Adaptador de inferencia remota en Ollama Cloud."""

    name = "ollama_cloud"
    default_version = "deepseek-v4.1-flash"
    BASE_URL = "https://ollama.com/api/chat"

    DEFAULT_INPUT_PRICE_PER_MILLION = 0.30
    DEFAULT_OUTPUT_PRICE_PER_MILLION = 1.20

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        input_price_per_million: float | None = None,
        output_price_per_million: float | None = None,
    ) -> None:
        """
        Configura Ollama Cloud sin realizar una llamada de red.

        Los precios por millón son estimaciones editables. Por defecto se usan los
        precios publicados de hora pico para deepseek-v4.1-flash. El descuento de
        tokens en caché no se aplica porque el endpoint /api/chat no expone de forma
        documentada un contador de tokens cacheados en la respuesta normalizada.
        """
        self.api_key = api_key or os.getenv("OLLAMA_API_KEY", "")
        if not self.api_key:
            raise ValueError("OLLAMA_API_KEY no configurada.")

        self.model = model or os.getenv(
            "OLLAMA_CLOUD_MODEL",
            self.default_version,
        )
        self.input_price_per_million = self._price(
            input_price_per_million,
            "OLLAMA_CLOUD_INPUT_USD_PER_MILLION",
            self.DEFAULT_INPUT_PRICE_PER_MILLION,
        )
        self.output_price_per_million = self._price(
            output_price_per_million,
            "OLLAMA_CLOUD_OUTPUT_USD_PER_MILLION",
            self.DEFAULT_OUTPUT_PRICE_PER_MILLION,
        )

    @staticmethod
    def _price(
        explicit: float | None,
        env_name: str,
        default: float,
    ) -> float:
        """Lee un precio finito no negativo desde un argumento o el entorno."""
        raw = explicit if explicit is not None else os.getenv(env_name, str(default))
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{env_name} debe ser un número finito no negativo.") from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{env_name} debe ser un número finito no negativo.")
        return value

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """
        Envía una solicitud al endpoint oficial de Ollama Cloud.

        Las llamadas a herramientas se devuelven como propuestas normalizadas. Este
        adaptador no ejecuta herramientas ni concede permisos a los modelos.
        """
        start_ms = self._now_ms()

        messages: list[dict[str, Any]] = (
            [dict(message) for message in request.messages]
            if request.messages
            else []
        )
        if not messages:
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

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
                response = await client.post(
                    self.BASE_URL,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            return self._error_response(request, ModelStatus.TIMEOUT, str(exc), start_ms)
        except httpx.ConnectError:
            return self._error_response(
                request,
                ModelStatus.ERROR,
                "No se pudo conectar con Ollama Cloud.",
                start_ms,
            )
        except httpx.HTTPStatusError as exc:
            # No incluimos cuerpos arbitrarios del proveedor en trazas o errores.
            return self._error_response(
                request,
                ModelStatus.ERROR,
                f"HTTP {exc.response.status_code} en Ollama Cloud; no se reintentó la solicitud.",
                start_ms,
            )
        except Exception as exc:
            return self._error_response(
                request,
                ModelStatus.ERROR,
                f"Error de transporte Ollama Cloud: {type(exc).__name__}",
                start_ms,
            )

        try:
            message = data.get("message", {})
            output = message.get("content", "") or ""
            raw_tool_calls = message.get("tool_calls", []) or []
            tool_calls: list[dict[str, Any]] = []

            for call in raw_tool_calls:
                function = call.get("function", {}) if isinstance(call, dict) else {}
                name = function.get("name")
                arguments = function.get("arguments", {})
                if isinstance(name, str) and isinstance(arguments, dict):
                    tool_calls.append({"name": name, "arguments": arguments})

            if not output and not tool_calls:
                return self._error_response(
                    request,
                    ModelStatus.REFUSED,
                    "Respuesta de Ollama Cloud sin texto ni llamadas a herramientas.",
                    start_ms,
                )

            usage_complete = (
                isinstance(data.get("prompt_eval_count"), int)
                and not isinstance(data.get("prompt_eval_count"), bool)
                and data["prompt_eval_count"] >= 0
                and isinstance(data.get("eval_count"), int)
                and not isinstance(data.get("eval_count"), bool)
                and data["eval_count"] >= 0
            )
            tokens_in = data["prompt_eval_count"] if usage_complete else 0
            tokens_out = data["eval_count"] if usage_complete else 0
            estimated_cost = (
                (
                    tokens_in * self.input_price_per_million
                    + tokens_out * self.output_price_per_million
                ) / 1_000_000
                if usage_complete
                else None
            )

            raw_response = dict(data)
            raw_response["chalamandra_cost_estimate"] = {
                "currency": "USD",
                "estimated": usage_complete,
                "usage_complete": usage_complete,
                "unavailable_reason": (
                    None if usage_complete else "provider_usage_counters_missing_or_invalid"
                ),
                "input_price_usd_per_million": self.input_price_per_million,
                "output_price_usd_per_million": self.output_price_per_million,
                "cached_input_discount_applied": False,
            }

            return ModelResponse(
                call_id=request.call_id,
                model=self.name,
                model_version=data.get("model", self.model),
                role=request.role,
                output=output,
                tool_calls=tool_calls,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=estimated_cost,
                latency_ms=self._elapsed_ms(start_ms),
                status=ModelStatus.OK,
                raw=raw_response,
            )
        except Exception as exc:
            return self._error_response(
                request,
                ModelStatus.ERROR,
                f"Error al interpretar respuesta Ollama Cloud: {type(exc).__name__}",
                start_ms,
            )
