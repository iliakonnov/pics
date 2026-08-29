"""exiftool wrapper: batch-read metadata for photos and videos.

Runs exiftool once for the whole batch (much faster than one process per
file). See config.py for a note about verifying the Sony MakerNotes tag
names against a real camera file.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from . import config
from .mediameta import MediaMeta

_FILE_NUMBER_RE = re.compile(r"(\d+)(?=\.\w+$)")

# Tags requested from exiftool, in the exact form used to look them up in
# the returned JSON. "-G1 -a -s" style group-qualified names are used so
# EXIF:DateTimeOriginal and QuickTime:CreateDate etc. can't collide.
_REQUEST_TAGS = [
    "-EXIF:DateTimeOriginal",
    "-EXIF:SubSecTimeOriginal",
    "-EXIF:OffsetTimeOriginal",
    "-EXIF:Make",
    "-EXIF:Model",
    "-EXIF:LensModel",
    "-EXIF:FocalLength",
    "-EXIF:FocalLengthIn35mmFormat",
    "-EXIF:ExposureTime",
    "-EXIF:FNumber",
    "-EXIF:ISO",
    "-EXIF:Orientation",
    "-Composite:ImageSize",
    "-QuickTime:CreateDate",
    "-QuickTime:ImageWidth",
    "-QuickTime:ImageHeight",
    "-QuickTime:Duration",
    "-QuickTime:Rotation",
    *[f"-{t}" for t in config.SONY_SEQUENCE_NUMBER_TAGS],
    *[f"-{t}" for t in config.SONY_SEQUENCE_LENGTH_TAGS],
    *[f"-{t}" for t in config.SONY_DRIVE_MODE_TAGS],
]


def check_tools_available() -> list[str]:
    """Return a list of human-readable errors for any missing required tool."""
    errors = []
    for name, binary in config.REQUIRED_TOOLS.items():
        if shutil.which(binary) is None:
            errors.append(f"'{binary}' ({name}) not found on PATH")
    return errors


def _run_exiftool(paths: list[Path]) -> list[dict]:
    if not paths:
        return []
    cmd = [config.EXIFTOOL_BIN, "-j", "-n", *_REQUEST_TAGS, *[str(p) for p in paths]]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):  # exiftool uses 1 for "minor warnings"
        raise RuntimeError(f"exiftool failed ({result.returncode}): {result.stderr.strip()}")
    if not result.stdout.strip():
        raise RuntimeError(f"exiftool produced no output: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _first_present(raw: dict, tags: tuple[str, ...]):
    for tag in tags:
        # exiftool -j output keys are unqualified (last path component)
        key = tag.split(":")[-1]
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return None


def _parse_datetime(raw: dict, *, is_video: bool) -> datetime:
    if is_video:
        raw_dt = raw.get("CreateDate")
        if raw_dt:
            # Sony writes local time into the QuickTime CreateDate atom in
            # practice, despite the QuickTime spec saying UTC. Treated as
            # local/naive here; revisit if your files disagree.
            return datetime.strptime(raw_dt, "%Y:%m:%d %H:%M:%S")
        # Fall back to filesystem mtime, filled in by caller if this raises.
        raise ValueError("no CreateDate on video")

    raw_dt = raw.get("DateTimeOriginal")
    if not raw_dt:
        raise ValueError("no DateTimeOriginal on photo")
    dt = datetime.strptime(raw_dt, "%Y:%m:%d %H:%M:%S")
    subsec = raw.get("SubSecTimeOriginal")
    if subsec not in (None, ""):
        # SubSecTimeOriginal is a decimal fraction expressed as a string,
        # e.g. "50" means .50s, "005" means .005s.
        frac_str = str(subsec)
        microseconds = int(round(float(f"0.{frac_str}") * 1_000_000))
        dt = dt.replace(microsecond=microseconds)
    return dt


def _file_number(path: Path) -> int | None:
    match = _FILE_NUMBER_RE.search(path.name)
    return int(match.group(1)) if match else None


def _build_exif_summary(raw: dict, *, is_video: bool) -> dict:
    exif: dict = {}
    make = raw.get("Make")
    model = raw.get("Model")
    if make or model:
        exif["camera"] = " ".join(p for p in (make, model) if p)
    if raw.get("LensModel"):
        exif["lens"] = raw["LensModel"]
    if raw.get("ExposureTime"):
        exposure = raw["ExposureTime"]
        exif["exposureTime"] = f"1/{round(1 / exposure)}" if 0 < exposure < 1 else str(exposure)
    if raw.get("FNumber"):
        exif["fNumber"] = raw["FNumber"]
    if raw.get("ISO"):
        exif["iso"] = raw["ISO"]
    if raw.get("FocalLength"):
        exif["focalLength"] = f"{raw['FocalLength']}mm"
    if raw.get("FocalLengthIn35mmFormat"):
        exif["focalLength35mm"] = raw["FocalLengthIn35mmFormat"]
    if not is_video and raw.get("Orientation") is not None:
        exif["orientation"] = raw["Orientation"]
    if is_video and raw.get("Duration"):
        exif["durationSeconds"] = raw["Duration"]
    return exif


def read_media_metadata(paths: list[Path]) -> dict[Path, MediaMeta]:
    """Batch-read metadata for a list of photo/video paths.

    Returns a dict keyed by the input Path. file_hash is left empty
    ("") here; the caller (scan.py) fills it in since hashing is an I/O
    operation independent of exiftool.
    """
    raw_by_source = {}
    for entry in _run_exiftool(paths):
        raw_by_source[Path(entry["SourceFile"])] = entry

    out: dict[Path, MediaMeta] = {}
    for path in paths:
        raw = raw_by_source.get(path)
        if raw is None:
            raw = {}
        is_video = path.suffix.lower() in config.VIDEO_EXTENSIONS
        try:
            captured_at = _parse_datetime(raw, is_video=is_video)
        except ValueError:
            captured_at = datetime.fromtimestamp(path.stat().st_mtime)

        if is_video:
            width = raw.get("ImageWidth")
            height = raw.get("ImageHeight")
        else:
            size = raw.get("ImageSize")  # "WxH" from Composite:ImageSize with -n
            width = height = None
            if isinstance(size, str) and "x" in size:
                w, h = size.split("x", 1)
                width, height = int(w), int(h)

        out[path] = MediaMeta(
            path=path,
            kind="video" if is_video else "photo",
            file_hash="",
            captured_at=captured_at,
            file_number=_file_number(path),
            width=width,
            height=height,
            orientation=raw.get("Orientation"),
            sequence_number=_first_present(raw, config.SONY_SEQUENCE_NUMBER_TAGS),
            sequence_length=_first_present(raw, config.SONY_SEQUENCE_LENGTH_TAGS),
            drive_mode=_first_present(raw, config.SONY_DRIVE_MODE_TAGS),
            exif=_build_exif_summary(raw, is_video=is_video),
        )
    return out
