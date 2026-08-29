"""Filesystem scanning: find JPEG/video files on a card and hash them.

Only JPEG photos and video files are considered — RAW, THM thumbnails and
Sony's .MOFF/.MODD sidecar files are ignored by extension. The whole card
root is walked recursively so both DCIM/1xxMSDCF (photos) and
PRIVATE/M4ROOT/CLIP (XAVC S video) are picked up in one pass.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import config


def find_media_files(root: Path) -> list[Path]:
    exts = config.PHOTO_EXTENSIONS | config.VIDEO_EXTENSIONS
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in exts and not p.name.startswith("._")
    )


def hash_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
