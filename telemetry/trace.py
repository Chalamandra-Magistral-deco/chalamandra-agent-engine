"""Almacén append-only de ModelResponse en formato JSONL."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from adapters.base import ModelResponse


DEFAULT_PATH = Path("logs/traces.jsonl")


class Tracer:
    """Registra cada ModelResponse en un archivo JSONL."""

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, response: ModelResponse) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            **response.model_dump(exclude={"raw"}),
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def iter_records(self, since: datetime | None = None) -> Iterator[dict]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since is not None:
                    ts = datetime.fromisoformat(rec["ts"])
                    if ts < since:
                        continue
                yield rec

    def count(self) -> int:
        if not self.path.exists():
            return 0
        with self.path.open("r", encoding="utf-8") as f:
            return sum(1 for _ in f)
