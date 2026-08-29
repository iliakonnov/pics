from datetime import datetime, timedelta
from pathlib import Path

from picscli.grouping import group_bursts
from picscli.mediameta import MediaMeta

BASE = datetime(2026, 8, 29, 14, 0, 0)


def photo(n, *, dt, seq_num=None, seq_len=None):
    return MediaMeta(
        path=Path(f"DSC{n:05d}.JPG"),
        kind="photo",
        file_hash=f"hash{n}",
        captured_at=dt,
        file_number=n,
        width=5472,
        height=3648,
        orientation=1,
        sequence_number=seq_num,
        sequence_length=seq_len,
        drive_mode=None,
    )


def video(n, *, dt):
    return MediaMeta(
        path=Path(f"C{n:04d}.MP4"),
        kind="video",
        file_hash=f"vhash{n}",
        captured_at=dt,
        file_number=n,
        width=1920,
        height=1080,
        orientation=None,
        sequence_number=None,
        sequence_length=None,
        drive_mode=None,
    )


def test_single_shots_far_apart_stay_separate():
    items = [
        photo(1, dt=BASE),
        photo(2, dt=BASE + timedelta(seconds=30)),
        photo(3, dt=BASE + timedelta(minutes=5)),
    ]
    groups = group_bursts(items)
    assert [len(g) for g in groups] == [1, 1, 1]


def test_rapid_shots_without_sequence_tags_cluster_by_gap():
    items = [
        photo(1, dt=BASE),
        photo(2, dt=BASE + timedelta(seconds=0.2)),
        photo(3, dt=BASE + timedelta(seconds=0.4)),
        photo(4, dt=BASE + timedelta(seconds=10)),
    ]
    groups = group_bursts(items)
    assert [len(g) for g in groups] == [3, 1]


def test_sequence_tags_restart_new_burst_even_with_small_gap():
    items = [
        photo(1, dt=BASE, seq_num=1, seq_len=3),
        photo(2, dt=BASE + timedelta(seconds=0.1), seq_num=2, seq_len=3),
        photo(3, dt=BASE + timedelta(seconds=0.2), seq_num=3, seq_len=3),
        # New burst starts right away (seq_num resets to 1) despite a tiny gap.
        photo(4, dt=BASE + timedelta(seconds=0.3), seq_num=1, seq_len=2),
        photo(5, dt=BASE + timedelta(seconds=0.4), seq_num=2, seq_len=2),
    ]
    groups = group_bursts(items)
    assert [len(g) for g in groups] == [3, 2]


def test_insane_gap_overrides_sequence_tags():
    items = [
        photo(1, dt=BASE, seq_num=1, seq_len=5),
        # Tags claim mid-sequence but the gap is huge -> treat as a new group.
        photo(2, dt=BASE + timedelta(minutes=10), seq_num=2, seq_len=5),
    ]
    groups = group_bursts(items)
    assert [len(g) for g in groups] == [1, 1]


def test_videos_are_always_singleton_bursts():
    items = [
        photo(1, dt=BASE),
        video(1, dt=BASE + timedelta(seconds=0.1)),
        video(2, dt=BASE + timedelta(seconds=0.2)),
    ]
    groups = group_bursts(items)
    assert [len(g) for g in groups] == [1, 1, 1]
    kinds = [g[0].kind for g in groups]
    assert kinds == ["photo", "video", "video"]


def test_groups_are_returned_in_chronological_order():
    items = [
        photo(3, dt=BASE + timedelta(minutes=5)),
        photo(1, dt=BASE),
        photo(2, dt=BASE + timedelta(seconds=30)),
    ]
    groups = group_bursts(items)
    starts = [g[0].captured_at for g in groups]
    assert starts == sorted(starts)


# --- regression tests built from real Sony ZV-1 files -------------------
#
# Values below are what exiftool 13.55 actually reports (with -n) for the
# sample set in ~/Pictures/test. The camera does NOT report a burst length:
# SequenceLength is 0 while shooting continuously, and SequenceNumber
# counts 1, 2, 3... Single shots report SequenceNumber 0.


def test_zv1_burst_frames_group_by_sequence_number():
    items = [
        photo(3137, dt=BASE, seq_num=1, seq_len=0),
        photo(3138, dt=BASE + timedelta(milliseconds=95), seq_num=2, seq_len=0),
        photo(3139, dt=BASE + timedelta(milliseconds=195), seq_num=3, seq_len=0),
    ]
    assert [len(g) for g in group_bursts(items)] == [3]


def test_zv1_two_bursts_under_a_second_apart_still_split():
    """The case a pure time gap cannot resolve.

    DSC03159 (…24.507, seq 3) and DSC03160 (…25.071, seq 1) are 0.564s
    apart — inside BURST_MAX_GAP_SECONDS — but are genuinely two separate
    presses of the shutter, and the sequence numbering proves it.
    """
    items = [
        photo(3157, dt=BASE, seq_num=1, seq_len=0),
        photo(3158, dt=BASE + timedelta(milliseconds=95), seq_num=2, seq_len=0),
        photo(3159, dt=BASE + timedelta(milliseconds=195), seq_num=3, seq_len=0),
        photo(3160, dt=BASE + timedelta(milliseconds=759), seq_num=1, seq_len=0),
        photo(3161, dt=BASE + timedelta(milliseconds=855), seq_num=2, seq_len=0),
        photo(3162, dt=BASE + timedelta(milliseconds=955), seq_num=3, seq_len=0),
    ]
    assert [len(g) for g in group_bursts(items)] == [3, 3]


def test_zv1_single_shots_report_sequence_zero_and_stay_separate():
    """ReleaseMode2=8 single shots: SequenceNumber is 0 on every frame."""
    items = [
        photo(3030, dt=BASE, seq_num=0, seq_len=1),
        photo(3031, dt=BASE + timedelta(seconds=26), seq_num=0, seq_len=1),
    ]
    assert [len(g) for g in group_bursts(items)] == [1, 1]


def test_zv1_single_shots_stay_separate_even_when_fired_quickly():
    items = [
        photo(3030, dt=BASE, seq_num=0, seq_len=1),
        photo(3031, dt=BASE + timedelta(milliseconds=300), seq_num=0, seq_len=1),
    ]
    assert [len(g) for g in group_bursts(items)] == [1, 1]


def test_zv1_third_mode_repeats_sequence_number_one():
    """ReleaseMode2=26 reports SequenceNumber 1 on every frame; repeats of
    1 must not chain into a burst."""
    items = [
        photo(3020, dt=BASE, seq_num=1, seq_len=0),
        photo(3021, dt=BASE + timedelta(milliseconds=400), seq_num=1, seq_len=0),
    ]
    assert [len(g) for g in group_bursts(items)] == [1, 1]
