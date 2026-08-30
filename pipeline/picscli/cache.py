"""Remember expensive derived results so a re-import doesn't redo them.

Cover selection and face grouping are the two steps that cost real time:
minutes of every core at full tilt for an album of any size. Everything
else in an import is cheap or already skipped when its output file exists,
but these two produce metadata rather than files, so they used to run
again on every pass.

A cache entry is keyed by a fingerprint of exactly what went into it — the
frames it looked at and the settings that shaped the answer — so changing
either recomputes, and changing neither does not.

Cache files live in the album directory but are never published: they are
working state, not something a viewer needs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SUFFIX = ".cache.json"


def path_for(album_dir: Path, name: str) -> Path:
    return album_dir / f"{name}{SUFFIX}"


def fingerprint(payload: dict) -> str:
    """A stable digest of the inputs a cached answer depends on."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def load(path: Path, key: str) -> dict | None:
    if not path.is_file():
        return None
    try:
        stored = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if stored.get("key") != key:
        return None
    return stored.get("data")


def save(path: Path, key: str, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"key": key, "data": data}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
