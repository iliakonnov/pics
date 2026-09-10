from __future__ import annotations

import os
from pathlib import Path

import click

from . import config, importer, preview, upload
from .manifest import Manifest

_DEFAULT_WEB_ROOT = Path(__file__).resolve().parents[2] / "web"


def library_option(f):
    return click.option(
        "--library",
        default=None,
        help="Local library root (default: $PICS_LIBRARY or ~/Pictures/zv1)",
    )(f)


def _settings(library: str | None) -> config.Settings:
    root = Path(library).expanduser() if library else None
    return config.load_settings(library_root=root)


def _local_albums(settings: config.Settings) -> list[str]:
    if not settings.albums_dir.is_dir():
        return []
    return sorted(p.name for p in settings.albums_dir.iterdir() if p.is_dir())


def _resolve_album(settings: config.Settings, album_id: str | None) -> str:
    if album_id:
        return album_id
    found = _local_albums(settings)
    if not found:
        raise click.ClickException("no local albums — run `pics import` first")
    if len(found) > 1:
        raise click.ClickException("several local albums; pick one with --album:\n  " + "\n  ".join(found))
    return found[0]


def _web_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(os.environ.get("PICS_WEB", _DEFAULT_WEB_ROOT)).expanduser().resolve()


@click.group()
def main() -> None:
    """Import, process and publish Sony ZV-1 photo/video albums."""


@main.command("import")
@click.argument("card_roots", nargs=-1, required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--title", default=None, help="Album title (default: capture date)")
@click.option("--album-id", default=None, help="Reuse a specific album id (to resume an interrupted import)")
@click.option("-j", "--jobs", default=None, type=int, help="Parallel workers (default: one per core)")
@click.option(
    "--mp4-min-seconds",
    default=None,
    type=float,
    help="Offer a real-time MP4 for bursts spanning at least this long "
    f"(default {config.BURST_MP4_MIN_SECONDS}; 0 disables)",
)
@click.option(
    "--faces",
    "group_faces",
    is_flag=True,
    help="Group photos by who is in them and offer a face filter (needs insightface)",
)
@click.option(
    "--max-faces",
    default=config.FACE_MAX_IDENTITIES,
    show_default=True,
    help="Most people to offer in the face filter",
)
@click.option(
    "--best-frame",
    "pick_covers",
    is_flag=True,
    help="Choose each burst's cover by sharpness and open eyes (needs mediapipe)",
)
@click.option(
    "--develop-jobs",
    default=None,
    type=int,
    help=f"Parallel darktable-cli instances for raw development (default {config.DEVELOP_JOBS}; "
    "kept separate from --jobs since darktable is internally multi-threaded and memory-hungry)",
)
@click.option(
    "--redevelop",
    is_flag=True,
    help="Re-develop every raw frame even if already developed with the current settings",
)
@click.option(
    "--skip-raw",
    is_flag=True,
    help="Ignore ARW files entirely and import camera JPEGs only",
)
@library_option
def import_cmd(
    card_roots: tuple[Path, ...],
    title: str | None,
    album_id: str | None,
    jobs: int | None,
    mp4_min_seconds: float | None,
    group_faces: bool,
    max_faces: int,
    pick_covers: bool,
    develop_jobs: int | None,
    redevelop: bool,
    skip_raw: bool,
    library: str | None,
) -> None:
    """Import JPEGs/RAWs/videos from one or more CARD_ROOTS as one new album."""
    settings = _settings(library)
    try:
        result_id = importer.run_import(
            card_roots,
            settings,
            title=title,
            album_id=album_id,
            jobs=jobs,
            mp4_min_seconds=mp4_min_seconds,
            group_faces=group_faces,
            pick_covers=pick_covers,
            max_faces=max_faces,
            develop_jobs=develop_jobs,
            redevelop=redevelop,
            skip_raw=skip_raw,
            log=click.echo,
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"\nImported album '{result_id}'.")
    click.echo(f"Local files: {settings.album_dir(result_id)}")
    click.echo(f"Review it with: pics preview --album {result_id}")
    click.echo(f"Publish it with: pics upload --album {result_id}")


@main.command("list")
@library_option
def list_cmd(library: str | None) -> None:
    """List locally known albums and their processed file counts."""
    settings = _settings(library)
    if not settings.state_db_path.exists():
        click.echo("no albums yet — run `pics import` first")
        return
    with Manifest(settings.state_db_path) as db:
        albums = db.list_albums()
        if not albums:
            click.echo("no albums yet")
            return
        for a in albums:
            files = db.files_for_album(a["id"])
            uploaded = sum(1 for f in files if f.uploaded_at)
            click.echo(f"{a['id']}  \"{a['title']}\"  {len(files)} file(s), {uploaded} uploaded")


@main.command("upload")
@click.option("--album", "album_id", default=None, help="Album id to publish (default: the only local album)")
@click.option("--web", "web_root_opt", default=None, help="Path to the web/ app root (default: repo's web/ dir)")
@click.option("--force", is_flag=True, help="Re-upload media even if already present remotely")
@click.option("-j", "--jobs", default=None, type=int, help="Parallel transfers (default: 2 per core, max 16)")
@library_option
def upload_cmd(
    album_id: str | None, web_root_opt: str | None, force: bool, jobs: int | None, library: str | None
) -> None:
    """Publish one album: originals to Yandex Disk, gallery to S3."""
    settings = _settings(library)
    resolved = _resolve_album(settings, album_id)
    web_root = _web_root(web_root_opt)

    try:
        disk_client = upload.get_disk_client(settings)
        click.echo("publishing originals to Yandex Disk...")
        disk_url = upload.sync_album_to_disk(disk_client, settings, resolved, force=force, jobs=jobs, log=click.echo)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    client = upload.get_client(settings)
    stats = upload.sync_album(
        client, settings, resolved, web_root, force=force, jobs=jobs, log=click.echo
    )
    click.echo(f"{resolved}: {stats.uploaded} uploaded, {stats.skipped} already present")
    click.echo("\nShare this link:")
    click.echo("  " + upload.album_url(settings, resolved))
    click.echo("\nOriginals (raw + full-resolution JPEG/video) on Yandex Disk:")
    click.echo("  " + disk_url)
    click.echo("\nThe directory is self-contained, so the local copy can now be deleted:")
    click.echo(f"  rm -rf {settings.album_dir(resolved)}")


@main.command("preview")
@click.option("--album", "album_id", default=None, help="Album id to preview (default: the only local album)")
@click.option("--out", "out_dir", default=None, help="Where to assemble the site (default: <library>/_preview)")
@click.option("--port", default=8000, show_default=True, help="Port to serve on")
@click.option("--web", "web_root_opt", default=None, help="Path to the web/ app root (default: repo's web/ dir)")
@click.option("--no-serve", is_flag=True, help="Just assemble the site and print the path")
@library_option
def preview_cmd(
    album_id: str | None,
    out_dir: str | None,
    port: int,
    web_root_opt: str | None,
    no_serve: bool,
    library: str | None,
) -> None:
    """Serve one album locally, exactly as it will be published."""
    settings = _settings(library)
    resolved = _resolve_album(settings, album_id)
    target = Path(out_dir).expanduser() if out_dir else settings.library_root / "_preview" / resolved

    try:
        root = preview.build_site(settings, _web_root(web_root_opt), target, resolved)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"album {resolved} assembled at {root}")

    if no_serve:
        return
    click.echo(f"serving http://localhost:{port}/  (Ctrl-C to stop)")
    try:
        preview.serve(root, port)
    except KeyboardInterrupt:
        click.echo("\nstopped")


