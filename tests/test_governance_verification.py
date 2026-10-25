from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from governance.contracts import (
    AgentReview,
    EvidenceKind,
    EvidenceProducer,
    EvidenceRecord,
    FinancialAnalysisRequest,
    ValidationStatus,
)
from governance.financial_flow import run_financial_flow, request_hash


def valid_request() -> FinancialAnalysisRequest:
    return FinancialAnalysisRequest(
        objective="Revisar una serie analítica de prueba",
        asset="TEST/USD",
        historical_prices=[100.0 + i for i in range(30)],
    )


def good_analysis(request: FinancialAnalysisRequest) -> dict:
    return {
        "status": "ok",
        "engine": "CatedralX",
        "decision": "hold",
        "signal_strength": 0.2,
        "signal_consistent": True,
        "trading_eligible": False,
        "orders_created": False,
        "orders_executed": False,
        "regime": "LATERAL",
        "consistency_warnings": [],
    }


def tool_evidence() -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id="tool-test-1",
        kind=EvidenceKind.TEST,
        producer=EvidenceProducer.EXTERNAL_TOOL,
        description="Comprobación reproducible del adaptador de análisis.",
        source="pytest:test_fixture",
        content_hash="sha256:" + "a" * 64,
    )


class CompleteReviewer:
    async def review(self, request, analysis) -> AgentReview:
        return AgentReview(
            reviewer_id="test-verifier-input",
            status="completed",
            evidence=[tool_evidence()],
            agent_runs=[],
        )


class AgentOnlyReviewer:
    async def review(self, request, analysis) -> AgentReview:
        return AgentReview(
            reviewer_id="untrusted-agent-output",
            status="completed",
            evidence=[
                EvidenceRecord(
                    evidence_id="agent-claim-1",
                    kind=EvidenceKind.SOURCE,
                    producer=EvidenceProducer.AGENT,
                    description="El agente afirma haber encontrado una fuente.",
                    source="https://example.invalid/not-verified",
                    content_hash="sha256:" + "b" * 64,
                )
            ],
        )


@pytest.mark.asyncio
async def test_complete_verified_circuit_can_accept() -> None:
    result = await run_financial_flow(
        valid_request(),
        analyzer=good_analysis,
        reviewer=CompleteReviewer(),
        job_id="job-test",
        trace_id="trace-test",
    )

    assert result.status.value == "accepted"
    assert result.validation.status == ValidationStatus.ACCEPTED
    assert result.decision["accepted"] is True
    assert result.decision["trading_authorized"] is False
    assert result.provenance.input_hash.startswith("sha256:")
    assert result.provenance.analysis_hash.startswith("sha256:")


@pytest.mark.asyncio
async def test_agent_claim_does_not_count_as_external_evidence() -> None:
    result = await run_financial_flow(
        valid_request(),
        analyzer=good_analysis,
        reviewer=AgentOnlyReviewer(),
    )

    assert result.status.value == "needs_evidence"
    assert result.validation.status == ValidationStatus.NEEDS_EVIDENCE
    assert result.decision["accepted"] is False
    assert any(
        check.name == "trusted_external_evidence" and not check.passed
        for check in result.validation.checks
    )


@pytest.mark.asyncio
async def test_inconsistent_catedralx_candidate_is_not_accepted() -> None:
    def inconsistent_analysis(request):
        return {
            **good_analysis(request),
            "status": "review_required",
            "signal_consistent": False,
            "consistency_warnings": ["signals contradict"],
        }

    result = await run_financial_flow(
        valid_request(),
        analyzer=inconsistent_analysis,
        reviewer=CompleteReviewer(),
    )

    assert result.status.value == "needs_evidence"
    assert result.decision["accepted"] is False


@pytest.mark.parametrize(
    "prices",
    [
        [100.0] * 29,
        [100.0] * 29 + [0.0],
        [100.0] * 29 + [-1.0],
        [100.0] * 29 + [math.inf],
        [100.0] * 29 + [math.nan],
        [100.0] * 29 + [True],
    ],
)
def test_invalid_prices_fail_contract(prices) -> None:
    with pytest.raises(ValidationError):
        FinancialAnalysisRequest(
            objective="Entrada inválida",
            asset="TEST/USD",
            historical_prices=prices,
        )


def test_request_hash_is_stable_for_same_canonical_input() -> None:
    first = valid_request()
    second = valid_request()
    assert request_hash(first) == request_hash(second)
