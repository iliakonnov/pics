import struct

import pytest

from picscli.develop import TEMPLATE_PATH, TemplateError, patch_exposure_ev, verify_template


@pytest.fixture
def template_text():
    return TEMPLATE_PATH.read_text()


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


def test_verify_rejects_temperature_entry(template_text):
    extra_li = '<rdf:li darktable:num="5" darktable:operation="temperature" darktable:modversion="1"/>'
    injected = template_text.replace("</rdf:Seq>\n   </darktable:history>", f"{extra_li}\n    </rdf:Seq>\n   </darktable:history>")
    injected = injected.replace('darktable:history_end="5"', 'darktable:history_end="6"')
    with pytest.raises(TemplateError, match="temperature"):
        verify_template(injected)


def test_verify_rejects_history_end_mismatch(template_text):
    broken = template_text.replace('darktable:history_end="5"', 'darktable:history_end="4"')
    with pytest.raises(TemplateError, match="history_end"):
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
