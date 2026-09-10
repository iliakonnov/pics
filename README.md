# pics

Import raw photos/video from a Sony ZV-1, develop ARW files into full-resolution
JPEGs with darktable, group everything into bursts, generate web-sized
derivatives, and publish: full-resolution originals to Yandex Disk, the gallery
itself to Yandex Object Storage (S3-compatible).

Each album is published to its own directory under an unguessable name and is
entirely self-contained — its own page, its own copy of the JS/CSS, its own
media. The directory URL is the share link. There is no index tying albums
together, so once an album is uploaded the local copy can be deleted and
forgotten.

Two parts:

- `pipeline/` — a Python CLI (`pics`) that does the import/convert/publish work.
- `web/` — a static, build-step-free JS app (album list + gallery + fullscreen
  viewer) that gets uploaded alongside the generated albums.

## 1. System dependencies

Required on PATH: `exiftool`, `ffmpeg`, `ffprobe`, `magick`/`identify` (ImageMagick),
`jpegtran`, `img2webp` (from `libwebp`).

On Arch/EndeavourOS:

```sh
sudo pacman -S perl-image-exiftool libwebp-tools
```

Note that `libwebp` alone ships only the library and man pages — the
`img2webp` binary lives in **libwebp-tools**. Arch also installs exiftool to
`/usr/bin/vendor_perl/`, which is on the default PATH via
`/etc/profile.d/perlbin.sh` but not in every restricted shell; set
`PICS_EXIFTOOL=/usr/bin/vendor_perl/exiftool` if `pics` cannot find it.

Run `pics import` once after installing — it checks all required tools up front
and fails fast with a clear message if anything is missing.

Only needed when the card has `.ARW` raw files (a JPEG-only import never
touches these): `darktable-cli` (from **darktable**, tested against 5.6.1)
and `dcraw_emu` (from **libraw**).

```sh
sudo pacman -S darktable libraw
```

If you have a working NVIDIA/AMD GPU, also install the matching OpenCL
package (e.g. `opencl-nvidia`) and set `PICS_DEVELOP_OPENCL_FIRST=1` once
you've confirmed `clinfo -l` lists it — darktable's NLM denoiser is a lot
faster on a GPU. Without a confirmed-working GPU, leave this unset: a
driver that merely *claims* OpenCL support without a real device behind it
doesn't make darktable fail, it just makes every frame take minutes instead
of ~15-30 seconds.

**On Windows with WSL**: WSL2's own OpenCL passthrough to the GPU is
unreliable, so run everything except darktable natively in WSL, but point
`PICS_DARKTABLE` at the real Windows build (`darktable-cli.exe`) so it gets
a proper driver and real GPU access — WSL can invoke a Windows `.exe`
directly. That native process can't resolve WSL's own paths, though, so
also set `PICS_DEVELOP_WINDOWS_TEMP_DIR` to a Windows-visible directory
given as its WSL path, e.g.:

```sh
PICS_DARKTABLE=/mnt/c/Program\ Files/darktable/bin/darktable-cli.exe
PICS_DEVELOP_WINDOWS_TEMP_DIR=/mnt/c/Users/<you>/AppData/Local/Temp/pics-develop
```

The source ARWs themselves must also live under `/mnt/<drive>/...` (i.e. on
a real Windows drive, not inside WSL's own filesystem) for the same reason.
`pics import` checks both of these up front and fails with a clear message
if either is missing.

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
lines) found in the working directory or any directory above it:

| Variable | Purpose | Default |
|---|---|---|
| `PICS_LIBRARY` | local archive root (working files + sqlite state) | `~/Pictures/zv1` |
| `PICS_S3_BUCKET` | target bucket name (gallery only — no more originals) | — |
| `PICS_S3_ACCESS_KEY` / `PICS_S3_SECRET_KEY` | static access key for the bucket | — |
| `PICS_S3_ENDPOINT` | S3-compatible endpoint | `https://storage.yandexcloud.net` |
| `PICS_S3_REGION` | | `ru-central1` |
| `PICS_YADISK_TOKEN` | OAuth token for full-resolution originals | — |
| `PICS_DEVELOP_OPENCL_FIRST` | try OpenCL before CPU when developing raws (`1` to enable) | `0` |

