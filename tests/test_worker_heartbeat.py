"""Tests for the atomic worker heartbeat."""
import json

from api import heartbeat


def test_writes_idle_then_processing(monkeypatch, tmp_path):
    hb = tmp_path / "worker_heartbeat.json"
    monkeypatch.setenv("CHALAMANDRA_WORKER_HEARTBEAT_PATH", str(hb))
    heartbeat.write_worker_heartbeat("idle")
    assert hb.exists()
    data = json.loads(hb.read_text(encoding="utf-8"))
    assert data["status"] == "idle"
    assert isinstance(data["pid"], int) and data["pid"] > 0
    assert isinstance(data["ts"], int)
    heartbeat.write_worker_heartbeat("processing", task_id="t-1")
    data = json.loads(hb.read_text(encoding="utf-8"))
    assert data["status"] == "processing"
    assert data["task_id"] == "t-1"
    assert not (tmp_path / "worker_heartbeat.json.tmp").exists()


def test_no_env_var_is_noop(monkeypatch, tmp_path):
    monkeypatch.delenv("CHALAMANDRA_WORKER_HEARTBEAT_PATH", raising=False)
    heartbeat.write_worker_heartbeat("idle")
    assert not list(tmp_path.iterdir())
