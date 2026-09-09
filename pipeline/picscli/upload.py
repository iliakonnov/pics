"""Publish one album: full-resolution originals to Yandex Disk, everything
the web gallery needs to Yandex Object Storage (S3-compatible).

Each album gets its own S3 directory, named with the album's unguessable
id, holding its own index.html, its own copy of the JS/CSS, its album.json
and its web-sized media (thumbnails, medium/display copies, previews,
clips, face crops, and the transcoded playback video) -- self-contained,
so the directory URL is the share link and the local copy can be deleted
afterwards. Full-resolution originals (the developed JPEG, the source ARW,
and the untouched source video) live on Yandex Disk instead, one directory
per album under the app folder, named by camera filename and organized
into JPG/RAW/VIDEO subdirectories; see sync_album_to_disk.

Content-hash-named media is uploaded to S3 with a long immutable
Cache-Control and skipped when the key already exists (identical hash
means identical bytes). The HTML, JSON and JS/CSS are mutable and always
re-uploaded with a no-cache header, so re-running an import or editing the
web app shows up immediately.
"""

from __future__ import annotations

import json
import mimetypes
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from . import cache, config
from .yadisk import YandexDisk

_IMMUTABLE_DIRS = {"thumb", "medium", "display", "preview", "video", "clip", "faces"}

# Full-resolution originals now live on Yandex Disk exclusively (see
# sync_album_to_disk) -- these directories are skipped in the S3 sync.
_YADISK_ONLY_DIRS = {"originals", "raw"}

# Legacy: albums published before originals moved to Yandex Disk have their
# originals/ in S3 COLD storage. cold_originals()/find_originals_not_cold()
# below remain as one-time catch-up tooling for those; new syncs never
# write anything under this prefix.
COLD_STORAGE_CLASS = "COLD"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}


@dataclass(slots=True)
class SyncStats:
    uploaded: int = 0
    skipped: int = 0
    keys_uploaded: list[str] = field(default_factory=list)


def guess_content_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _CONTENT_TYPES:
        return _CONTENT_TYPES[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def get_client(settings: config.Settings):
    if not settings.s3_bucket or not settings.s3_access_key or not settings.s3_secret_key:
        raise RuntimeError(
            "S3 not configured: set PICS_S3_BUCKET, PICS_S3_ACCESS_KEY, PICS_S3_SECRET_KEY "
            "(env vars or a .env file)"
        )
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )


