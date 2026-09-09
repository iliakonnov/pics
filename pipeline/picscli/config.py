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

# Raw development tools. Not in REQUIRED_TOOLS: only demanded when an import
# actually contains ARW files (see develop.check_available()), so a
# JPEG-only card never needs darktable installed.
DARKTABLE_BIN = os.environ.get("PICS_DARKTABLE", "darktable-cli")
DCRAW_EMU_BIN = os.environ.get("PICS_DCRAW_EMU", "dcraw_emu")

# --- file types -------------------------------------------------------------

PHOTO_EXTENSIONS = {".jpg", ".jpeg"}
RAW_EXTENSIONS = {".arw"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mts", ".m2ts"}

# --- burst grouping ---------------------------------------------------------

# MakerNotes tag names (priority order) holding Sony's continuous-shooting
# sequence info. First one present on a file wins. SequenceNumber is
# preferred: it reads 0 for single shots, where SequenceImageNumber holds a
# meaningless constant.
SONY_SEQUENCE_NUMBER_TAGS = ("MakerNotes:SequenceNumber", "MakerNotes:SequenceImageNumber")
SONY_SEQUENCE_LENGTH_TAGS = ("MakerNotes:SequenceLength", "MakerNotes:SequenceFileNumber")
SONY_DRIVE_MODE_TAGS = ("MakerNotes:DriveMode", "MakerNotes:ReleaseMode2")

# ReleaseMode2 values that mean "this burst is exposure-bracketed" (Sony.pm:
# 2 = Continuous - Exposure Bracketing, 23 = Single-frame - Exposure
# Bracketing). Verified against a real bracketed burst (album hnoUQgBATI,
# burst b0050): four frames at ReleaseMode2=2, SequenceNumber 1..4,
# ExposureCompensation cycling 0/-0.3/+0.3/-0.7. Mode 3 (DRO/WB bracketing)
# is deliberately excluded: those frames share one exposure.
SONY_BRACKET_RELEASE_MODES = {2, 23}

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
# Cover selection (optional; needs mediapipe + opencv).
FACE_LANDMARK_MODEL = str(Path(__file__).resolve().parent.parent / "models" / "face_landmarker.task")
QUALITY_SCAN_SCALE = 0.25   # detection is unaffected; decoding dominates anyway
QUALITY_MAX_FACES = 6
# Eyes are not simply open or shut: a score of 0.38 is a half-closed,
# mid-blink look that a single 0.5 cutoff waves through. Frames are sorted
# into these bands and the sharpest frame from the best non-empty band
# wins, so a clearly open-eyed frame beats a slightly sharper squint.
QUALITY_BLINK_BANDS = (0.15, 0.4)

# Face grouping (optional; needs insightface + scikit-learn).
FACE_MODEL = "buffalo_l"
FACE_DET_SIZE = 640
FACE_SCAN_SCALE = 0.5      # display copies are 2560px; half is plenty for detection
FACE_MIN_SCORE = 0.6
FACE_WORKERS = 4           # ~600MB of model per worker, so not one per core
# Chosen against a real 955-photo album: 0.7 split one person in two, 0.9
# merged two people, 0.8 landed on exactly the five who were there.
FACE_CLUSTER_DISTANCE = 0.8
# Someone glimpsed in a couple of frames is a passer-by, and offering them
# as a filter button that yields three photos is just clutter. On a real
# 364-burst album this is what separates the five people who were actually
# there from two stray three-photo detections.
FACE_MIN_PHOTOS = 5
FACE_MIN_SHARE = 0.01      # ...or 1% of the album's bursts, whichever is larger
FACE_MAX_IDENTITIES = 8    # the filter has to stay small
FACE_AVATAR_SIZE = 96

BURST_MP4_MIN_SECONDS = 1.5
BURST_MP4_HEIGHT = 1080
BURST_MP4_FPS = 30  # frames sampled across the clip for the animated preview

JPEG_ORIGINAL_QUALITY_NOTE = (
    "originals are re-encoded losslessly to progressive JPEG (jpegtran), pixels unchanged "
    "-- unless the source is a raw file, in which case the original IS the darktable-developed JPEG"
)

# --- raw development (ARW -> JPEG) -------------------------------------------

# darktable export quality for the developed JPEG, which becomes the album
# "original". jpegtran re-encodes it losslessly to progressive afterwards.
DEVELOP_JPEG_QUALITY = 92

# darktable-cli is internally multi-threaded (OpenMP + OpenCL) and each
# instance can use several GB of RAM, unlike the single-threaded
# magick/ffmpeg workers in imaging.py. Two instances keep a 12-thread/16GB
# box busy without thrashing.
DEVELOP_JOBS = 2
# Try CPU before OpenCL by default: a claimed-but-broken GPU/driver doesn't
# make darktable-cli fail, it just makes it pathologically slow (minutes
# instead of ~15s per frame) in a way the failure-based retry can't catch.
# Flip this once you've confirmed `clinfo -l` lists a working GPU.
DEVELOP_OPENCL_FIRST = os.environ.get("PICS_DEVELOP_OPENCL_FIRST", "0") == "1"
DEVELOP_TIMEOUT_SECONDS = 300

# Exposure analysis (rawanalysis.py): target the raw's median luminance at
# this fraction of full scale, but never push the 99.5th percentile past
# the highlight ceiling -- sigmoid rolls off gracefully above 1.0, so a
# modest headroom is fine and protects against clipped highlights vetoing
# a needed lift in a dark scene.
DEVELOP_TARGET_P50 = 0.18
DEVELOP_HIGHLIGHT_CEILING = 1.5
DEVELOP_EV_MIN = -1.5
DEVELOP_EV_MAX = 2.0
# Constant offset between the analyzer's linear-light measurement and what
# darktable's pipeline actually renders at EV 0; tuned once by comparing
# developed output against camera JPEGs on the test set.
DEVELOP_EV_TRIM = 0.0
# Frames sampled per burst for exposure analysis (first/middle/last);
# bracketed bursts analyze every frame instead.
DEVELOP_SAMPLE_FRAMES = 3
# A burst's shared EV is only recomputed (triggering redevelopment) if it
# drifts from the cached value by more than this.
DEVELOP_EV_EPSILON = 0.05


@dataclass(slots=True)
class Settings:
    library_root: Path
    s3_bucket: str | None = None
    s3_endpoint: str = "https://storage.yandexcloud.net"
    s3_region: str = "ru-central1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    yadisk_token: str | None = None

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


def _find_dotenv(start: Path) -> Path | None:
    """Look for .env in the current directory and its parents.

    Credentials live at the top of the checkout, but `pics` is just as
    likely to be run from pipeline/ or anywhere else inside it.
    """
    for directory in [start, *start.parents]:
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_settings(library_root: Path | None = None, env_file: Path | None = None) -> Settings:
    env = dict(os.environ)
    found = env_file or _find_dotenv(Path.cwd())
    if found:
        env.update(_load_dotenv(found))

    root = library_root or Path(env.get("PICS_LIBRARY", "~/Pictures/zv1")).expanduser()
    return Settings(
        library_root=root.expanduser(),
        s3_bucket=env.get("PICS_S3_BUCKET"),
        s3_endpoint=env.get("PICS_S3_ENDPOINT", "https://storage.yandexcloud.net"),
        s3_region=env.get("PICS_S3_REGION", "ru-central1"),
        s3_access_key=env.get("PICS_S3_ACCESS_KEY"),
        s3_secret_key=env.get("PICS_S3_SECRET_KEY"),
        yadisk_token=env.get("PICS_YADISK_TOKEN"),
    )
