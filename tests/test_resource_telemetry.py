import json
import stat

from telemetry.resources import record_task_resources


def test_task_resource_record_is_persisted_without_task_payload(tmp_path, monkeypatch):
    target = tmp_path / "state" / "task_resources.jsonl"
    monkeypatch.setenv("CHALAMANDRA_RESOURCE_TRACE_PATH", str(target))

    @record_task_resources
    def run(task):
        # Deliberately include sensitive input in the task; it must not be persisted.
        return {"status": "candidate", "candidate_id": "candidate-1"}

    result = run({
        "task_id": "task-1",
        "trace_id": "1234567890abcdef1234567890abcdef",
        "payload": "DO NOT PERSIST THIS PROMPT",
    })

    assert result["status"] == "candidate"
    record = json.loads(target.read_text(encoding="utf-8").splitlines()[0])
    assert record["task_id"] == "task-1"
    assert record["trace_id"] == "1234567890abcdef1234567890abcdef"
    assert record["result_status"] == "candidate"
    assert record["candidate_id"] == "candidate-1"
    assert record["cpu_seconds"] >= 0
    assert record["wall_time_s"] >= 0
    assert record["energy_wh"] is None
    assert record["energy_status"] == "not_measured_by_vm_runtime"
    assert "payload" not in record
    assert "DO NOT PERSIST" not in target.read_text(encoding="utf-8")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_resource_telemetry_failure_does_not_change_task_result(tmp_path, monkeypatch):
    target_is_directory = tmp_path / "trace-is-a-directory"
    target_is_directory.mkdir()
    monkeypatch.setenv("CHALAMANDRA_RESOURCE_TRACE_PATH", str(target_is_directory))

    @record_task_resources
    def run(task):
        return {"status": "completed"}

    # The recorder cannot append to a directory; that observability failure must
    # never alter the business result.
    assert run({"task_id": "task-2"}) == {"status": "completed"}
