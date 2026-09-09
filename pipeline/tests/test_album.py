from datetime import datetime

from picscli.album import Burst, Frame, build_album_json


def make_frame(h="h1", disk=None):
    return Frame(
        hash=h,
        kind="photo",
        thumb=f"thumb/{h}.jpg",
        original=f"originals/{h}.jpg",
        display=f"display/{h}.jpg",
        medium=f"medium/{h}.jpg",
        video=None,
        width=5472,
        height=3648,
        size_bytes=1234,
        exif={"camera": "SONY ZV-1"},
        disk=disk,
    )


def test_build_album_json_shape():
    burst = Burst(
        id="b0000",
        kind="photo",
        captured_at=datetime(2026, 8, 29, 14, 0, 0),
        thumb_w=480,
        thumb_h=320,
        frames=[make_frame("h1"), make_frame("h2")],
        preview="preview/b0000.webp",
        cover_index=0,
    )
    doc = build_album_json(album_id="a1", title="Beach", date="2026-08-29", bursts=[burst], generated_at=datetime(2026, 8, 29, 15, 0, 0))

    assert doc["id"] == "a1"
    assert doc["bursts"][0]["count"] == 2
    assert doc["bursts"][0]["preview"] == "preview/b0000.webp"
    assert doc["bursts"][0]["frames"][0]["hash"] == "h1"
    assert doc["bursts"][0]["frames"][1]["display"] == "display/h2.jpg"
    assert doc["bursts"][0]["frames"][0]["medium"] == "medium/h1.jpg"
    assert doc["diskUrl"] is None


def test_disk_field_emitted_when_present():
    burst = Burst(
        id="b0000",
        kind="photo",
        captured_at=datetime(2026, 8, 29, 14, 0, 0),
        thumb_w=480,
        thumb_h=320,
        frames=[make_frame("h1", disk={"jpg": "JPG/DSC01234.JPG", "raw": "RAW/DSC01234.ARW"})],
        cover_index=0,
    )
    doc = build_album_json(
        album_id="a1", title="Beach", date="2026-08-29", bursts=[burst],
        generated_at=datetime(2026, 8, 29, 15, 0, 0), disk_url="https://disk.yandex.ru/d/abc123",
    )
    assert doc["diskUrl"] == "https://disk.yandex.ru/d/abc123"
    assert doc["bursts"][0]["frames"][0]["disk"] == {"jpg": "JPG/DSC01234.JPG", "raw": "RAW/DSC01234.ARW"}
