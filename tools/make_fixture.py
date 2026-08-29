#!/usr/bin/env python3
"""Build a synthetic album site for UI testing, without a camera.

Generates flat-colour placeholder images and a tiny test-pattern video,
then writes the album.json / albums.json a real import would produce and
copies web/ over the top. Useful for exercising the viewer (especially
the touch shuttle) when no real photos are at hand.

    python3 tools/make_fixture.py /tmp/pics-site
    python3 -m http.server 8791 --directory /tmp/pics-site
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "web"
ALBUM_ID = "2026-08-29-demo01"

BURST_FRAMES = 6  # long enough that a fast shuttle wraps around


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def solid(path: Path, w: int, h: int, hue: int, label: str, pointsize: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "magick", "-size", f"{w}x{h}", f"xc:hsl({hue},70%,45%)",
            "-gravity", "center", "-pointsize", str(pointsize),
            "-fill", "white", "-annotate", "0", label, str(path),
        ]
    )


def build(site: Path) -> None:
    if site.exists():
        shutil.rmtree(site)
    album = site / "albums" / ALBUM_ID

    shutil.copytree(WEB / "assets", site / "assets")
    shutil.copy(WEB / "index.html", site / "index.html")
    album.mkdir(parents=True)
    for sub in ("thumb", "display", "originals", "video"):
        (album / sub).mkdir()
    shutil.copy(WEB / "album.html", album / "index.html")

    bursts = []

    frames = []
    for i in range(1, BURST_FRAMES + 1):
        hue = (i * 47) % 360
        solid(album / "thumb" / f"burstA{i}.jpg", 480, 320, hue, f"A#{i}", 60)
        solid(album / "display" / f"burstA{i}.jpg", 1600, 1067, hue, f"Burst A #{i}", 130)
        shutil.copy(album / "display" / f"burstA{i}.jpg", album / "originals" / f"burstA{i}.jpg")
        frames.append(
            {
                "hash": f"burstA{i}",
                "thumb": f"thumb/burstA{i}.jpg",
                "display": f"display/burstA{i}.jpg",
                "original": f"originals/burstA{i}.jpg",
                "video": None,
                "w": 5472, "h": 3648, "bytes": 8_000_000 + i,
                "exif": {
                    "camera": "SONY ZV-1", "lens": "9.4-25.7mm f/1.8-2.8",
                    "exposureTime": "1/250", "fNumber": 2.8, "iso": 200,
                    "focalLength": "9.4mm", "orientation": 1,
                },
            }
        )
    # Stand-in for the animated webp (img2webp may not be installed).
    shutil.copy(album / "thumb" / "burstA2.jpg", album / "burstA-preview.jpg")
    bursts.append(
        {
            "id": "b0000", "type": "photo", "capturedAt": "2026-08-29T14:00:00.500",
            "count": len(frames), "coverIndex": 0, "thumbW": 480, "thumbH": 320,
            "preview": "burstA-preview.jpg", "frames": frames,
        }
    )

    # A second multi-frame burst, so "the preview follows the finger from
    # tile to tile" is actually testable.
    frames_d = []
    for i in range(1, 4):
        hue = (200 + i * 25) % 360
        solid(album / "thumb" / f"burstD{i}.jpg", 480, 300, hue, f"D#{i}", 60)
        solid(album / "display" / f"burstD{i}.jpg", 1600, 1000, hue, f"Burst D #{i}", 130)
        shutil.copy(album / "display" / f"burstD{i}.jpg", album / "originals" / f"burstD{i}.jpg")
        frames_d.append(
            {
                "hash": f"burstD{i}",
                "thumb": f"thumb/burstD{i}.jpg",
                "display": f"display/burstD{i}.jpg",
                "original": f"originals/burstD{i}.jpg",
                "video": None,
                "w": 5472, "h": 3420, "bytes": 8_100_000 + i,
                "exif": {"camera": "SONY ZV-1", "exposureTime": "1/400", "fNumber": 3.5, "iso": 160},
            }
        )
    shutil.copy(album / "thumb" / "burstD2.jpg", album / "burstD-preview.jpg")
    bursts.append(
        {
            "id": "b0001", "type": "photo", "capturedAt": "2026-08-29T14:02:00",
            "count": len(frames_d), "coverIndex": 0, "thumbW": 480, "thumbH": 300,
            "preview": "burstD-preview.jpg", "frames": frames_d,
        }
    )

    solid(album / "thumb" / "singleB.jpg", 480, 360, 200, "Single B", 44)
    solid(album / "display" / "singleB.jpg", 1600, 1200, 200, "Single B", 110)
    shutil.copy(album / "display" / "singleB.jpg", album / "originals" / "singleB.jpg")
    bursts.append(
        {
            "id": "b0002", "type": "photo", "capturedAt": "2026-08-29T14:05:00",
            "count": 1, "coverIndex": 0, "thumbW": 480, "thumbH": 360, "preview": None,
            "frames": [
                {
                    "hash": "singleB", "thumb": "thumb/singleB.jpg",
                    "display": "display/singleB.jpg", "original": "originals/singleB.jpg",
                    "video": None, "w": 5472, "h": 4104, "bytes": 7_500_000,
                    "exif": {"camera": "SONY ZV-1", "exposureTime": "1/500", "fNumber": 4.0, "iso": 100},
                }
            ],
        }
    )

    solid(album / "thumb" / "videoC.jpg", 480, 270, 0, "Video C", 40)
    (album / "video").mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
            "-i", "testsrc=size=640x360:duration=2:rate=15",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(album / "video" / "videoC.mp4"),
        ]
    )
    shutil.copy(album / "video" / "videoC.mp4", album / "originals" / "videoC.mp4")
    bursts.append(
        {
            "id": "b0003", "type": "video", "capturedAt": "2026-08-29T14:10:00",
            "count": 1, "coverIndex": 0, "thumbW": 480, "thumbH": 270, "preview": None,
            "frames": [
                {
                    "hash": "videoC", "thumb": "thumb/videoC.jpg", "display": None,
                    "original": "originals/videoC.mp4", "video": "video/videoC.mp4",
                    "w": 1920, "h": 1080, "bytes": 20_000_000,
                    "exif": {"camera": "SONY ZV-1", "durationSeconds": 2.0},
                }
            ],
        }
    )

    album_doc = {
        "id": ALBUM_ID, "title": "Пляж", "date": "2026-08-29",
        "generatedAt": "2026-08-29T21:45:00", "bursts": bursts,
    }
    (album / "album.json").write_text(json.dumps(album_doc, indent=2, ensure_ascii=False), encoding="utf-8")

    index = {
        "albums": [
            {
                "id": ALBUM_ID, "title": "Пляж", "date": "2026-08-29",
                "cover": f"albums/{ALBUM_ID}/thumb/burstA1.jpg",
                "coverW": 480, "coverH": 320,
                "burstCount": len(bursts), "frameCount": sum(b["count"] for b in bursts),
            }
        ]
    }
    (site / "albums.json").write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"fixture site: {site}")


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/pics-site"))
