"""Almacén append-only de ModelResponse en formato JSONL."""

from __future__ import annotations

import json
import os
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
        """
        Añade una respuesta al archivo JSONL de trazas.

        Args:
            response (ModelResponse): Respuesta normalizada que se debe persistir; el campo
            `raw` se excluye para no guardar el payload crudo del proveedor.

        Returns:
            None.

        Raises:
            OSError: Si no puede abrir, asegurar permisos o escribir el archivo.
            TypeError: Si el contenido del modelo no puede convertirse a JSON.

        Side effects:
            Crea el directorio padre y añade una línea al archivo. Abre el archivo con
            permisos restrictivos (0600) y evita seguir symlinks cuando el sistema lo admite.

        Example:
            tracer.record(response)
        """
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            **response.model_dump(exclude={"raw"}),
        }
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            os.close(fd)
            raise
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def iter_records(self, since: datetime | None = None) -> Iterator[dict]:
        """
        Itera registros JSONL válidos, opcionalmente desde una fecha mínima.

        Args:
            since (datetime | None): Fecha/hora inclusiva de filtro; los registros más
                antiguos se omiten. Las marcas de tiempo deben incluir una zona compatible.

        Yields:
            dict: Registro JSON decodificado que supera el filtro.

        Raises:
            OSError: Si el archivo existe pero no se puede leer.
            KeyError | ValueError: Si falta `ts` o la fecha no se puede analizar cuando se
            solicita el filtro `since`.

        Side effects:
            Lee el archivo local; las líneas vacías y los JSON malformados se omiten.

        Example:
            for record in tracer.iter_records():
                print(record["model"])
        """
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
        """
        Cuenta las líneas físicas del archivo de trazas.

        Returns:
            int: Número de líneas en el archivo; devuelve cero si aún no existe.

        Raises:
            OSError: Si el archivo existe pero no se puede abrir o leer.

        Side effects:
            Lee el archivo local. El conteo incluye líneas corruptas o vacías porque
            cuenta líneas físicas, no registros JSON válidos.

        Example:
            total_lines = tracer.count()
        """
        if not self.path.exists():
            return 0
        with self.path.open("r", encoding="utf-8") as f:
            return sum(1 for _ in f)
