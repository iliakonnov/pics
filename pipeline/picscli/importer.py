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
import string
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from . import album as album_mod
from . import cache, config, develop, developplan, faces as faces_mod, grouping, imaging, metadata, quality, rawanalysis, scan
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


ALBUM_ID_LENGTH = 10


def make_album_id(_first_item: MediaMeta | None = None) -> str:
    """An unguessable directory name.

    The album id *is* the secret: it becomes the published directory and
    the link handed to friends, so it must not encode the date or anything
    else about the contents. Letters only, so the link stays easy to read
    out and retype; 52**10 is about 57 bits, far past guessing.
    """
    return "".join(secrets.choice(string.ascii_letters) for _ in range(ALBUM_ID_LENGTH))


def _process_photo_frame(item: MediaMeta, album_dir: Path) -> tuple[album_mod.Frame, tuple[int, int]]:
    original_rel = f"originals/{item.file_hash}.jpg"
    thumb_rel = f"thumb/{item.file_hash}.webp"
    display_rel = f"display/{item.file_hash}.jpg"
    medium_rel = f"medium/{item.file_hash}.webp"

    original_path = album_dir / original_rel
    width, height = item.width, item.height
    if item.is_raw:
        raw_path = album_dir / f"raw/{item.file_hash}{item.ext}"
        if not raw_path.exists():
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item.path, raw_path)
        # The develop wave (run before this pool) already wrote the
        # developed JPEG here; derivatives are built from it rather than
        # re-decoding the ARW, and its pixel dimensions (post lens-correction
        # crop) replace the ARW's own EXIF-reported size, which can be off
        # by the crop margin.
        if not original_path.exists():
            raise RuntimeError(f"developed original missing for {item.path.name} -- develop stage failed?")
        width, height = imaging.image_dimensions(original_path)
        src = original_path
    else:
        if not original_path.exists():
            imaging.to_progressive_jpeg(item.path, original_path)
        src = item.path

    thumb_path = album_dir / thumb_rel
    if not thumb_path.exists():
        imaging.make_thumb(src, thumb_path)
    thumb_w, thumb_h = imaging.image_dimensions(thumb_path)

    display_path = album_dir / display_rel
    if not display_path.exists():
        imaging.make_display_jpeg(src, display_path)

    medium_path = album_dir / medium_rel
    if not medium_path.exists():
        imaging.make_medium(src, medium_path)

    return album_mod.Frame(
        hash=item.file_hash,
        kind="photo",
        thumb=thumb_rel,
        original=original_rel,
        display=display_rel,
        medium=medium_rel,
        video=None,
        width=width,
        height=height,
        size_bytes=original_path.stat().st_size,
        exif=item.exif,
    ), (thumb_w, thumb_h)


def _frame_disk_paths(item: MediaMeta, camera_name: str) -> dict:
    """Where this frame's full-resolution file(s) live under the album's
    Yandex Disk directory, keyed by camera filename (see developplan.disk_names)."""
    if item.kind == "video":
        return {"video": f"VIDEO/{camera_name}"}
    if item.is_raw:
        jpg_name = Path(camera_name).with_suffix(".JPG").name
        return {"jpg": f"JPG/{jpg_name}", "raw": f"RAW/{camera_name}"}
    return {"jpg": f"JPG/{camera_name}"}


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


def burst_real_seconds(group: list[MediaMeta]) -> float:
    return (group[-1].captured_at - group[0].captured_at).total_seconds()


