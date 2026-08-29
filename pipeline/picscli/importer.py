"""Orchestrates one card import: scan -> identify -> group -> convert -> album.json.

This is the "expensive", not-easily-unit-tested glue that drives the pure
grouping.py / album.py logic and the imaging.py tool wrappers against real
files. It is exercised through `pics import`; see tests/ for coverage of
the pure pieces it calls into.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from . import album as album_mod
from . import config, grouping, imaging, metadata, scan
from .manifest import Manifest
from .mediameta import MediaMeta

Logger = object  # any callable(str) -> None


def _default_log(msg: str) -> None:
    print(msg)


def make_album_id(first_item: MediaMeta) -> str:
    date = first_item.captured_at.date().isoformat()
    return f"{date}-{uuid.uuid4().hex[:6]}"


def _process_photo_frame(item: MediaMeta, album_dir: Path) -> album_mod.Frame:
    original_rel = f"originals/{item.file_hash}.jpg"
    thumb_rel = f"thumb/{item.file_hash}.jpg"
    display_rel = f"display/{item.file_hash}.jpg"

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

    return album_mod.Frame(
        hash=item.file_hash,
        kind="photo",
        thumb=thumb_rel,
        original=original_rel,
        display=display_rel,
        video=None,
        width=item.width,
        height=item.height,
        size_bytes=original_path.stat().st_size,
        exif=item.exif,
    ), (thumb_w, thumb_h)


def _process_video_frame(item: MediaMeta, album_dir: Path) -> tuple[album_mod.Frame, tuple[int, int]]:
    original_rel = f"originals/{item.file_hash}{item.ext}"
    thumb_rel = f"thumb/{item.file_hash}.jpg"
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
    log: Logger = _default_log,
) -> str:
    tool_errors = metadata.check_tools_available()
    if tool_errors:
        raise RuntimeError("missing required tools:\n" + "\n".join(f"  - {e}" for e in tool_errors))

    files = scan.find_media_files(card_root)
    if not files:
        raise RuntimeError(f"no JPEG/video files found under {card_root}")
    log(f"found {len(files)} file(s)")

    log("reading metadata (exiftool)...")
    meta_by_path = metadata.read_media_metadata(files)
    items: list[MediaMeta] = []
    for path in files:
        item = meta_by_path[path]
        item.file_hash = scan.hash_file(path)
        items.append(item)

    with Manifest(settings.state_db_path) as db:
        resolved_album_id = album_id or make_album_id(sorted(items, key=lambda i: i.captured_at)[0])
        date = min(i.captured_at for i in items).date().isoformat()
        resolved_title = title or date
        db.ensure_album(resolved_album_id, resolved_title, date)
        log(f"album: {resolved_album_id}")

        # Drop files already archived under a *different* album (dedup across
        # imports). Files already in *this* album (a resumed run) are kept.
        kept_items = []
        for item in items:
            existing = db.find_by_hash(item.file_hash)
            if existing and existing.album_id != resolved_album_id:
                log(f"  skip (already in album {existing.album_id}): {item.path.name}")
                continue
            kept_items.append(item)

        if not kept_items:
            raise RuntimeError("every file in this batch was already imported into a different album")

        groups = grouping.group_bursts(kept_items)
        log(f"grouped into {len(groups)} burst(s)")

        album_dir = settings.album_dir(resolved_album_id)
        bursts: list[album_mod.Burst] = []

        for gi, group in enumerate(groups):
            burst_id = f"b{gi:04d}"
            frames: list[album_mod.Frame] = []
            cover_dims: tuple[int, int] | None = None

            for fi, item in enumerate(group):
                if item.kind == "photo":
                    frame, dims = _process_photo_frame(item, album_dir)
                else:
                    frame, dims = _process_video_frame(item, album_dir)
                frames.append(frame)
                if fi == 0:
                    cover_dims = dims

                db.register_file(
                    file_hash=item.file_hash,
                    kind=item.kind,
                    source_path=str(item.path),
                    album_id=resolved_album_id,
                    burst_id=burst_id,
                    frame_index=fi,
                    captured_at=item.captured_at.isoformat(),
                )
                db.mark_processed(item.file_hash)

            preview = _build_preview(group, frames, album_dir, burst_id)

            bursts.append(
                album_mod.Burst(
                    id=burst_id,
                    kind=group[0].kind,
                    captured_at=group[0].captured_at,
                    thumb_w=cover_dims[0],
                    thumb_h=cover_dims[1],
                    frames=frames,
                    preview=preview,
                    cover_index=0,
                )
            )
            log(f"  [{gi + 1}/{len(groups)}] {burst_id}: {group[0].kind}, {len(group)} frame(s)")

        album_json = album_mod.build_album_json(
            album_id=resolved_album_id,
            title=resolved_title,
            date=date,
            bursts=bursts,
            generated_at=datetime.now(),
        )
        (album_dir / "album.json").write_text(json.dumps(album_json, indent=2, ensure_ascii=False), encoding="utf-8")

        index_path = settings.albums_index_path
        existing_index = json.loads(index_path.read_text()) if index_path.exists() else None
        new_index = album_mod.upsert_albums_index(existing_index, album_mod.album_summary(album_json))
        index_path.write_text(json.dumps(new_index, indent=2, ensure_ascii=False), encoding="utf-8")

        db.commit()

    log(f"done: {resolved_album_id} ({len(bursts)} burst(s))")
    return resolved_album_id
