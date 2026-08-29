"""Assemble one album locally and serve it, without touching S3.

Mirrors exactly what `pics upload` publishes: the album directory is the
whole site, served at the root, so what you review locally is what your
friends will see. Media directories are symlinked rather than copied, so
previewing an album full of 20MB originals costs nothing on disk.
"""

from __future__ import annotations

import functools
import http.server
import shutil
import socketserver
from pathlib import Path

from . import config


def build_site(settings: config.Settings, web_root: Path, out_dir: Path, album_id: str) -> Path:
    src = settings.album_dir(album_id)
    if not src.is_dir():
        raise RuntimeError(f"no local album directory for {album_id}: {src}")

    out_dir.mkdir(parents=True, exist_ok=True)

    assets_dst = out_dir / "assets"
    if assets_dst.exists():
        shutil.rmtree(assets_dst)
    shutil.copytree(web_root / "assets", assets_dst)
    shutil.copy(web_root / "album.html", out_dir / "index.html")
    shutil.copy(src / "album.json", out_dir / "album.json")

    for child in src.iterdir():
        if not child.is_dir():
            continue
        link = out_dir / child.name
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            continue
        link.symlink_to(child)

    return out_dir


def serve(root: Path, port: int) -> None:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server(("127.0.0.1", port), handler) as httpd:
        httpd.serve_forever()
