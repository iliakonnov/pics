"""Pure builders for album.json and the root albums.json index.

Deliberately free of file I/O so it can be unit tested with synthetic
Frame/Burst objects — no real photos required.
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
    cover_index: int = 0


def build_album_json(*, album_id: str, title: str, date: str, bursts: list[Burst], generated_at: datetime) -> dict:
    return {
        "id": album_id,
        "title": title,
        "date": date,
        "generatedAt": generated_at.isoformat(),
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
                "frames": [
                    {
                        "hash": f.hash,
                        "thumb": f.thumb,
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


def album_summary(album_json: dict) -> dict:
    """A compact entry for the root albums.json index."""
    bursts = album_json["bursts"]
    cover_burst = bursts[0] if bursts else None
    cover_frame = cover_burst["frames"][cover_burst["coverIndex"]] if cover_burst else None
    frame_count = sum(b["count"] for b in bursts)
    return {
        "id": album_json["id"],
        "title": album_json["title"],
        "date": album_json["date"],
        "cover": f"albums/{album_json['id']}/{cover_frame['thumb']}" if cover_frame else None,
        "coverW": cover_burst["thumbW"] if cover_burst else None,
        "coverH": cover_burst["thumbH"] if cover_burst else None,
        "burstCount": len(bursts),
        "frameCount": frame_count,
    }


def upsert_albums_index(index: dict | None, summary: dict) -> dict:
    """Return a new index dict with `summary` inserted/replacing its album id, sorted by date."""
    albums = [a for a in (index or {}).get("albums", []) if a["id"] != summary["id"]]
    albums.append(summary)
    albums.sort(key=lambda a: (a["date"], a["id"]))
    return {"albums": albums}
