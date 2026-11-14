"""Integridad del registro completo del candidato (hallazgo C-1).

Antes del fix, evidence_digest cubría solo la lista evidence[]: editar
economic_assessment, summary u orders_* en candidates.jsonl no se detectaba
y el resultado manipulado se entregaba como aceptado. Ahora cada registro
lleva record_digest sobre el registro completo y get_candidate() falla
cerrado si no coincide.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api import candidate_store


def _candidate(**overrides):
    candidate = {
        "candidate_id": "integrity-candidate-1",
        "result_id": "integrity-candidate-1",
        "status": "candidate",
        "trace_id": "a" * 32,
        "catedralx_trace_id": "a" * 32,
        "economic_assessment": {"status": "ANALYSIS_COMPLETE", "revenue_total": 24000.0},
        "summary": "oportunidad diferida",
        "orders_created": False,
        "orders_executed": False,
        "evidence": [{"kind": "catedralx_output", "digest": "0" * 64}],
    }
    candidate.update(overrides)
    return candidate


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    # tests/conftest.py aísla los stores con monkeypatch de _state_dir() a
    # tmp_path: el ledger vive en tmp_path/candidates.jsonl, no bajo HOME.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CHALAMANDRA_STATE_DIR", raising=False)
    return tmp_path


def _ledger_path(home: Path) -> Path:
    return home / "candidates.jsonl"


def _rewrite_ledger(home: Path, mutate) -> None:
    path = _ledger_path(home)
    record = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
    mutate(record)
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")


def test_save_attaches_full_record_digest(isolated_home):
    candidate_store.save_candidate(_candidate())
    saved = candidate_store.get_candidate("integrity-candidate-1")
    assert saved is not None
    assert saved["record_digest_alg"] == "hmac-sha256"
    assert len(saved["record_digest"]) == 64
    assert saved["economic_assessment"]["revenue_total"] == 24000.0


def test_tampered_assessment_is_rejected_on_read(isolated_home):
    candidate_store.save_candidate(_candidate())
    _rewrite_ledger(
        isolated_home,
        lambda record: record["economic_assessment"].update({"revenue_total": 999999.0, "note": "TAMPERED"}),
    )
    with pytest.raises(ValueError, match="record_digest"):
        candidate_store.get_candidate("integrity-candidate-1")


def test_tampered_orders_flag_is_rejected_on_read(isolated_home):
    candidate_store.save_candidate(_candidate())
    _rewrite_ledger(isolated_home, lambda record: record.update({"orders_created": True}))
    with pytest.raises(ValueError, match="record_digest"):
        candidate_store.get_candidate("integrity-candidate-1")


def test_tampered_evidence_is_rejected_on_read(isolated_home):
    candidate_store.save_candidate(_candidate())
    _rewrite_ledger(
        isolated_home,
        lambda record: record.update({"evidence": [{"kind": "forged", "digest": "f" * 64}]}),
    )
    with pytest.raises(ValueError, match="record_digest"):
        candidate_store.get_candidate("integrity-candidate-1")


def test_legacy_record_without_digest_is_rejected(isolated_home):
    path = _ledger_path(isolated_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_candidate(), ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="record_digest"):
        candidate_store.get_candidate("integrity-candidate-1")


def test_identical_retry_does_not_duplicate(isolated_home):
    candidate_store.save_candidate(_candidate())
    candidate_store.save_candidate(_candidate())
    lines = _ledger_path(isolated_home).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_hmac_digest_when_key_configured(isolated_home, monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_CANDIDATE_HMAC_KEY", "b6" * 32)
    candidate_store.save_candidate(_candidate())
    saved = candidate_store.get_candidate("integrity-candidate-1")
    assert saved is not None
    assert saved["record_digest_alg"] == "hmac-sha256"


def test_hmac_digest_rejects_wrong_key_on_read(isolated_home, monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_CANDIDATE_HMAC_KEY", "c7" * 32)
    candidate_store.save_candidate(_candidate())
    monkeypatch.setenv("CHALAMANDRA_CANDIDATE_HMAC_KEY", "d8" * 32)
    with pytest.raises(ValueError, match="record_digest"):
        candidate_store.get_candidate("integrity-candidate-1")



def test_missing_hmac_key_fails_closed_on_write(isolated_home, monkeypatch):
    monkeypatch.delenv("CHALAMANDRA_CANDIDATE_HMAC_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CHALAMANDRA_CANDIDATE_HMAC_KEY"):
        candidate_store.save_candidate(_candidate())


def test_stripping_both_digest_fields_is_rejected(isolated_home):
    candidate_store.save_candidate(_candidate())

    def strip_integrity_fields(record):
        record.pop("record_digest", None)
        record.pop("record_digest_alg", None)

    _rewrite_ledger(isolated_home, strip_integrity_fields)
    with pytest.raises(ValueError, match="record_digest"):
        candidate_store.get_candidate("integrity-candidate-1")


def test_state_dir_override_moves_all_ledgers(tmp_path, monkeypatch):
    # CHALAMANDRA_STATE_DIR es la raíz del estado compartido API<->worker:
    # TaskBus, candidatos y aceptaciones deben vivir bajo <raíz>/agents.
    # conftest aísla _state_dir() a tmp_path; aquí se restaura el real.
    from api import task_bus
    from api.state_dir import agents_state_dir

    monkeypatch.setattr(candidate_store, "_state_dir", agents_state_dir)
    monkeypatch.setattr(task_bus, "_state_dir", agents_state_dir)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHALAMANDRA_STATE_DIR", str(tmp_path / "state"))
    candidate_store.save_candidate(_candidate())
    task_bus.enqueue_task({"objective": "state-dir probe"})
    agents_dir = tmp_path / "state" / "agents"
    assert (agents_dir / "candidates.jsonl").exists()
    assert (agents_dir / "tasks.db").exists()
    assert not (tmp_path / "home" / ".local" / "state").exists()


def test_hmac_digest_rejects_tamper(isolated_home, monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_CANDIDATE_HMAC_KEY", "e9" * 32)
    candidate_store.save_candidate(_candidate())
    _rewrite_ledger(isolated_home, lambda record: record.update({"summary": "TAMPERED SUMMARY"}))
    with pytest.raises(ValueError, match="record_digest"):
        candidate_store.get_candidate("integrity-candidate-1")