@main.command("cold-originals")
@click.option("--dry-run", is_flag=True, help="List what would change without modifying anything")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt")
@library_option
def cold_originals_cmd(dry_run: bool, yes: bool, library: str | None) -> None:
    """Legacy: move any already-uploaded S3 originals/ object to COLD storage.

    Full-resolution originals now publish to Yandex Disk instead (see
    `pics upload`) and are never written to S3, so this only matters for
    albums published before that change (across every album in the bucket,
    including ones no longer kept locally).
    """
    settings = _settings(library)
    client = upload.get_client(settings)
    keys = upload.find_originals_not_cold(client, settings.s3_bucket)
    if not keys:
        click.echo("nothing to do — every original is already in COLD storage.")
        return

    click.echo(f"{len(keys)} object(s) under originals/ are not yet in COLD storage.")
    if dry_run:
        for key in keys:
            click.echo(f"  {key}")
        return

    if not yes and not click.confirm(
        f"Move {len(keys)} object(s) to COLD? (COLD storage carries a minimum "
        "storage duration and per-retrieval fees — check current Yandex Object "
        "Storage pricing first)"
    ):
        raise click.Abort()

    moved = upload.cold_originals(settings, settings.s3_bucket, keys, log=click.echo)
    click.echo(f"moved {moved} object(s) to COLD storage.")


@main.command("setup-bucket")
@library_option
def setup_bucket_cmd(library: str | None) -> None:
    """One-time: enable public-read + static website hosting on the S3 bucket."""
    settings = _settings(library)
    client = upload.get_client(settings)
    url = upload.setup_bucket(client, settings.s3_bucket)
    click.echo(f"bucket '{settings.s3_bucket}' configured for public static hosting.")
    click.echo(f"expected site URL: {url}")
    click.echo("(verify the exact website-hosting hostname in the Yandex Cloud console)")


if __name__ == "__main__":
    main()
