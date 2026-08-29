"""Burst (drive) grouping.

Pure logic, no file I/O — deliberately kept that way so it can be unit
tested with synthetic MediaMeta objects without needing real camera files.
"""

from __future__ import annotations

from . import config
from .mediameta import MediaMeta


def _sort_key(item: MediaMeta):
    return (item.captured_at, item.file_number if item.file_number is not None else 0)


def _group_photos(photos: list[MediaMeta], max_gap_seconds: float) -> list[list[MediaMeta]]:
    groups: list[list[MediaMeta]] = []
    for photo in sorted(photos, key=_sort_key):
        if not groups:
            groups.append([photo])
            continue

        prev = groups[-1][-1]
        gap = (photo.captured_at - prev.captured_at).total_seconds()

        has_sequence = (
            photo.sequence_number is not None
            and photo.sequence_length is not None
            and photo.sequence_length > 1
        )

        if has_sequence:
            # Sequence numbering restarting at 1 marks a new burst — checked
            # against the current photo's own tags only, so a short burst
            # immediately following a longer one (different sequence_length)
            # still splits correctly. A gap much larger than expected
            # overrides the tags, in case they were mis-parsed (tag names
            # are unverified, see config.py).
            sane_gap = gap <= max_gap_seconds * 5
            starts_new_group = photo.sequence_number == 1 or not sane_gap
        else:
            starts_new_group = gap > max_gap_seconds

        if starts_new_group:
            groups.append([photo])
        else:
            groups[-1].append(photo)

    return groups


def group_bursts(
    items: list[MediaMeta], *, max_gap_seconds: float = config.BURST_MAX_GAP_SECONDS
) -> list[list[MediaMeta]]:
    """Group photos into continuous-shooting bursts; each video is its own group.

    Grouping prefers Sony sequence MakerNotes tags (SequenceImageNumber /
    SequenceLength) when present and consistent between consecutive shots,
    and falls back to clustering by the gap between capture timestamps
    (using SubSecTimeOriginal for sub-second precision) otherwise. A large
    gap always forces a new group even when the sequence tags disagree, as
    a safety net against unexpected tag values.

    Returns groups in chronological order (by each group's first item).
    """
    photos = [i for i in items if i.kind == "photo"]
    videos = [i for i in items if i.kind == "video"]

    groups = _group_photos(photos, max_gap_seconds)
    groups.extend([video] for video in videos)

    groups.sort(key=lambda group: _sort_key(group[0]))
    return groups
