"""Central configuration: paths, external tool names, thresholds, sizes.

Tuned for a Sony ZV-1 and verified against real files from one
(exiftool 13.55). What the camera actually writes, read with `-n`:

    mode                ReleaseMode2  SequenceNumber  SequenceLength
    continuous burst    1             1, 2, 3, ...    0
    single shot         8             0               1
    (a third mode)      26            1               0

So SequenceLength is NOT a frame count -- it is 0 during continuous
shooting. The usable burst signal is SequenceNumber counting up from 1;
every other mode reports 0 or repeats the same value. See grouping.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- external tools -------------------------------------------------------

EXIFTOOL_BIN = os.environ.get("PICS_EXIFTOOL", "exiftool")
FFMPEG_BIN = os.environ.get("PICS_FFMPEG", "ffmpeg")
FFPROBE_BIN = os.environ.get("PICS_FFPROBE", "ffprobe")
MAGICK_BIN = os.environ.get("PICS_MAGICK", "magick")
JPEGTRAN_BIN = os.environ.get("PICS_JPEGTRAN", "jpegtran")
IMG2WEBP_BIN = os.environ.get("PICS_IMG2WEBP", "img2webp")

REQUIRED_TOOLS = {
    "exiftool": EXIFTOOL_BIN,
    "ffmpeg": FFMPEG_BIN,
    "ffprobe": FFPROBE_BIN,
    "magick": MAGICK_BIN,
    "jpegtran": JPEGTRAN_BIN,
    "img2webp": IMG2WEBP_BIN,
}

# --- file types -------------------------------------------------------------

PHOTO_EXTENSIONS = {".jpg", ".jpeg"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mts", ".m2ts"}

# --- burst grouping ---------------------------------------------------------

# MakerNotes tag names (priority order) holding Sony's continuous-shooting
# sequence info. First one present on a file wins. SequenceNumber is
# preferred: it reads 0 for single shots, where SequenceImageNumber holds a
# meaningless constant.
SONY_SEQUENCE_NUMBER_TAGS = ("MakerNotes:SequenceNumber", "MakerNotes:SequenceImageNumber")
SONY_SEQUENCE_LENGTH_TAGS = ("MakerNotes:SequenceLength", "MakerNotes:SequenceFileNumber")
SONY_DRIVE_MODE_TAGS = ("MakerNotes:DriveMode", "MakerNotes:ReleaseMode2")

# Used when a file carries no usable sequence tags at all. Also a sanity
# net: photos more than 5x this far apart never share a burst even if the
# tags claim otherwise.
BURST_MAX_GAP_SECONDS = 1.0

# --- generated asset sizes ---------------------------------------------------

# Thumbnails and the medium copy are WebP: measured against these very
# photos it is ~25-35% smaller than JPEG at matching distortion. The
# display copy stays JPEG, and the download is always the untouched
# progressive-JPEG original.
THUMB_MAX_DIM = 480
THUMB_QUALITY = 80

# Shown while shuttling through a burst. Sized to cover a phone screen at
# 3x DPR (~1170px) without the memory cost of the display tier: a decoded
# 2560px frame is ~17MB, so a 24-frame burst would be ~420MB resident,
# where 1280px frames come to ~105MB.
MEDIUM_MAX_DIM = 1280
MEDIUM_QUALITY = 82

DISPLAY_MAX_DIM = 2560
DISPLAY_QUALITY = 85

PREVIEW_WEBP_MAX_DIM = 480
PREVIEW_WEBP_MAX_FRAMES = 24
PREVIEW_WEBP_FPS = 8  # frames per second in the generated animation

VIDEO_MAX_HEIGHT = 1080
VIDEO_CRF = 23
VIDEO_AUDIO_BITRATE = "128k"
VIDEO_PREVIEW_SAMPLE_FRAMES = 16

# A burst is also offered as a real-time MP4 when it spans at least this
# many seconds of actual shooting. The ZV-1 fires fast: at 10fps a
# 10-frame burst is only 0.9s, so this threshold decides how many clips
# an album gets. See --mp4-min-seconds.
BURST_MP4_MIN_SECONDS = 1.5
BURST_MP4_HEIGHT = 1080
BURST_MP4_FPS = 30  # frames sampled across the clip for the animated preview

JPEG_ORIGINAL_QUALITY_NOTE = "originals are re-encoded losslessly to progressive JPEG (jpegtran), pixels unchanged"


@dataclass(slots=True)
class Settings:
    library_root: Path
    s3_bucket: str | None = None
    s3_endpoint: str = "https://storage.yandexcloud.net"
    s3_region: str = "ru-central1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    @property
    def state_db_path(self) -> Path:
        return self.library_root / "state.db"

    @property
    def albums_dir(self) -> Path:
        return self.library_root / "albums"

    def album_dir(self, album_id: str) -> Path:
        return self.albums_dir / album_id


def _load_dotenv(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE .env parser (no external dependency)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_settings(library_root: Path | None = None, env_file: Path | None = None) -> Settings:
    env = dict(os.environ)
    env.update(_load_dotenv(env_file or Path.cwd() / ".env"))

    root = library_root or Path(env.get("PICS_LIBRARY", "~/Pictures/zv1")).expanduser()
    return Settings(
        library_root=root.expanduser(),
        s3_bucket=env.get("PICS_S3_BUCKET"),
        s3_endpoint=env.get("PICS_S3_ENDPOINT", "https://storage.yandexcloud.net"),
        s3_region=env.get("PICS_S3_REGION", "ru-central1"),
        s3_access_key=env.get("PICS_S3_ACCESS_KEY"),
        s3_secret_key=env.get("PICS_S3_SECRET_KEY"),
    )
