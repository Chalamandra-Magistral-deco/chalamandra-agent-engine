"""C-5 (liberación): el smoke E2E manual plegado a pytest.

Antes, el flujo oportunidad con API y worker como procesos separados y
receptor webhook real solo existía como script manual (/tmp/smoke_e2e.py),
fuera de CI. Este test es ese mismo flujo en pytest: solicitud real de
oportunidad por HTTP, CatedralX real en el worker separado, aceptación
independiente firmada con el CLI real y entrega webhook real. También
comprueba en vivo el fix H-01 (trace_id distinto -> 400).
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt
import pytest
from nacl.signing import SigningKey

from api import candidate_store

_default_catedralx_root = Path(__file__).resolve().parents[2] / "CatedralX"
CATEDRALX_ROOT = Path(os.environ.get("CATEDRALX_PATH", _default_catedralx_root)).resolve()
if not (CATEDRALX_ROOT / "BIN" / "catedral").is_file():
    pytest.skip(
        "CatedralX checkout is not available; set CATEDRALX_PATH to run this cross-repo test.",
        allow_module_level=True,
    )

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACE_ID = "abcdef0123456789abcdef0123456789"
TEST_VERIFIER_SEED_HEX = "44" * 32
TEST_VERIFIER_KEY = SigningKey(bytes.fromhex(TEST_VERIFIER_SEED_HEX))
VERIFIER_ID = "isolated-opportunity-e2e-verifier"


def _http_json(port, method, path, token, payload=None, headers=None):
    request_headers = {"Authorization": f"Bearer {token}"}
    request_headers.update(headers or {})
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request(
        method, path, body=body,
        headers={
            **request_headers,
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    response = connection.getresponse()
    status = response.status
    raw = response.read().decode("utf-8")
    connection.close()
    return status, json.loads(raw) if raw else {}


def _opportunity_payload(trace_id):
    return {
        "opportunity_id": "pytest-smoke-001",
        "name": "Servicio digital de prueba",
        "category": "digital_service",
        "income_model": "hybrid",
        "currency": "MXN",
        "horizon_months": 12,
        "capital_required": 5000,
        "available_capital": 3000,
        "revenue": [{"amount": 2000, "frequency": "monthly", "evidence_id": "smoke-ev-1"}],
        "costs": [{"amount": 500, "frequency": "monthly", "evidence_id": "smoke-ev-1"}],
        "evidence": [{
            "evidence_id": "smoke-ev-1",
            "source": "synthetic_smoke_fixture",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "digest": hashlib.sha256(b"synthetic-smoke-evidence").hexdigest(),
        }],
        "assumptions": ["La demanda mensual permanece estable durante el horizonte."],
        "scenarios": [
            {"name": "conservative", "revenue_multiplier": 0.7, "cost_multiplier": 1.2},
            {"name": "base", "revenue_multiplier": 1.0, "cost_multiplier": 1.0},
            {"name": "optimistic", "revenue_multiplier": 1.2, "cost_multiplier": 0.9},
        ],
        "trace_id": trace_id,
    }


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_opportunity_subprocess_api_worker_webhook_full_flow(tmp_path, monkeypatch):
    worker_home = tmp_path / "worker-home"
    worker_home.mkdir()
    monkeypatch.setenv("HOME", str(worker_home))
    monkeypatch.setenv("CATEDRALX_PATH", str(CATEDRALX_ROOT))
    monkeypatch.setenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "false")
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_ID", VERIFIER_ID)
    monkeypatch.setenv(
        "CHALAMANDRA_VERIFIER_PUBLIC_KEY", TEST_VERIFIER_KEY.verify_key.encode().hex()
    )
    monkeypatch.setenv("CHALAMANDRA_JWT_SECRET", "synthetic-smoke-jwt-secret-32-bytes-ok")
    monkeypatch.setenv(
        "CHALAMANDRA_CATEDRALX_JWT_SECRET", "synthetic-smoke-service-jwt-secret"
    )
    monkeypatch.setenv(
        "CHALAMANDRA_WEBHOOK_SECRET", "synthetic-smoke-webhook-secret-32-bytes-min"
    )
    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_PRIVATE_HOST_ALLOWLIST", "127.0.0.1")
    for secret_name in (
        "CHALAMANDRA_ED25519_SEED",
        "CHALAMANDRA_VERIFIER_SECRET",
        "CHALAMANDRA_HMAC_SECRET",
    ):
        monkeypatch.delenv(secret_name, raising=False)

    def _jwt(secret, scope, subject):
        return jwt.encode(
            {"scope": scope, "sub": subject, "exp": int(time.time()) + 600},
            secret,
            algorithm="HS256",
        )

    service_jwt = _jwt(
        "synthetic-smoke-service-jwt-secret", "orchestration:run", "catedralx-service"
    )
    read_jwt = _jwt(
        "synthetic-smoke-jwt-secret-32-bytes-ok",
        "orchestration:read results:read jobs:read",
        "smoke-reader",
    )
    acceptance_jwt = _jwt(
        "synthetic-smoke-jwt-secret-32-bytes-ok", "acceptance:write", VERIFIER_ID
    )
    subs_jwt = _jwt(
        "synthetic-smoke-jwt-secret-32-bytes-ok", "subscriptions:write", "smoke-admin"
    )

    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            received.append(json.loads(self.rfile.read(length).decode()))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            return

    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=receiver.serve_forever, daemon=True).start()
    receiver_url = f"http://127.0.0.1:{receiver.server_address[1]}/events"

    api_port = _free_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["CHALAMANDRA_API_HOST"] = "127.0.0.1"
    env["CHALAMANDRA_API_PORT"] = str(api_port)
    api_process = subprocess.Popen(
        [sys.executable, "-m", "api.server"],
        cwd=REPO_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    worker_process = subprocess.Popen(
        [sys.executable, "-m", "api.worker_loop"],
        cwd=REPO_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if api_process.poll() is not None:
                output, _ = api_process.communicate(timeout=2)
                raise AssertionError(
                    "API process exited before becoming healthy: " + output[-4000:]
                )
            try:
                status, _ = _http_json(api_port, "GET", "/v1/results/nope", read_jwt)
                if status in (202, 404):
                    break
            except OSError:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("API process did not become healthy within 30 seconds")

        # 0) Suscripción para que la distribución del aceptado tenga destino.
        status, sub = _http_json(
            api_port, "POST", "/v1/subscriptions", subs_jwt,
            {"endpoint_url": receiver_url, "event_types": ["result.accepted"]},
        )
        assert status in (200, 201) and sub.get("status") == "enabled", sub

        # 1) H-01 en vivo: trace_id del payload distinto al de traceparent -> 400.
        status, body = _http_json(
            api_port, "POST", "/internal/v1/orchestration/runs", service_jwt,
            {
                "objective": "catedralx_opportunity_evaluation",
                "payload": _opportunity_payload("fedcfedcfedcfedcfedcfedcfedcfedc"),
                "permissions": {"allowed_tools": [], "side_effects": "none"},
                "success_criteria": ["Return reproducible economic scenarios."],
            },
            headers={
                "Idempotency-Key": "pytest-smoke-mismatch-001",
                "traceparent": f"00-{TRACE_ID}-0123456789abcdef-01",
            },
        )
        assert status == 400 and body.get("error") == "trace_id_mismatch", body

        # 2) Solicitud real con traza coincidente -> 202.
        status, queued = _http_json(
            api_port, "POST", "/internal/v1/orchestration/runs", service_jwt,
            {
                "objective": "catedralx_opportunity_evaluation",
                "payload": _opportunity_payload(TRACE_ID),
                "permissions": {"allowed_tools": [], "side_effects": "none"},
                "success_criteria": ["Return reproducible economic scenarios."],
            },
            headers={
                "Idempotency-Key": "pytest-smoke-ok-001",
                "traceparent": f"00-{TRACE_ID}-0123456789abcdef-01",
            },
        )
        assert status == 202 and queued["trace_id"] == TRACE_ID, queued

        # 3) Esperar al candidato (worker separado, CatedralX real).
        state_dir = worker_home / ".local" / "state" / "chalamandra" / "agents"
        candidate = None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            candidates_file = state_dir / "candidates.jsonl"
            if candidates_file.exists():
                for line in candidates_file.read_text().splitlines():
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (
                        record.get("trace_id") == TRACE_ID
                        and record.get("status") == "candidate"
                    ):
                        candidate = record
                        break
            if candidate:
                break
            if worker_process.poll() is not None:
                output, _ = worker_process.communicate(timeout=2)
                raise AssertionError(
                    "Worker process exited before producing the candidate: "
                    + output[-4000:]
                )
            time.sleep(0.5)
        assert candidate, "no apareció el candidato"
        candidate_id = candidate["candidate_id"]
        assert candidate["catedralx_trace_id"] == TRACE_ID
        assert candidate["economic_assessment"]["scenarios"]["base"]["revenue_total"] == 24000.0
        assert candidate["orders_created"] is False
        assert candidate["orders_executed"] is False

        # 4) Sin aceptación: 202 pending_verification.
        status, pending = _http_json(
            api_port, "GET", f"/v1/results/{candidate_id}", read_jwt
        )
        assert status == 202 and pending["status"] == "pending_verification"

        # 5) Aceptación independiente firmada Ed25519 con el CLI real -> 201.
        decision = {
            "decision_id": str(uuid.uuid4()),
            "candidate_id": candidate_id,
            "outcome": "accepted",
            "criteria_checked": [
                "synthetic end-to-end trace continuity",
                "evidence digest integrity",
                "analysis-only execution boundary",
            ],
            "reasons": [],
            "missing_evidence": [],
            "verifier_id": VERIFIER_ID,
            "evidence_digest": candidate_store.compute_evidence_digest(candidate),
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "trace_id": candidate["trace_id"],
        }
        verifier_env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(REPO_ROOT),
            "CHALAMANDRA_ED25519_SEED": TEST_VERIFIER_SEED_HEX,
            "CHALAMANDRA_VERIFIER_ID": VERIFIER_ID,
        }
        signed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "verifier" / "independent_acceptance_verifier.py"),
                "--confirm", f"ACCEPT:{candidate_id}",
            ],
            input=json.dumps(decision),
            capture_output=True, text=True, env=verifier_env, timeout=10,
        )
        assert signed.returncode == 0, signed.stderr
        decision["signature"] = json.loads(signed.stdout)["signature"]
        status, acc = _http_json(
            api_port, "POST", "/v1/acceptance", acceptance_jwt, decision
        )
        assert status == 201 and acc["outcome"] == "accepted", acc

        # 6) GET tras aceptación -> 200 accepted, sin órdenes.
        status, delivered = _http_json(
            api_port, "GET", f"/v1/results/{candidate_id}", read_jwt
        )
        assert status == 200 and delivered["delivery_status"] == "accepted"
        assert delivered["orders_created"] is False
        assert delivered["orders_executed"] is False

        # 7) El receptor webhook real recibió exactamente 1 × result.accepted.
        time.sleep(1)
        assert len(received) == 1
        assert received[0].get("event_type") == "result.accepted"
    finally:
        for process in (api_process, worker_process):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        receiver.shutdown()
        receiver.server_close()
