"""
Contrato abstracto para todos los adaptadores de modelo.

Cualquier modelo (Gemini, GPT, Claude, Grok, DeepSeek, Qwen, local)
debe implementar ModelAdapter.generate() y devolver un ModelResponse
con la telemetría completa. Sin telemetría, no hay verificación.
"""

from __future__ import annotations

import math
import os
import time
import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _positive_int_env(name: str, default: int) -> int:
    """Lee un entero positivo opcional de la configuración del entorno."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser un entero positivo") from exc
    if value < 1:
        raise ValueError(f"{name} debe ser un entero positivo")
    return value


def _positive_float_env(name: str, default: float) -> float:
    """Lee un número real finito positivo de la configuración del entorno."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser un número positivo") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} debe ser un número positivo")
    return value


# ---------------------------------------------------------------------------
# Enums canónicos
# ---------------------------------------------------------------------------


class ModelRole(str, Enum):
    """Rol que el sistema asigna al modelo en una llamada concreta."""

    AGENT = "agent"
    SPECIALIST = "specialist"
    CRITIC = "critic"
    RESEARCHER = "researcher"
    CODER = "coder"
    ANALYST = "analyst"
    GENERATOR = "generator"
    VERIFIER = "verifier"
    PLANNER = "planner"
    SYNTHESIZER = "synthesizer"
    RED_TEAM = "red_team"


class ModelStatus(str, Enum):
    """Estado final de una llamada a un modelo."""

    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    REFUSED = "refused"


class Skill(str, Enum):
    """Skills que el Skill Router puede exigir."""

    RESEARCH = "research"
    REASONING = "reasoning"
    CODING = "coding"
    DEBUGGING = "debugging"
    CREATIVE = "creative"
    STRATEGY = "strategy"
    ANALYSIS = "analysis"
    DATA = "data"
    AUTOMATION = "automation"
    VISION = "vision"
    DOCUMENTS = "documents"
    WEB = "web"
    VERIFICATION = "verification"
    RED_TEAM = "red_team"
    SYNTHESIS = "synthesis"
    PLANNING = "planning"
    META_REASONING = "meta_reasoning"


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------


class ModelRequest(BaseModel):
    """Entrada canónica a cualquier modelo."""

    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    prompt: str
    system_prompt: str | None = None
    temperature: float = 0.0
    max_tokens: int = Field(
        default_factory=lambda: _positive_int_env("DEFAULT_MAX_TOKENS", 4096),
        ge=1,
    )
    timeout_seconds: float = Field(
        default_factory=lambda: _positive_float_env("DEFAULT_TIMEOUT_SECONDS", 60.0),
        gt=0,
    )
    role: ModelRole = ModelRole.AGENT
    skills: list[Skill] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Native tool definitions and conversation history are optional so adapters
    # that do not support tool calling remain backward-compatible.
    tools: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[dict[str, Any]] | None = None


class ModelResponse(BaseModel):
    """Salida canónica de cualquier modelo. Trazable y verificable."""

    call_id: str
    model: str
    model_version: str = "unknown"
    role: ModelRole
    output: str
    confidence_self_reported: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    status: ModelStatus = ModelStatus.OK
    errors: list[str] = Field(default_factory=list)
    raw: dict[str, Any] | None = None  # respuesta cruda del proveedor, opcional
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        """
        Calcula el total de tokens de entrada y salida registrados.

        Returns:
            int: Suma de `tokens_in` y `tokens_out`; no consulta al proveedor ni estima tokens ausentes.

        Example:
            total = response.total_tokens
        """
        return self.tokens_in + self.tokens_out


# ---------------------------------------------------------------------------
# Adapter abstracto
# ---------------------------------------------------------------------------


class ModelAdapter(ABC):
    """
    Interfaz que TODO adaptador de modelo debe implementar.

    Reglas:
      - generate() es async y nunca lanza excepción por fallo del modelo.
        Si el modelo falla, devuelve ModelResponse con status=ERROR o TIMEOUT.
      - Debe medir latencia real y poblar tokens/costo si el proveedor lo expone.
      - No inventa valores: si un dato no está disponible, lo deja en 0 o None.
    """

    #: Identificador estable del modelo. Ej: "gemini", "gpt", "claude".
    name: str = "unnamed"

    #: Skills verificadas explícitamente para este adaptador.
    #: No se deben declarar capacidades que no hayan sido comprobadas.
    verified_skills: tuple[Skill, ...] = ()

    #: Versión por defecto si el proveedor no la devuelve.
    default_version: str = "unknown"

    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Ejecuta la llamada al modelo y devuelve respuesta trazable."""
        raise NotImplementedError

    # -----------------------------------------------------------------------
    # Utilidades comunes para subclases
    # -----------------------------------------------------------------------

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _elapsed_ms(self, start_ms: int) -> int:
        return self._now_ms() - start_ms

    def _error_response(
        self,
        request: ModelRequest,
        status: ModelStatus,
        error: str,
        start_ms: int,
    ) -> ModelResponse:
        return ModelResponse(
            call_id=request.call_id,
            model=self.name,
            model_version=self.default_version,
            role=request.role,
            output="",
            status=status,
            errors=[error],
            latency_ms=self._elapsed_ms(start_ms),
        )
