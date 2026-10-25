"""Primitivas compartidas para persistencia JSON Lines del motor."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def state_dir() -> Path:
    """Devuelve el directorio de estado compartido del motor."""
    return Path.home() / ".local" / "state" / "chalamandra" / "agents"


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Añade un registro JSONL conservando la representación existente."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Lee registros JSONL e ignora líneas vacías o JSON malformado."""
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records
