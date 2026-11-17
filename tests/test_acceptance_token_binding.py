"""C-7 (liberación): el JWT de aceptación liga `sub` con `verifier_id`.

Antes, la separación verificador/API dependía solo del secreto de la
semilla Ed25519: el JWT de aceptación no ligaba su `sub` con el
`verifier_id` de la decisión firmada. Ahora, cuando el token declara
`sub`, debe coincidir con el `verifier_id` del cuerpo; si no, 401
`acceptance_token_subject_verifier_mismatch`. Los tokens sin `sub` se
aceptan como antes para no romper emisores existentes.
"""
import json
import threading
import urllib.error
import urllib.request
from contextlib import ExitStack
from unittest.mock import patch

import jwt
import pytest
from nacl.signing import SigningKey

from api.crypto_signer import create_canonical_payload
from api.server import create_server

TEST_JWT_SECRET = "test-jwt-secret-for-acceptance-binding"
TEST_VERIFIER_SEED_HEX = "11" * 32
TEST_VERIFIER_KEY = SigningKey(bytes.fromhex(TEST_VERIFIER_SEED_HEX))


def make_jwt(scopes="jobs:write jobs:read results:read acceptance:write", subject=None):
    import time

    payload = {"scope": scopes, "exp": int(time.time()) + 3600}
    if subject is not None:
        payload["sub"] = subject
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


def make_decision(**overrides):
    decision = {
        "decision_id": "dec-1",
        "candidate_id": "test-123",
        "outcome": "accepted",
        "criteria_checked": ["schema", "evidence_digest", "traceability"],
        "reasons": [],
        "missing_evidence": [],
        "verifier_id": "independent-test-verifier",
        "evidence_digest": "a" * 64,
        "decided_at": "2026-10-28T12:00:00+00:00",
        "trace_id": "1234567890abcdef1234567890abcdef",
    }
    decision.update(overrides)
    return decision


def sign_decision(payload):
    return TEST_VERIFIER_KEY.sign(
        create_canonical_payload(payload)
    ).signature.hex()


@pytest.fixture
def api_server(monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("CHALAMANDRA_CATEDRALX_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv(
        "CHALAMANDRA_VERIFIER_PUBLIC_KEY",
        TEST_VERIFIER_KEY.verify_key.encode().hex(),
    )
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_ID", "independent-test-verifier")
    monkeypatch.delenv("CHALAMANDRA_ED25519_SEED", raising=False)
    monkeypatch.delenv("CHALAMANDRA_VERIFIER_SECRET", raising=False)
    monkeypatch.delenv("CHALAMANDRA_HMAC_SECRET", raising=False)
    server = create_server("127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _post(url, data, headers=None):
    headers = dict(headers or {})
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=json.dumps(data).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _post_acceptance(api_server, tmp_path, payload, token):
    candidate = {
        "candidate_id": "test-123", "status": "candidate",
        "evidence": [], "trace_id": "1234567890abcdef1234567890abcdef",
    }
    with ExitStack() as stack:
        stack.enter_context(
            patch("api.candidate_store.get_candidate", return_value=candidate)
        )
        stack.enter_context(
            patch(
                "api.candidate_store.compute_evidence_digest",
                return_value="a" * 64,
            )
        )
        stack.enter_context(
            patch(
                "api.acceptance_store._acceptance_file",
                return_value=tmp_path / "acceptance_store.jsonl",
            )
        )
        stack.enter_context(
            patch("api.task_bus.mark_result_accepted", return_value=1)
        )
        stack.enter_context(
            patch(
                "api.task_bus.get_result_task_statuses",
                return_value=[{"task_id": "task-1", "status": "accepted"}],
            )
        )
        stack.enter_context(
            patch("api.webhook_dispatcher.dispatch_event", return_value=1)
        )
        return _post(
            f"{api_server}/v1/acceptance",
            payload,
            {"Authorization": f"Bearer {token}"},
        )


def _signed_payload(**overrides):
    payload = make_decision(**overrides)
    payload["signature"] = sign_decision(payload)
    return payload


def test_acceptance_token_sub_matching_verifier_is_accepted(
    api_server, tmp_path
):
    status, body = _post_acceptance(
        api_server,
        tmp_path,
        _signed_payload(),
        make_jwt(subject="independent-test-verifier"),
    )
    assert status == 201
    assert body["outcome"] == "accepted"


def test_acceptance_token_sub_mismatching_verifier_is_rejected(
    api_server, tmp_path
):
    status, body = _post_acceptance(
        api_server,
        tmp_path,
        _signed_payload(),
        make_jwt(subject="otro-verificador"),
    )
    assert status == 401
    assert body["error"] == "acceptance_token_subject_verifier_mismatch"


def test_acceptance_token_without_sub_is_rejected(
    api_server, tmp_path
):
    status, body = _post_acceptance(
        api_server, tmp_path, _signed_payload(), make_jwt()
    )
    assert status == 401
    assert body["error"] == "acceptance_token_subject_verifier_mismatch"
    assert not (tmp_path / "acceptance_store.jsonl").exists()
