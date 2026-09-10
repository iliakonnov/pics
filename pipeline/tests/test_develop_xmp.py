import re
import struct

import pytest

from picscli.develop import TEMPLATE_PATH, TemplateError, patch_exposure_ev, patch_lens_focal, verify_template


@pytest.fixture
def template_text():
    return TEMPLATE_PATH.read_text()


@pytest.fixture
def history_end(template_text):
    return int(re.search(r'darktable:history_end="(\d+)"', template_text).group(1))


def test_checked_in_template_passes_verification(template_text):
    verify_template(template_text)  # must not raise


def test_verify_rejects_modversion_bump(template_text):
    bumped = template_text.replace('darktable:operation="exposure" darktable:enabled="1" darktable:modversion="7"',
                                    'darktable:operation="exposure" darktable:enabled="1" darktable:modversion="8"')
    with pytest.raises(TemplateError, match="modversion"):
        verify_template(bumped)


def test_verify_rejects_pinned_denoise_profile(template_text):
    # Flip the a[0] autodetect sentinel from -1.0 to 0.0.
    sentinel_hex = struct.pack("<f", -1.0).hex()
    replaced_hex = struct.pack("<f", 0.0).hex()
    assert sentinel_hex in template_text
    broken = template_text.replace(sentinel_hex, replaced_hex, 1)
    with pytest.raises(TemplateError, match="auto"):
        verify_template(broken)


def test_verify_rejects_temperature_entry(template_text, history_end):
    extra_li = f'<rdf:li darktable:num="{history_end}" darktable:operation="temperature" darktable:modversion="1"/>'
    injected = template_text.replace("</rdf:Seq>\n   </darktable:history>", f"{extra_li}\n    </rdf:Seq>\n   </darktable:history>")
    injected = injected.replace(f'darktable:history_end="{history_end}"', f'darktable:history_end="{history_end + 1}"')
    with pytest.raises(TemplateError, match="temperature"):
        verify_template(injected)


def test_verify_rejects_history_end_mismatch(template_text, history_end):
    broken = template_text.replace(f'darktable:history_end="{history_end}"', f'darktable:history_end="{history_end - 1}"')
    with pytest.raises(TemplateError, match="history_end"):
        verify_template(broken)


def test_verify_rejects_non_laplacian_highlights_mode(template_text):
    # mode is the first (i32) field of the 48-byte highlights v4 params;
    # flip it from 3 (guided laplacians) to 5 (inpaint opposed, the
    # per-image default we're deliberately overriding).
    entries = [m for m in template_text.split("<rdf:li") if 'darktable:operation="highlights"' in m]
    hexval = entries[0].split('darktable:params="')[1].split('"')[0]
    raw = bytearray(bytes.fromhex(hexval))
    struct.pack_into("<i", raw, 0, 5)
    broken = template_text.replace(hexval, raw.hex(), 1)
    with pytest.raises(TemplateError, match="guided laplacians"):
        verify_template(broken)


def test_patch_exposure_ev_round_trips(template_text):
    patched = patch_exposure_ev(template_text, 1.375)
    entries = [m for m in patched.split("<rdf:li") if 'darktable:operation="exposure"' in m]
    assert len(entries) == 1
    hexval = entries[0].split('darktable:params="')[1].split('"')[0]
    raw = bytes.fromhex(hexval)
    mode, black, ev, deflicker_pct, deflicker_target, ceb, chp = struct.unpack("<i f f f f i i", raw)
    assert ev == pytest.approx(1.375)
    assert (mode, black, deflicker_pct, deflicker_target, ceb, chp) == (0, 0.0, 50.0, -4.0, 0, 1)


def test_patch_exposure_ev_at_placeholder_value_still_patches(template_text):
    # EV 0.0 matches the template's own placeholder -- must not be treated
    # as a failed patch just because before/after text happens to match.
    patched = patch_exposure_ev(template_text, 0.0)
    assert patched  # no exception


