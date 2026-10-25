"""Primer circuito vertical: solicitud, CatedralX, revisión y verificación.

Este módulo no crea un broker ni un scheduler. Las dependencias se inyectan para
que el mismo circuito se pueda probar sin modelos externos, datos de mercado o
infraestructura de producción.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import inspect
import json
import os
from typing import Any, Protocol
from uuid import uuid4

from governance.contracts import (
    AgentReview,
    AgentRun,
    DecisionResult,
    EvidenceKind,
    EvidenceProducer,
    EvidenceRecord,
    FinancialAnalysisRequest,
    JobStatus,
    Provenance,
)
from governance.verification import verify_candidate


class ReviewProvider(Protocol):
    async def review(
        self,
        request: FinancialAnalysisRequest,
        analysis: dict[str, Any],
    ) -> AgentReview:
        """Devuelve una revisión candidata con trazas y evidencia estructurada."""


def _sha256(value: Any) -> str:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def request_hash(request: FinancialAnalysisRequest) -> str:
    return _sha256(request.model_dump(mode="json"))


async def analyze_with_catedralx(
    request: FinancialAnalysisRequest,
) -> dict[str, Any]:
    """Invoca el adaptador ya existente, sin reimplementar el motor."""
    from pipeline.catedralx_tool import catedralx_reasoning_tool

    return await asyncio.to_thread(
        catedralx_reasoning_tool,
        historical_prices=request.historical_prices,
        current_price=request.historical_prices[-1],
        asset=request.asset,
        data_source="provided",
    )


class UnconfiguredReviewProvider:
    """Falla cerrada cuando no hay un adaptador de revisión configurado."""

    async def review(
        self,
        request: FinancialAnalysisRequest,
        analysis: dict[str, Any],
    ) -> AgentReview:
        return AgentReview(
            reviewer_id="agent-review-not-configured",
            status="unconfigured",
            limitations=[
                "No existe un proveedor de revisión habilitado en esta ejecución.",
                "El resultado de CatedralX queda como candidato y no puede aceptarse.",
            ],
        )


class MasterAgentReviewProvider:
    """Adaptador preliminar para el motor existente de agentes.

    El resultado narrativo del Master se conserva como traza, pero nunca se
    transforma automáticamente en evidencia externa. Por diseño esta versión
    devuelve status=partial; conectar el motor generativo no basta para aceptar.
    """

    async def review(
        self,
        request: FinancialAnalysisRequest,
        analysis: dict[str, Any],
    ) -> AgentReview:
        try:
            from core.master import Master

            engine = Master()
            task = (
                "Revisa críticamente el candidato analítico de CatedralX. "
                "Busca errores de entrada, supuestos no justificados, límites del "
                "método y contraevidencia. Distingue hechos comprobados de hipótesis. "
                "No declares aceptado el resultado y no inventes fuentes ni pruebas.\\n"
                f"Objetivo: {request.objective}\\n"
                f"Activo: {request.asset}\\n"
                "Candidato JSON: "
                + json.dumps(analysis, ensure_ascii=False, sort_keys=True, default=str)
            )
            raw_result = await engine.execute_task(task)
            if dataclasses.is_dataclass(raw_result):
                result_payload = dataclasses.asdict(raw_result)
            elif isinstance(raw_result, dict):
                result_payload = raw_result
            else:
                result_payload = {"result": str(raw_result)}
            rendered = json.dumps(
                result_payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            output_hash = _sha256({"agent_output": rendered})
            return AgentReview(
                reviewer_id="chalamandra-agent-engine",
                status="partial",
                limitations=[
                    "El Master produjo una salida generativa, no una cadena de evidencia "
                    "verificada por herramientas.",
                    "Se requiere convertir las pruebas y fuentes reales en EvidenceRecord "
                    "con producer=external_tool antes de aceptar.",
                ],
                agent_runs=[
                    AgentRun(
                        agent_id="Master.execute_task",
                        status="partial",
                        output_hash=output_hash,
                        summary=rendered[:3500],
                    )
                ],
            )
        except Exception as exc:
            # No exponer mensajes internos, rutas ni posibles datos de proveedor.
            return AgentReview(
                reviewer_id="chalamandra-agent-engine",
                status="failed",
                limitations=[
                    f"La revisión de agentes falló ({type(exc).__name__}). "
                    "El candidato no será aceptado."
                ],
                agent_runs=[
                    AgentRun(
                        agent_id="Master.execute_task",
                        status="failed",
                    )
                ],
            )


def default_review_provider() -> ReviewProvider:
    """La inferencia con modelos no se activa por defecto."""
    enabled = os.getenv("CHALAMANDRA_ENABLE_AGENT_REVIEW", "").strip().lower()
    if enabled in {"1", "true", "yes"}:
        return MasterAgentReviewProvider()
    return UnconfiguredReviewProvider()


async def _call_analyzer(
    analyzer: Any,
    request: FinancialAnalysisRequest,
) -> dict[str, Any]:
    if inspect.iscoroutinefunction(analyzer):
        value = await analyzer(request)
    else:
        value = await asyncio.to_thread(analyzer, request)
    if not isinstance(value, dict):
        raise TypeError("El analizador debe devolver un objeto JSON.")
    return value


async def run_financial_flow(
    request: FinancialAnalysisRequest,
    *,
    analyzer: Any = analyze_with_catedralx,
    reviewer: ReviewProvider | None = None,
    job_id: str | None = None,
    trace_id: str | None = None,
) -> DecisionResult:
    """Ejecuta un circuito request → CatedralX → revisión → verificación.

    La aceptación se calcula aquí mediante reglas propias; el candidato no
    puede enviar su propio ValidationRecord ni su propio estado accepted.
    """
    reviewer = reviewer or default_review_provider()
    job_id = job_id or str(uuid4())
    trace_id = trace_id or str(uuid4())
    input_digest = request_hash(request)

    try:
        analysis = await _call_analyzer(analyzer, request)
    except Exception as exc:
        analysis = {
            "status": "error",
            "engine": "CatedralX",
            "error_type": type(exc).__name__,
            "signal_consistent": False,
            "trading_eligible": False,
            "orders_created": False,
            "orders_executed": False,
        }

    if analysis.get("status") == "error":
        review = AgentReview(
            reviewer_id="agent-review-skipped",
            status="failed",
            limitations=[
                "La revisión de agentes no se ejecutó porque falló el motor analítico."
            ],
        )
    else:
        try:
            review = await reviewer.review(request, analysis)
            if not isinstance(review, AgentReview):
                raise TypeError("El proveedor debe devolver AgentReview.")
        except Exception as exc:
            review = AgentReview(
                reviewer_id="agent-review-provider",
                status="failed",
                limitations=[
                    f"Falló el proveedor de revisión ({type(exc).__name__})."
                ],
            )

    analysis_digest = _sha256(analysis)
    evidence: list[EvidenceRecord] = [
        EvidenceRecord(
            evidence_id="input-" + input_digest[7:23],
            kind=EvidenceKind.OBSERVATION,
            producer=EvidenceProducer.SYSTEM,
            description=(
                "Serie de precios recibida en la petición; su origen externo "
                "no se ha autenticado en este circuito."
            ),
            source="caller_payload",
            content_hash=input_digest,
        ),
        EvidenceRecord(
            evidence_id="analysis-" + analysis_digest[7:23],
            kind=EvidenceKind.CALCULATION,
            producer=EvidenceProducer.SYSTEM,
            description=(
                "Salida íntegra del adaptador de CatedralX; este hash acredita "
                "integridad del resultado, no demuestra por sí solo la calidad "
                "de los datos de entrada."
            ),
            source="CatedralX",
            content_hash=analysis_digest,
        ),
        *review.evidence,
        *review.counterevidence,
    ]

    validation = verify_candidate(analysis, review)
    status_map = {
        "accepted": JobStatus.ACCEPTED,
        "rejected": JobStatus.REJECTED,
        "needs_evidence": JobStatus.NEEDS_EVIDENCE,
        "blocked": JobStatus.BLOCKED,
        "not_evaluated": JobStatus.NEEDS_EVIDENCE,
    }
    job_status = status_map[validation.status.value]

    candidate_decision: dict[str, Any] | None
    if analysis.get("status") == "error":
        candidate_decision = None
    else:
        candidate_decision = {
            "analytical_label": analysis.get("decision"),
            "candidate": True,
            "accepted": validation.status.value == "accepted",
            "trading_authorized": False,
            "engine_status": analysis.get("status"),
            "signal_strength": analysis.get("signal_strength"),
            "signal_strength_is_probability": False,
            "regime": analysis.get("regime"),
            "consistency_warnings": analysis.get("consistency_warnings", []),
        }

    counterevidence = [item.description for item in review.counterevidence]
    counterevidence.extend(review.unresolved_contradictions)
    assumptions = [
        "Los precios fueron suministrados por el solicitante; este prototipo no autentica su origen.",
        "La fuerza de señal de CatedralX es heurística, no una probabilidad calibrada.",
        "La ejecución de órdenes permanece deshabilitada.",
        *review.limitations,
    ]

    return DecisionResult(
        trace_id=trace_id,
        job_id=job_id,
        status=job_status,
        objective=request.objective,
        evidence=evidence,
        assumptions=assumptions,
        counterevidence=counterevidence,
        validation=validation,
        decision=candidate_decision,
        provenance=Provenance(
            engine=str(analysis.get("engine", "CatedralX")),
            engine_version=os.getenv("CATEDRALX_ENGINE_VERSION", "unreported"),
            agent_runs=review.agent_runs,
            input_hash=input_digest,
            analysis_hash=analysis_digest,
        ),
    )
