import asyncio
import json
import os
import stat

import pytest
from pydantic import ValidationError

from adapters.base import ModelRequest, ModelResponse, ModelRole
from core.orchestrator import Orchestrator
from telemetry.stats import cost_totals
from telemetry.trace import Tracer


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission check")
def test_trace_file_is_created_with_private_permissions(tmp_path) -> None:
    path = tmp_path / "traces.jsonl"
    tracer = Tracer(path)

    tracer.record(
        ModelResponse(
            call_id="call-1",
            model="ollama",
            role=ModelRole.AGENT,
            output="private result",
        )
    )

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission check")
def test_existing_trace_file_permissions_are_restricted(tmp_path) -> None:
    path = tmp_path / "traces.jsonl"
    path.write_text("existing trace\n", encoding="utf-8")
    path.chmod(0o664)
    tracer = Tracer(path)

    tracer.record(
        ModelResponse(
            call_id="call-2",
            model="ollama",
            role=ModelRole.AGENT,
            output="private result",
        )
    )

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text(encoding="utf-8").startswith("existing trace\n")


def test_default_trace_path_uses_persistent_state_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CHALAMANDRA_AGENTS_TRACE_PATH", raising=False)
    monkeypatch.setenv("CHALAMANDRA_STATE_DIR", str(tmp_path))

    tracer = Tracer()
    tracer.record(
        ModelResponse(
            call_id="call-persisted",
            model="ollama",
            role=ModelRole.AGENT,
            output="ok",
            trace_id="1234567890abcdef1234567890abcdef",
        )
    )

    expected = tmp_path / "agents" / "model_traces.jsonl"
    assert tracer.path == expected
    record = json.loads(expected.read_text(encoding="utf-8").splitlines()[0])
    assert record["trace_id"] == "1234567890abcdef1234567890abcdef"


def test_explicit_trace_path_overrides_state_dir(tmp_path, monkeypatch) -> None:
    explicit = tmp_path / "custom" / "model-traces.jsonl"
    monkeypatch.setenv("CHALAMANDRA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CHALAMANDRA_AGENTS_TRACE_PATH", str(explicit))

    assert Tracer().path == explicit


def test_w3c_trace_id_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        ModelRequest(prompt="test", trace_id="not-a-w3c-trace-id")
    with pytest.raises(ValidationError):
        ModelResponse(
            call_id="call-invalid",
            model="ollama",
            role=ModelRole.AGENT,
            output="ok",
            trace_id="0" * 32,
        )


def test_orchestrator_propagates_request_trace_id_to_model_trace(tmp_path) -> None:
    class FakeAdapter:
        async def generate(self, request):
            return ModelResponse(
                call_id=request.call_id,
                model="fake",
                role=request.role,
                output="ok",
            )

    tracer = Tracer(tmp_path / "traces.jsonl")
    orchestrator = Orchestrator(tracer=tracer)
    orchestrator.adapters = {"fake": FakeAdapter()}
    request = ModelRequest(
        prompt="test", trace_id="1234567890abcdef1234567890abcdef"
    )

    response = asyncio.run(orchestrator.run("fake", request))

    assert response.trace_id == request.trace_id
    stored = list(tracer.iter_records())
    assert len(stored) == 1
    assert stored[0]["trace_id"] == request.trace_id


def test_cost_totals_exposes_missing_cost_coverage() -> None:
    report = cost_totals(
        [
            {"model": "a", "cost_usd": 0.25},
            {"model": "a", "cost_usd": None},
            {"model": "a"},
        ]
    )

    assert report == {
        "count": 3,
        "cost_usd": 0.25,
        "cost_known_count": 1,
        "cost_missing_count": 2,
        "cost_coverage_pct": 33.33,
    }


def test_master_propagates_trace_id_to_planner_and_executor():
    from core.master import Master

    trace_id = "1234567890abcdef1234567890abcdef"
    captured = {}

    class FakeDecomposer:
        async def decompose(self, task, *, trace_id=None):
            captured["planner_trace_id"] = trace_id
            return ["subtask"]

    class FakeExecutor:
        async def execute_plan(self, task, subtasks, *, trace_id=None):
            captured["executor_trace_id"] = trace_id
            return {"status": "completed"}

    master = Master.__new__(Master)
    master.decomposer = FakeDecomposer()
    master.executor = FakeExecutor()

    result = asyncio.run(master.execute_task("test task", trace_id=trace_id))

    assert result["status"] == "completed"
    assert captured == {
        "planner_trace_id": trace_id,
        "executor_trace_id": trace_id,
    }
