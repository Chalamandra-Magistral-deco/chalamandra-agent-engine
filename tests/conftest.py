"""Aísla state dirs para que ningún test escriba en producción."""
import pytest


@pytest.fixture(autouse=True)
def isolate_state_dirs(tmp_path, monkeypatch):
    from api import (
        task_bus,
        candidate_store,
        subscription_store,
        acceptance_store,
        trace_store,
    )

    monkeypatch.setattr(task_bus, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(candidate_store, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(subscription_store, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(acceptance_store, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(trace_store, "_trace_file", lambda: tmp_path / "traces.jsonl")
    yield
