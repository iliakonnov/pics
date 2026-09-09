import struct

import numpy as np
import pytest

from picscli.rawanalysis import ExposureStats, combine_burst_evs, ev_for_stats, luminance_percentiles, parse_ppm16


def _make_ppm(width: int, height: int, fill: int, maxval: int = 65535) -> bytes:
    header = f"P6\n{width} {height}\n{maxval}\n".encode()
    pixels = struct.pack(f">{width * height * 3}H", *([fill] * (width * height * 3)))
    return header + pixels


def test_parse_ppm16_flat_image():
    data = _make_ppm(4, 3, fill=32768)
    arr = parse_ppm16(data)
    assert arr.shape == (3, 4, 3)
    assert arr.dtype == np.float32
    assert np.allclose(arr, 32768 / 65535, atol=1e-6)


def test_parse_ppm16_rejects_non_p6():
    with pytest.raises(ValueError):
        parse_ppm16(b"P5\n1 1\n255\n\x00")


def test_parse_ppm16_rejects_truncated_data():
    data = _make_ppm(4, 4, fill=1000)[:-10]
    with pytest.raises(ValueError):
        parse_ppm16(data)


def test_luminance_percentiles_flat_image_all_equal():
    arr = np.full((10, 10, 3), 0.5, dtype=np.float32)
    stats = luminance_percentiles(arr)
    assert stats.p50 == pytest.approx(0.5, abs=1e-5)
    assert stats.p995 == pytest.approx(0.5, abs=1e-5)


def test_ev_for_stats_dark_scene_clamps_at_max():
    stats = ExposureStats(p50=0.001, p995=0.01)
    ev = ev_for_stats(stats, ev_min=-1.5, ev_max=2.0, trim=0.0)
    assert ev == 2.0


def test_ev_for_stats_bright_scene_goes_negative():
    stats = ExposureStats(p50=0.6, p995=0.99)
    ev = ev_for_stats(stats, target_p50=0.18, highlight_ceiling=1.5, ev_min=-1.5, ev_max=2.0, trim=0.0)
    assert ev < 0


def test_ev_for_stats_highlight_cap_overrides_midtone_push():
    # Midtones alone would ask for a big lift, but near-clipped highlights
    # (p995 close to 1.0) must cap it well below that.
    stats = ExposureStats(p50=0.02, p995=0.95)
    ev = ev_for_stats(stats, target_p50=0.18, highlight_ceiling=1.5, ev_min=-1.5, ev_max=2.0, trim=0.0)
    assert ev < 1.0


def test_ev_for_stats_black_frame_does_not_explode():
    stats = ExposureStats(p50=0.0, p995=0.0)
    ev = ev_for_stats(stats, ev_min=-1.5, ev_max=2.0)
    assert ev == 2.0


def test_combine_burst_evs_uses_median_robust_to_outlier():
    assert combine_burst_evs([0.5, 0.6, 0.55, 5.0, 0.52]) == pytest.approx(0.55)


def test_combine_burst_evs_single_value():
    assert combine_burst_evs([1.25]) == pytest.approx(1.25)