Create the static key pair for the bucket in the Yandex Cloud console
(Object Storage → your bucket → for a *service account* with
`storage.editor`, generate a static access key) and put it in a `.env` file
(already gitignored) next to where you run `pics`.

For `PICS_YADISK_TOKEN`: register an app at
[oauth.yandex.ru](https://oauth.yandex.ru/) (or reuse one) with the
`cloud_api:disk.app_folder` scope, then get a token for it and put it in
the same `.env` file. Originals live under that app's private folder on
your Yandex Disk (`Приложения/<app name>/`), one directory per album,
which the app publishes read-only links into.

## 4. Usage

```sh
# One-time: make the bucket publicly readable and enable static website hosting.
pics setup-bucket

# Import everything on the card as one new album. Work is spread across all
# cores; -j sets the worker count. ARW files are developed automatically
# (see below); camera JPEGs alongside an ARW of the same name are skipped.
pics import /run/media/$USER/SONY_CARD --title "Выходные на море"

# Several DCIM folders (e.g. one per shooting day) become one album in a
# single call: grouping, cover selection and face clustering all need to
# see every frame at once, so importing each folder separately -- even
# into the same --album-id -- rewrites album.json from scratch each time
# and leaves only the last folder's frames in it.
pics import /run/media/$USER/SONY_CARD/DCIM/1000904 /run/media/$USER/SONY_CARD/DCIM/1010905

# Optional extras, each needing its own heavy dependency:
#   --best-frame  choose each burst's cover (mediapipe)
#   --faces       group photos by person and show a face filter (insightface)
# Raw-develop specific:
#   --develop-jobs N  parallel darktable-cli instances (default 2 — kept
#                      separate from -j since darktable is itself
#                      multi-threaded and memory-hungry)
#   --redevelop       re-develop every raw frame even if unchanged
#   --skip-raw        ignore ARW files, import camera JPEGs only

# Look it over locally first, served exactly as it will be published:
pics preview --port 8000

# Publish it: full-resolution originals to Yandex Disk, the gallery to S3.
# Prints both links; --album can be omitted when there is only one local album.
pics upload

pics list

# The published directory needs nothing else, so afterwards:
rm -rf ~/Pictures/zv1/albums/<album-id>
```

`import` never uploads by itself. `upload` re-uploads the page, JS/CSS and
album.json unconditionally (they're mutable) and skips media that already
exists remotely — S3 media is named by content hash, so an existing key is
always identical content; Yandex Disk files are named by camera filename
and are skipped by an existence check instead (or use `--force`).

Albums published before raw support (originals in S3 COLD storage) keep
working as-is; nothing migrates automatically. That legacy path is still
available:

```sh
pics cold-originals --dry-run   # see what would move, no changes made
pics cold-originals             # move them (asks for confirmation first)
```

The album id is the secret: ten random letters (~57 bits), used as both the
directory name and the share link, so it encodes nothing about the date or
contents and stays easy to read out. Pass `--album-id` to choose your own.

Uploads run in parallel too (`-j`, default two per core): an album is
thousands of small files, where one round trip at a time is dominated by
latency rather than bandwidth.

Re-running `pics import` with the *same* `--album-id` **and the same
CARD_ROOTS** resumes or repairs an interrupted run rather than starting a
new album; existing derivatives are left alone. It is not a way to grow an
album incrementally with different folders on separate calls — each call
rebuilds album.json from only the CARD_ROOTS it was given, so pass every
folder at once instead (see above). Duplicates are removed only *within*
one import (the same file copied twice onto the card) — the same photos
may legitimately be imported again later into a fresh album.

## 5. How it works

- **Scanning** (`picscli/scan.py`) walks the card recursively and keeps
  `.jpg`/`.jpeg`, `.arw` and `.mp4/.mov/.mts/.m2ts` files (THM and Sony's
  `.MOFF`/`.MODD` sidecars are ignored by extension). When a camera JPEG
  and an ARW share a filename — the norm while switching to raw-only
  shooting — the JPEG is dropped: the ARW gets developed into the album
  original instead, and keeping both would put two near-identical frames
  in the same burst.
- **Metadata** (`picscli/metadata.py`) batch-reads EXIF/QuickTime/MakerNotes
  tags with one `exiftool` call for the whole import.
- **Grouping** (`picscli/grouping.py`) clusters photos into bursts, preferring
  Sony's continuous-shooting sequence MakerNotes tags and falling back to
  timestamp-gap clustering (using `SubSecTimeOriginal`) when those aren't
  present. Each video is always its own single-item burst.

  Verified against real ZV-1 files (exiftool 13.55): during continuous
  shooting `SequenceNumber` counts 1, 2, 3... while `SequenceLength` is
  **0** — it is not a frame count. Single shots report `SequenceNumber` 0.
  So a burst continues only on a strict +1 step, which correctly splits
  two bursts fired 0.5s apart — something a time gap alone cannot do.

- **Raw development** (`picscli/develop.py`, `rawanalysis.py`, `developplan.py`),
  when the card has ARW files, runs after grouping and before conversion:
  darktable-cli renders each raw into `originals/<hash>.jpg` — the album's
  full-resolution original — using a fixed base look (embedded lens
  correction, profiled non-local-means denoising in ISO-auto mode, sigmoid
  tone mapping, an AA-filter sharpen) plus one **auto-exposure correction
  per burst**: a cheap `dcraw_emu` decode gives a raw histogram, from which
  an EV is derived and shared by every frame of the burst — so lighting
  never jumps between shots of the same moment. Singles get their own EV.

  **Exposure-bracketed bursts are detected** (Sony's `ReleaseMode2`
  MakerNote, verified against a real bracketed set: value `2` with
  `ExposureCompensation` cycling across frames) and are developed frame by
  frame instead — no shared EV, since the whole point of a bracket is
  different exposures. They stay one scrollable burst in the gallery but
  are skipped for the animated preview, the real-time clip, and
  `--best-frame` cover scoring, since comparing sharpness or blink across
  wildly different exposures is meaningless.

  Re-running `import` skips frames whose raw analysis, EV and rendering
  settings haven't changed (cached in `develop.cache.json`), so a resumed
  or repeated import costs seconds rather than re-running darktable.
  `--redevelop` forces it anyway.

- **Conversion** (`picscli/imaging.py`), run across all cores — each
  subprocess is pinned to a single thread (`MAGICK_THREAD_LIMIT`), so the
  worker pool is the only source of parallelism rather than fighting
  ImageMagick's own: camera-JPEG originals are losslessly re-encoded to
  progressive JPEG (`jpegtran`, full EXIF kept, pixels unchanged) — raw
  originals already exist at this point, written by the develop stage
  above; a ~2560px "display" JPEG, a ~1280px "medium" copy and a ~480px
  thumbnail are generated from whichever original applies
  (auto-oriented, EXIF stripped from these derivatives only); an animated
  WebP preview is built from the burst's own thumbnails (photos, only when
  the burst also clears `--mp4-min-seconds` — see below) or from frames
  sampled across the clip (video, always). Video is transcoded to 1080p H.264/AAC with
  `+faststart` for in-browser playback; the untouched source video file is
  staged for Yandex Disk alongside it.
  Three sizes rather than two because of what a burst costs. At display
  size an average frame is 439KB, so a 24-frame burst is 10MB — and, worse,
  ~17MB *decoded* per frame, or ~420MB resident for the burst. The 1280px
  medium copy averages 106KB (2.5MB per burst) and still covers a phone
  screen at 3x DPR, which is what the viewer preloads and animates.

- **Burst clips**: a burst that spans at least `--mp4-min-seconds` of real
  shooting time is also rendered as an MP4 that plays at the speed it
  happened. Frame timings come from EXIF `SubSecTimeOriginal`, which the
  ZV-1 writes on every frame, so uneven pacing within a burst survives;
  ffmpeg's concat demuxer takes the per-frame durations and the result is
  resampled to constant 30fps, because variable-frame-rate MP4 plays back
  inconsistently across devices.

  Pick the threshold from the data: the camera fires fast, so bursts are
  short in *real* time even when they hold many frames. Of 283 multi-frame
  bursts in a 965-photo album, 1 spans over 1.5s, 14 over 0.5s and 56 over
  0.3s. The published album uses 0.5s: 14 clips, 9.3MB in total.

- **Cover selection** (`picscli/quality.py`, `--best-frame`, optional):
  picks the frame of each burst worth showing, by sharpness (variance of
  the Laplacian) among the frames whose subjects have their eyes open
  (MediaPipe blendshapes, graded into bands rather than one cutoff — a
  half-blink scores about 0.4). On a real album it moved 154 of 283
  multi-frame bursts off their first frame.

  General image-quality metrics were measured against 20 real bursts and
  are *not* a substitute: brisque, niqe, clipiqa and nima each picked a
  frame with someone mid-blink regularly (niqe in five bursts of the six
  that had one) and each picked the burst's *blurriest* frame several
  times. They score naturalness and aesthetics, not "which of these
  near-identical shots is the good one".

- **Face grouping** (`picscli/faces.py`, `--faces`, optional): detects and
  embeds faces with insightface, clusters them by cosine distance, and
  offers the result as a filter above the grid. Only the chosen cover of
  each burst is scanned — the frames of a burst are the same moment.
  Nobody is named or matched across albums. Four workers rather than one
  per core, because each model copy costs about 600MB.

- **Caching** (`picscli/cache.py`): cover selection and face grouping are
  the only steps that cost minutes rather than seconds, and unlike the
  image conversions they produce metadata rather than files, so they used
  to run again on every import. Their results are now kept in
  `covers.cache.json` and `faces.cache.json` beside the album, keyed by a
  fingerprint of the frames they looked at and the settings that shaped
  the answer — change either and they recompute, change neither and a
  re-import costs 39s instead of 242s. Cache files are never uploaded.

- **Publishing** (`picscli/upload.py`, `picscli/yadisk.py`) splits across two
  services. Full-resolution originals — the developed/re-encoded JPEG, the
  source ARW, and the untouched source video — go to a Yandex Disk
  directory at `app:/<album-id>/`, split into `JPG/`, `RAW/` and `VIDEO/`
  subdirectories and named by camera filename (`DSC01234.JPG`,
  `DSC01234.ARW`, ...), which is then published for a public link; `album.json`
  records that link as `diskUrl`, plus each frame's path(s) under it. The
  gallery itself (thumbnails, medium/display copies, previews, clips, face
  crops, transcoded playback video, the page and its JS/CSS) goes to S3:
  content-hash-named media gets `Cache-Control: public, max-age=31536000,
  immutable`; HTML/JSON/JS/CSS get `no-cache`. The bucket is public-read
  (per your choice — no signed URLs).

## 6. Web app

Plain ES modules, no bundler. `web/index.html` lists albums from
`/albums.json`; `web/album.html` is uploaded as `albums/<id>/index.html` for
every album and reads `./album.json` relative to itself. Gallery tiles show
their animated WebP preview on hover with a mouse. Touch has no hover, so
there the preview follows the finger: hold a tile to peek at its
animation, slide sideways to hand the preview to the next tile, lift to
stop. Only one preview ever runs at a time, so a phone is never decoding
a screenful of animations at once. A quick tap still opens the burst;
anything longer counts as a peek and deliberately does not open it, and
the long-press "save image" sheet is suppressed so it can't interrupt.

Fullscreen viewer controls:

| | frames within a burst | between bursts | zoom | close |
|---|---|---|---|---|
| keyboard | Up / Down | Left / Right | — | Escape |
| mouse | wheel; scroll the filmstrip | side arrows | double-click, then drag to pan | close button |
| touch | **drag the filmstrip** (speed control) or tap a thumbnail | horizontal swipe on the image | pinch or double-tap, then drag to pan | swipe down |

Zoom goes up to 6x and anchors on the point being pinched or tapped, so
the detail under your fingers stays put. While zoomed, a drag pans the
photo rather than changing burst — the two gestures never fight, and you
leave a zoomed photo by pinching back in or double-tapping. Pinching
almost all the way back snaps cleanly to 1x, and changing frame or burst
always drops the zoom.

Because the drag-to-shuttle gesture has no visible control, a pair of
nudging fingertips appears in the empty space either side of the
thumbnails. It shows once per page load, retires the moment the gesture is
used, and fades on its own after a few seconds; a reload brings it back.

The touch filmstrip is a **shuttle/jog control, not a scrollbar**: the
finger's horizontal displacement from where it landed sets the *speed* of
playback, not the frame. Push further right and the burst runs forward
faster (up to 24 fps, the ZV-1's own top burst rate), further left and it
runs backward, and it wraps around the ends of the burst. There's a small
deadzone so a tap isn't read as a drag, and a quadratic ramp so slow,
precise stepping is possible just outside it. Lifting the finger stops the
animation immediately on the current frame — no inertia. To pick a specific
frame directly, tap its thumbnail. While shuttling, the stage shows the
medium copy, preloaded for the whole burst when it opened, so scrubbing
stays sharp; frames that have not arrived yet fall back to the thumbnail,
and the full display image is restored the moment the finger lifts.

Grid covers and filmstrip thumbnails carry `loading="lazy"`, so a long
album fetches what is on screen rather than everything: of 374 tiles, 67
load up front and the rest arrive while scrolling. Opening a burst pulls
only a window of medium-size frames around the one on screen — the rest is
fetched when a finger lands on the filmstrip, since that is the moment
scrubbing is about to start. An 18-frame burst costs 7 frames on open
instead of 18.

The stage scales every frame to fit — both up and down. That matters
because a shuttle shows the 480px thumbnail, which is smaller than a phone
screen: sized with `max-width/max-height` alone it would sit at its
natural size surrounded by black bars.

The current burst+frame is reflected in the URL hash so a link can point at
a specific frame, and the browser Back button always closes the viewer back
to the grid in one step.

### Testing the UI

`tools/` has a repeatable harness that drives a real headless browser, so
none of this depends on having photos or a phone at hand:

```sh
python3 tools/make_fixture.py /tmp/pics-site      # synthetic album (needs magick + ffmpeg)
python3 -m http.server 8791 --directory /tmp/pics-site   # the album is the site root &

python3 -m venv /tmp/pw-venv && /tmp/pw-venv/bin/pip install playwright
/tmp/pw-venv/bin/python tools/ui_test.py          # drives the system chromium
```

It exercises hover and finger-following previews, keyboard/wheel
navigation, the touch shuttle (speed ramp, deadzone, wrapping,
stop-on-release, tap-to-select), pinch/double-tap zoom and panning, burst
swipes (including beside a video player) and deep-link + Back, asserting
on real DOM state and writing screenshots to `/tmp/pics-screens`. All 52
checks currently pass. It has *not*
been run on physical phone hardware — Chromium's touch emulation is a good
proxy but not identical, so give the shuttle a try on a real device before
relying on it.

## 7. Known gaps / next steps

- Sony MakerNotes tag names for burst-sequence detection are unverified (see
  above) — check against a real card.
- No per-photo/per-burst editing (hide, delete, reorder, retitle) — the
  pipeline is import-only today.
- `setup-bucket`'s printed website endpoint URL is a best guess
  (`http://<bucket>.website.yandexcloud.net`); confirm the exact hostname in
  the Yandex Cloud console after running it.
- Raw development is tuned for the Sony ZV-1 specifically (the base style
  assumes its embedded lens-correction metadata and its darktable noise
  profiles); a different camera would need its own `data/zv1_base.xmp`-equivalent.
- The exposure-bracket `ReleaseMode2` value is confirmed against one real
  bracketed burst; a heuristic (cycling `ExposureCompensation`/shutter speed)
  covers firmware variants that might report bracketing differently, but
  hasn't itself been exercised against a real bracket.
