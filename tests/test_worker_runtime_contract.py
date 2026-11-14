"""Worker runtime invariants: lease fencing and bounded execution lifetime."""
from api import worker_loop


def test_worker_claim_lease_exceeds_hard_task_timeout():
    assert worker_loop.WORKER_TASK_LEASE_SECONDS > worker_loop.WORKER_TASK_TIMEOUT_SECONDS


def test_worker_loop_requests_the_configured_lease(monkeypatch):
    claimed = []
    monkeypatch.setattr(
        worker_loop.candidate_store,
        "reconcile_pending_publications",
        lambda: {
            "published": 0,
            "recovered": 0,
            "already_present": 0,
            "conflicted": 0,
        },
    )
    monkeypatch.setattr(
        worker_loop,
        "write_worker_heartbeat",
        lambda *args, **kwargs: None,
    )

    def get_next_task(*, lease_seconds):
        claimed.append(lease_seconds)
        return None

    monkeypatch.setattr(worker_loop.task_bus, "get_next_task", get_next_task)

    class StopAfterOnePoll:
        def __init__(self):
            self.checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks > 1

        def wait(self, _seconds):
            return True

    worker_loop._run_worker_loop(stop_event=StopAfterOnePoll())

    assert claimed == [worker_loop.WORKER_TASK_LEASE_SECONDS]
