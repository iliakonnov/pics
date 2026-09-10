"""Develop a raw ARW into the album's full-resolution JPEG via darktable-cli.

The base look (Lensfun geometric correction, guided-laplacians highlight
reconstruction, profiled denoise in non-local-means-auto mode, sigmoid tone
mapping, an AA-filter sharpen) lives in the checked-in XMP template at
data/zv1_base.xmp -- built by hand from darktable 5.6.1's own module
parameter structs (verified against source and against real factory
presets pulled from its data.db) and confirmed against the real binary.

Highlight reconstruction uses "guided laplacians" (mode=3) rather than
darktable's own per-image default ("inpaint opposed"): opposed is faster
but can fail where a clipped highlight borders a different color, while
guided laplacians is built for exactly the small/medium specular highlights
this camera's night and event shots are full of (streetlights, headlights,
wet reflections) and is documented as immune to white-balance
discrepancies. Every field besides `mode` keeps darktable's own compiled-in
default (128px reconstruction diameter, 30 iterations, recovery off) --
untuned, since a wrong guess here just costs render time, not correctness.

Lens correction uses Lensfun's camera+lens database (method=LENSFUN), not
darktable's "embedded metadata" method: the ZV-1 doesn't populate the
per-shot MakerNote fields darktable's embedded-metadata path reads
(`Sony:DistortionCorrParamsPresent` is 0 on every real file checked), so
that method silently no-ops -- confirmed by A/B rendering a real photo with
a straight building facade: embedded-metadata mode (ours or darktable's own
auto-picked default) left the facade visibly curved, identical to lens
correction being fully disabled. A community-contributed Lensfun profile
for the ZV-1 (mount "sonyZV1") exists but isn't in the Arch-packaged
database; `lensfun-update-data` fetches it into ~/.local/share/lensfun. See
check_available(), which fails loudly if that profile can't be found,
rather than silently rendering uncorrected geometry again.

The exposure module's EV and the lens module's focal length/aperture are
patched per image (this camera is a 9.4-25.7mm zoom, and Lensfun's
distortion correction varies a lot across that range -- a=0.046 at the wide
end vs a=0.030 at the tele end in the calibration data); everything else in
the template is a fixed, image-independent default.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import config, imaging

TEMPLATE_PATH = Path(__file__).resolve().parent / "data" / "zv1_base.xmp"

_REQUIRED_OPERATIONS = {
    "lens": "10",
    "denoiseprofile": "12",
    "exposure": "7",
    "diffuse": "2",
    "sigmoid": "3",
    "highlights": "4",
}
_FORBIDDEN_OPERATIONS = {"temperature", "channelmixerrgb"}

_LI_RE = re.compile(r"<rdf:li\b[^>]*/>")
_ATTR_RE = re.compile(r'darktable:(\w+)="([^"]*)"')

# Offset of the `exposure` float within the 28-byte exposure v7 params
# struct: mode(i32) black(f32) exposure(f32) ...
_EXPOSURE_EV_OFFSET = 8

# Offsets within the 356-byte lens v10 params struct: method(i32)
# modify_flags(i32) inverse(i32) scale(f32) crop(f32) focal(f32)
# aperture(f32) distance(f32) target_geom(i32) camera[128] lens[128] ...
_LENS_FOCAL_OFFSET = 20
_LENS_APERTURE_OFFSET = 24
_LENS_CAMERA_NAME = "ZV-1"
_LENS_LENS_NAME = "ZV-1 & compatibles"
_LENS_CROP_FACTOR = 2.70


class TemplateError(Exception):
    pass


def _parse_history_entries(template_text: str) -> list[dict]:
    entries = []
    for match in _LI_RE.finditer(template_text):
        attrs = dict(_ATTR_RE.findall(match.group(0)))
        attrs["_span"] = match.span()
        entries.append(attrs)
    return entries


def verify_template(template_text: str) -> None:
    """Fail loudly if the template doesn't look like what we expect --
    catches a darktable upgrade shifting a module's param layout, or the
    template being hand-edited into something we can no longer patch."""
    entries = _parse_history_entries(template_text)
    by_op = {e["operation"]: e for e in entries if "operation" in e}

    declared_end = re.search(r'darktable:history_end="(\d+)"', template_text)
    if not declared_end or int(declared_end.group(1)) != len(entries):
        raise TemplateError(f"history_end doesn't match entry count ({len(entries)} entries)")

    for op, expected_version in _REQUIRED_OPERATIONS.items():
        entry = by_op.get(op)
        if entry is None:
            raise TemplateError(f"template is missing the '{op}' module")
        if entry.get("modversion") != expected_version:
            raise TemplateError(
                f"'{op}' is at modversion {entry.get('modversion')}, expected {expected_version} "
                "-- darktable's param layout for this module may have changed"
            )

    forbidden = _FORBIDDEN_OPERATIONS & set(by_op)
    if forbidden:
        raise TemplateError(f"template unexpectedly enables {forbidden} -- per-image defaults should handle these")

    exposure_bytes = bytes.fromhex(by_op["exposure"]["params"])
    if len(exposure_bytes) != 28:
        raise TemplateError(f"exposure params are {len(exposure_bytes)} bytes, expected 28")
    mode, black, _ev, deflicker_pct, deflicker_target, _ceb, _chp = struct.unpack("<i f f f f i i", exposure_bytes)
    if (mode, black, deflicker_pct, deflicker_target) != (0, 0.0, 50.0, -4.0):
        raise TemplateError("exposure params sanity fields don't match the expected layout")

    denoise_bytes = bytes.fromhex(by_op["denoiseprofile"]["params"])
    if len(denoise_bytes) != 416:
        raise TemplateError(f"denoiseprofile params are {len(denoise_bytes)} bytes, expected 416")
    a0 = struct.unpack_from("<f", denoise_bytes, 32)[0]
    denoise_mode = struct.unpack_from("<i", denoise_bytes, 56)[0]
    if a0 != -1.0 or denoise_mode != 3:
        raise TemplateError(
            f"denoiseprofile isn't in ISO-auto mode (a[0]={a0}, mode={denoise_mode}); "
            "expected a[0]=-1.0, mode=3 (non-local means auto)"
        )

    lens_bytes = bytes.fromhex(by_op["lens"]["params"])
    if len(lens_bytes) != 356:
        raise TemplateError(f"lens params are {len(lens_bytes)} bytes, expected 356")
    method, modify_flags = struct.unpack_from("<ii", lens_bytes, 0)
    crop = struct.unpack_from("<f", lens_bytes, 16)[0]
    camera_name = lens_bytes[36:164].split(b"\x00", 1)[0].decode()
    lens_name = lens_bytes[164:292].split(b"\x00", 1)[0].decode()
    if method != 1:
        raise TemplateError(
            f"lens method is {method}, expected 1 (Lensfun database) -- 'embedded metadata' (0) "
            "silently no-ops on this camera, see develop.py's module docstring"
        )
    if camera_name != _LENS_CAMERA_NAME or lens_name != _LENS_LENS_NAME:
        raise TemplateError(
            f"lens camera/lens name is {camera_name!r}/{lens_name!r}, "
            f"expected {_LENS_CAMERA_NAME!r}/{_LENS_LENS_NAME!r} -- Lensfun won't find the profile"
        )
    if abs(crop - _LENS_CROP_FACTOR) > 0.01:
        raise TemplateError(f"lens crop factor is {crop}, expected {_LENS_CROP_FACTOR}")
    if not (modify_flags & 4):
        raise TemplateError("lens modify_flags doesn't include distortion correction")

    highlights_bytes = bytes.fromhex(by_op["highlights"]["params"])
    if len(highlights_bytes) != 48:
        raise TemplateError(f"highlights params are {len(highlights_bytes)} bytes, expected 48")
    highlights_mode = struct.unpack_from("<i", highlights_bytes, 0)[0]
    if highlights_mode != 3:
        raise TemplateError(
            f"highlights mode is {highlights_mode}, expected 3 (guided laplacians)"
        )


def patch_exposure_ev(template_text: str, ev: float) -> str:
    """Return template_text with the exposure module's EV set to `ev`.

    Regex-locates the single exposure history entry, patches the 4-byte
    float at its known offset in the hex-encoded params, and splices the
    result back in -- no XML re-serialization, so everything else in the
    file is byte-for-byte unchanged.
    """
    entries = _parse_history_entries(template_text)
    exposure_entries = [e for e in entries if e.get("operation") == "exposure"]
    if len(exposure_entries) != 1:
        raise TemplateError(f"expected exactly one exposure entry, found {len(exposure_entries)}")
    entry = exposure_entries[0]

    old_hex = entry["params"]
    raw = bytearray(bytes.fromhex(old_hex))
    struct.pack_into("<f", raw, _EXPOSURE_EV_OFFSET, ev)
    new_hex = bytes(raw).hex()

    start, end = entry["_span"]
    old_li = template_text[start:end]
    old_attr = f'darktable:params="{old_hex}"'
    if old_attr not in old_li:
        raise TemplateError("failed to patch the exposure params attribute")
    new_li = old_li.replace(old_attr, f'darktable:params="{new_hex}"')
    return template_text[:start] + new_li + template_text[end:]


def patch_lens_focal(template_text: str, focal_mm: float, aperture: float | None) -> str:
    """Return template_text with the lens module's focal length (and
    aperture, if known) set to this image's actual values.

    The ZV-1 is a 9.4-25.7mm zoom, and Lensfun's distortion correction is
    calibrated per focal length across that range, so a single fixed value
    in the template would only be right for one zoom position.
    """
    entries = _parse_history_entries(template_text)
    lens_entries = [e for e in entries if e.get("operation") == "lens"]
    if len(lens_entries) != 1:
        raise TemplateError(f"expected exactly one lens entry, found {len(lens_entries)}")
    entry = lens_entries[0]

    old_hex = entry["params"]
    raw = bytearray(bytes.fromhex(old_hex))
    struct.pack_into("<f", raw, _LENS_FOCAL_OFFSET, focal_mm)
    if aperture:
        struct.pack_into("<f", raw, _LENS_APERTURE_OFFSET, aperture)
    new_hex = bytes(raw).hex()

    start, end = entry["_span"]
    old_li = template_text[start:end]
    old_attr = f'darktable:params="{old_hex}"'
    if old_attr not in old_li:
        raise TemplateError("failed to patch the lens params attribute")
    new_li = old_li.replace(old_attr, f'darktable:params="{new_hex}"')
    return template_text[:start] + new_li + template_text[end:]


def _lensfun_database_files() -> list[Path]:
    roots = [
        Path("/usr/share/lensfun"),
        Path("/usr/share/lensfun-updates"),
        Path.home() / ".local/share/lensfun",
    ]
    files = []
    for root in roots:
        if root.is_dir():
            files.extend(root.rglob("*.xml"))
    return files


def check_lensfun_zv1_profile() -> bool:
    """Whether some installed Lensfun database file has a ZV-1 lens
    profile. The Arch-packaged database doesn't (as of this writing);
    `lensfun-update-data` fetches the community-contributed one that does."""
    # The XML escapes "&" as "&amp;" ("ZV-1 & compatibles" in the database).
    needle = f"<model>{_LENS_LENS_NAME}</model>".replace("&", "&amp;").encode()
    for path in _lensfun_database_files():
        try:
            if needle in path.read_bytes():
                return True
        except OSError:
            continue
    return False


def template_fingerprint() -> str:
    version = _darktable_version()
    text = TEMPLATE_PATH.read_text()
    return hashlib.sha256(f"{text}\n{version}".encode()).hexdigest()


def _darktable_version() -> str:
    result = subprocess.run([config.DARKTABLE_BIN, "--version"], capture_output=True, text=True, check=False)
    return result.stdout.splitlines()[0].strip() if result.stdout else ""


def check_available() -> list[str]:
    errors = []
    if shutil.which(config.DARKTABLE_BIN) is None:
        errors.append(f"'{config.DARKTABLE_BIN}' (darktable-cli) not found on PATH")
    if shutil.which(config.DCRAW_EMU_BIN) is None:
        errors.append(f"'{config.DCRAW_EMU_BIN}' (dcraw_emu, from LibRaw) not found on PATH")
    if not check_lensfun_zv1_profile():
        errors.append(
            "no Lensfun profile for the Sony ZV-1 found -- the packaged database doesn't have one; "
            "run `lensfun-update-data` to fetch the community-contributed one, then retry"
        )
    if not TEMPLATE_PATH.is_file():
        errors.append(f"missing raw-develop template: {TEMPLATE_PATH}")
    else:
        try:
            verify_template(TEMPLATE_PATH.read_text())
        except TemplateError as exc:
            errors.append(f"raw-develop template failed verification: {exc}")
    return errors


@dataclass(slots=True)
class DevelopResult:
    source: Path
    dest: Path
    ok: bool
    used_fallback: str | None = None
    error: str | None = None
    seconds: float = 0.0


def _run_darktable_cli(
    arw: Path, xmp: Path, dst: Path, configdir: Path, *, opencl: bool, jobs: int
) -> subprocess.CompletedProcess:
    cmd = [
        config.DARKTABLE_BIN,
        str(arw),
        str(xmp),
        str(dst),
        "--width", "0",
        "--height", "0",
        "--hq", "true",
        "--upscale", "false",
        "--apply-custom-presets", "false",
        "--icc-type", "SRGB",
        "--core",
        "--configdir", str(configdir),
        "--conf", f"plugins/imageio/format/jpeg/quality={config.DEVELOP_JPEG_QUALITY}",
        "--conf", "plugins/darkroom/workflow=none",
        "--conf", "write_sidecar_files=never",
        "--conf", f"opencl={'TRUE' if opencl else 'FALSE'}",
    ]
    # darktable-cli is internally OpenMP-parallel and by default grabs every
    # core; with several instances running at once (config.DEVELOP_JOBS)
    # that oversubscribes the machine badly -- one frame went from ~16s
    # isolated to 2.5+ minutes under two-way contention in testing. Divide
    # the cores evenly across instances instead, same approach imaging.py
    # takes for magick/ffmpeg (_SINGLE_THREADED).
    threads_per_instance = max(1, (os.cpu_count() or 4) // max(1, jobs))
    env = {**os.environ, "OMP_NUM_THREADS": str(threads_per_instance), "OMP_THREAD_LIMIT": str(threads_per_instance)}
    return subprocess.run(
        cmd, capture_output=True, text=True, check=False, timeout=config.DEVELOP_TIMEOUT_SECONDS, env=env
    )


def _extract_embedded_preview(arw: Path, dst: Path) -> bool:
    result = subprocess.run(
        [config.EXIFTOOL_BIN, "-b", "-PreviewImage", str(arw)],
        capture_output=True, check=False,
    )
    if result.returncode != 0 or not result.stdout:
        return False
    dst.write_bytes(result.stdout)
    return True


def develop(
    arw: Path,
    ev: float,
    dst: Path,
    *,
    focal_mm: float,
    aperture: float | None = None,
    jobs: int = config.DEVELOP_JOBS,
) -> DevelopResult:
    """Develop `arw` into a progressive JPEG at `dst`, atomically.

    `focal_mm` (this shot's actual focal length) drives the lens module's
    Lensfun distortion correction, which varies a lot across the ZV-1's
    zoom range -- there is no sensible fixed default.

    `jobs` is how many of these run concurrently (the importer's
    --develop-jobs), used only to divide CPU threads evenly between
    instances -- it does not change what this call itself does.

    Never raises: failures are reported in the returned DevelopResult with
    a fallback chain (sibling out-of-camera JPEG, then the ARW's own
    embedded preview) so a bad frame doesn't stall the whole import.
    """
    start = time.monotonic()
    dst.parent.mkdir(parents=True, exist_ok=True)
    template_text = patch_exposure_ev(TEMPLATE_PATH.read_text(), ev)
    template_text = patch_lens_focal(template_text, focal_mm, aperture)

    with tempfile.TemporaryDirectory(prefix="pics-develop-") as tmp_str:
        tmp = Path(tmp_str)
        xmp_path = tmp / "develop.xmp"
        xmp_path.write_text(template_text)
        configdir = tmp / "dtconfig"
        configdir.mkdir()
        raw_out = tmp / "developed.jpg"

        last_error = ""
        # CPU first: OpenCL that *claims* to init but has no real GPU behind
        # it (a broken/absent driver) doesn't fail -- it just runs
        # pathologically slowly (minutes/frame instead of ~15s), which the
        # retry-on-failure logic below can't detect. CPU-only is the
        # verified-fast, bounded-time path on this kind of setup; OpenCL is
        # only worth trying second, as a possible speed-up.
        for opencl in (True, False) if config.DEVELOP_OPENCL_FIRST else (False, True):
            try:
                result = _run_darktable_cli(arw, xmp_path, raw_out, configdir, opencl=opencl, jobs=jobs)
            except subprocess.TimeoutExpired:
                last_error = f"darktable-cli timed out after {config.DEVELOP_TIMEOUT_SECONDS}s"
                continue
            if result.returncode == 0 and raw_out.is_file() and raw_out.stat().st_size > 100_000:
                imaging.to_progressive_jpeg(raw_out, dst)
                return DevelopResult(source=arw, dest=dst, ok=True, seconds=time.monotonic() - start)
            last_error = result.stderr.strip()[-2000:] or f"exit code {result.returncode}"

        sibling_jpg = arw.with_suffix(".JPG")
        if not sibling_jpg.is_file():
            sibling_jpg = arw.with_suffix(".jpg")
        if sibling_jpg.is_file():
            imaging.to_progressive_jpeg(sibling_jpg, dst)
            return DevelopResult(
                source=arw, dest=dst, ok=True, used_fallback="sibling_jpg",
                error=last_error, seconds=time.monotonic() - start,
            )

        if _extract_embedded_preview(arw, raw_out):
            imaging.to_progressive_jpeg(raw_out, dst)
            return DevelopResult(
                source=arw, dest=dst, ok=True, used_fallback="embedded_preview",
                error=last_error, seconds=time.monotonic() - start,
            )

        return DevelopResult(source=arw, dest=dst, ok=False, error=last_error, seconds=time.monotonic() - start)


def run_selftest(sample_arw: Path) -> None:
    """Render the same ARW at EV 0 and EV +2 and check the brighter one is
    actually brighter -- proves the patched float drives the exposure
    module in the installed darktable-cli, not just in our own decoder.

    A 2 EV push through sigmoid's compression measured ~2.3x brighter on a
    real ZV-1 file (nowhere near the naive linear 4x -- sigmoid rolls off
    on purpose), so this only checks for a clear, sane increase.
    """
    verify_template(TEMPLATE_PATH.read_text())
    focal_result = subprocess.run(
        [config.EXIFTOOL_BIN, "-n", "-T", "-FocalLength", str(sample_arw)],
        capture_output=True, text=True, check=False,
    )
    try:
        focal_mm = float(focal_result.stdout.strip())
    except ValueError:
        focal_mm = config.DEVELOP_FALLBACK_FOCAL_MM  # if the sample's own focal length can't be read

    with tempfile.TemporaryDirectory(prefix="pics-selftest-") as tmp_str:
        tmp = Path(tmp_str)
        dark = tmp / "ev0.jpg"
        bright = tmp / "ev2.jpg"
        r0 = develop(sample_arw, 0.0, dark, focal_mm=focal_mm)
        r2 = develop(sample_arw, 2.0, bright, focal_mm=focal_mm)
        if not (r0.ok and r2.ok):
            raise TemplateError(f"selftest render failed: {r0.error or r2.error}")

        import numpy as np
        from PIL import Image

        mean0 = np.asarray(Image.open(dark).convert("L"), dtype=np.float32).mean()
        mean2 = np.asarray(Image.open(bright).convert("L"), dtype=np.float32).mean()
        ratio = mean2 / max(mean0, 1e-6)
        if not (1.3 <= ratio <= 4.0):
            raise TemplateError(
                f"EV+2 render is {ratio:.2f}x EV0 (expected roughly 1.3-4.0x through sigmoid) "
                "-- the exposure patch may not be taking effect"
            )
