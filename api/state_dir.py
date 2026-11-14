"""Shared state directory for the Agents runtime ledgers.

Semantics mirror api/trace_store.py: CHALAMANDRA_STATE_DIR is the state
root (default ~/.local/state/chalamandra) and the Agents ledgers live under
<root>/agents. Every store must use agents_state_dir() so API and worker
always share the same TaskBus, candidates, acceptances and distributions,
whether the root is the default or an explicit override.
"""

import os
from pathlib import Path


def agents_state_dir() -> Path:
    root = os.getenv("CHALAMANDRA_STATE_DIR", "").strip()
    base = Path(root).expanduser() if root else Path.home() / ".local" / "state" / "chalamandra"
    return base / "agents"
