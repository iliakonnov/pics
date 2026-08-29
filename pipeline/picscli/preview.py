"""Assemble the site locally and serve it, without touching S3.

Media directories are symlinked rather than copied, so previewing an
album full of 20MB originals costs nothing on disk.
"""

from __future__ import annotations

import functools
import http.server
import shutil
import socketserver
from pathlib import Path

from . import config


def build_site(settings: config.Settings, web_root: Path, out_dir: Path, album_ids: list[str]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)

    assets_dst = out_dir / "assets"
    if assets_dst.exists():
        shutil.rmtree(assets_dst)
    shutil.copytree(web_root / "assets", assets_dst)
    shutil.copy(web_root / "index.html", out_dir / "index.html")

    if settings.albums_index_path.is_file():
        shutil.copy(settings.albums_index_path, out_dir / "albums.json")

    albums_dst = out_dir / "albums"
    albums_dst.mkdir(exist_ok=True)

    for album_id in album_ids:
        src = settings.album_dir(album_id)
        if not src.is_dir():
            continue
        dst = albums_dst / album_id
        dst.mkdir(exist_ok=True)
        shutil.copy(web_root / "album.html", dst / "index.html")
        shutil.copy(src / "album.json", dst / "album.json")
        for child in src.iterdir():
            if not child.is_dir():
                continue
            link = dst / child.name
            if link.is_symlink() or link.exists():
                if link.is_symlink():
                    link.unlink()
                else:
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
