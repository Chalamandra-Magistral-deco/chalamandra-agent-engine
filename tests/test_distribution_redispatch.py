"""C-3 (liberación): un suscriptor añadido después de `dispatched` recibe el resultado.

Antes, reserve_dispatch() deduplicaba por result_id global: tras el primer
despacho, ningún suscriptor nuevo recibía nada. Ahora el re-despacho se decide
por suscriptor: solo quienes no tienen una entrega 'delivered' registrada
vuelven a recibir el evento (misma event_id estable); los ya entregados no
se re-envían.
"""
from unittest.mock import Mock

from api import distribution_store, subscription_store
from api.safe_http import ResolvedWebhookTarget
import api.webhook_dispatcher as webhook_dispatcher

TRACE_ID = "6c0a78fc04a34d9b8e8f1fbced8a0041"
TARGET = ResolvedWebhookTarget(
    scheme="https",
    hostname="example.com",
    port=443,
    request_target="/hook",
    addresses=("93.184.216.34",),
)


def _use_tmp_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(distribution_store, "_state_dir", lambda: tmp_path)


def test_dispatched_result_redispatches_only_to_undelivered_subscribers(
    monkeypatch, tmp_path
):
    _use_tmp_ledger(monkeypatch, tmp_path)

    first = distribution_store.reserve_dispatch("res-1", "evt-1", TRACE_ID)
    assert first["status"] == "reserved"
    assert distribution_store.finish_dispatch(
        "res-1", "evt-1", status="dispatched", delivered_count=1
    )
    # sub-1 ya recibió; sub-2 no existe aún en el ledger de entregas.
    delivery = distribution_store.reserve_subscription_delivery(
        "res-1", "sub-1", "evt-1", TRACE_ID
    )
    assert delivery["status"] == "reserved"
    assert distribution_store.finish_subscription_delivery(
        "res-1", "sub-1", "evt-1", status="delivered"
    )

    # Solo sub-1: deduplicación clásica, sin re-despacho.
    replay = distribution_store.reserve_dispatch(
        "res-1", "evt-2", TRACE_ID, subscription_ids=["sub-1"]
    )
    assert replay["status"] == "already_dispatched"
    assert replay["event_id"] == "evt-1"

    # sub-2 se añadió después: hay re-despacho con la misma event_id estable.
    redispatch = distribution_store.reserve_dispatch(
        "res-1", "evt-2", TRACE_ID, subscription_ids=["sub-1", "sub-2"]
    )
    assert redispatch["status"] == "reserved"
    assert redispatch["event_id"] == "evt-1"
    assert redispatch["redispatch_for"] == ["sub-2"]
    assert distribution_store.finish_dispatch(
        "res-1", "evt-1", status="dispatched", delivered_count=2
    )

    # Sin subscription_ids el comportamiento antiguo se conserva intacto.
    legacy = distribution_store.reserve_dispatch("res-1", "evt-3", TRACE_ID)
    assert legacy["status"] == "already_dispatched"


def _patch_live_dispatch(monkeypatch, subscriptions):
    """Despacho real contra los stores, con transporte HTTP simulado."""
    monkeypatch.setenv("CHALAMANDRA_WEBHOOK_SECRET", "test-webhook-secret-32-bytes-minimum")
    monkeypatch.setattr(subscription_store, "list_subscriptions", lambda: subscriptions)
    monkeypatch.setattr(subscription_store, "resolve_safe_url", lambda url: TARGET)
    monkeypatch.setattr(webhook_dispatcher, "post_json", Mock(return_value=202))
    monkeypatch.setattr(webhook_dispatcher, "_log_dead_letter", lambda *a, **k: None)
    monkeypatch.setattr(webhook_dispatcher.time, "sleep", lambda *_: None)


def _sub(sub_id):
    return {
        "subscription_id": sub_id,
        "endpoint_url": "https://example.com/hook",
        "event_types": ["result.accepted"],
        "status": "enabled",
    }


def test_late_subscriber_receives_result_without_resending_to_first(
    monkeypatch, tmp_path
):
    _use_tmp_ledger(monkeypatch, tmp_path)
    _patch_live_dispatch(monkeypatch, [_sub("sub-1")])

    sent = webhook_dispatcher.dispatch_event("result.accepted", "res-2", TRACE_ID)
    assert sent == 1
    assert webhook_dispatcher.post_json.call_count == 1

    # Llega sub-2; el segundo despacho solo le envía a él.
    _patch_live_dispatch(monkeypatch, [_sub("sub-1"), _sub("sub-2")])
    post = webhook_dispatcher.post_json
    sent_again = webhook_dispatcher.dispatch_event("result.accepted", "res-2", TRACE_ID)
    assert sent_again == 2
    assert post.call_count == 1  # solo sub-2; sub-1 ya estaba entregado

    # Tercer despacho sin suscriptores nuevos: no re-envía a nadie.
    _patch_live_dispatch(monkeypatch, [_sub("sub-1"), _sub("sub-2")])
    post = webhook_dispatcher.post_json
    sent_third = webhook_dispatcher.dispatch_event("result.accepted", "res-2", TRACE_ID)
    assert sent_third == 2
    assert post.call_count == 0
