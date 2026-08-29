"""Burst (drive) grouping.

Pure logic, no file I/O — deliberately kept that way so it can be unit
tested with synthetic MediaMeta objects without needing real camera files.
"""

from __future__ import annotations

from . import config
from .mediameta import MediaMeta


def _sort_key(item: MediaMeta):
    return (item.captured_at, item.file_number if item.file_number is not None else 0)


def _starts_new_group(prev: MediaMeta, photo: MediaMeta, max_gap_seconds: float) -> bool:
    gap = (photo.captured_at - prev.captured_at).total_seconds()

    # Sanity net: nothing this far apart is one burst, whatever the tags say.
    if gap > max_gap_seconds * 5:
        return True

    if prev.sequence_number is not None and photo.sequence_number is not None:
        # The ZV-1 numbers continuous-shooting frames 1, 2, 3...; single
        # shots report 0 and other modes repeat a constant. So only a
        # strict +1 step (onto at least frame 2) continues a burst, which
        # correctly splits two bursts fired within a second of each other
        # — something a pure time gap cannot do.
        return not (photo.sequence_number >= 2 and photo.sequence_number == prev.sequence_number + 1)

    # No usable tags (another camera, stripped metadata): fall back to time.
    return gap > max_gap_seconds


def _group_photos(photos: list[MediaMeta], max_gap_seconds: float) -> list[list[MediaMeta]]:
    groups: list[list[MediaMeta]] = []
    for photo in sorted(photos, key=_sort_key):
        if groups and not _starts_new_group(groups[-1][-1], photo, max_gap_seconds):
            groups[-1].append(photo)
        else:
            groups.append([photo])
    return groups


def group_bursts(
    items: list[MediaMeta], *, max_gap_seconds: float = config.BURST_MAX_GAP_SECONDS
) -> list[list[MediaMeta]]:
    """Group photos into continuous-shooting bursts; each video is its own group.

    Driven by Sony's SequenceNumber MakerNote (see config.py for the
    verified values), falling back to clustering on the gap between
    capture timestamps — which use SubSecTimeOriginal, so they resolve
    the ~95ms between frames of a 24fps burst.

    Returns groups in chronological order (by each group's first item).
    """
    photos = [i for i in items if i.kind == "photo"]
    videos = [i for i in items if i.kind == "video"]

    groups = _group_photos(photos, max_gap_seconds)
    groups.extend([video] for video in videos)

    groups.sort(key=lambda group: _sort_key(group[0]))
    return groups