def object_exists(client, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def _put_file(client, bucket: str, key: str, path: Path, *, immutable: bool) -> None:
    cache_control = "public, max-age=31536000, immutable" if immutable else "no-cache"
    extra_args = {
        "ContentType": guess_content_type(path),
        "CacheControl": cache_control,
        "ACL": "public-read",
    }
    client.upload_file(str(path), bucket, key, ExtraArgs=extra_args)


def sync_album(
    client,
    settings: config.Settings,
    album_id: str,
    web_root: Path,
    *,
    force: bool = False,
    jobs: int | None = None,
    log=lambda _msg: None,
) -> SyncStats:
    """Upload everything the album needs under the key prefix `album_id/`.

    Transfers run in parallel: an album is thousands of small files, and
    one round trip at a time is dominated by latency rather than
    bandwidth. boto3 clients are not thread-safe, so each worker gets its
    own.
    """
    stats = SyncStats()
    bucket = settings.s3_bucket
    album_dir = settings.album_dir(album_id)
    if not album_dir.is_dir():
        raise RuntimeError(f"no local album directory for {album_id}: {album_dir}")

    # (key, local path, immutable)
    jobs = jobs or min(16, (os.cpu_count() or 4) * 2)
    planned: list[tuple[str, Path, bool]] = [
        (f"{album_id}/index.html", web_root / "album.html", False),
        *[
            (f"{album_id}/assets/{a.name}", a, False)
            for a in sorted((web_root / "assets").iterdir())
            if a.is_file()
        ],
    ]
    for path in sorted(album_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name.endswith(cache.SUFFIX):
            continue  # working state, of no use to a viewer
        rel = path.relative_to(album_dir)
        top = rel.parts[0]
        if top in _YADISK_ONLY_DIRS:
            continue  # full-resolution originals: Yandex Disk only, see sync_album_to_disk
        planned.append((f"{album_id}/{rel.as_posix()}", path, top in _IMMUTABLE_DIRS))

    local = threading.local()
    lock = threading.Lock()
    done = 0
    total = len(planned)
    step = max(1, total // 20)

    def worker(job):
        nonlocal done
        key, path, immutable = job
        if not hasattr(local, "client"):
            local.client = get_client(settings)
        skipped = False
        if immutable and not force and object_exists(local.client, bucket, key):
            skipped = True
        else:
            _put_file(local.client, bucket, key, path, immutable=immutable)
        with lock:
            done += 1
            if done == total or done % step == 0:
                log(f"  uploaded {done}/{total}")
        return key, skipped

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for future in as_completed([pool.submit(worker, job) for job in planned]):
            key, skipped = future.result()
            if skipped:
                stats.skipped += 1
            else:
                stats.uploaded += 1
                stats.keys_uploaded.append(key)

    return stats


def get_disk_client(settings: config.Settings) -> YandexDisk:
    return YandexDisk(settings.yadisk_token)


def _local_path_for_disk_entry(album_dir: Path, frame: dict, key: str) -> Path:
    if key == "raw":
        matches = list((album_dir / "raw").glob(f"{frame['hash']}.*"))
        if not matches:
            raise RuntimeError(f"raw file missing locally for frame {frame['hash']}")
        return matches[0]
    return album_dir / frame["original"]


def sync_album_to_disk(
    disk: YandexDisk,
    settings: config.Settings,
    album_id: str,
    *,
    force: bool = False,
    jobs: int | None = None,
    log=lambda _msg: None,
) -> str:
    """Upload every frame's full-resolution file(s) to app:/<album_id>/ on
    Yandex Disk (JPG/RAW/VIDEO subdirectories, camera filenames), publish
    that directory, and record the resulting share link as album.json's
    diskUrl. Returns the public URL.
    """
    album_dir = settings.album_dir(album_id)
    album_json_path = album_dir / "album.json"
    if not album_json_path.is_file():
        raise RuntimeError(f"no local album.json for {album_id}: {album_json_path}")
    album_data = json.loads(album_json_path.read_text(encoding="utf-8"))

    root = f"app:/{album_id}"
    plan: list[tuple[Path, str]] = []
    needed_dirs: set[str] = set()
    for burst in album_data["bursts"]:
        for frame in burst["frames"]:
            for key, rel in (frame.get("disk") or {}).items():
                needed_dirs.add(rel.split("/", 1)[0])
                plan.append((_local_path_for_disk_entry(album_dir, frame, key), f"{root}/{rel}"))

    disk.ensure_dir(root)
    for d in sorted(needed_dirs):
        disk.ensure_dir(f"{root}/{d}")

    jobs = jobs or min(4, os.cpu_count() or 4)
    lock = threading.Lock()
    done = 0
    uploaded = 0
    skipped = 0
    total = len(plan)
    step = max(1, total // 20)

    def worker(job: tuple[Path, str]) -> None:
        nonlocal done, uploaded, skipped
        local, remote = job
        did_skip = not force and disk.exists(remote)
        if not did_skip:
            disk.upload(local, remote)
        with lock:
            done += 1
            if did_skip:
                skipped += 1
            else:
                uploaded += 1
            if done == total or done % step == 0:
                log(f"  yandex disk: {done}/{total}")

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for future in as_completed([pool.submit(worker, job) for job in plan]):
            future.result()
    log(f"  yandex disk: {uploaded} uploaded, {skipped} already present")

    public_url = disk.publish(root)
    album_data["diskUrl"] = public_url
    album_json_path.write_text(json.dumps(album_data, indent=2, ensure_ascii=False), encoding="utf-8")
    return public_url


def find_originals_not_cold(client, bucket: str) -> list[str]:
    """List keys under any album's `originals/` that aren't in COLD storage yet.

    Bucket-wide (not per local album): once uploaded, an album's local copy
    is expected to be deleted, so this is the only way to find originals
    from albums that no longer exist on disk.
    """
    keys = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            parts = obj["Key"].split("/", 2)
            if len(parts) >= 2 and parts[1] == "originals" and obj.get("StorageClass", "STANDARD") != COLD_STORAGE_CLASS:
                keys.append(obj["Key"])
    return keys


def cold_originals(
    settings: config.Settings, bucket: str, keys: list[str], *, jobs: int | None = None, log=lambda _msg: None
) -> int:
    """Transition existing objects to COLD storage in place (copy-to-self).

    New originals already land in COLD at upload time (see `sync_album`);
    this is the one-time catch-up for objects uploaded before that.
    """
    jobs = jobs or min(16, (os.cpu_count() or 4) * 2)
    local = threading.local()
    lock = threading.Lock()
    done = 0
    total = len(keys)
    step = max(1, total // 20)

    def worker(key: str) -> None:
        nonlocal done
        if not hasattr(local, "client"):
            local.client = get_client(settings)
        local.client.copy_object(
            Bucket=bucket,
            Key=key,
            CopySource={"Bucket": bucket, "Key": key},
            StorageClass=COLD_STORAGE_CLASS,
            MetadataDirective="COPY",
            ACL="public-read",
        )
        with lock:
            done += 1
            if done == total or done % step == 0:
                log(f"  moved to cold {done}/{total}")

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for future in as_completed([pool.submit(worker, key) for key in keys]):
            future.result()
    return total


def album_url(settings: config.Settings, album_id: str) -> str:
    return f"https://{settings.s3_bucket}.website.yandexcloud.net/{album_id}/"


BUCKET_POLICY_TEMPLATE = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "PublicReadGetObject",
            "Effect": "Allow",
            "Principal": "*",
            "Action": ["s3:GetObject"],
            "Resource": "arn:aws:s3:::{bucket}/*",
        }
    ],
}


def setup_bucket(client, bucket: str) -> str:
    """Enable public-read access and static website hosting on the bucket.

    Idempotent - safe to run again after the bucket already exists/is
    configured. Returns the (best-guess) website endpoint URL; verify the
    exact hostname in the Yandex Cloud console, since Object Storage's
    website-hosting domain naming is account/console-configuration
    dependent and wasn't verified against a live bucket while writing this.
    """
    policy = json.loads(json.dumps(BUCKET_POLICY_TEMPLATE).replace("{bucket}", bucket))
    client.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))
    client.put_bucket_website(
        Bucket=bucket,
        WebsiteConfiguration={
            "IndexDocument": {"Suffix": "index.html"},
            "ErrorDocument": {"Key": "index.html"},
        },
    )
    return f"https://{bucket}.website.yandexcloud.net"
