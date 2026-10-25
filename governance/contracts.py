"""Contratos versionados para trabajos, evidencia y decisiones.

Un resultado de agente es un candidato. La aceptación se registra únicamente
después de ejecutar el verificador independiente.
"""
from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    VALIDATING = "validating"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_EVIDENCE = "needs_evidence"
    BLOCKED = "blocked"
    FAILED = "failed"


class ValidationStatus(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_EVIDENCE = "needs_evidence"
    BLOCKED = "blocked"


class EvidenceKind(StrEnum):
    SOURCE = "source"
    OBSERVATION = "observation"
    CALCULATION = "calculation"
    TEST = "test"
    INFERENCE = "inference"


class EvidenceProducer(StrEnum):
    SYSTEM = "system"
    EXTERNAL_TOOL = "external_tool"
    AGENT = "agent"
    VERIFIER = "verifier"


class FinancialAnalysisRequest(ContractModel):
    objective: str = Field(min_length=1, max_length=4000)
    asset: str = Field(default="unspecified", min_length=1, max_length=64)
    historical_prices: list[float] = Field(min_length=30, max_length=5000)

    @field_validator("historical_prices", mode="before")
    @classmethod
    def validate_raw_prices(cls, value: Any) -> list[float]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("historical_prices debe ser una lista numérica.")
        prices: list[float] = []
        for index, item in enumerate(value):
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError(f"historical_prices[{index}] debe ser numérico.")
            number = float(item)
            if not math.isfinite(number) or number <= 0:
                raise ValueError(
                    f"historical_prices[{index}] debe ser positivo y finito."
                )
            prices.append(number)
        return prices


class EvidenceRecord(ContractModel):
    evidence_id: str = Field(min_length=1, max_length=128)
    kind: EvidenceKind
    description: str = Field(min_length=1, max_length=4000)
    producer: EvidenceProducer = EvidenceProducer.AGENT
    source: str | None = Field(default=None, max_length=2048)
    observed_at: datetime | None = None
    content_hash: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def validate_source_provenance(self) -> "EvidenceRecord":
        if self.producer == EvidenceProducer.EXTERNAL_TOOL and not (
            self.source and self.content_hash
        ):
            raise ValueError(
                "La evidencia de herramienta externa requiere source y content_hash."
            )
        return self


class ValidationCheck(ContractModel):
    name: str = Field(min_length=1, max_length=128)
    passed: bool
    details: str = Field(default="", max_length=4000)


class ValidationRecord(ContractModel):
    status: ValidationStatus = ValidationStatus.NOT_EVALUATED
    verifier: str = Field(default="catedralx-independent-verifier", min_length=1)
    checks: list[ValidationCheck] = Field(default_factory=list)

    @model_validator(mode="after")
    def accepted_means_all_checks_pass(self) -> "ValidationRecord":
        if self.status == ValidationStatus.ACCEPTED:
            if not self.checks or any(not check.passed for check in self.checks):
                raise ValueError(
                    "No se puede aceptar un resultado con verificaciones fallidas "
                    "o sin comprobaciones."
                )
        return self


class AgentRun(ContractModel):
    agent_id: str = Field(min_length=1, max_length=128)
    status: Literal["completed", "partial", "failed", "skipped"]
    agent_version: str | None = Field(default=None, max_length=128)
    output_hash: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    summary: str | None = Field(default=None, max_length=4000)


class AgentReview(ContractModel):
    """Salida estructurada de una revisión; no tiene campo de aceptación."""

    reviewer_id: str = Field(min_length=1, max_length=128)
    status: Literal["completed", "partial", "failed", "unconfigured"]
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    counterevidence: list[EvidenceRecord] = Field(default_factory=list)
    unresolved_contradictions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    agent_runs: list[AgentRun] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=8000)


class Provenance(ContractModel):
    engine: str = Field(min_length=1, max_length=128)
    engine_version: str = Field(default="unreported", max_length=128)
    agent_runs: list[AgentRun] = Field(default_factory=list)
    input_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    analysis_hash: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )


class DecisionResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    trace_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    status: JobStatus
    objective: str = Field(min_length=1, max_length=4000)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    counterevidence: list[str] = Field(default_factory=list)
    validation: ValidationRecord
    decision: dict[str, Any] | None = None
    provenance: Provenance

    @model_validator(mode="after")
    def align_decision_and_validation(self) -> "DecisionResult":
        if self.status == JobStatus.ACCEPTED:
            if self.validation.status != ValidationStatus.ACCEPTED:
                raise ValueError(
                    "status=accepted requiere validation.status=accepted."
                )
            if self.decision is None:
                raise ValueError("Un resultado aceptado requiere una decisión.")
        return self
