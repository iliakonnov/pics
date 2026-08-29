"""Local sqlite state: dedup by content hash and idempotency across reruns.

Two jobs:
  1. Global dedup — a file already archived (by sha256 content hash) under
     any album is skipped on a later import, even if the camera reused a
     filename after wraparound or the file was copied from a different
     folder on the card.
  2. Resumability — track which files have been processed (converted) and
     uploaded so a crashed/interrupted `import` or `upload` run can just be
     re-run.

Upload state here is a local hint only; `upload.py` also does its own
skip-if-exists check against S3 directly, so losing this database never
causes silent re-publishing gaps.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS albums (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    source_path TEXT NOT NULL,
    album_id TEXT NOT NULL,
    burst_id TEXT NOT NULL,
    frame_index INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    processed_at TEXT,
    uploaded_at TEXT,
    FOREIGN KEY (album_id) REFERENCES albums (id)
);

CREATE INDEX IF NOT EXISTS idx_files_album ON files (album_id);
"""


@dataclass(slots=True)
class FileRecord:
    hash: str
    kind: str
    source_path: str
    album_id: str
    burst_id: str
    frame_index: int
    captured_at: str
    processed_at: str | None
    uploaded_at: str | None


class Manifest:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def __enter__(self) -> "Manifest":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self._conn.commit()
        self._conn.close()

    # -- albums --------------------------------------------------------

    def ensure_album(self, album_id: str, title: str, date: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO albums (id, title, date, created_at) VALUES (?, ?, ?, ?)",
            (album_id, title, date, datetime.now().isoformat()),
        )

    def album_exists(self, album_id: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM albums WHERE id = ?", (album_id,)).fetchone()
        return row is not None

    def list_albums(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM albums ORDER BY date, created_at").fetchall()

    # -- files -----------------------------------------------------------

    def find_by_hash(self, file_hash: str) -> FileRecord | None:
        row = self._conn.execute("SELECT * FROM files WHERE hash = ?", (file_hash,)).fetchone()
        return FileRecord(**dict(row)) if row else None

    def register_file(
        self,
        *,
        file_hash: str,
        kind: str,
        source_path: str,
        album_id: str,
        burst_id: str,
        frame_index: int,
        captured_at: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO files
                (hash, kind, source_path, album_id, burst_id, frame_index, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (file_hash, kind, source_path, album_id, burst_id, frame_index, captured_at),
        )

    def mark_processed(self, file_hash: str) -> None:
        self._conn.execute(
            "UPDATE files SET processed_at = ? WHERE hash = ?",
            (datetime.now().isoformat(), file_hash),
        )

    def mark_uploaded(self, file_hash: str) -> None:
        self._conn.execute(
            "UPDATE files SET uploaded_at = ? WHERE hash = ?",
            (datetime.now().isoformat(), file_hash),
        )

    def is_processed(self, file_hash: str) -> bool:
        row = self._conn.execute(
            "SELECT processed_at FROM files WHERE hash = ?", (file_hash,)
        ).fetchone()
        return bool(row and row["processed_at"])

    def files_for_album(self, album_id: str) -> list[FileRecord]:
        rows = self._conn.execute(
            "SELECT * FROM files WHERE album_id = ? ORDER BY burst_id, frame_index",
            (album_id,),
        ).fetchall()
        return [FileRecord(**dict(r)) for r in rows]

    def commit(self) -> None:
        self._conn.commit()
