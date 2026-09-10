"""Turn burst groups into per-frame raw-development plans.

Pure logic, no file I/O -- like grouping.py, kept unit-testable with
synthetic MediaMeta objects. Two independent decisions live here:

- is_exposure_bracket(): does this group's exposure vary on purpose?
- build_develop_plans(): given each raw frame's analyzed EV, decide what
  EV every frame actually develops with (shared across a normal burst,
  independent within a bracket, its own within a single).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from . import config, rawanalysis
from .mediameta import MediaMeta

# A bias/shutter cycle needs at least this many EV of spread between its
# extremes to count as a deliberate bracket rather than metering noise.
_HEURISTIC_MIN_SPREAD = 0.3
_BIAS_ROUND = 6  # round to 1/6 EV before counting distinct steps


@dataclass(slots=True)
class FramePlan:
    file_hash: str
    path: Path
    ev: float
    bracketed: bool
    # This frame's own zoom position/aperture (never shared across a burst,
    # unlike ev): the ZV-1's Lensfun distortion correction depends heavily
    # on focal length, and reading it is a free EXIF field, not an
    # expensive raw-pixel analysis like exposure is.
    focal_mm: float
    aperture: float | None


def _heuristic_bracket(group: list[MediaMeta]) -> bool:
    if len(group) < 3:
        return False

    biases = [f.exposure_compensation for f in group if f.exposure_compensation is not None]
    if len(biases) == len(group):
        steps = {round(b * _BIAS_ROUND) for b in biases}
        if len(steps) >= 3 and (max(steps) - min(steps)) >= round(_HEURISTIC_MIN_SPREAD * _BIAS_ROUND):
            return True

    isos = {f.exif.get("iso") for f in group}
    fnumbers = {f.exif.get("fNumber") for f in group}
    times = [f.exposure_time_s for f in group if f.exposure_time_s]
    if len(times) == len(group) and len(isos) <= 1 and len(fnumbers) <= 1:
        median_t = sorted(times)[len(times) // 2]
        if median_t > 0:
            deltas = {round(math.log2(t / median_t) * _BIAS_ROUND) for t in times}
            if len(deltas) >= 3 and (max(deltas) - min(deltas)) >= round(_HEURISTIC_MIN_SPREAD * _BIAS_ROUND):
                return True

    return False


def is_exposure_bracket(group: list[MediaMeta]) -> bool:
    """True when this group's frames were shot as an exposure bracket
    (each frame meant to have a different exposure), as opposed to a
    normal continuous burst (same exposure, different instant)."""
    modes = {f.release_mode2 for f in group if f.release_mode2 is not None}
    if modes & config.SONY_BRACKET_RELEASE_MODES:
        return True
    if modes and modes.issubset({3}):
        # DRO/WB bracketing: frames share one exposure, not an EV bracket.
        return False
    return _heuristic_bracket(group)


def frames_to_analyze(groups: list[list[MediaMeta]]) -> list[MediaMeta]:
    """Which raw frames need an EV estimate: every frame of a bracket
    (each gets its own EV), else first/middle/last of a burst, else the
    single frame itself."""
    selected: list[MediaMeta] = []
    for group in groups:
        raw_frames = [f for f in group if f.is_raw]
        if not raw_frames:
            continue
        if len(raw_frames) == 1 or is_exposure_bracket(group):
            selected.extend(raw_frames)
            continue
        n = len(raw_frames)
        count = min(config.DEVELOP_SAMPLE_FRAMES, n)
        if count <= 1:
            indices = [n // 2]
        else:
            step = (n - 1) / (count - 1)
            indices = sorted({round(i * step) for i in range(count)})
        selected.extend(raw_frames[i] for i in indices)
    return selected


def build_develop_plans(
    groups: list[list[MediaMeta]],
    ev_by_hash: dict[str, float],
) -> tuple[dict[str, FramePlan], set[int]]:
    """Returns (plan per raw file_hash, indices of groups that are
    exposure brackets)."""
    plans: dict[str, FramePlan] = {}
    bracketed_groups: set[int] = set()

    for gi, group in enumerate(groups):
        raw_frames = [f for f in group if f.is_raw]
        if not raw_frames:
            continue

        bracketed = len(raw_frames) > 1 and is_exposure_bracket(group)
        if bracketed:
            bracketed_groups.add(gi)

        def _plan_for(f: MediaMeta, ev: float, bracketed: bool) -> FramePlan:
            focal_mm = f.focal_length_mm if f.focal_length_mm else config.DEVELOP_FALLBACK_FOCAL_MM
            return FramePlan(
                file_hash=f.file_hash, path=f.path, ev=ev, bracketed=bracketed,
                focal_mm=focal_mm, aperture=f.exif.get("fNumber"),
            )

        if bracketed or len(raw_frames) == 1:
            for f in raw_frames:
                ev = ev_by_hash.get(f.file_hash, 0.0)
                plans[f.file_hash] = _plan_for(f, ev, bracketed)
        else:
            sampled_evs = [ev_by_hash[f.file_hash] for f in raw_frames if f.file_hash in ev_by_hash]
            shared_ev = rawanalysis.combine_burst_evs(sampled_evs) if sampled_evs else 0.0
            for f in raw_frames:
                plans[f.file_hash] = _plan_for(f, shared_ev, False)

    return plans, bracketed_groups


def disk_names(items: list[MediaMeta]) -> dict[str, str]:
    """Camera filename per file_hash, de-duplicated within the album.

    Sony filenames wrap around (DSC09999 -> DSC00001), so two files from
    different card fills can legitimately share a name in one import;
    the later one (by capture time) gets its hash appended.
    """
    names: dict[str, str] = {}
    seen: dict[str, str] = {}  # lowercased name -> file_hash that claimed it
    for item in sorted(items, key=lambda i: i.captured_at):
        base = item.path.name
        key = base.lower()
        if key in seen and seen[key] != item.file_hash:
            stem = item.path.stem
            base = f"{stem}_{item.file_hash[:6]}{item.path.suffix}"
            key = base.lower()
        seen[key] = item.file_hash
        names[item.file_hash] = base
    return names
