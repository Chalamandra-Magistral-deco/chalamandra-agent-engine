#!/usr/bin/env python3
"""Disposable Docker E2E: real API + worker containers, synthetic MXN opportunity.

The API and worker run in separate Docker containers. This runner is a third
container on the same isolated CI network. It starts the allowlisted webhook
receiver, submits one accepted and one rejected opportunity, signs decisions
in a separate verifier process, and proves only the accepted result is delivered.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import jwt

sys.path.insert(0, "/app")
from api import candidate_store  # noqa: E402

API_HOST = os.getenv("CHALAMANDRA_E2E_API_HOST", "chalamandra-agents-api-ci")
API_PORT = int(os.getenv("CHALAMANDRA_E2E_API_PORT", "8766"))
API_BASE = f"http://{API_HOST}:{API_PORT}"
STATE_ROOT = Path(
    os.getenv(
        "CHALAMANDRA_STATE_DIR",
        "/home/chalamandra/.local/state/chalamandra",
    )
).expanduser()
CANDIDATES_FILE = STATE_ROOT / "agents" / "candidates.jsonl"
VERIFIER_CLI = Path("/app/verifier/independent_acceptance_verifier.py")
RECEIVER_HOST = "0.0.0.0"
RECEIVER_PORT = 8767
RECEIVER_DNS = "chalamandra-e2e-runner-ci"
VERIFIER_ID = os.environ["CHALAMANDRA_VERIFIER_ID"]
VERIFIER_SEED = os.environ["CHALAMANDRA_ED25519_SEED"]
JWT_SECRET = os.environ["CHALAMANDRA_JWT_SECRET"]
SERVICE_SECRET = os.environ["CHALAMANDRA_CATEDRALX_JWT_SECRET"]
WEBHOOK_SECRET = os.environ["CHALAMANDRA_WEBHOOK_SECRET"]
TRACE_ACCEPT = "abcdef0123456789abcdef0123456789"
TRACE_REJECT = "1234567890abcdef1234567890abcdef"
TRACE_MISMATCH = "fedcfedcfedcfedcfedcfedcfedcfedc"

received_events: list[dict[str, Any]] = []
received_lock = threading.Lock()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_jwt(secret: str, scope: str, subject: str) -> str:
    token = jwt.encode(
        {"scope": scope, "sub": subject, "exp": int(time.time()) + 900},
        secret,
        algorithm="HS256",
    )
    return token.decode("ascii") if isinstance(token, bytes) else token


READ_TOKEN = make_jwt(JWT_SECRET, "orchestration:read results:read jobs:read", "docker-e2e-reader")
SERVICE_RUN_TOKEN = make_jwt(
    SERVICE_SECRET, "orchestration:run", "catedralx-service"
)
SERVICE_PUBLISH_TOKEN = make_jwt(
    SERVICE_SECRET, "results:publish", "catedralx-service"
)
SUBSCRIPTION_TOKEN = make_jwt(JWT_SECRET, "subscriptions:write", "docker-e2e-admin")
ACCEPTANCE_TOKEN = make_jwt(JWT_SECRET, "acceptance:write", VERIFIER_ID)


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_response(400)
            self.end_headers()
            return
        with received_lock:
            received_events.append(
                {"payload": payload, "headers": dict(self.headers.items())}
            )
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args: Any) -> None:
        return


def request_json(
    method: str,
    path: str,
    token: str,
    payload: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    connection = http.client.HTTPConnection(API_HOST, API_PORT, timeout=15)
    request_headers = {"Authorization": f"Bearer {token}"}
    request_headers.update(headers or {})
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{method} {path}: API returned invalid JSON: {raw[:500]}") from exc
        return response.status, parsed
    finally:
        connection.close()


def opportunity_payload(trace_id: str, opportunity_id: str) -> dict[str, Any]:
    observed_at = datetime.now(timezone.utc).isoformat()
    return {
        "opportunity_id": opportunity_id,
        "name": "Servicio digital sintético de prueba",
        "category": "digital_service",
        "income_model": "hybrid",
        "currency": "MXN",
        "horizon_months": 12,
        "capital_required": 5000,
        "available_capital": 3000,
        "revenue": [
            {"amount": 2000, "frequency": "monthly", "evidence_id": "docker-e2e-ev-1"}
        ],
        "costs": [
            {"amount": 500, "frequency": "monthly", "evidence_id": "docker-e2e-ev-1"}
        ],
        "evidence": [
            {
                "evidence_id": "docker-e2e-ev-1",
                "source": "synthetic_docker_e2e_fixture",
                "observed_at": observed_at,
                "digest": hashlib.sha256(b"synthetic-docker-e2e-evidence").hexdigest(),
            }
        ],
        "assumptions": [
            "La demanda mensual permanece estable durante el horizonte sintético."
        ],
        "scenarios": [
            {"name": "conservative", "revenue_multiplier": 0.7, "cost_multiplier": 1.2},
            {"name": "base", "revenue_multiplier": 1.0, "cost_multiplier": 1.0},
            {"name": "optimistic", "revenue_multiplier": 1.2, "cost_multiplier": 0.9},
        ],
        "trace_id": trace_id,
    }


def read_candidate_for_trace(trace_id: str) -> dict[str, Any] | None:
    if not CANDIDATES_FILE.is_file():
        return None
    found = None
    for line in CANDIDATES_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("trace_id") == trace_id and record.get("status") == "candidate":
            found = record
    return found


def wait_for_candidate(task_id: str, trace_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 90
    last_job: dict[str, Any] = {}
    while time.monotonic() < deadline:
        candidate = read_candidate_for_trace(trace_id)
        if candidate is not None:
            candidate_store._verify_record_digest(candidate)
            return candidate
        status, job = request_json("GET", f"/v1/jobs/{task_id}", READ_TOKEN)
        if status == 200:
            last_job = job
            if job.get("status") in {"blocked", "failed", "dead_letter", "rejected"}:
                raise AssertionError(f"Worker terminó sin candidato: {job}")
        time.sleep(0.35)
    raise TimeoutError(
        f"No apareció candidato para trace_id={trace_id}; último estado de job={last_job}"
    )


def submit_opportunity(trace_id: str, opportunity_id: str) -> tuple[str, dict[str, Any]]:
    payload = {
        "objective": "catedralx_opportunity_evaluation",
        "payload": opportunity_payload(trace_id, opportunity_id),
        "permissions": {"allowed_tools": [], "side_effects": "none"},
        "success_criteria": [
            "Return reproducible synthetic MXN business scenarios without external effects."
        ],
    }
    status, queued = request_json(
        "POST",
        "/internal/v1/orchestration/runs",
        SERVICE_RUN_TOKEN,
        payload,
        headers={
            "Idempotency-Key": f"docker-business-e2e-{opportunity_id}",
            "traceparent": f"00-{trace_id}-0123456789abcdef-01",
        },
    )
    require(status == 202, f"Solicitud de oportunidad rechazada: HTTP {status}: {queued}")
    require(queued.get("trace_id") == trace_id, f"Traza de cola incorrecta: {queued}")
    task_id = queued.get("task_id")
    require(isinstance(task_id, str) and task_id, f"task_id ausente: {queued}")
    return task_id, queued


def sign_decision(decision: dict[str, Any]) -> dict[str, Any]:
    action = "ACCEPT" if decision["outcome"] == "accepted" else "REJECT"
    result = subprocess.run(
        [
            sys.executable,
            str(VERIFIER_CLI),
            "--confirm",
            f"{action}:{decision['candidate_id']}",
        ],
        input=json.dumps(decision, ensure_ascii=False, allow_nan=False),
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": "/app",
            "CHALAMANDRA_ED25519_SEED": VERIFIER_SEED,
            "CHALAMANDRA_VERIFIER_ID": VERIFIER_ID,
        },
        timeout=10,
        check=False,
    )
    require(
        result.returncode == 0,
        f"Verificador independiente rechazó la decisión: {result.stderr.strip()}",
    )
    signed = json.loads(result.stdout)
    require(isinstance(signed.get("signature"), str), "El verificador no emitió firma")
    return signed


def decision_for(candidate: dict[str, Any], outcome: str) -> dict[str, Any]:
    if outcome == "accepted":
        criteria = [
            "Docker API-to-worker-to-CatedralX trace continuity",
            "synthetic MXN scenario and evaluation hash",
            "evidence digest integrity",
            "analysis-only execution boundary",
        ]
        reasons: list[str] = []
        missing: list[str] = []
    else:
        criteria = ["independent rejection path and non-distribution boundary"]
        reasons = ["Synthetic E2E rejection case; no production evidence is claimed."]
        missing = ["No external business authorization is supplied by this test."]
    return {
        "decision_id": str(uuid.uuid4()),
        "candidate_id": candidate["candidate_id"],
        "outcome": outcome,
        "criteria_checked": criteria,
        "reasons": reasons,
        "missing_evidence": missing,
        "verifier_id": VERIFIER_ID,
        "evidence_digest": candidate_store.compute_evidence_digest(candidate),
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "trace_id": candidate["trace_id"],
    }


def current_received() -> list[dict[str, Any]]:
    with received_lock:
        return list(received_events)


def wait_for_event_count(count: int, timeout: float = 10.0) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = current_received()
        if len(events) >= count:
            return events
        time.sleep(0.1)
    return current_received()


def verify_accepted_webhook(
    event_record: dict[str, Any], candidate_id: str, trace_id: str
) -> None:
    payload = event_record["payload"]
    headers = event_record["headers"]
    require(payload.get("event_type") == "result.accepted", f"Tipo de evento inválido: {payload}")
    require(payload.get("result_id") == candidate_id, f"Webhook candidate_id incorrecto: {payload}")
    require(payload.get("trace_id") == trace_id, f"Webhook trace_id incorrecto: {payload}")
    require(headers.get("X-Trace-Id") == trace_id, "X-Trace-Id no coincide")
    traceparent = headers.get("traceparent", "")
    require(traceparent.startswith(f"00-{trace_id}-"), f"traceparent no correlacionado: {traceparent}")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected = hmac.new(WEBHOOK_SECRET.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    supplied = headers.get("X-Chalamandra-Signature", "")
    require(hmac.compare_digest(supplied, expected), "Firma HMAC del webhook inválida")


def exercise_acceptance(trace_id: str, outcome: str, opportunity_id: str) -> str:
    task_id, _queued = submit_opportunity(trace_id, opportunity_id)
    candidate = wait_for_candidate(task_id, trace_id)
    candidate_id = candidate.get("candidate_id")
    require(isinstance(candidate_id, str) and candidate_id, "candidate_id ausente")
    require(candidate.get("catedralx_trace_id") == trace_id, "CatedralX perdió la traza")
    require(candidate.get("orders_created") is False, "El candidato creó órdenes")
    require(candidate.get("orders_executed") is False, "El candidato ejecutó órdenes")
    require(candidate.get("analysis_only") is True, "El candidato no es analysis-only")
    assessment = candidate.get("economic_assessment")
    require(isinstance(assessment, dict), "economic_assessment ausente")
    require(assessment.get("analysis_only") is True, "CatedralX no marcó analysis_only")
    require(assessment.get("orders_created") is False, "CatedralX reportó órdenes creadas")
    require(assessment.get("orders_executed") is False, "CatedralX reportó órdenes ejecutadas")
    require(assessment.get("authorization") == "NOT_GRANTED", "CatedralX concedió autorización")
    require(
        assessment.get("scenarios", {}).get("base", {}).get("revenue_total") == 24000.0,
        "El escenario base MXN no coincide con el fixture determinista",
    )

    pending_status, pending = request_json(
        "GET", f"/v1/results/{candidate_id}", READ_TOKEN
    )
    require(
        pending_status == 202 and pending.get("status") == "pending_verification",
        f"El resultado no quedó pendiente de aceptación: {pending_status} {pending}",
    )
    require(len(current_received()) == 0 if outcome == "accepted" else len(current_received()) == 1,
            "Se distribuyó un resultado antes de la aceptación")

    decision = sign_decision(decision_for(candidate, outcome))
    acceptance_status, accepted = request_json(
        "POST", "/v1/acceptance", ACCEPTANCE_TOKEN, decision
    )
    require(
        acceptance_status == 201 and accepted.get("outcome") == outcome,
        f"Decisión independiente no registrada: HTTP {acceptance_status}: {accepted}",
    )

    if outcome == "accepted":
        events = wait_for_event_count(1)
        require(len(events) == 1, f"Se esperaba exactamente un webhook aceptado; recibidos={len(events)}")
        verify_accepted_webhook(events[0], candidate_id, trace_id)
        final_status, final_result = request_json(
            "GET", f"/v1/results/{candidate_id}", READ_TOKEN
        )
        require(
            final_status == 200
            and final_result.get("delivery_status") == "accepted"
            and final_result.get("orders_created") is False
            and final_result.get("orders_executed") is False,
            f"Resultado aceptado inconsistente: {final_status} {final_result}",
        )
    else:
        final_status, final_result = request_json(
            "GET", f"/v1/results/{candidate_id}", READ_TOKEN
        )
        require(
            final_status == 403 and final_result.get("error") == "result_not_accepted",
            f"Resultado rechazado quedó visible como aceptado: {final_status} {final_result}",
        )
        distribution_status, distribution = request_json(
            "POST",
            "/internal/v1/distributions",
            SERVICE_PUBLISH_TOKEN,
            {"result_id": candidate_id, "event_type": "result.accepted"},
        )
        require(
            distribution_status == 409
            and distribution.get("error") == "result_not_independently_accepted",
            f"El API permitió distribuir un resultado rechazado: {distribution_status} {distribution}",
        )
        time.sleep(0.5)
        events = current_received()
        require(
            len(events) == 1 and events[0]["payload"].get("result_id") != candidate_id,
            f"El resultado rechazado produjo una entrega: {events}",
        )
    return candidate_id


def main() -> int:
    require(VERIFIER_CLI.is_file(), f"Falta el CLI del verificador montado: {VERIFIER_CLI}")
    require(len(VERIFIER_SEED) == 64, "Semilla de verificador sintética mal configurada")
    require(CANDIDATES_FILE.parent.is_dir(), f"El volumen de estado no está montado: {CANDIDATES_FILE.parent}")

    receiver = ThreadingHTTPServer((RECEIVER_HOST, RECEIVER_PORT), Receiver)
    receiver_thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    receiver_thread.start()
    try:
        health_status, health = request_json("GET", "/healthz", READ_TOKEN)
        require(health_status == 200, f"API Docker no saludable: {health_status} {health}")

        subscription_status, subscription = request_json(
            "POST",
            "/v1/subscriptions",
            SUBSCRIPTION_TOKEN,
            {
                "endpoint_url": f"http://{RECEIVER_DNS}:{RECEIVER_PORT}/events",
                "event_types": ["result.accepted"],
            },
        )
        require(
            subscription_status == 201 and subscription.get("status") == "enabled",
            f"Suscripción E2E rechazada: {subscription_status} {subscription}",
        )

        mismatch_status, mismatch = request_json(
            "POST",
            "/internal/v1/orchestration/runs",
            SERVICE_RUN_TOKEN,
            {
                "objective": "catedralx_opportunity_evaluation",
                "payload": opportunity_payload(TRACE_MISMATCH, "docker-e2e-mismatch"),
                "permissions": {"allowed_tools": [], "side_effects": "none"},
                "success_criteria": ["Reject a mismatched W3C trace before enqueue."],
            },
            headers={
                "Idempotency-Key": "docker-business-e2e-trace-mismatch",
                "traceparent": f"00-{TRACE_ACCEPT}-0123456789abcdef-01",
            },
        )
        require(
            mismatch_status == 400 and mismatch.get("error") == "trace_id_mismatch",
            f"API no rechazó el desajuste de traza: {mismatch_status} {mismatch}",
        )

        accepted_id = exercise_acceptance(
            TRACE_ACCEPT, "accepted", "docker-e2e-accepted-mxn"
        )
        rejected_id = exercise_acceptance(
            TRACE_REJECT, "rejected", "docker-e2e-rejected-mxn"
        )

        print("DOCKER BUSINESS E2E PASSED")
        print(f"accepted_candidate_id={accepted_id}")
        print(f"rejected_candidate_id={rejected_id}")
        print("trace_mismatch=400")
        print("accepted_webhook=1 (HMAC and traceparent verified)")
        print("rejected_webhook=0")
        print("orders_created=false")
        print("orders_executed=false")
        return 0
    finally:
        receiver.shutdown()
        receiver.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
