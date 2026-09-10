"""Shared media metadata model used by scanning, grouping and album building."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import config


@dataclass(slots=True)
class MediaMeta:
    path: Path
    kind: str  # "photo" | "video"
    file_hash: str
    captured_at: datetime
    file_number: int | None
    width: int | None
    height: int | None
    orientation: int | None
    sequence_number: int | None
    sequence_length: int | None
    drive_mode: str | None
    exif: dict = field(default_factory=dict)
    # Raw-development inputs (None for JPEG sources and older imports).
    release_mode2: int | None = None
    exposure_compensation: float | None = None
    exposure_time_s: float | None = None
    focal_length_mm: float | None = None

    @property
    def ext(self) -> str:
        return self.path.suffix.lower()

    @property
    def is_raw(self) -> bool:
        return self.ext in config.RAW_EXTENSIONS
