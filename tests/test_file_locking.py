"""F-7 (liberación): los flock() de los ledgers tienen timeout acotado.

Antes, JsonlIndex.locked(), trace_store.record_request() y el reconciliador
de feedback usaban LOCK_EX bloqueante sin timeout: un poseedor colgado
bloqueaba las escrituras del ledger para siempre. Ahora la espera es
acotada y expira con TimeoutError (subclase de OSError: fail-closed en los
llamadores que ya capturan OSError).
"""
import fcntl
import time

import pytest

from api import locking, trace_store
from api.jsonl_index import JsonlIndex


def test_acquire_exclusive_lock_rejects_non_positive_timeout():
    with pytest.raises(ValueError):
        locking.acquire_exclusive_lock(0, timeout_seconds=0)


def test_jsonl_index_lock_times_out_instead_of_blocking_forever(tmp_path):
    ledger = tmp_path / "events.jsonl"
    ledger.write_text("", encoding="utf-8")
    store = JsonlIndex(ledger, ("result_id",), lock_timeout_seconds=0.2)

    # Un poseedor externo retiene el lock del ledger.
    lock_path = store.lock_path
    with lock_path.open("a", encoding="utf-8") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            with store.locked():
                raise AssertionError("no debe adquirir el lock retenido")
        assert time.monotonic() - started < 2.0


def test_jsonl_index_lock_is_released_after_use(tmp_path):
    ledger = tmp_path / "events.jsonl"
    ledger.write_text("", encoding="utf-8")
    store = JsonlIndex(ledger, ("result_id",), lock_timeout_seconds=0.2)
    with store.locked():
        pass
    # Si el lock no se liberara, esto expirarían con TimeoutError.
    with store.locked() as db:
        store.append(db, {"result_id": "r-1"})
    with store.locked() as db:
        assert [r["result_id"] for r in store.find(db, "result_id", "r-1")] == ["r-1"]


def test_trace_store_lock_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(
        trace_store, "DEFAULT_TRACE_LOCK_TIMEOUT_SECONDS", 0.2
    )
    target = tmp_path / "traces.jsonl"
    target.write_text("", encoding="utf-8")
    with target.open("a", encoding="utf-8") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            trace_store.record_request(
                trace_id="1234567890abcdef1234567890abcdef",
                traceparent=None,
                method="GET",
                path="/healthz",
                status=200,
                client_ip="127.0.0.1",
            )
        assert time.monotonic() - started < 2.0
