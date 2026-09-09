"""Filesystem scanning: find JPEG/RAW/video files on a card and hash them.

JPEG photos, Sony ARW raw files and video files are considered — THM
thumbnails and Sony's .MOFF/.MODD sidecar files are ignored by extension.
The whole card root is walked recursively so both DCIM/1xxMSDCF (photos)
and PRIVATE/M4ROOT/CLIP (XAVC S video) are picked up in one pass.

When a raw file and a camera JPEG share a filename (the norm during the
switch from JPEG to raw-only shooting), the JPEG is dropped: the raw gets
developed into the album original instead, and importing both would put
two near-identical frames in the same burst.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import config


def drop_jpeg_when_raw_sibling(paths: list[Path]) -> list[Path]:
    """Keep the ARW and drop the JPEG whenever DSC01234.ARW and
    DSC01234.JPG sit in the same directory. Pure and order-preserving."""
    raw_stems = {
        (p.parent, p.stem.lower())
        for p in paths
        if p.suffix.lower() in config.RAW_EXTENSIONS
    }
    return [
        p
        for p in paths
        if not (p.suffix.lower() in config.PHOTO_EXTENSIONS and (p.parent, p.stem.lower()) in raw_stems)
    ]


def find_media_files(root: Path) -> list[Path]:
    """Every photo/raw/video file on the card, unpaired.

    Callers that keep raw files should run the result through
    drop_jpeg_when_raw_sibling(); `--skip-raw` importer runs intentionally
    skip that step (after removing ARWs) so a card's camera JPEGs still
    import even though raw siblings exist.
    """
    exts = config.PHOTO_EXTENSIONS | config.RAW_EXTENSIONS | config.VIDEO_EXTENSIONS
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
