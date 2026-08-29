"""Publish one album to Yandex Object Storage (S3-compatible).

Each album is uploaded to its own directory, named with the album's
unguessable id, and is entirely self-contained: its own index.html, its
own copy of the JS/CSS, its album.json and its media. The directory URL
is the share link, and nothing outside it is needed to view it — so the
album can be forgotten locally afterwards, and no index ties albums
together.

Content-hash-named media is uploaded with a long immutable Cache-Control
and skipped when the key already exists (identical hash means identical
bytes). The HTML, JSON and JS/CSS are mutable and always re-uploaded with
a no-cache header, so re-running an import or editing the web app shows
up immediately.
"""

from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from . import config

_IMMUTABLE_DIRS = {"originals", "thumb", "medium", "display", "preview", "video"}

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
    client.upload_file(
        str(path),
        bucket,
        key,
        ExtraArgs={
            "ContentType": guess_content_type(path),
            "CacheControl": cache_control,
            "ACL": "public-read",
        },
    )


def sync_album(client, settings: config.Settings, album_id: str, web_root: Path, *, force: bool = False) -> SyncStats:
    """Upload everything the album needs under the key prefix `album_id/`."""
    stats = SyncStats()
    bucket = settings.s3_bucket
    album_dir = settings.album_dir(album_id)
    if not album_dir.is_dir():
        raise RuntimeError(f"no local album directory for {album_id}: {album_dir}")

    def put(key: str, path: Path, *, immutable: bool) -> None:
        _put_file(client, bucket, key, path, immutable=immutable)
        stats.uploaded += 1
        stats.keys_uploaded.append(key)

    # The page itself, plus its own copy of the app: no shared root.
    put(f"{album_id}/index.html", web_root / "album.html", immutable=False)
    for asset in sorted((web_root / "assets").iterdir()):
        if asset.is_file():
            put(f"{album_id}/assets/{asset.name}", asset, immutable=False)

    for path in sorted(album_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(album_dir)
        key = f"{album_id}/{rel.as_posix()}"

        if rel.parts[0] in _IMMUTABLE_DIRS:
            if not force and object_exists(client, bucket, key):
                stats.skipped += 1
                continue
            put(key, path, immutable=True)
        else:
            put(key, path, immutable=False)  # album.json

    return stats


def album_url(settings: config.Settings, album_id: str) -> str:
    return f"http://{settings.s3_bucket}.website.yandexcloud.net/{album_id}/"


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
    return f"http://{bucket}.website.yandexcloud.net"
