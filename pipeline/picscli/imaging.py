"""Wrappers around jpegtran / ImageMagick / ffmpeg / img2webp.

Every function shells out to an external tool and raises RuntimeError with
the tool's stderr on failure. Pure helpers that don't need the tools (like
select_preview_frames) are kept separate so they stay unit-testable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import config


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr.strip()}")


def image_dimensions(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [config.MAGICK_BIN, "identify", "-format", "%w %h", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"identify failed for {path}: {result.stderr.strip()}")
    w, h = result.stdout.split()
    return int(w), int(h)


def to_progressive_jpeg(src: Path, dst: Path) -> None:
    """Lossless re-encode to progressive JPEG, preserving all metadata."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([config.JPEGTRAN_BIN, "-copy", "all", "-optimize", "-progressive", "-outfile", str(dst), str(src)])


def make_resized_jpeg(src: Path, dst: Path, *, max_dim: int, quality: int) -> tuple[int, int]:
    """Auto-orient, downscale (never upscale) and strip metadata. Returns final (w, h)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            config.MAGICK_BIN,
            str(src),
            "-auto-orient",
            "-resize",
            f"{max_dim}x{max_dim}>",
            "-quality",
            str(quality),
            "-interlace",
            "Plane",
            "-strip",
            str(dst),
        ]
    )
    return image_dimensions(dst)


def make_resized_webp(src: Path, dst: Path, *, max_dim: int, quality: int) -> tuple[int, int]:
    """Auto-orient, downscale (never upscale) and strip metadata. Returns final (w, h)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            config.MAGICK_BIN,
            str(src),
            "-auto-orient",
            "-resize",
            f"{max_dim}x{max_dim}>",
            "-quality",
            str(quality),
            "-strip",
            str(dst),
        ]
    )
    return image_dimensions(dst)


def make_thumb(src: Path, dst: Path) -> tuple[int, int]:
    return make_resized_webp(src, dst, max_dim=config.THUMB_MAX_DIM, quality=config.THUMB_QUALITY)


def make_medium(src: Path, dst: Path) -> tuple[int, int]:
    return make_resized_webp(src, dst, max_dim=config.MEDIUM_MAX_DIM, quality=config.MEDIUM_QUALITY)


def make_display_jpeg(src: Path, dst: Path) -> tuple[int, int]:
    return make_resized_jpeg(src, dst, max_dim=config.DISPLAY_MAX_DIM, quality=config.DISPLAY_QUALITY)


def select_preview_frames(frames: list, max_frames: int) -> list:
    """Evenly decimate a sequence down to at most max_frames, keeping first/last.

    Pure function (works on any indexable sequence) so it's testable
    without touching real files.
    """
    if len(frames) <= max_frames:
        return list(frames)
    if max_frames <= 1:
        return [frames[0]]
    step = (len(frames) - 1) / (max_frames - 1)
    indices = sorted({round(i * step) for i in range(max_frames)})
    return [frames[i] for i in indices]


def make_animated_webp(frame_paths: list[Path], dst: Path, *, fps: int = config.PREVIEW_WEBP_FPS) -> None:
    if len(frame_paths) < 2:
        raise ValueError("need at least 2 frames to build an animated webp")
    dst.parent.mkdir(parents=True, exist_ok=True)
    delay_ms = max(round(1000 / fps), 20)
    cmd = [config.IMG2WEBP_BIN, "-d", str(delay_ms), "-loop", "0", "-lossy", "-q", "70"]
    cmd += [str(frame) for frame in frame_paths]
    cmd += ["-o", str(dst)]
    _run(cmd)


def probe_video_duration(src: Path) -> float:
    result = subprocess.run(
        [config.FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"ffprobe failed for {src}: {result.stderr.strip()}")
    return float(result.stdout.strip())


def probe_video_dimensions(src: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            config.FFPROBE_BIN,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(src),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"ffprobe failed for {src}: {result.stderr.strip()}")
    w, h = result.stdout.strip().split(",")
    return int(w), int(h)


def extract_poster_frame(src: Path, dst: Path, *, at_seconds: float = 0.5) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        duration = probe_video_duration(src)
    except RuntimeError:
        duration = None
    ts = min(at_seconds, duration / 4) if duration else 0.0
    _run(
        [
            config.FFMPEG_BIN,
            "-y",
            "-ss",
            f"{ts:.3f}",
            "-i",
            str(src),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(dst),
        ]
    )


def sample_video_frames(src: Path, out_dir: Path, *, count: int, max_dim: int) -> list[Path]:
    """Sample ~count frames evenly across the whole clip, for an animated preview."""
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_video_duration(src)
    fps = count / duration if duration > 0 else 1.0
    pattern = out_dir / "frame_%03d.jpg"
    _run(
        [
            config.FFMPEG_BIN,
            "-y",
            "-i",
            str(src),
            "-vf",
            f"fps={fps:.4f},scale='min({max_dim},iw)':-2:flags=lanczos",
            "-frames:v",
            str(count),
            str(pattern),
        ]
    )
    return sorted(out_dir.glob("frame_*.jpg"))


def transcode_video(
    src: Path,
    dst: Path,
    *,
    height: int = config.VIDEO_MAX_HEIGHT,
    crf: int = config.VIDEO_CRF,
    audio_bitrate: str = config.VIDEO_AUDIO_BITRATE,
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            config.FFMPEG_BIN,
            "-y",
            "-i",
            str(src),
            "-vf",
            f"scale=-2:'min({height},ih)'",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            audio_bitrate,
            "-movflags",
            "+faststart",
            "-metadata:s:v:0",
            "rotate=0",
            str(dst),
        ]
    )
