"""Central configuration: paths, external tool names, thresholds, sizes.

Values here are meant to be reasonably good defaults for a Sony ZV-1
shooter. The Sony MakerNotes tag names in SONY_SEQUENCE_TAGS /
SONY_DRIVE_MODE_TAGS have NOT been verified against a real ZV-1 file
(no sample files were available while writing this). Before relying on
sequence-based burst grouping, run:

    exiftool -G1 -a -s -Sony:all -Composite:all sample.JPG

against a real photo from the camera and confirm the tag names below
still match; adjust if exiftool reports different names for your
firmware version.
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

# Candidate MakerNotes tag names (in priority order) exiftool may use for
# Sony continuous-shooting sequence info. First one present on a file wins.
SONY_SEQUENCE_NUMBER_TAGS = ("MakerNotes:SequenceImageNumber", "MakerNotes:SequenceNumber")
SONY_SEQUENCE_LENGTH_TAGS = ("MakerNotes:SequenceLength", "MakerNotes:SequenceFileNumber")
SONY_DRIVE_MODE_TAGS = ("MakerNotes:DriveMode", "MakerNotes:ReleaseMode2")

# Fallback / sanity-check clustering: consecutive photos more than this many
# seconds apart never belong to the same burst, regardless of what the
# sequence tags say.
BURST_MAX_GAP_SECONDS = 1.0

# --- generated asset sizes ---------------------------------------------------

THUMB_MAX_DIM = 480
THUMB_QUALITY = 80

DISPLAY_MAX_DIM = 2560
DISPLAY_QUALITY = 85

PREVIEW_WEBP_MAX_DIM = 480
PREVIEW_WEBP_MAX_FRAMES = 24
PREVIEW_WEBP_FPS = 8  # frames per second in the generated animation

VIDEO_MAX_HEIGHT = 1080
VIDEO_CRF = 23
VIDEO_AUDIO_BITRATE = "128k"
VIDEO_PREVIEW_SAMPLE_FRAMES = 16  # frames sampled across the clip for the animated preview

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

    @property
    def albums_index_path(self) -> Path:
        return self.library_root / "albums.json"

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
