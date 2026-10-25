from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from api.app import create_app


def test_default_membrane_fails_closed_without_identity_provider() -> None:
    def forbidden_to_run(request):
        pytest.fail("The analyzer must not execute before authentication.")

    client = TestClient(create_app(analyzer=forbidden_to_run))
    response = client.post(
        "/v1/jobs",
        json={
            "objective": "Solicitud sin identidad verificada",
            "asset": "TEST/USD",
            "historical_prices": [100.0 + i for i in range(30)],
        },
        headers={"Idempotency-Key": "auth-test-key-000001"},
    )

    assert response.status_code == 503
    assert "validador JWT" in response.json()["detail"]
