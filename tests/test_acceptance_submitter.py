from __future__ import annotations

import io
import json
import pytest
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from nacl.signing import SigningKey

from api.crypto_signer import create_canonical_payload
from verifier.submit_acceptance import main


def _signed_decision(key: SigningKey) -> dict[str, object]:
    decision: dict[str, object] = {
        "decision_id": "decision-submit-test-001",
        "candidate_id": "candidate-submit-test-001",
        "outcome": "accepted",
        "criteria_checked": ["synthetic-test-fixture"],
        "reasons": [],
        "missing_evidence": [],
        "verifier_id": "submit-test-verifier",
        "evidence_digest": "a" * 64,
        "decided_at": "2026-10-28T12:00:00+00:00",
        "trace_id": "1234567890abcdef1234567890abcdef",
    }
    decision["signature"] = key.sign(create_canonical_payload(decision)).signature.hex()
    return decision


def test_submitter_posts_verified_signed_decision_with_trace_context(
    monkeypatch, capsys
):
    key = SigningKey(bytes.fromhex("22" * 32))
    decision = _signed_decision(key)
    received: list[dict[str, object]] = []

    class Sink(BaseHTTPRequestHandler):
        def log_message(self, format_string, *args):
            return

        def do_POST(self):
            body = json.loads(
                self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
            )
            received.append(
                {
                    "body": body,
                    "authorization": self.headers.get("Authorization"),
                    "content_type": self.headers.get("Content-Type"),
                    "traceparent": self.headers.get("traceparent"),
                    "x_trace_id": self.headers.get("X-Trace-Id"),
                }
            )
            response = json.dumps(
                {
                    "decision_id": decision["decision_id"],
                    "outcome": "accepted",
                    "idempotent_replay": False,
                }
            ).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv(
            "CHALAMANDRA_VERIFIER_PUBLIC_KEY", key.verify_key.encode().hex()
        )
        monkeypatch.setenv("CHALAMANDRA_ACCEPTANCE_TOKEN", "unit-test-token")
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(decision)))

        status = main(
            ["--url", f"http://127.0.0.1:{server.server_address[1]}/v1/acceptance"]
        )
        output = capsys.readouterr()

        assert status == 0
        result = json.loads(output.out)
        assert result["status"] == "accepted_by_api"
        assert result["webhook_delivery_confirmed"] is False
        assert len(received) == 1
        message = received[0]
        assert message["body"] == decision
        assert message["authorization"] == "Bearer unit-test-token"
        assert message["content_type"] == "application/json"
        assert message["x_trace_id"] == decision["trace_id"]
        traceparent = message["traceparent"]
        assert isinstance(traceparent, str)
        assert traceparent.startswith(f"00-{decision['trace_id']}-")
        assert traceparent.endswith("-01")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_submitter_rejects_modified_decision_before_network_call(monkeypatch, capsys):
    key = SigningKey(bytes.fromhex("33" * 32))
    decision = _signed_decision(key)
    decision["outcome"] = "rejected"
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_PUBLIC_KEY", key.verify_key.encode().hex())
    monkeypatch.setenv("CHALAMANDRA_ACCEPTANCE_TOKEN", "unit-test-token")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(decision)))

    status = main(["--url", "http://127.0.0.1:1/v1/acceptance"])
    output = capsys.readouterr()

    assert status == 2
    error = json.loads(output.err)
    assert error["status"] == "submission_not_sent"
    assert "signature" in error["error"]


def test_submitter_requires_bearer_token(monkeypatch, capsys):
    key = SigningKey(bytes.fromhex("44" * 32))
    decision = _signed_decision(key)
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_PUBLIC_KEY", key.verify_key.encode().hex())
    monkeypatch.delenv("CHALAMANDRA_ACCEPTANCE_TOKEN", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(decision)))

    status = main(["--url", "http://127.0.0.1:1/v1/acceptance"])
    output = capsys.readouterr()

    assert status == 2
    assert "CHALAMANDRA_ACCEPTANCE_TOKEN_missing" in output.err


def test_submitter_rejects_public_plain_http_destination(monkeypatch):
    from verifier.submit_acceptance import _validate_url

    monkeypatch.delenv("CHALAMANDRA_API_ALLOW_PRIVATE_HTTP", raising=False)
    with pytest.raises(ValueError, match="acceptance_http_requires"):
        _validate_url("http://8.8.8.8/v1/acceptance")


def test_submitter_requires_opt_in_for_private_plain_http(monkeypatch):
    from verifier.submit_acceptance import _validate_url

    monkeypatch.delenv("CHALAMANDRA_API_ALLOW_PRIVATE_HTTP", raising=False)
    with pytest.raises(ValueError, match="acceptance_http_requires"):
        _validate_url("http://10.20.30.40:8766/v1/acceptance")

    monkeypatch.setenv("CHALAMANDRA_API_ALLOW_PRIVATE_HTTP", "true")
    _validate_url("http://10.20.30.40:8766/v1/acceptance")


def test_submitter_rejects_mixed_public_private_dns_for_http(monkeypatch):
    import socket

    from verifier.submit_acceptance import _validate_url

    monkeypatch.setenv("CHALAMANDRA_API_ALLOW_PRIVATE_HTTP", "true")
    answers = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.20.30.40", 8766)),
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 8766)),
    ]
    monkeypatch.setattr(
        "verifier.submit_acceptance.socket.getaddrinfo",
        lambda *_args, **_kwargs: answers,
    )
    with pytest.raises(ValueError, match="acceptance_http_requires"):
        _validate_url("http://agents-api.internal:8766/v1/acceptance")


def test_submitter_still_allows_loopback_http_and_public_https():
    from verifier.submit_acceptance import _validate_url

    _validate_url("http://127.0.0.1:8766/v1/acceptance")
    _validate_url("https://agents.example.com/v1/acceptance")
