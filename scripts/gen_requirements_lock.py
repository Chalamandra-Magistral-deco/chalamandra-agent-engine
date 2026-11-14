"""Genera requirements.lock: cierre transitivo exacto de las dependencias
de runtime de pyproject.toml, con las versiones instaladas en el entorno
donde se ejecuta.

Uso (dentro del venv que pasó la suite)::

    python scripts/gen_requirements_lock.py > requirements.lock

El lock resultante debe verificarse antes de comprometerse:

    1. ``pip install --dry-run --ignore-installed -r requirements.lock``
       (todas las versiones deben resolverse en el índice).
    2. venv limpio: instalar el lock + ``pip install --no-deps -e .``
       + pytest, y correr la suite completa.

No incluye dependencias de dev/test (pytest, ruff, mypy): viven en
``pyproject.toml`` bajo ``[project.optional-dependencies]`` (``dev``).
Regenerar tras cada cambio de dependencias de runtime.
"""

import re
import sys
import tomllib
from importlib.metadata import distribution, distributions, version
from packaging.requirements import Requirement
from pathlib import Path


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _top_level_deps() -> list[str]:
    pyproject = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text())
    return [Requirement(d).name for d in pyproject["project"]["dependencies"]]


def main() -> int:
    by_norm = {}
    for dist in distributions():
        name = dist.metadata["Name"]
        if name:
            by_norm[_norm(name)] = name

    seen: dict[str, str] = {}
    stack = [_norm(name) for name in _top_level_deps()]
    while stack:
        name = stack.pop()
        if name in seen or name not in by_norm:
            continue
        real = by_norm[name]
        seen[name] = real
        for req_text in distribution(real).requires or []:
            req = Requirement(req_text)
            # Sin extras: los markers de extra (test/dev/cli) no forman
            # parte del cierre de runtime.
            if req.marker is not None and not req.marker.evaluate({"extra": ""}):
                continue
            stack.append(_norm(req.name))

    lines = sorted(f"{seen[n]}=={version(seen[n])}" for n in seen)
    print("# requirements.lock — cierre exacto de dependencias de runtime")
    print("# Generado con scripts/gen_requirements_lock.py desde el venv que")
    print("# pasó la suite. Método: cierre transitivo de las dependencias de")
    print("# [project] en pyproject.toml (markers de extras excluidos), con")
    print("# las versiones instaladas en ese venv.")
    print("# No incluye dev/test (pytest, ruff, mypy): ver pyproject [dev].")
    print("# Python 3.12. Regenerar tras cada cambio de dependencias.")
    print("\n".join(lines))
    print(f"# total: {len(lines)} paquetes", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