def test_patch_exposure_ev_only_touches_exposure_entry(template_text):
    patched = patch_exposure_ev(template_text, 2.0)
    # Every other history entry's params must be byte-identical.
    for op in ("lens", "denoiseprofile", "diffuse", "sigmoid"):
        before = template_text.split(f'darktable:operation="{op}"')[1].split('darktable:params="')[1].split('"')[0]
        after = patched.split(f'darktable:operation="{op}"')[1].split('darktable:params="')[1].split('"')[0]
        assert before == after


# --- lens module: Lensfun method, not "embedded metadata" -------------------
#
# The ZV-1 doesn't populate the per-shot MakerNote fields darktable's
# "embedded metadata" method reads (Sony:DistortionCorrParamsPresent=0 on
# every real file checked), so that method silently no-ops -- confirmed by
# A/B rendering a real photo with a straight building facade. These guard
# against regressing back to it.


def test_verify_rejects_embedded_metadata_lens_method(template_text):
    # method=0 (embedded metadata) in place of method=1 (Lensfun): only the
    # first 4 bytes (the method field) of the lens params change.
    entries = [m for m in template_text.split("<rdf:li") if 'darktable:operation="lens"' in m]
    hexval = entries[0].split('darktable:params="')[1].split('"')[0]
    raw = bytearray(bytes.fromhex(hexval))
    struct.pack_into("<i", raw, 0, 0)
    broken = template_text.replace(hexval, raw.hex(), 1)
    with pytest.raises(TemplateError, match="Lensfun"):
        verify_template(broken)


def test_verify_rejects_wrong_lens_name(template_text):
    entries = [m for m in template_text.split("<rdf:li") if 'darktable:operation="lens"' in m]
    hexval = entries[0].split('darktable:params="')[1].split('"')[0]
    raw = bytearray(bytes.fromhex(hexval))
    # lens[128] starts right after camera[128], at offset 36+128=164.
    wrong_name = b"some other lens\x00" + b"\x00" * (128 - 16)
    raw[164:292] = wrong_name
    broken = template_text.replace(hexval, raw.hex(), 1)
    with pytest.raises(TemplateError, match="camera/lens name"):
        verify_template(broken)


def test_patch_lens_focal_round_trips(template_text):
    patched = patch_lens_focal(template_text, 18.2, 2.8)
    entries = [m for m in patched.split("<rdf:li") if 'darktable:operation="lens"' in m]
    assert len(entries) == 1
    hexval = entries[0].split('darktable:params="')[1].split('"')[0]
    raw = bytes.fromhex(hexval)
    method, modify_flags, inverse = struct.unpack_from("<iii", raw, 0)
    scale, crop, focal, aperture = struct.unpack_from("<ffff", raw, 12)
    assert focal == pytest.approx(18.2)
    assert aperture == pytest.approx(2.8)
    assert (method, modify_flags, inverse, scale, crop) == (1, 7, 0, 1.0, pytest.approx(2.70))


def test_patch_lens_focal_without_aperture_leaves_placeholder(template_text):
    # aperture=None (e.g. missing EXIF): don't stomp the placeholder with 0.
    original_hex = [m for m in template_text.split("<rdf:li") if 'darktable:operation="lens"' in m][0]
    original_hex = original_hex.split('darktable:params="')[1].split('"')[0]
    original_aperture = struct.unpack_from("<f", bytes.fromhex(original_hex), 24)[0]

    patched = patch_lens_focal(template_text, 18.2, None)
    entries = [m for m in patched.split("<rdf:li") if 'darktable:operation="lens"' in m]
    hexval = entries[0].split('darktable:params="')[1].split('"')[0]
    focal, aperture = struct.unpack_from("<ff", bytes.fromhex(hexval), 20)
    assert focal == pytest.approx(18.2)
    assert aperture == pytest.approx(original_aperture)


def test_patch_lens_focal_only_touches_lens_entry(template_text):
    patched = patch_lens_focal(template_text, 25.7, 1.8)
    for op in ("exposure", "denoiseprofile", "diffuse", "sigmoid"):
        before = template_text.split(f'darktable:operation="{op}"')[1].split('darktable:params="')[1].split('"')[0]
        after = patched.split(f'darktable:operation="{op}"')[1].split('darktable:params="')[1].split('"')[0]
        assert before == after
