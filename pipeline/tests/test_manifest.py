from picscli.manifest import Manifest


def test_dedup_and_resume_roundtrip(tmp_path):
    db_path = tmp_path / "state.db"

    with Manifest(db_path) as db:
        db.ensure_album("2026-08-29-abc123", "Beach day", "2026-08-29")
        assert db.album_exists("2026-08-29-abc123")
        assert not db.album_exists("nope")

        db.register_file(
            file_hash="h1",
            kind="photo",
            source_path="/card/DSC0001.JPG",
            album_id="2026-08-29-abc123",
            burst_id="b0000",
            frame_index=0,
            captured_at="2026-08-29T14:00:00",
        )
        assert db.find_by_hash("h1") is not None
        assert db.find_by_hash("missing") is None
        assert not db.is_processed("h1")

        db.mark_processed("h1")
        assert db.is_processed("h1")

        db.mark_uploaded("h1")

    # Reopen to confirm persistence across connections.
    with Manifest(db_path) as db:
        record = db.find_by_hash("h1")
        assert record.album_id == "2026-08-29-abc123"
        assert record.uploaded_at is not None

        files = db.files_for_album("2026-08-29-abc123")
        assert len(files) == 1
        assert files[0].hash == "h1"

        albums = db.list_albums()
        assert [a["id"] for a in albums] == ["2026-08-29-abc123"]


def test_register_file_is_idempotent(tmp_path):
    with Manifest(tmp_path / "state.db") as db:
        db.ensure_album("album1", "t", "2026-08-29")
        for _ in range(3):
            db.register_file(
                file_hash="h1",
                kind="photo",
                source_path="/card/DSC0001.JPG",
                album_id="album1",
                burst_id="b0000",
                frame_index=0,
                captured_at="2026-08-29T14:00:00",
            )
        assert len(db.files_for_album("album1")) == 1
