"""Tests for persistent global result-level distribution idempotency."""
from __future__ import annotations

from pathlib import Path

import pytest

from api import distribution_store


def _use_tmp_ledger(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(distribution_store, "_state_dir", lambda: tmp_path)
    return tmp_path / "distribution_events.jsonl"


def test_completed_result_is_deduplicated_across_event_ids(monkeypatch, tmp_path):
    ledger = _use_tmp_ledger(monkeypatch, tmp_path)
    trace_id = "1234567890abcdef1234567890abcdef"

    reservation = distribution_store.reserve_dispatch("logical-result-1", "event-first", trace_id)
    assert reservation["status"] == "reserved"
    assert distribution_store.finish_dispatch(
        "logical-result-1", "event-first", status="dispatched", delivered_count=2
    )

    replay = distribution_store.reserve_dispatch("logical-result-1", "event-second", trace_id)
    assert replay["status"] == "already_dispatched"
    assert replay["event_id"] == "event-first"
    assert replay["delivered_count"] == 2
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2


def test_failed_delivery_can_be_retried(monkeypatch, tmp_path):
    _use_tmp_ledger(monkeypatch, tmp_path)
    trace_id = "2234567890abcdef1234567890abcdef"

    first = distribution_store.reserve_dispatch("logical-result-retry", "event-retry-1", trace_id)
    assert first["status"] == "reserved"
    assert distribution_store.finish_dispatch(
        "logical-result-retry", "event-retry-1", status="failed", delivered_count=0,
        error_code="synthetic_test_failure",
    )

    # Retries keep one stable event identity so downstream consumers can deduplicate
    # after a timeout/partial failure; subscriber-level state decides what is retried.
    second = distribution_store.reserve_dispatch("logical-result-retry", "event-retry-2", trace_id)
    assert second["status"] == "reserved"
    assert second["event_id"] == "event-retry-1"


def test_event_id_cannot_be_reused_for_another_result(monkeypatch, tmp_path):
    _use_tmp_ledger(monkeypatch, tmp_path)
    trace_id = "3234567890abcdef1234567890abcdef"
    distribution_store.reserve_dispatch("logical-result-a", "one-global-event", trace_id)

    with pytest.raises(ValueError, match="event_id ya está asignado"):
        distribution_store.reserve_dispatch("logical-result-b", "one-global-event", trace_id)


def test_corrupt_distribution_ledger_fails_closed(monkeypatch, tmp_path):
    ledger = _use_tmp_ledger(monkeypatch, tmp_path)
    ledger.write_text("{invalid json\n", encoding="utf-8")

    with pytest.raises(ValueError, match="distribution_events.jsonl inválido"):
        distribution_store.reserve_dispatch(
            "logical-result-corrupt", "event-corrupt", "4234567890abcdef1234567890abcdef"
        )
