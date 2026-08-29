# pics

Import photos/video from a Sony ZV-1, group them into bursts, generate web-sized
derivatives, and publish a static gallery to Yandex Object Storage (S3-compatible).

Two parts:

- `pipeline/` — a Python CLI (`pics`) that does the import/convert/publish work.
- `web/` — a static, build-step-free JS app (album list + gallery + fullscreen
  viewer) that gets uploaded alongside the generated albums.

## 1. System dependencies

Required on PATH: `exiftool`, `ffmpeg`, `ffprobe`, `magick`/`identify` (ImageMagick),
`jpegtran`, `img2webp` (from `libwebp`).

On this machine `ffmpeg`/`ffprobe`/`magick`/`jpegtran`/`cjpeg` were already
installed; `exiftool` and `libwebp` (for `img2webp`) were not, and installing them
needs sudo, which this session doesn't have non-interactively:

```sh
sudo pacman -S perl-image-exiftool libwebp
```

Run `pics import` once after installing — it checks all required tools up front
and fails fast with a clear message if anything is missing.

## 2. Python setup

```sh
cd pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs the `pics` command (and `boto3`/`click`).

Run the test suite (pure logic only — burst grouping, the sqlite manifest,
album.json building; these don't need exiftool/ffmpeg or real photos):

```sh
pytest
```

## 3. Configuration

`pics` reads config from environment variables, or a `.env` file (`KEY=VALUE`
lines) in the current directory:

| Variable | Purpose | Default |
|---|---|---|
| `PICS_LIBRARY` | local archive root (originals + generated files + sqlite state) | `~/Pictures/zv1` |
| `PICS_S3_BUCKET` | target bucket name | — |
| `PICS_S3_ACCESS_KEY` / `PICS_S3_SECRET_KEY` | static access key for the bucket | — |
| `PICS_S3_ENDPOINT` | S3-compatible endpoint | `https://storage.yandexcloud.net` |
| `PICS_S3_REGION` | | `ru-central1` |

Create the static key pair for the bucket in the Yandex Cloud console
(Object Storage → your bucket → for a *service account* with
`storage.editor`, generate a static access key) and put it in a `.env` file
(already gitignored) next to where you run `pics`.

## 4. Usage

```sh
# One-time: make the bucket publicly readable and enable static website hosting.
pics setup-bucket

# Import everything on the card as one new album.
pics import /run/media/$USER/SONY_CARD --title "Выходные на море"

# Review the result locally (originals/thumb/display/preview/video under
# $PICS_LIBRARY/albums/<album-id>/, plus album.json), then publish it:
pics upload --album 2026-08-29-a1b2c3

# Or publish everything that's changed:
pics upload --all

pics list
```

`import` never uploads by itself — review the generated album locally first.
`upload` re-uploads `index.html`/`album.html`/JS/CSS/JSON unconditionally
(they're mutable) and skips media files that already exist remotely (they're
named by content hash, so an existing key is always identical content).

Re-running `pics import` on the same card is safe: files are deduplicated
globally by content hash (sha256), so nothing gets reprocessed or
double-published. Re-running with the *same* `--album-id` resumes/repairs an
interrupted import instead of creating a new one.

## 5. How it works

- **Scanning** (`picscli/scan.py`) walks the card recursively and keeps only
  `.jpg`/`.jpeg` and `.mp4/.mov/.mts/.m2ts` files (RAW, THM, and Sony's
  `.MOFF`/`.MODD` sidecars are ignored by extension).
- **Metadata** (`picscli/metadata.py`) batch-reads EXIF/QuickTime/MakerNotes
  tags with one `exiftool` call for the whole import.
- **Grouping** (`picscli/grouping.py`) clusters photos into bursts, preferring
  Sony's continuous-shooting sequence MakerNotes tags and falling back to
  timestamp-gap clustering (using `SubSecTimeOriginal`) when those aren't
  present. Each video is always its own single-item burst.

  ⚠️ **The exact Sony MakerNotes tag names in `picscli/config.py`
  (`SONY_SEQUENCE_NUMBER_TAGS` etc.) were not verified against a real ZV-1
  file** — there were no sample photos available while building this. Before
  trusting sequence-based grouping, run:

  ```sh
  exiftool -G1 -a -s -Sony:all -Composite:all path/to/sample.JPG
  ```

  against a real burst from your card and check the tag names still match;
  the timestamp-gap fallback (1s default, `BURST_MAX_GAP_SECONDS`) will
  otherwise carry continuous-shooting grouping on its own, just less
  precisely at the exact boundary between two bursts shot back-to-back.

