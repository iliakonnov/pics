"""Estimate a per-photo exposure correction from the raw pixel data.

The pure math (parse_ppm16 / luminance_percentiles / ev_for_stats /
combine_burst_evs) has no I/O and is unit tested directly. estimate_ev is
the only impure entry point: it shells out to LibRaw's dcraw_emu for a
half-size, camera-white-balanced, linear 16-bit decode (~0.5s/frame,
verified against real ZV-1 ARWs) and reduces it to one EV number.
"""

from __future__ import annotations

import math
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import config

ANALYZER_VERSION = "1"

_LUMA_WEIGHTS = (0.2126, 0.7152, 0.0722)  # Rec.709


@dataclass(slots=True)
class ExposureStats:
    p50: float  # median linear luminance, [0, 1]
    p995: float  # 99.5th percentile linear luminance, [0, 1]


def parse_ppm16(data: bytes) -> np.ndarray:
    """Parse a binary PPM (P6, 16-bit big-endian) into a float32 [0, 1] array.

    dcraw_emu's `-4 -Z -` output: a P6 header (width, height, maxval as
    whitespace-separated ASCII, '#' comment lines allowed) followed by
    raw big-endian uint16 samples.
    """
    if not data.startswith(b"P6"):
        raise ValueError("not a P6 (binary RGB) PPM")

    idx = 2
    values: list[int] = []
    while len(values) < 3:
        while data[idx] in b" \t\r\n":
            idx += 1
        if data[idx : idx + 1] == b"#":
            while data[idx] not in b"\r\n":
                idx += 1
            continue
        start = idx
        while data[idx] not in b" \t\r\n":
            idx += 1
        values.append(int(data[start:idx]))
    idx += 1  # the single whitespace byte separating the header from pixel data
    width, height, maxval = values
    if maxval <= 0 or maxval > 65535:
        raise ValueError(f"unsupported PPM maxval: {maxval}")

    expected = width * height * 3 * 2
    pixels = np.frombuffer(data, dtype=">u2", count=width * height * 3, offset=idx)
    if pixels.size < width * height * 3:
        raise ValueError(f"truncated PPM: expected {expected} pixel bytes, got {len(data) - idx}")
    return (pixels.astype(np.float32) / maxval).reshape(height, width, 3)


def luminance_percentiles(rgb: np.ndarray) -> ExposureStats:
    luma = rgb[..., 0] * _LUMA_WEIGHTS[0] + rgb[..., 1] * _LUMA_WEIGHTS[1] + rgb[..., 2] * _LUMA_WEIGHTS[2]
    p50, p995 = np.percentile(luma, [50, 99.5])
    return ExposureStats(p50=float(p50), p995=float(p995))


def ev_for_stats(
    stats: ExposureStats,
    *,
    target_p50: float = config.DEVELOP_TARGET_P50,
    highlight_ceiling: float = config.DEVELOP_HIGHLIGHT_CEILING,
    ev_min: float = config.DEVELOP_EV_MIN,
    ev_max: float = config.DEVELOP_EV_MAX,
    trim: float = config.DEVELOP_EV_TRIM,
) -> float:
    """EV correction that lifts the median toward target_p50, capped so the
    near-highlights (p99.5) don't get pushed past highlight_ceiling."""
    ev_mid = math.log2(target_p50 / max(stats.p50, 1e-6))
    ev_highlight = math.log2(highlight_ceiling / max(stats.p995, 1e-6))
    ev = min(ev_mid, ev_highlight) + trim
    return max(ev_min, min(ev_max, ev))


def combine_burst_evs(evs: list[float]) -> float:
    """One EV shared by every frame of a non-bracketed burst: the median is
    robust to a single outlier frame (blown highlight, hand over the lens)."""
    return statistics.median(evs)


def _run_dcraw(path: Path) -> bytes:
    result = subprocess.run(
        [config.DCRAW_EMU_BIN, "-h", "-4", "-w", "-o", "1", "-Z", "-", str(path)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"dcraw_emu failed for {path}: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def estimate_ev(arw: Path) -> float | None:
    """The EV correction for one raw file, or None if it couldn't be analyzed
    (corrupt file, missing tool) -- callers should fall back to EV 0.0."""
    try:
        data = _run_dcraw(arw)
        rgb = parse_ppm16(data)
        stats = luminance_percentiles(rgb)
        return ev_for_stats(stats)
    except (RuntimeError, ValueError, OSError):
        return None
