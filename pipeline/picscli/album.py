"""Pure builder for an album's album.json.

Albums are standalone: each one is published to its own directory and
shared by link, so there is no cross-album index. Deliberately free of
file I/O so it can be unit tested with synthetic Frame/Burst objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class Frame:
    hash: str
    kind: str  # "photo" | "video"
    thumb: str  # path relative to the album dir, e.g. "thumb/<hash>.jpg"
    original: str
    display: str | None  # photo only
    medium: str | None  # photo only: mid-size copy used while shuttling
    video: str | None  # video only (transcoded mp4, relative path)
    width: int | None  # native/unrotated pixel width
    height: int | None
    size_bytes: int
    exif: dict = field(default_factory=dict)


@dataclass(slots=True)
class Burst:
    id: str
    kind: str  # "photo" | "video"
    captured_at: datetime
    thumb_w: int
    thumb_h: int
    frames: list[Frame]
    preview: str | None = None  # animated webp, relative path; None for single-frame photo bursts
    faces: list[str] = field(default_factory=list)  # identity ids visible in this burst
    clip: str | None = None  # real-time mp4, for bursts long enough to be worth one
    cover_index: int = 0


def build_album_json(
    *,
    album_id: str,
    title: str,
    date: str,
    bursts: list[Burst],
    generated_at: datetime,
    faces: list[dict] | None = None,
) -> dict:
    return {
        "id": album_id,
        "title": title,
        "date": date,
        "generatedAt": generated_at.isoformat(),
        "faces": faces or [],
        "bursts": [
            {
                "id": b.id,
                "type": b.kind,
                "capturedAt": b.captured_at.isoformat(),
                "count": len(b.frames),
                "coverIndex": b.cover_index,
                "thumbW": b.thumb_w,
                "thumbH": b.thumb_h,
                "preview": b.preview,
                "clip": b.clip,
                "faces": b.faces,
                "frames": [
                    {
                        "hash": f.hash,
                        "thumb": f.thumb,
                        "medium": f.medium,
                        "display": f.display,
                        "original": f.original,
                        "video": f.video,
                        "w": f.width,
                        "h": f.height,
                        "bytes": f.size_bytes,
                        "exif": f.exif,
                    }
                    for f in b.frames
                ],
            }
            for b in bursts
        ],
    }