- **Conversion** (`picscli/imaging.py`): originals are losslessly re-encoded
  to progressive JPEG (`jpegtran`, full EXIF kept, pixels unchanged); a
  ~2560px "display" JPEG and a ~480px thumbnail are generated
  (auto-oriented, EXIF stripped from these derivatives only); an animated
  WebP preview is built from the burst's own thumbnails (photos) or from
  frames sampled across the clip (video) whenever there's more than one
  frame to show. Video is transcoded to 1080p H.264/AAC with
  `+faststart`; the original video file is archived and published as-is
  alongside it.
- **Publishing** (`picscli/upload.py`): content-hash-named media gets
  `Cache-Control: public, max-age=31536000, immutable`; HTML/JSON/JS/CSS get
  `no-cache`. The bucket is public-read (per your choice — no signed URLs).

## 6. Web app

Plain ES modules, no bundler. `web/index.html` lists albums from
`/albums.json`; `web/album.html` is uploaded as `albums/<id>/index.html` for
every album and reads `./album.json` relative to itself. Gallery tiles show
their animated WebP preview on hover (mouse) or automatically when scrolled
into view (touch, since there's no hover there).

Fullscreen viewer controls:

| | frames within a burst | between bursts | close |
|---|---|---|---|
| keyboard | Up / Down | Left / Right | Escape |
| mouse | wheel; scroll the filmstrip | side arrows | close button |
| touch | **drag the filmstrip** (speed control) or tap a thumbnail | horizontal swipe on the image | swipe down |

The touch filmstrip is a **shuttle/jog control, not a scrollbar**: the
finger's horizontal displacement from where it landed sets the *speed* of
playback, not the frame. Push further right and the burst runs forward
faster (up to 24 fps, the ZV-1's own top burst rate), further left and it
runs backward, and it wraps around the ends of the burst. There's a small
deadzone so a tap isn't read as a drag, and a quadratic ramp so slow,
precise stepping is possible just outside it. Lifting the finger stops the
animation immediately on the current frame — no inertia. To pick a specific
frame directly, tap its thumbnail. While shuttling, the stage shows the
(already-loaded) thumbnail so frames can change at full rate without waiting
on 2560px JPEGs; the sharp image is restored the moment the finger lifts.

The current burst+frame is reflected in the URL hash so a link can point at
a specific frame, and the browser Back button always closes the viewer back
to the grid in one step.

### Testing the UI

`tools/` has a repeatable harness that drives a real headless browser, so
none of this depends on having photos or a phone at hand:

```sh
python3 tools/make_fixture.py /tmp/pics-site      # synthetic album (needs magick + ffmpeg)
python3 -m http.server 8791 --directory /tmp/pics-site &

python3 -m venv /tmp/pw-venv && /tmp/pw-venv/bin/pip install playwright
/tmp/pw-venv/bin/python tools/ui_test.py          # drives the system chromium
```

It exercises hover/touch previews, keyboard/wheel navigation, the touch
shuttle (speed ramp, deadzone, wrapping, stop-on-release, tap-to-select),
burst swipes and deep-link + Back, asserting on real DOM state and writing
screenshots to `/tmp/pics-screens`. All checks currently pass. It has *not*
been run on physical phone hardware — Chromium's touch emulation is a good
proxy but not identical, so give the shuttle a try on a real device before
relying on it.

## 7. Known gaps / next steps

- Sony MakerNotes tag names for burst-sequence detection are unverified (see
  above) — check against a real card.
- No pinch-zoom/pan in the fullscreen viewer; "download original" is the
  escape hatch for inspecting full detail.
- No per-photo/per-burst editing (hide, delete, reorder, retitle) — the
  pipeline is import-only today.
- `setup-bucket`'s printed website endpoint URL is a best guess
  (`http://<bucket>.website.yandexcloud.net`); confirm the exact hostname in
  the Yandex Cloud console after running it.
