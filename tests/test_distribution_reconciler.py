"""C-4 (liberación): el worker reintenta automáticamente distribuciones fallidas.

Antes, una distribución fallida tras aceptación solo se reintentaba a mano
(replay del POST /v1/acceptance o /internal/v1/distributions). Ahora el
reconciliador del worker la reintenta solo, con un intervalo mínimo entre
intentos por resultado para no llenar el ledger de eventos fallidos.
"""
import threading
from unittest.mock import Mock

import pytest
from nacl.signing import SigningKey

from api import (
    acceptance_store,
    candidate_store,
    distribution_reconciler,
    distribution_store,
    subscription_store,
    worker_loop,
)
from api.crypto_signer import create_canonical_payload
from api.safe_http import ResolvedWebhookTarget
import api.webhook_dispatcher as webhook_dispatcher

TRACE_ID = "1234567890abcdef1234567890abcdef"
VERIFIER_SEED_HEX = "11" * 32
VERIFIER_KEY = SigningKey(bytes.fromhex(VERIFIER_SEED_HEX))
TARGET = ResolvedWebhookTarget(
    scheme="https",
    hostname="example.com",
    port=443,
    request_target="/hook",
    addresses=("93.184.216.34",),
)


@pytest.fixture
def verifier_env(monkeypatch):
    monkeypatch.setenv(
        "CHALAMANDRA_VERIFIER_PUBLIC_KEY", VERIFIER_KEY.verify_key.encode().hex()
    )
    monkeypatch.setenv("CHALAMANDRA_VERIFIER_ID", "reconciler-test-verifier")
    monkeypatch.setenv(
        "CHALAMANDRA_WEBHOOK_SECRET", "test-webhook-secret-32-bytes-minimum"
    )


def _accept_candidate(monkeypatch, candidate_id="cand-reconcile-1"):
    """Guarda un candidato y registra una decisión aceptada firmada real."""
    candidate = {
        "candidate_id": candidate_id,
        "result_id": candidate_id,
        "status": "candidate",
        "summary": "candidato para reconciliación",
        "evidence": [],
        "trace_id": TRACE_ID,
    }
    candidate_store.save_candidate(candidate)
    decision = {
        "decision_id": "dec-reconcile-1",
        "candidate_id": candidate_id,
        "outcome": "accepted",
        "criteria_checked": ["schema"],
        "reasons": [],
        "missing_evidence": [],
        "verifier_id": "reconciler-test-verifier",
        "evidence_digest": candidate_store.compute_evidence_digest(candidate),
        "decided_at": "2026-11-14T07:00:00+00:00",
        "trace_id": TRACE_ID,
    }
    signature = VERIFIER_KEY.sign(
        create_canonical_payload(decision)
    ).signature.hex()
    decision["signature"] = signature
    acceptance_store.record_acceptance(decision)
    return candidate_id


def _patch_no_subscriptions(monkeypatch):
    monkeypatch.setattr(subscription_store, "list_subscriptions", lambda: [])


def _patch_working_subscription(monkeypatch):
    monkeypatch.setattr(
        subscription_store,
        "list_subscriptions",
        lambda: [{
            "subscription_id": "sub-1",
            "endpoint_url": "https://example.com/hook",
            "event_types": ["result.accepted"],
            "status": "enabled",
        }],
    )
    monkeypatch.setattr(subscription_store, "resolve_safe_url", lambda url: TARGET)
    monkeypatch.setattr(webhook_dispatcher, "post_json", Mock(return_value=202))
    monkeypatch.setattr(webhook_dispatcher, "_log_dead_letter", lambda *a, **k: None)
    monkeypatch.setattr(webhook_dispatcher.time, "sleep", lambda *_: None)


