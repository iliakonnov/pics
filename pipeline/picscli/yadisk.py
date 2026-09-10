"""Thin client for the Yandex Disk REST API (app-folder scope).

Every album gets one directory under the app folder (app:/<album_id>/),
holding JPG/RAW/VIDEO subdirectories of full-resolution originals named by
their camera filename. This is deliberately minimal: just enough to
create directories, upload a file, and publish a directory for sharing.

API reference: https://yandex.ru/dev/disk-api/doc/ru/reference/upload
              https://yandex.ru/dev/disk-api/doc/ru/reference/publish
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

_BASE_URL = "https://cloud-api.yandex.net/v1/disk"
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 5


class YandexDiskError(RuntimeError):
    pass


class YandexDisk:
    def __init__(self, token: str):
        if not token:
            raise YandexDiskError(
                "Yandex Disk not configured: set PICS_YADISK_TOKEN (env var or a .env file). "
                "Get a token for your OAuth app at https://oauth.yandex.ru/"
            )
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"OAuth {token}"

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = self._session.request(method, url, timeout=60, **kwargs)
            except requests.RequestException as exc:
                last_exc = exc
            else:
                if response.status_code not in _RETRY_STATUSES:
                    return response
                last_exc = YandexDiskError(f"{method} {url} -> {response.status_code}: {response.text[:200]}")
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(2**attempt)
        raise YandexDiskError(str(last_exc))

    def ensure_dir(self, path: str) -> None:
        """Create `path` (and every parent) if it doesn't already exist."""
        parts = path.rstrip("/").split("/")
        # parts[0] is the "app:" scheme component; build up incrementally.
        current = parts[0]
        for part in parts[1:]:
            current = f"{current}/{part}"
            response = self._request("PUT", f"{_BASE_URL}/resources", params={"path": current})
            if response.status_code not in (201, 409):
                raise YandexDiskError(f"failed to create directory {current}: {response.status_code} {response.text[:200]}")

    def delete(self, path: str, *, permanently: bool = True) -> None:
        """Remove a file or directory (default: skip the trash -- these are
        machine-managed album directories, not something a person would
        want to dig out of Disk's trash later)."""
        response = self._request(
            "DELETE", f"{_BASE_URL}/resources", params={"path": path, "permanently": str(permanently).lower()}
        )
        if response.status_code not in (202, 204, 404):
            raise YandexDiskError(f"failed to delete {path}: {response.status_code} {response.text[:200]}")

    def exists(self, path: str) -> bool:
        response = self._request("GET", f"{_BASE_URL}/resources", params={"path": path, "fields": "name"})
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        raise YandexDiskError(f"failed to check {path}: {response.status_code} {response.text[:200]}")

    def upload(self, local: Path, path: str) -> None:
        response = self._request(
            "GET", f"{_BASE_URL}/resources/upload", params={"path": path, "overwrite": "true"}
        )
        if response.status_code != 200:
            raise YandexDiskError(f"failed to get upload URL for {path}: {response.status_code} {response.text[:200]}")
        href = response.json()["href"]
        # Read the whole file into memory rather than streaming it from an
        # open file handle: _request's retry-on-5xx would otherwise resend
        # whatever was left in an already-partially-read stream, silently
        # uploading a truncated file.
        put_response = self._request("PUT", href, data=local.read_bytes())
        if put_response.status_code not in (201, 202):
            raise YandexDiskError(f"upload failed for {path}: {put_response.status_code} {put_response.text[:200]}")

    def publish(self, path: str) -> str:
        response = self._request("PUT", f"{_BASE_URL}/resources/publish", params={"path": path})
        if response.status_code not in (200, 202):
            raise YandexDiskError(f"failed to publish {path}: {response.status_code} {response.text[:200]}")
        info = self._request("GET", f"{_BASE_URL}/resources", params={"path": path, "fields": "public_url"})
        if info.status_code != 200:
            raise YandexDiskError(f"failed to read public_url for {path}: {info.status_code} {info.text[:200]}")
        public_url = info.json().get("public_url")
        if not public_url:
            raise YandexDiskError(f"{path} was published but has no public_url")
        return public_url
