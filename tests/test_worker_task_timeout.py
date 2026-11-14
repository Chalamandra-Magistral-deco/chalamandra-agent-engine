"""F-6 (liberación): el TaskTimeout durante la ejecución llega a la rama de timeout del loop.

Antes, el `except Exception` de process_task() tragaba el TaskTimeout del
SIGALRM y lo convertía en `failed` genérico; la rama `except TaskTimeout` de
_run_worker_loop() solo cubría la publicación final y era inalcanzable.
Ahora process_task() lo re-lanza y el loop marca la tarea como failed con
reason `timeout_60s` explícito. Sigue siendo fail-closed: sin candidato,
sin publicación y sin órdenes.
"""
import hashlib
import threading
from datetime import datetime, timezone

import pytest

from api import task_bus, worker_loop
from api.task_contracts import CATEDRALX_OPPORTUNITY_EVALUATION

TRACE_ID = "abcdefabcdefabcdefabcdefabcdefab"


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "tasks.db"
    monkeypatch.setattr(task_bus, "_db_path", lambda: db)
    task_bus.init_db()
    return db


def _opportunity_payload():
    return {
        "opportunity_id": "opp-timeout-01",
        "name": "Servicio digital",
        "category": "digital_service",
        "income_model": "hybrid",
        "currency": "MXN",
        "horizon_months": 12,
        "capital_required": 5000,
        "available_capital": 3000,
        "revenue": [{"amount": 2000, "frequency": "monthly", "evidence_id": "ev-1"}],
        "costs": [{"amount": 500, "frequency": "monthly", "evidence_id": "ev-1"}],
        "evidence": [{
            "evidence_id": "ev-1", "source": "controlled_test_fixture",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "digest": hashlib.sha256(b"fixture").hexdigest(),
        }],
        "assumptions": ["La demanda mensual permanece estable durante el horizonte."],
        "scenarios": [
            {"name": "conservative", "revenue_multiplier": 0.7, "cost_multiplier": 1.2},
            {"name": "base", "revenue_multiplier": 1.0, "cost_multiplier": 1.0},
            {"name": "optimistic", "revenue_multiplier": 1.2, "cost_multiplier": 0.9},
        ],
        "trace_id": TRACE_ID,
    }


def test_process_task_re_raises_task_timeout_instead_of_swallowing_it(
    isolated_db, monkeypatch
):
    task_id = task_bus.enqueue_task({
        "objective": CATEDRALX_OPPORTUNITY_EVALUATION,
        "payload": _opportunity_payload(),
        "trace_id": TRACE_ID,
    })
    claimed = task_bus.get_next_task(lease_seconds=120)
    assert claimed["task_id"] == task_id

    def _boom(payload):
        raise worker_loop.TaskTimeout("task exceeded 60s timeout")

    monkeypatch.setattr(worker_loop, "_run_catedralx_opportunity", _boom)
    with pytest.raises(worker_loop.TaskTimeout):
        worker_loop.process_task(claimed)

    # El timeout no se convierte en candidato `failed`: la tarea sigue en
    # `running` con su lease para que el loop la marque con el motivo
    # explícito; no hay publicación en el outbox.
    assert task_bus.get_task_status(task_id)["status"] == "running"
    assert task_bus.get_pending_candidate_publications() == []


def test_worker_loop_timeout_branch_marks_task_failed_with_timeout_reason(
    isolated_db, monkeypatch
):
    monkeypatch.setenv("CHALAMANDRA_WORKER_POLL_INTERVAL", "0.05")
    task_id = task_bus.enqueue_task({
        "objective": CATEDRALX_OPPORTUNITY_EVALUATION,
        "payload": _opportunity_payload(),
        "trace_id": TRACE_ID,
    })

    def _boom(task):
        raise worker_loop.TaskTimeout("task exceeded 60s timeout")

    monkeypatch.setattr(worker_loop, "process_task", _boom)
    stop_event = threading.Event()
    threading.Timer(1.0, stop_event.set).start()
    worker_loop._run_worker_loop(stop_event)

    completed = task_bus.get_task_status(task_id)
    assert completed["status"] == "failed"
    assert completed["lease_token"] is None
    import json

    candidate = json.loads(completed["candidate_result"])
    assert candidate["status"] == "failed"
    assert candidate["reason"] == "timeout_60s"
