"""
Contrato abstracto para todos los adaptadores de modelo.

Cualquier modelo (Gemini, GPT, Claude, Grok, DeepSeek, Qwen, local)
debe implementar ModelAdapter.generate() y devolver un ModelResponse
con la telemetría completa. Sin telemetría, no hay verificación.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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
    max_tokens: int = 4096
    timeout_seconds: float = 60.0
    role: ModelRole = ModelRole.AGENT
    skills: list[Skill] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


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

    @property
    def total_tokens(self) -> int:
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
