"""Shared media metadata model used by scanning, grouping and album building."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


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

    @property
    def ext(self) -> str:
        return self.path.suffix.lower()