def test_failed_distribution_is_retried_and_delivered(monkeypatch, verifier_env):
    candidate_id = _accept_candidate(monkeypatch)
    _patch_no_subscriptions(monkeypatch)

    # La aceptación sin suscriptores deja un evento fallido.
    sent = webhook_dispatcher.dispatch_event(
        "result.accepted", candidate_id, TRACE_ID, event_id="acceptance:dec-reconcile-1"
    )
    assert sent == 0
    assert distribution_store.latest_dispatch(candidate_id)["status"] == "failed"

    # Aparece un suscriptor que funciona: la reconciliación lo entrega.
    _patch_working_subscription(monkeypatch)
    report = distribution_reconciler.reconcile_failed_distributions(
        min_interval_seconds=0
    )
    assert report == {
        "examined": 1,
        "retried": 1,
        "delivered": 1,
        "still_failed": 0,
        "skipped_throttled": 0,
        "skipped_invalid": 0,
    }
    assert webhook_dispatcher.post_json.call_count == 1
    assert distribution_store.latest_dispatch(candidate_id)["status"] == "dispatched"


def test_dispatched_result_is_not_retried(monkeypatch, verifier_env):
    candidate_id = _accept_candidate(monkeypatch)
    _patch_working_subscription(monkeypatch)

    sent = webhook_dispatcher.dispatch_event(
        "result.accepted", candidate_id, TRACE_ID, event_id="acceptance:dec-reconcile-1"
    )
    assert sent == 1
    calls = webhook_dispatcher.post_json.call_count

    report = distribution_reconciler.reconcile_failed_distributions(
        min_interval_seconds=0
    )
    assert report["examined"] == 1
    assert report["retried"] == 0
    assert webhook_dispatcher.post_json.call_count == calls


def test_retry_is_throttled_by_interval(monkeypatch, verifier_env):
    candidate_id = _accept_candidate(monkeypatch)
    _patch_no_subscriptions(monkeypatch)
    webhook_dispatcher.dispatch_event(
        "result.accepted", candidate_id, TRACE_ID, event_id="acceptance:dec-reconcile-1"
    )

    # El último intento fallido es reciente: no se reintenta todavía.
    report = distribution_reconciler.reconcile_failed_distributions(
        min_interval_seconds=300
    )
    assert report["retried"] == 0
    assert report["skipped_throttled"] == 1

    # Con intervalo 0 sí se reintenta (y vuelve a fallar sin suscriptores).
    report = distribution_reconciler.reconcile_failed_distributions(
        min_interval_seconds=0
    )
    assert report["retried"] == 1
    assert report["still_failed"] == 1


def test_rejected_decision_is_not_reconciled(monkeypatch, verifier_env):
    candidate = {
        "candidate_id": "cand-reconcile-2",
        "result_id": "cand-reconcile-2",
        "status": "candidate",
        "summary": "candidato rechazado",
        "evidence": [],
        "trace_id": TRACE_ID,
    }
    candidate_store.save_candidate(candidate)
    decision = {
        "decision_id": "dec-reconcile-2",
        "candidate_id": "cand-reconcile-2",
        "outcome": "rejected",
        "criteria_checked": ["schema"],
        "reasons": ["no procede"],
        "missing_evidence": [],
        "verifier_id": "reconciler-test-verifier",
        "evidence_digest": candidate_store.compute_evidence_digest(candidate),
        "decided_at": "2026-11-14T07:00:00+00:00",
        "trace_id": TRACE_ID,
    }
    decision["signature"] = VERIFIER_KEY.sign(
        create_canonical_payload(decision)
    ).signature.hex()
    acceptance_store.record_acceptance(decision)

    report = distribution_reconciler.reconcile_failed_distributions(
        min_interval_seconds=0
    )
    assert report["examined"] == 0
    assert report["retried"] == 0


def test_worker_loop_runs_reconciliation(monkeypatch):
    """El loop del worker invoca al reconciliador sin tumbarse si falla."""
    calls = []
    monkeypatch.setattr(
        distribution_reconciler,
        "reconcile_failed_distributions",
        lambda: calls.append(1) or {
            "examined": 0, "retried": 0, "delivered": 0,
            "still_failed": 0, "skipped_throttled": 0, "skipped_invalid": 0,
        },
    )
    monkeypatch.setenv("CHALAMANDRA_WORKER_POLL_INTERVAL", "0.05")
    monkeypatch.setenv("CHALAMANDRA_DISTRIBUTION_RECONCILE_INTERVAL_SECONDS", "5")
    stop_event = threading.Event()
    threading.Timer(0.5, stop_event.set).start()
    worker_loop._run_worker_loop(stop_event)
    assert len(calls) >= 1
