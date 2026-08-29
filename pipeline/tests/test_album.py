from datetime import datetime

from picscli.album import Burst, Frame, album_summary, build_album_json, upsert_albums_index


def make_frame(h="h1"):
    return Frame(
        hash=h,
        kind="photo",
        thumb=f"thumb/{h}.jpg",
        original=f"originals/{h}.jpg",
        display=f"display/{h}.jpg",
        video=None,
        width=5472,
        height=3648,
        size_bytes=1234,
        exif={"camera": "SONY ZV-1"},
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


def test_album_summary_uses_first_burst_cover():
    burst1 = Burst(id="b0000", kind="photo", captured_at=datetime(2026, 8, 29, 14, 0, 0), thumb_w=480, thumb_h=320, frames=[make_frame("h1")])
    burst2 = Burst(id="b0001", kind="video", captured_at=datetime(2026, 8, 29, 14, 5, 0), thumb_w=480, thumb_h=270, frames=[make_frame("h2")])
    doc = build_album_json(album_id="a1", title="Beach", date="2026-08-29", bursts=[burst1, burst2], generated_at=datetime.now())

    summary = album_summary(doc)
    assert summary["cover"] == "albums/a1/thumb/h1.jpg"
    assert summary["burstCount"] == 2
    assert summary["frameCount"] == 2


def test_album_summary_empty_album():
    doc = build_album_json(album_id="empty", title="Empty", date="2026-08-29", bursts=[], generated_at=datetime.now())
    summary = album_summary(doc)
    assert summary["cover"] is None
    assert summary["burstCount"] == 0


def test_upsert_albums_index_replaces_same_id_and_sorts_by_date():
    index = {"albums": [{"id": "a1", "date": "2026-08-01"}, {"id": "a2", "date": "2026-08-15"}]}
    updated = upsert_albums_index(index, {"id": "a1", "date": "2026-08-20"})

    ids_dates = [(a["id"], a["date"]) for a in updated["albums"]]
    assert ids_dates == [("a2", "2026-08-15"), ("a1", "2026-08-20")]


def test_upsert_albums_index_from_empty():
    updated = upsert_albums_index(None, {"id": "a1", "date": "2026-08-20"})
    assert updated == {"albums": [{"id": "a1", "date": "2026-08-20"}]}
