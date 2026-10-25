import os
import stat

import pytest

from adapters.base import ModelResponse, ModelRole
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
