"""Orchestrates one card import: scan -> identify -> group -> convert -> album.json.

This is the "expensive", not-easily-unit-tested glue that drives the pure
grouping.py / album.py logic and the imaging.py tool wrappers against real
files. It is exercised through `pics import`; see tests/ for coverage of
the pure pieces it calls into.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from . import album as album_mod
from . import config, grouping, imaging, metadata, scan
from .manifest import Manifest
from .mediameta import MediaMeta

Logger = object  # any callable(str) -> None


def default_jobs() -> int:
    """One worker per core. The work is almost entirely spent inside
    jpegtran/magick/ffmpeg subprocesses, so threads are enough — they
    release the GIL while waiting and never touch shared state."""
    return os.cpu_count() or 4


def _run_parallel(tasks, worker, jobs, *, label, log):
    """Run worker(task) over tasks, reporting progress as results land.

    Returns {index: result}. Exceptions propagate once every worker has
    been given the chance to finish, so a failure can't leave orphaned
    subprocesses mid-import.
    """
    results = {}
    if not tasks:
        return results
    done = 0
    lock = threading.Lock()
    total = len(tasks)
    step = max(1, total // 20)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(worker, task): i for i, task in enumerate(tasks)}
        for future in as_completed(futures):
            i = futures[future]
            results[i] = future.result()
            with lock:
                done += 1
                if done == total or done % step == 0:
                    log(f"  {label}: {done}/{total}")
    return results


def _default_log(msg: str) -> None:
    print(msg)


def make_album_id(_first_item: MediaMeta | None = None) -> str:
    """An unguessable directory name.

    The album id *is* the secret: it becomes the published directory and
    the link handed to friends, so it must not encode the date or anything
    else about the contents. ~96 bits of randomness.
    """
    return secrets.token_urlsafe(12)


def _process_photo_frame(item: MediaMeta, album_dir: Path) -> album_mod.Frame:
    original_rel = f"originals/{item.file_hash}.jpg"
    thumb_rel = f"thumb/{item.file_hash}.webp"
    display_rel = f"display/{item.file_hash}.jpg"
    medium_rel = f"medium/{item.file_hash}.webp"

    original_path = album_dir / original_rel
    if not original_path.exists():
        imaging.to_progressive_jpeg(item.path, original_path)

    thumb_path = album_dir / thumb_rel
    if not thumb_path.exists():
        imaging.make_thumb(item.path, thumb_path)
    thumb_w, thumb_h = imaging.image_dimensions(thumb_path)

    display_path = album_dir / display_rel
    if not display_path.exists():
        imaging.make_display_jpeg(item.path, display_path)

    medium_path = album_dir / medium_rel
    if not medium_path.exists():
        imaging.make_medium(item.path, medium_path)

    return album_mod.Frame(
        hash=item.file_hash,
        kind="photo",
        thumb=thumb_rel,
        original=original_rel,
        display=display_rel,
        medium=medium_rel,
        video=None,
        width=item.width,
        height=item.height,
        size_bytes=original_path.stat().st_size,
        exif=item.exif,
    ), (thumb_w, thumb_h)


def _process_video_frame(item: MediaMeta, album_dir: Path) -> tuple[album_mod.Frame, tuple[int, int]]:
    original_rel = f"originals/{item.file_hash}{item.ext}"
    thumb_rel = f"thumb/{item.file_hash}.webp"
    video_rel = f"video/{item.file_hash}.mp4"

    original_path = album_dir / original_rel
    if not original_path.exists():
        original_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.path, original_path)

    video_path = album_dir / video_rel
    if not video_path.exists():
        imaging.transcode_video(item.path, video_path)

    thumb_path = album_dir / thumb_rel
    if not thumb_path.exists():
        with tempfile.TemporaryDirectory() as tmp:
            poster = Path(tmp) / "poster.jpg"
            imaging.extract_poster_frame(item.path, poster)
            imaging.make_thumb(poster, thumb_path)
    thumb_w, thumb_h = imaging.image_dimensions(thumb_path)

    width, height = item.width, item.height
    if not width or not height:
        width, height = imaging.probe_video_dimensions(item.path)

    return album_mod.Frame(
        hash=item.file_hash,
        kind="video",
        thumb=thumb_rel,
        original=original_rel,
        display=None,
        medium=None,
        video=video_rel,
        width=width,
        height=height,
        size_bytes=original_path.stat().st_size,
        exif=item.exif,
    ), (thumb_w, thumb_h)


def _build_preview(group: list[MediaMeta], frames: list[album_mod.Frame], album_dir: Path, burst_id: str) -> str | None:
    preview_rel = f"preview/{burst_id}.webp"
    preview_path = album_dir / preview_rel
    if preview_path.exists():
        return preview_rel

    kind = group[0].kind
    if kind == "photo":
        if len(frames) < 2:
            return None
        thumb_paths = [album_dir / f.thumb for f in frames]
        selected = imaging.select_preview_frames(thumb_paths, config.PREVIEW_WEBP_MAX_FRAMES)
        imaging.make_animated_webp(selected, preview_path)
        return preview_rel

    # video: sample frames straight from the source clip
    with tempfile.TemporaryDirectory() as tmp:
        sampled = imaging.sample_video_frames(
            group[0].path,
            Path(tmp),
            count=config.VIDEO_PREVIEW_SAMPLE_FRAMES,
            max_dim=config.PREVIEW_WEBP_MAX_DIM,
        )
        if len(sampled) < 2:
            return None
        imaging.make_animated_webp(sampled, preview_path)
    return preview_rel


def run_import(
    card_root: Path,
    settings: config.Settings,
    *,
    title: str | None = None,
    album_id: str | None = None,
    jobs: int | None = None,
    log: Logger = _default_log,
) -> str:
    jobs = jobs or default_jobs()
    tool_errors = metadata.check_tools_available()
    if tool_errors:
        raise RuntimeError("missing required tools:\n" + "\n".join(f"  - {e}" for e in tool_errors))

    files = scan.find_media_files(card_root)
    if not files:
        raise RuntimeError(f"no JPEG/video files found under {card_root}")
    log(f"found {len(files)} file(s)")

    log("reading metadata (exiftool)...")
    meta_by_path = metadata.read_media_metadata(files)

    log(f"hashing {len(files)} file(s)...")
    hashes = _run_parallel(files, scan.hash_file, jobs, label="hashed", log=log)
    items: list[MediaMeta] = []
    for i, path in enumerate(files):
        item = meta_by_path[path]
        item.file_hash = hashes[i]
        items.append(item)

    with Manifest(settings.state_db_path) as db:
        resolved_album_id = album_id or make_album_id()
        date = min(i.captured_at for i in items).date().isoformat()
        resolved_title = title or date
        db.ensure_album(resolved_album_id, resolved_title, date)
        log(f"album: {resolved_album_id}")

        # Deduplicate only within this import (the same shot copied twice
        # onto the card). Albums are independent and get deleted locally
        # once published, so the same photos may legitimately be imported
        # again later into a new album.
        kept_items = []
        seen_hashes = set()
        for item in items:
            if item.file_hash in seen_hashes:
                log(f"  skip (duplicate of another file in this import): {item.path.name}")
                continue
            seen_hashes.add(item.file_hash)
            kept_items.append(item)

        groups = grouping.group_bursts(kept_items)
        log(f"grouped into {len(groups)} burst(s)")

        album_dir = settings.album_dir(resolved_album_id)
        bursts: list[album_mod.Burst] = []

        # Convert every frame first, across all bursts at once: the work is
        # per-file and independent, so one flat pool keeps every core busy
        # instead of stalling on bursts that happen to be short.
        flat = [(gi, fi, item) for gi, group in enumerate(groups) for fi, item in enumerate(group)]

        def convert(task):
            _, _, item = task
            if item.kind == "photo":
                return _process_photo_frame(item, album_dir)
            return _process_video_frame(item, album_dir)

        log(f"converting {len(flat)} file(s) on {jobs} worker(s)...")
        converted = _run_parallel(flat, convert, jobs, label="converted", log=log)

        frames_by_group: dict[int, dict[int, album_mod.Frame]] = {}
        dims_by_group: dict[int, tuple[int, int]] = {}
        for i, (gi, fi, item) in enumerate(flat):
            frame, dims = converted[i]
            frames_by_group.setdefault(gi, {})[fi] = frame
            if fi == 0:
                dims_by_group[gi] = dims

            db.register_file(
                file_hash=item.file_hash,
                kind=item.kind,
                source_path=str(item.path),
                album_id=resolved_album_id,
                burst_id=f"b{gi:04d}",
                frame_index=fi,
                captured_at=item.captured_at.isoformat(),
            )
            db.mark_processed(item.file_hash)

        # Previews depend on the thumbnails above, so they form a second wave.
        preview_tasks = [
            (gi, group, [frames_by_group[gi][fi] for fi in sorted(frames_by_group[gi])])
            for gi, group in enumerate(groups)
            if len(group) > 1 or group[0].kind == "video"
        ]
        log(f"building {len(preview_tasks)} animated preview(s)...")
        previews = _run_parallel(
            preview_tasks,
            lambda t: _build_preview(t[1], t[2], album_dir, f"b{t[0]:04d}"),
            jobs,
            label="previews",
            log=log,
        )
        preview_by_group = {preview_tasks[i][0]: rel for i, rel in previews.items()}

        for gi, group in enumerate(groups):
            ordered = [frames_by_group[gi][fi] for fi in sorted(frames_by_group[gi])]
            bursts.append(
                album_mod.Burst(
                    id=f"b{gi:04d}",
                    kind=group[0].kind,
                    captured_at=group[0].captured_at,
                    thumb_w=dims_by_group[gi][0],
                    thumb_h=dims_by_group[gi][1],
                    frames=ordered,
                    preview=preview_by_group.get(gi),
                    cover_index=0,
                )
            )

        album_json = album_mod.build_album_json(
            album_id=resolved_album_id,
            title=resolved_title,
            date=date,
            bursts=bursts,
            generated_at=datetime.now(),
        )
        (album_dir / "album.json").write_text(json.dumps(album_json, indent=2, ensure_ascii=False), encoding="utf-8")
        db.commit()

    log(f"done: {resolved_album_id} ({len(bursts)} burst(s))")
    return resolved_album_id
