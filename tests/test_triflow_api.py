from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from api.app import Principal, create_app
from governance.contracts import (
    AgentReview,
    EvidenceKind,
    EvidenceProducer,
    EvidenceRecord,
    FinancialAnalysisRequest,
)


def test_evidence_gated_request_and_idempotency() -> None:
    analyzer_calls = {"count": 0}

    def analyzer(request: FinancialAnalysisRequest):
        analyzer_calls["count"] += 1
        return {
            "status": "ok",
            "engine": "CatedralX",
            "decision": "hold",
            "signal_strength": 0.1,
            "signal_consistent": True,
            "trading_eligible": False,
            "orders_created": False,
            "orders_executed": False,
            "regime": "LATERAL",
            "consistency_warnings": [],
        }

    class Reviewer:
        async def review(self, request, analysis):
            return AgentReview(
                reviewer_id="test-tool-adapter",
                status="completed",
                evidence=[
                    EvidenceRecord(
                        evidence_id="tool-1",
                        kind=EvidenceKind.TEST,
                        producer=EvidenceProducer.EXTERNAL_TOOL,
                        description="Resultado de prueba reproducible.",
                        source="pytest:fixture",
                        content_hash="sha256:" + "c" * 64,
                    )
                ],
            )

    app = create_app(
        analyzer=analyzer,
        reviewer=Reviewer(),
        principal_dependency=lambda: Principal(
            subject="user-1",
            scopes=frozenset({"jobs:create", "jobs:read"}),
        ),
    )
    client = TestClient(app)
    payload = {
        "objective": "Analiza una serie de prueba",
        "asset": "TEST/USD",
        "historical_prices": [100.0 + i for i in range(30)],
    }
    headers = {"Idempotency-Key": "test-key-00000001"}

    first = client.post("/v1/jobs", json=payload, headers=headers)
    second = client.post("/v1/jobs", json=payload, headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "accepted"
    assert first.json()["validation"]["status"] == "accepted"
    assert first.json()["decision"]["trading_authorized"] is False
    assert second.status_code == 200
    assert second.json()["job_id"] == first.json()["job_id"]
    assert analyzer_calls["count"] == 1


def test_idempotency_key_cannot_be_reused_for_different_payload() -> None:
    def analyzer(request):
        return {
            "status": "ok",
            "engine": "CatedralX",
            "decision": "hold",
            "signal_consistent": True,
            "trading_eligible": False,
            "orders_created": False,
            "orders_executed": False,
        }

    class Reviewer:
        async def review(self, request, analysis):
            return AgentReview(
                reviewer_id="not-enough-evidence",
                status="partial",
            )

    app = create_app(
        analyzer=analyzer,
        reviewer=Reviewer(),
        principal_dependency=lambda: Principal(
            subject="user-1",
            scopes=frozenset({"jobs:create", "jobs:read"}),
        ),
    )
    client = TestClient(app)
    headers = {"Idempotency-Key": "test-key-00000002"}
    base = {
        "objective": "Analiza A",
        "asset": "TEST/USD",
        "historical_prices": [100.0 + i for i in range(30)],
    }
    assert client.post("/v1/jobs", json=base, headers=headers).status_code == 200
    changed = {**base, "objective": "Analiza B"}
    assert client.post("/v1/jobs", json=changed, headers=headers).status_code == 409


def test_missing_scope_is_rejected_before_analysis() -> None:
    def analyzer(request):
        pytest.fail("El analizador no debe ejecutarse sin autorización.")

    app = create_app(
        analyzer=analyzer,
        principal_dependency=lambda: Principal(
            subject="user-1",
            scopes=frozenset({"jobs:read"}),
        ),
    )
    client = TestClient(app)
    response = client.post(
        "/v1/jobs",
        json={
            "objective": "No autorizado",
            "asset": "TEST/USD",
            "historical_prices": [100.0 + i for i in range(30)],
        },
        headers={"Idempotency-Key": "test-key-00000003"},
    )
    assert response.status_code == 403
