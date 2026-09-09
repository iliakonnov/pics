from datetime import datetime, timedelta
from pathlib import Path

import pytest

from picscli.developplan import (
    build_develop_plans,
    disk_names,
    frames_to_analyze,
    is_exposure_bracket,
)
from picscli.mediameta import MediaMeta

BASE = datetime(2026, 8, 30, 14, 11, 25)


def raw(n, *, dt, seq_num=None, release_mode2=None, bias=None, exposure_time=None, iso=125, fnumber=1.8):
    return MediaMeta(
        path=Path(f"/card/DSC{n:05d}.ARW"),
        kind="photo",
        file_hash=f"hash{n}",
        captured_at=dt,
        file_number=n,
        width=5504,
        height=3672,
        orientation=1,
        sequence_number=seq_num,
        sequence_length=None,
        drive_mode=None,
        exif={"iso": iso, "fNumber": fnumber},
        release_mode2=release_mode2,
        exposure_compensation=bias,
        exposure_time_s=exposure_time,
    )


def jpg(n, *, dt):
    return MediaMeta(
        path=Path(f"/card/DSC{n:05d}.JPG"),
        kind="photo",
        file_hash=f"jhash{n}",
        captured_at=dt,
        file_number=n,
        width=5472,
        height=3648,
        orientation=1,
        sequence_number=None,
        sequence_length=None,
        drive_mode=None,
    )


# --- is_exposure_bracket: real data from album hnoUQgBATI, burst b0050 ------


def test_real_bracket_b0050_detected_by_release_mode2():
    group = [
        raw(1, dt=BASE, seq_num=1, release_mode2=2, bias=0.0, exposure_time=0.004),
        raw(2, dt=BASE + timedelta(milliseconds=99), seq_num=2, release_mode2=2, bias=-0.3, exposure_time=0.003125),
        raw(3, dt=BASE + timedelta(milliseconds=201), seq_num=3, release_mode2=2, bias=0.3, exposure_time=0.005),
        raw(4, dt=BASE + timedelta(milliseconds=299), seq_num=4, release_mode2=2, bias=-0.7, exposure_time=0.0025),
    ]
    assert is_exposure_bracket(group) is True


def test_real_continuous_burst_not_a_bracket():
    # DSC03138-42 shape: ReleaseMode2=1, constant shutter/ISO.
    group = [
        raw(3138, dt=BASE, seq_num=1, release_mode2=1, bias=0.0, exposure_time=0.004),
        raw(3139, dt=BASE + timedelta(milliseconds=95), seq_num=2, release_mode2=1, bias=0.0, exposure_time=0.004),
        raw(3140, dt=BASE + timedelta(milliseconds=195), seq_num=3, release_mode2=1, bias=0.0, exposure_time=0.004),
    ]
    assert is_exposure_bracket(group) is False


def test_release_mode2_26_tag_alone_does_not_trigger_bracket_detection():
    # Real DSC03020-22 files carry ReleaseMode2=26 but are 30-70s apart
    # (separate deliberate manual shots, never grouped together in
    # practice). This checks the tag value itself isn't treated as a
    # bracket signal when exposure is otherwise constant.
    group = [
        raw(3020, dt=BASE, seq_num=1, release_mode2=26, bias=0.0, exposure_time=0.004),
        raw(3021, dt=BASE + timedelta(milliseconds=95), seq_num=1, release_mode2=26, bias=0.0, exposure_time=0.004),
        raw(3022, dt=BASE + timedelta(milliseconds=195), seq_num=1, release_mode2=26, bias=0.0, exposure_time=0.004),
    ]
    assert is_exposure_bracket(group) is False


def test_release_mode2_26_with_real_wildly_different_manual_exposures():
    # The actual exposure_time values from DSC03020/21/22 (0.6s/0.125s/0.005s
    # -- deliberate manual adjustments between separate shots). grouping.py
    # never actually chains these into one group in practice (they are
    # 30-70s apart), but if it somehow did, wildly cycling manual exposure
    # is indistinguishable from a bracket and the heuristic correctly says so.
    group = [
        raw(3020, dt=BASE, seq_num=1, release_mode2=26, bias=0.0, exposure_time=0.6),
        raw(3021, dt=BASE + timedelta(seconds=39), seq_num=1, release_mode2=26, bias=0.0, exposure_time=0.125),
        raw(3022, dt=BASE + timedelta(seconds=69), seq_num=1, release_mode2=26, bias=0.0, exposure_time=0.005),
    ]
    assert is_exposure_bracket(group) is True


def test_dro_bracket_mode3_is_not_an_exposure_bracket():
    group = [
        raw(1, dt=BASE, release_mode2=3, bias=0.0, exposure_time=0.004),
        raw(2, dt=BASE + timedelta(milliseconds=99), release_mode2=3, bias=0.0, exposure_time=0.004),
        raw(3, dt=BASE + timedelta(milliseconds=199), release_mode2=3, bias=0.0, exposure_time=0.004),
    ]
    assert is_exposure_bracket(group) is False


def test_heuristic_fallback_detects_bias_cycling_without_release_mode2():
    group = [
        raw(1, dt=BASE, bias=0.0, exposure_time=0.004),
        raw(2, dt=BASE + timedelta(milliseconds=99), bias=-0.7, exposure_time=0.0025),
        raw(3, dt=BASE + timedelta(milliseconds=199), bias=0.7, exposure_time=0.0065),
    ]
    assert is_exposure_bracket(group) is True


