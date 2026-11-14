"""Indexed access to append-only JSONL ledgers.

The JSONL file remains the source of truth. SQLite stores rebuildable byte offsets
and selected lookup keys so readers do not rescan the complete audit history.
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from api.locking import acquire_exclusive_lock


class JsonlIndex:
    def __init__(
        self,
        path: Path,
        fields: tuple[str, ...],
        lock_timeout_seconds: float = 10.0,
    ) -> None:
        if not fields or len(fields) > 2:
            raise ValueError("JsonlIndex supports one or two indexed fields")
        self.path = Path(path)
        self.fields = fields
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.index_path = self.path.with_suffix(self.path.suffix + ".index.sqlite3")
        # F-7: espera acotada por el lock del ledger (antes LOCK_EX sin
        # timeout: un poseedor colgado bloqueaba escrituras para siempre).
        self.lock_timeout_seconds = lock_timeout_seconds

    @contextmanager
    def locked(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        with self.lock_path.open("a", encoding="utf-8") as lock_stream:
            os.chmod(self.lock_path, 0o600)
            acquire_exclusive_lock(
                lock_stream.fileno(), timeout_seconds=self.lock_timeout_seconds
            )
            db = sqlite3.connect(str(self.index_path), timeout=20)
            os.chmod(self.index_path, 0o600)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS records ("
                    "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "byte_offset INTEGER NOT NULL, byte_length INTEGER NOT NULL, "
                    "key0 TEXT, key1 TEXT)"
                )
                db.execute("CREATE INDEX IF NOT EXISTS idx_records_key0_seq ON records(key0, seq)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_records_key1_seq ON records(key1, seq)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_records_key0_key1_seq ON records(key0, key1, seq)")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS metadata ("
                    "id INTEGER PRIMARY KEY CHECK(id=1), size INTEGER NOT NULL, "
                    "mtime_ns INTEGER NOT NULL, inode INTEGER NOT NULL)"
                )
                self._synchronize(db)
                yield db
                db.commit()
            finally:
                db.close()
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)

    def _synchronize(self, db: sqlite3.Connection) -> None:
        stat = self.path.stat() if self.path.exists() else None
        current = (
            stat.st_size if stat else 0,
            stat.st_mtime_ns if stat else 0,
            stat.st_ino if stat else 0,
        )
        saved = db.execute(
            "SELECT size, mtime_ns, inode FROM metadata WHERE id=1"
        ).fetchone()
        if saved == current:
            return

        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM records")
        if self.path.exists():
            with self.path.open("rb") as stream:
                while True:
                    offset = stream.tell()
                    line = stream.readline()
                    if not line:
                        break
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ValueError(
                            f"{self.path.name} inválido en byte {offset}"
                        ) from exc
                    if not isinstance(record, dict):
                        raise ValueError(f"Non-object JSONL record in {self.path.name}")
                    values = [record.get(field) for field in self.fields]
                    db.execute(
                        "INSERT INTO records(byte_offset, byte_length, key0, key1) "
                        "VALUES (?, ?, ?, ?)",
                        (offset, len(line), self._text(values[0]),
                         self._text(values[1]) if len(values) > 1 else None),
                    )
        stat = self.path.stat() if self.path.exists() else None
        current = (
            stat.st_size if stat else 0,
            stat.st_mtime_ns if stat else 0,
            stat.st_ino if stat else 0,
        )
        db.execute(
            "INSERT INTO metadata(id, size, mtime_ns, inode) VALUES(1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET size=excluded.size, "
            "mtime_ns=excluded.mtime_ns, inode=excluded.inode",
            current,
        )
        db.commit()

    @staticmethod
    def _text(value: Any) -> str | None:
        return value if isinstance(value, str) else None

    def _decode_rows(
        self, rows: list[tuple[int, int]]
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        with self.path.open("rb") as stream:
            for offset, length in rows:
                stream.seek(offset)
                payload = stream.read(length)
                try:
                    record = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"Indexed JSONL record became invalid in {self.path.name}"
                    ) from exc
                if not isinstance(record, dict):
                    raise ValueError(f"Non-object JSONL record in {self.path.name}")
                results.append(record)
        return results

    def find(
        self, db: sqlite3.Connection, field: str, value: str
    ) -> list[dict[str, Any]]:
        if field not in self.fields:
            raise ValueError(f"Field is not indexed: {field}")
        column = "key" + str(self.fields.index(field))
        rows = db.execute(
            f"SELECT byte_offset, byte_length FROM records "
            f"WHERE {column}=? ORDER BY seq ASC",
            (value,),
        ).fetchall()
        return self._decode_rows(rows) if rows else []

    def find_pair(
        self, db: sqlite3.Connection, first: str, second: str
    ) -> list[dict[str, Any]]:
        if len(self.fields) != 2:
            raise ValueError("Pair lookups require two indexed fields")
        rows = db.execute(
            "SELECT byte_offset, byte_length FROM records "
            "WHERE key0=? AND key1=? ORDER BY seq ASC",
            (first, second),
        ).fetchall()
        return self._decode_rows(rows) if rows else []

    def append(self, db: sqlite3.Connection, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(
                record, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ) + "\n"
        ).encode("utf-8")
        with self.path.open("ab") as stream:
            offset = stream.tell()
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(self.path, 0o600)
        values = [record.get(field) for field in self.fields]
        db.execute(
            "INSERT INTO records(byte_offset, byte_length, key0, key1) "
            "VALUES (?, ?, ?, ?)",
            (offset, len(encoded), self._text(values[0]),
             self._text(values[1]) if len(values) > 1 else None),
        )
        stat = self.path.stat()
        db.execute(
            "INSERT INTO metadata(id, size, mtime_ns, inode) VALUES(1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET size=excluded.size, "
            "mtime_ns=excluded.mtime_ns, inode=excluded.inode",
            (stat.st_size, stat.st_mtime_ns, stat.st_ino),
        )

    def latest_by_key(
        self, db: sqlite3.Connection, field: str
    ) -> list[dict[str, Any]]:
        """Return the newest record per indexed key without parsing old ledger entries."""
        if field not in self.fields:
            raise ValueError(f"Field is not indexed: {field}")
        column = "key" + str(self.fields.index(field))
        rows = db.execute(
            "SELECT record.byte_offset, record.byte_length "
            "FROM records AS record "
            "JOIN ("
            f"SELECT {column}, MAX(seq) AS latest_seq FROM records "
            f"WHERE {column} IS NOT NULL GROUP BY {column}"
            ") AS latest ON latest.latest_seq = record.seq "
            "ORDER BY record.seq ASC"
        ).fetchall()
        return self._decode_rows(rows) if rows else []