def _build_clip(group: list[MediaMeta], frames: list[album_mod.Frame], album_dir: Path, burst_id: str) -> str | None:
    """Render the burst at the speed it happened, from EXIF timings."""
    clip_rel = f"clip/{burst_id}.mp4"
    clip_path = album_dir / clip_rel
    if clip_path.exists():
        return clip_rel

    gaps = [
        (group[i + 1].captured_at - group[i].captured_at).total_seconds()
        for i in range(len(group) - 1)
    ]
    if not gaps:
        return None
    # The last frame has no following shot to measure against, so it is
    # held for the burst's typical gap.
    typical = sorted(gaps)[len(gaps) // 2]
    holds = [*gaps, typical]

    timed = [(album_dir / f.display, hold) for f, hold in zip(frames, holds) if f.display]
    if len(timed) < 2:
        return None
    imaging.make_burst_mp4(
        timed, clip_path, height=config.BURST_MP4_HEIGHT, fps=config.BURST_MP4_FPS
    )
    return clip_rel


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
    mp4_min_seconds: float | None = None,
    group_faces: bool = False,
    pick_covers: bool = False,
    max_faces: int = config.FACE_MAX_IDENTITIES,
    develop_jobs: int | None = None,
    redevelop: bool = False,
    skip_raw: bool = False,
    log: Logger = _default_log,
) -> str:
    jobs = jobs or default_jobs()
    mp4_min_seconds = config.BURST_MP4_MIN_SECONDS if mp4_min_seconds is None else mp4_min_seconds
    tool_errors = metadata.check_tools_available()
    if tool_errors:
        raise RuntimeError("missing required tools:\n" + "\n".join(f"  - {e}" for e in tool_errors))

    files = scan.find_media_files(card_root)
    if skip_raw:
        files = [p for p in files if p.suffix.lower() not in config.RAW_EXTENSIONS]
    else:
        before = len(files)
        files = scan.drop_jpeg_when_raw_sibling(files)
        if before != len(files):
            log(f"  dropped {before - len(files)} camera JPEG(s) with a raw sibling")
    if not files:
        raise RuntimeError(f"no JPEG/RAW/video files found under {card_root}")
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

        # Raw development (ARW -> originals/<hash>.jpg) runs as its own wave
        # here, after grouping (so a burst's frames can share one exposure
        # correction) and before the flat conversion pool below (which
        # assumes originals/ already exists for raw frames).
        bracketed_groups: set[int] = set()
        template_fp: str | None = None
        raw_items = [i for i in kept_items if i.is_raw]
        if raw_items:
            develop_errors = develop.check_available()
            if develop_errors:
                raise RuntimeError("missing raw-develop tools:\n" + "\n".join(f"  - {e}" for e in develop_errors))

            dev_cache_path = cache.path_for(album_dir, "develop")
            dev_key = cache.fingerprint({"analyzer": rawanalysis.ANALYZER_VERSION})
            remembered_dev = cache.load(dev_cache_path, dev_key)
            ev_by_hash: dict[str, float] = dict(remembered_dev["evByHash"]) if remembered_dev else {}
            developed_records: dict[str, dict] = dict(remembered_dev["developed"]) if remembered_dev else {}

            to_analyze = [f for f in developplan.frames_to_analyze(groups) if f.file_hash not in ev_by_hash]
            if to_analyze:
                log(f"analyzing exposure for {len(to_analyze)} raw frame(s)...")
                analyzed = _run_parallel(
                    to_analyze, lambda f: rawanalysis.estimate_ev(f.path), jobs, label="analyzed", log=log
                )
                for i, f in enumerate(to_analyze):
                    ev_by_hash[f.file_hash] = analyzed[i] if analyzed[i] is not None else 0.0
                cache.save(dev_cache_path, dev_key, {"evByHash": ev_by_hash, "developed": developed_records})

            plans, bracketed_groups = developplan.build_develop_plans(groups, ev_by_hash)
            template_fp = develop.template_fingerprint()

            def _needs_develop(plan: developplan.FramePlan) -> bool:
                original_path = album_dir / f"originals/{plan.file_hash}.jpg"
                if redevelop or not original_path.exists():
                    return True
                record = developed_records.get(plan.file_hash)
                if record is None or record.get("params") != template_fp:
                    return True
                return abs(record.get("ev", 0.0) - plan.ev) > config.DEVELOP_EV_EPSILON

            to_develop = [p for p in plans.values() if _needs_develop(p)]
            if to_develop:
                # Cascade: anything about to be (re)developed must rebuild
                # its derivatives and its burst's preview/clip too.
                redeveloping = {p.file_hash for p in to_develop}
                for gi, group in enumerate(groups):
                    if not ({f.file_hash for f in group if f.is_raw} & redeveloping):
                        continue
                    for f in group:
                        for rel in (
                            f"display/{f.file_hash}.jpg",
                            f"medium/{f.file_hash}.webp",
                            f"thumb/{f.file_hash}.webp",
                        ):
                            (album_dir / rel).unlink(missing_ok=True)
                    bid = f"b{gi:04d}"
                    (album_dir / f"preview/{bid}.webp").unlink(missing_ok=True)
                    (album_dir / f"clip/{bid}.mp4").unlink(missing_ok=True)

                save_lock = threading.Lock()
                completed = 0

                def _develop_one(plan: developplan.FramePlan):
                    nonlocal completed
                    dest = album_dir / f"originals/{plan.file_hash}.jpg"
                    result = develop.develop(
                        plan.path, plan.ev, dest,
                        focal_mm=plan.focal_mm, aperture=plan.aperture,
                        jobs=develop_jobs or config.DEVELOP_JOBS,
                    )
                    if not result.ok:
                        raise RuntimeError(f"failed to develop {plan.path.name}: {result.error}")
                    with save_lock:
                        developed_records[plan.file_hash] = {"ev": plan.ev, "params": template_fp}
                        completed += 1
                        if completed % 25 == 0:
                            cache.save(dev_cache_path, dev_key, {"evByHash": ev_by_hash, "developed": developed_records})
                    return result

                log(f"developing {len(to_develop)} raw frame(s) on {develop_jobs or config.DEVELOP_JOBS} worker(s)...")
                _run_parallel(to_develop, _develop_one, develop_jobs or config.DEVELOP_JOBS, label="developed", log=log)
                cache.save(dev_cache_path, dev_key, {"evByHash": ev_by_hash, "developed": developed_records})
            else:
                log("develop: reusing all previously developed originals (nothing changed)")

        disk_names_map = developplan.disk_names(kept_items)

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
            frame.disk = _frame_disk_paths(item, disk_names_map[item.file_hash])
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
        # Exposure-bracketed bursts are skipped: a preview strobing between
        # wildly different exposures reads as broken, not useful. A photo
        # burst also needs to clear the same real-time-seconds bar as a
        # clip (see clip_tasks below): a burst too short for a clip just
        # flickers between a couple of near-identical frames on hover,
        # which reads as noise rather than a preview. Video bursts have no
        # competing "clip" concept, so they always get one.
        preview_tasks = [
            (gi, group, [frames_by_group[gi][fi] for fi in sorted(frames_by_group[gi])])
            for gi, group in enumerate(groups)
            if gi not in bracketed_groups
            and (
                group[0].kind == "video"
                or (len(group) > 1 and burst_real_seconds(group) >= mp4_min_seconds)
            )
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

        # Bursts worth watching as a clip: long enough in real time that
        # playing them back at true speed actually shows the motion.
        # Exposure brackets are excluded for the same reason as previews.
        clip_tasks = [
            (gi, group, [frames_by_group[gi][fi] for fi in sorted(frames_by_group[gi])])
            for gi, group in enumerate(groups)
            if group[0].kind == "photo"
            and len(group) > 1
            and gi not in bracketed_groups
            and burst_real_seconds(group) >= mp4_min_seconds
        ]
        if clip_tasks:
            log(f"rendering {len(clip_tasks)} real-time clip(s)...")
        clips = _run_parallel(
            clip_tasks,
            lambda t: _build_clip(t[1], t[2], album_dir, f"b{t[0]:04d}"),
            jobs,
            label="clips",
            log=log,
        )
        clip_by_group = {clip_tasks[i][0]: rel for i, rel in clips.items()}

        # Which frame of each burst to show as its cover. Exposure brackets
        # are excluded: comparing sharpness/blink across frames shot at
        # different exposures is meaningless, so they keep cover 0 (the
        # metered/0-EV frame in Sony's bracket order).
        covers: dict[int, int] = {}
        if pick_covers and quality.available():
            cover_scoring_bursts = [
                (f"b{gi:04d}", [frames_by_group[gi][fi].hash for fi in sorted(frames_by_group[gi])])
                for gi, group in enumerate(groups)
                if group[0].kind == "photo" and len(group) > 1 and gi not in bracketed_groups
            ]
            cover_key = cache.fingerprint(
                {
                    "bursts": cover_scoring_bursts,
                    "scale": config.QUALITY_SCAN_SCALE,
                    "bands": list(config.QUALITY_BLINK_BANDS),
                    "max_faces": config.QUALITY_MAX_FACES,
                    "develop_params": template_fp,
                }
            )
            cover_cache = cache.path_for(album_dir, "covers")
            remembered = cache.load(cover_cache, cover_key)
        else:
            remembered = None

        if remembered is not None:
            covers = {int(gi): fi for gi, fi in remembered["covers"].items()}
            log(f"covers: reusing {len(covers)} already chosen (nothing changed)")
        elif pick_covers and quality.available():
            scoring = [
                (gi, [album_dir / frames_by_group[gi][fi].display
                      for fi in sorted(frames_by_group[gi]) if frames_by_group[gi][fi].display])
                for gi, group in enumerate(groups)
                if group[0].kind == "photo" and len(group) > 1 and gi not in bracketed_groups
            ]
            log(f"choosing a cover for {len(scoring)} burst(s)...")
            scored = _run_parallel(
                scoring,
                lambda t: quality.pick_best([quality.score_frame(p) for p in t[1]]),
                jobs,
                label="covers",
                log=log,
            )
            covers = {scoring[i][0]: best for i, best in scored.items()}
            moved = sum(1 for v in covers.values() if v != 0)
            log(f"  {moved} of {len(covers)} bursts got a better cover than their first frame")
            cache.save(cover_cache, cover_key, {"covers": {str(k): v for k, v in covers.items()}})
        elif pick_covers:
            log("cover selection requested but mediapipe/opencv are not installed")

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
                    clip=clip_by_group.get(gi),
                    cover_index=covers.get(gi, 0),
                )
            )

        # Who is in which burst, so the album can be filtered by person.
        face_entries: list[dict] = []
        if group_faces and faces_mod.available():
            # One frame per burst is enough: the frames of a burst are the
            # same moment, so scanning all of them multiplies the work for
            # the same answer.
            photo_refs = []
            for gi, group in enumerate(groups):
                if group[0].kind != "photo":
                    continue
                order = sorted(frames_by_group[gi])
                cover = frames_by_group[gi][order[covers.get(gi, 0)] if covers.get(gi, 0) < len(order) else order[0]]
                if cover.display:
                    photo_refs.append((f"b{gi:04d}", cover.hash, album_dir / cover.display))
            face_key = cache.fingerprint(
                {
                    "photos": [(bid, h) for bid, h, _p in photo_refs],
                    "model": config.FACE_MODEL,
                    "det_size": config.FACE_DET_SIZE,
                    "scale": config.FACE_SCAN_SCALE,
                    "min_score": config.FACE_MIN_SCORE,
                    "distance": config.FACE_CLUSTER_DISTANCE,
                    "min_photos": config.FACE_MIN_PHOTOS,
                    "min_share": config.FACE_MIN_SHARE,
                    "max_identities": max_faces,
                }
            )
            face_cache = cache.path_for(album_dir, "faces")
            remembered_faces = cache.load(face_cache, face_key)
            # A cached answer is only usable while its avatars still exist.
            if remembered_faces is not None and not all(
                (album_dir / f["avatar"]).is_file() for f in remembered_faces["faces"]
            ):
                remembered_faces = None

            if remembered_faces is not None:
                face_entries = remembered_faces["faces"]
                by_burst = remembered_faces["by_burst"]
                log(f"faces: reusing {len(face_entries)} group(s) found earlier (nothing changed)")
            else:
                log(f"grouping faces across {len(photo_refs)} burst(s)...")
                identities, by_burst = faces_mod.group_by_face(
                    album_dir, photo_refs, max_identities=max_faces, jobs=jobs, log=log
                )
                face_entries = [
                    {"id": i.id, "avatar": i.avatar, "photos": i.photo_count, "bursts": len(i.burst_ids)}
                    for i in identities
                ]
                cache.save(face_cache, face_key, {"faces": face_entries, "by_burst": by_burst})

            for burst in bursts:
                burst.faces = by_burst.get(burst.id, [])
        elif group_faces:
            log("face grouping requested but insightface/scikit-learn are not installed")

        album_json = album_mod.build_album_json(
            album_id=resolved_album_id,
            title=resolved_title,
            date=date,
            bursts=bursts,
            generated_at=datetime.now(),
            faces=face_entries,
        )
        (album_dir / "album.json").write_text(json.dumps(album_json, indent=2, ensure_ascii=False), encoding="utf-8")
        db.commit()

    log(f"done: {resolved_album_id} ({len(bursts)} burst(s))")
    return resolved_album_id
