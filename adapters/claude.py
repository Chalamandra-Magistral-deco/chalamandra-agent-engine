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
    default_version = "claude-sonnet-5-5"
    BASE_URL = "https://api.anthropic.com/v1/messages"
    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ):
        """
        Configura la clave y el identificador del modelo Anthropic.

        Args:
            api_key (str | None): Clave explícita; si se omite, lee `ANTHROPIC_API_KEY`.
            model (str | None): Modelo explícito; si se omite, lee `CLAUDE_MODEL` o usa `default_version`.

        Returns:
            None.

        Raises:
            ValueError: Si no se dispone de una clave de API.

        Security:
            La clave permanece en la instancia del adapter y no debe registrarse en logs.
            Inicializar el adapter no demuestra conectividad ni realiza una llamada facturable.

        Example:
            adapter = ClaudeAdapter(api_key=secret_from_vault)
        """

        self.api_key: str = api_key or os.getenv("ANTHROPIC_API_KEY") or ""
        self.model = model or os.getenv(
            "CLAUDE_MODEL",
            self.default_version,
        )

        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY no configurada.")

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """
        Envía un prompt a Anthropic Messages API y normaliza la respuesta.

        Args:
            request (ModelRequest): Prompt, configuración de generación, rol y límite de tiempo.

        Returns:
            ModelResponse: Respuesta trazable con versión, texto, tokens, latencia y estado.
            Los fallos HTTP, de conexión o de análisis se representan en `status` y `errors`;
            las respuestas sin texto se marcan como `REFUSED`.

        Raises:
            asyncio.CancelledError: Puede propagarse si se cancela la coroutine.

        Side effects:
            Realiza solicitudes HTTPS al proveedor, puede repetir ciertos errores transitorios
            hasta cuatro intentos totales y puede incurrir en coste según la cuenta de Anthropic.
            No registra secretos en el resultado estructurado.

        Example:
            response = await adapter.generate(ModelRequest(prompt="Resume el texto"))
        """
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
