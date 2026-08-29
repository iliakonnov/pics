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


def _web_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(os.environ.get("PICS_WEB", _DEFAULT_WEB_ROOT)).expanduser().resolve()


@click.group()
def main() -> None:
    """Import, process and publish Sony ZV-1 photo/video albums."""


@main.command("import")
@click.argument("card_root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--title", default=None, help="Album title (default: capture date)")
@click.option("--album-id", default=None, help="Reuse a specific album id (to resume an interrupted import)")
@library_option
def import_cmd(card_root: Path, title: str | None, album_id: str | None, library: str | None) -> None:
    """Import JPEGs/videos from CARD_ROOT as one new album."""
    settings = _settings(library)
    try:
        result_id = importer.run_import(card_root, settings, title=title, album_id=album_id, log=click.echo)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"\nImported album '{result_id}'.")
    click.echo(f"Local files: {settings.album_dir(result_id)}")
    click.echo("Review it, then publish with: pics upload --album " + result_id)


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
@click.option("--album", "album_id", default=None, help="Upload only this album id")
@click.option("--all", "upload_all", is_flag=True, help="Upload every local album")
@click.option("--web", "web_root_opt", default=None, help="Path to the web/ app root (default: repo's web/ dir)")
@click.option("--force", is_flag=True, help="Re-upload immutable media even if already present remotely")
@library_option
def upload_cmd(
    album_id: str | None,
    upload_all: bool,
    web_root_opt: str | None,
    force: bool,
    library: str | None,
) -> None:
    """Publish the web app, albums.json and one/all albums to S3."""
    if not album_id and not upload_all:
        raise click.ClickException("pass --album ID or --all")

    settings = _settings(library)
    web_root = _web_root(web_root_opt)
    client = upload.get_client(settings)

    site_stats = upload.sync_site_assets(client, settings, web_root)
    click.echo(f"site assets: {site_stats.uploaded} uploaded")

    album_ids: list[str]
    if upload_all:
        if not settings.albums_dir.is_dir():
            raise click.ClickException("no local albums found")
        album_ids = sorted(p.name for p in settings.albums_dir.iterdir() if p.is_dir())
    else:
        album_ids = [album_id]

    with Manifest(settings.state_db_path) as db:
        for aid in album_ids:
            stats = upload.sync_album(client, settings, aid, web_root, force=force)
            click.echo(f"{aid}: {stats.uploaded} uploaded, {stats.skipped} already present")
            for f in db.files_for_album(aid):
                db.mark_uploaded(f.hash)


@main.command("preview")
@click.option("--album", "album_id", default=None, help="Preview only this album id (default: all)")
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
    """Build the site locally and serve it — nothing is uploaded."""
    settings = _settings(library)
    web_root = _web_root(web_root_opt)

    if album_id:
        album_ids = [album_id]
    else:
        if not settings.albums_dir.is_dir():
            raise click.ClickException("no local albums yet — run `pics import` first")
        album_ids = sorted(p.name for p in settings.albums_dir.iterdir() if p.is_dir())
    if not album_ids:
        raise click.ClickException("no albums to preview")

    target = Path(out_dir).expanduser() if out_dir else settings.library_root / "_preview"
    root = preview.build_site(settings, web_root, target, album_ids)
    click.echo(f"site assembled at {root} ({len(album_ids)} album(s))")

    if no_serve:
        return
    click.echo(f"serving http://localhost:{port}/  (Ctrl-C to stop)")
    try:
        preview.serve(root, port)
    except KeyboardInterrupt:
        click.echo("\nstopped")


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