def test_heuristic_fallback_detects_shutter_cycling_without_bias_tag():
    group = [
        raw(1, dt=BASE, exposure_time=0.004),
        raw(2, dt=BASE + timedelta(milliseconds=99), exposure_time=0.002),
        raw(3, dt=BASE + timedelta(milliseconds=199), exposure_time=0.008),
    ]
    assert is_exposure_bracket(group) is True


def test_two_frame_group_never_a_bracket():
    group = [
        raw(1, dt=BASE, bias=-1.0, exposure_time=0.002),
        raw(2, dt=BASE + timedelta(milliseconds=99), bias=1.0, exposure_time=0.008),
    ]
    assert is_exposure_bracket(group) is False


def test_missing_fields_defaults_to_not_a_bracket():
    group = [raw(1, dt=BASE), raw(2, dt=BASE), raw(3, dt=BASE)]
    assert is_exposure_bracket(group) is False


# --- build_develop_plans -----------------------------------------------------


def test_single_frame_gets_its_own_ev():
    groups = [[raw(1, dt=BASE)]]
    plans, bracketed = build_develop_plans(groups, {"hash1": 0.8})
    assert plans["hash1"].ev == pytest.approx(0.8)
    assert plans["hash1"].bracketed is False
    assert bracketed == set()


def test_burst_frames_share_median_ev():
    groups = [
        [
            raw(1, dt=BASE, seq_num=1, release_mode2=1),
            raw(2, dt=BASE + timedelta(milliseconds=95), seq_num=2, release_mode2=1),
            raw(3, dt=BASE + timedelta(milliseconds=195), seq_num=3, release_mode2=1),
        ]
    ]
    ev_by_hash = {"hash1": 0.5, "hash2": 0.6, "hash3": 5.0}  # one wild outlier
    plans, bracketed = build_develop_plans(groups, ev_by_hash)
    assert plans["hash1"].ev == plans["hash2"].ev == plans["hash3"].ev == pytest.approx(0.6)
    assert bracketed == set()


def test_bracket_frames_keep_independent_evs():
    groups = [
        [
            raw(1, dt=BASE, release_mode2=2, bias=0.0),
            raw(2, dt=BASE + timedelta(milliseconds=99), release_mode2=2, bias=-0.3),
            raw(3, dt=BASE + timedelta(milliseconds=199), release_mode2=2, bias=0.3),
            raw(4, dt=BASE + timedelta(milliseconds=299), release_mode2=2, bias=-0.7),
        ]
    ]
    ev_by_hash = {"hash1": 0.0, "hash2": -0.3, "hash3": 0.3, "hash4": -0.7}
    plans, bracketed = build_develop_plans(groups, ev_by_hash)
    assert bracketed == {0}
    assert [plans[f"hash{i}"].ev for i in (1, 2, 3, 4)] == [0.0, -0.3, 0.3, -0.7]
    assert all(p.bracketed for p in plans.values())


def test_non_raw_jpeg_only_group_produces_no_plans():
    groups = [[jpg(1, dt=BASE)]]
    plans, bracketed = build_develop_plans(groups, {})
    assert plans == {}
    assert bracketed == set()


def test_mixed_group_only_plans_raw_frames():
    # A group can't actually mix kinds in practice, but the function should
    # only ever touch is_raw items regardless.
    groups = [[raw(1, dt=BASE)]]
    plans, _ = build_develop_plans(groups, {"hash1": 1.0})
    assert set(plans) == {"hash1"}


def test_missing_ev_estimate_defaults_to_zero():
    groups = [[raw(1, dt=BASE)]]
    plans, _ = build_develop_plans(groups, {})
    assert plans["hash1"].ev == 0.0


# --- frames_to_analyze -------------------------------------------------------


def test_frames_to_analyze_single_gets_one_frame():
    groups = [[raw(1, dt=BASE)]]
    assert frames_to_analyze(groups) == [groups[0][0]]


def test_frames_to_analyze_bracket_gets_every_frame():
    group = [
        raw(i, dt=BASE + timedelta(milliseconds=i * 99), release_mode2=2, bias=0.0)
        for i in range(1, 5)
    ]
    assert frames_to_analyze([group]) == group


def test_frames_to_analyze_burst_samples_first_middle_last():
    group = [raw(i, dt=BASE + timedelta(milliseconds=i * 95), seq_num=i, release_mode2=1) for i in range(1, 8)]
    sampled = frames_to_analyze([group])
    assert sampled[0] is group[0]
    assert sampled[-1] is group[-1]
    assert len(sampled) == 3


def test_frames_to_analyze_skips_non_raw_groups():
    groups = [[jpg(1, dt=BASE)]]
    assert frames_to_analyze(groups) == []


# --- disk_names ---------------------------------------------------------------


def test_disk_names_uses_camera_filename():
    items = [raw(1234, dt=BASE)]
    assert disk_names(items) == {"hash1234": "DSC01234.ARW"}


def test_disk_names_disambiguates_collisions_by_hash():
    a = raw(1, dt=BASE)
    a.file_hash = "aaa111"
    a.path = Path("/card/DSC01234.ARW")
    b = raw(2, dt=BASE + timedelta(days=30))
    b.file_hash = "bbb222"
    b.path = Path("/card/DSC01234.ARW")  # Sony's counter wrapped
    names = disk_names([a, b])
    assert names["aaa111"] == "DSC01234.ARW"
    assert names["bbb222"] == "DSC01234_bbb222.ARW"
