"""Verificación determinista separada de la generación de candidatos."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from governance.contracts import (
    AgentReview,
    EvidenceKind,
    EvidenceProducer,
    ValidationCheck,
    ValidationRecord,
    ValidationStatus,
)


VERIFIER_ID = "catedralx-independent-verifier"


def verify_candidate(
    analysis: Mapping[str, Any],
    review: AgentReview,
) -> ValidationRecord:
    """Evalúa un candidato usando reglas locales, no la decisión del agente.

    Una salida narrativa de un modelo no cuenta como evidencia externa.
    La evidencia externa debe haber sido registrada por una herramienta de
    confianza y contener fuente y hash de contenido.
    """
    engine_ok = analysis.get("status") == "ok"
    raw_decision = analysis.get("decision")
    decision_present = (
        raw_decision is not None
        and str(raw_decision).strip().lower() not in {"", "none", "null", "undefined"}
    )
    signal_consistent = analysis.get("signal_consistent") is True
    actions_disabled = (
        analysis.get("trading_eligible") is False
        and analysis.get("orders_created") is False
        and analysis.get("orders_executed") is False
    )
    review_complete = review.status == "completed"
    trusted_evidence = any(
        item.producer == EvidenceProducer.EXTERNAL_TOOL
        and item.kind in {EvidenceKind.SOURCE, EvidenceKind.OBSERVATION, EvidenceKind.TEST}
        and bool(item.source)
        and bool(item.content_hash)
        for item in review.evidence
    )
    no_unresolved_contradictions = not review.unresolved_contradictions

    checks = [
        ValidationCheck(
            name="catedralx_analysis_ok",
            passed=engine_ok,
            details=f"status del motor: {analysis.get('status', 'missing')}",
        ),
        ValidationCheck(
            name="decision_present",
            passed=decision_present,
            details="La salida del motor contiene una etiqueta analítica.",
        ),
        ValidationCheck(
            name="signals_consistent",
            passed=signal_consistent,
            details="La salida declara consistencia de señal.",
        ),
        ValidationCheck(
            name="actions_disabled",
            passed=actions_disabled,
            details="El análisis no puede crear ni ejecutar órdenes.",
        ),
        ValidationCheck(
            name="agent_review_completed",
            passed=review_complete,
            details=f"status de revisión: {review.status}",
        ),
        ValidationCheck(
            name="trusted_external_evidence",
            passed=trusted_evidence,
            details=(
                "Se encontró evidencia de herramienta con fuente y hash."
                if trusted_evidence
                else "No existe evidencia externa verificable proveniente de una herramienta."
            ),
        ),
        ValidationCheck(
            name="no_unresolved_contradictions",
            passed=no_unresolved_contradictions,
            details=(
                "No hay contradicciones pendientes."
                if no_unresolved_contradictions
                else "Hay contradicciones pendientes que impiden aceptar."
            ),
        ),
    ]

    # Violaciones de seguridad se bloquean inmediatamente.
    if not actions_disabled:
        status = ValidationStatus.BLOCKED
    # Un motor fallido requiere corregir la entrada o el análisis.
    elif analysis.get("status") == "error":
        status = ValidationStatus.BLOCKED
    elif not signal_consistent and analysis.get("status") == "ok":
        status = ValidationStatus.REJECTED
    elif (
        engine_ok
        and decision_present
        and signal_consistent
        and actions_disabled
        and review_complete
        and trusted_evidence
        and no_unresolved_contradictions
    ):
        status = ValidationStatus.ACCEPTED
    else:
        status = ValidationStatus.NEEDS_EVIDENCE

    return ValidationRecord(status=status, verifier=VERIFIER_ID, checks=checks)
