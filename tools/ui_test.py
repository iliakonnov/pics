#!/usr/bin/env python3
"""Drive the gallery in a real (headless) browser against the fixture site.

Covers the interactions that are easy to break and impossible to check by
reading code: hover/touch previews, keyboard and wheel frame nav, the
touch shuttle on the filmstrip, burst swipes, and deep-link + Back.

Setup (once):
    python3 -m venv /tmp/pw-venv && /tmp/pw-venv/bin/pip install playwright

Run:
    python3 tools/make_fixture.py /tmp/pics-site
    python3 -m http.server 8791 --directory /tmp/pics-site &
    /tmp/pw-venv/bin/python tools/ui_test.py

Uses the system Chromium (PICS_CHROMIUM to override) rather than
Playwright's own download.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("PICS_TEST_URL", "http://localhost:8791")
ALBUM = f"{BASE}/albums/2026-08-29-demo01/"
CHROMIUM = os.environ.get("PICS_CHROMIUM", "/usr/bin/chromium")
OUT = Path(os.environ.get("PICS_TEST_SHOTS", "/tmp/pics-screens"))

failures: list[str] = []
errors: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        failures.append(f"{label} {detail}")


def on_console(label, msg):
    if msg.type != "error":
        return
    url = (msg.location or {}).get("url", "")
    if "favicon" in url or "favicon" in msg.text:
        return
    errors.append(f"[{label}] {msg.text} ({url})")


def watch(page, label):
    page.on("console", lambda m: on_console(label, m))
    page.on("pageerror", lambda e: errors.append(f"[{label}] pageerror: {e}"))


def shot(page, name):
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f"{name}.png"))


def touch(page, selector, kind, x, y):
    """Dispatch a single-finger TouchEvent at viewport coords (x, y)."""
    touch_multi(page, selector, kind, [[x, y]])


def touch_multi(page, selector, kind, points):
    """Dispatch a TouchEvent with one Touch per (x, y) in points."""
    page.evaluate(
        """([selector, kind, points]) => {
            const el = document.querySelector(selector);
            const touches = points.map((p, i) => new Touch({
                identifier: i, target: el, clientX: p[0], clientY: p[1],
            }));
            const live = kind === 'touchend' ? [] : touches;
            el.dispatchEvent(new TouchEvent(kind, {
                touches: live, targetTouches: live, changedTouches: touches,
                bubbles: true, cancelable: true,
            }));
        }""",
        [selector, kind, points],
    )


def stage_scale(page) -> float:
    """Current zoom factor read back off the rendered transform matrix."""
    return page.eval_on_selector(
        "#viewer-media img",
        "el => new DOMMatrixReadOnly(getComputedStyle(el).transform).a",
    )


def stage_translate(page) -> tuple[float, float]:
    return tuple(
        page.eval_on_selector(
            "#viewer-media img",
            "el => { const m = new DOMMatrixReadOnly(getComputedStyle(el).transform); return [m.e, m.f]; }",
        )
    )


def tile_center(page, index: int):
    box = page.query_selector_all(".burst-tile")[index].bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def preview_running(page, index: int) -> bool:
    return page.evaluate(
        """(i) => {
            const p = document.querySelectorAll('.burst-tile')[i].querySelector('.preview');
            return !!p && p.style.display !== 'none' && !!p.getAttribute('src');
        }""",
        index,
    )


def active_frame(page) -> int:
    return page.eval_on_selector(
        "#filmstrip", "el => Array.from(el.children).findIndex(c => c.classList.contains('active'))"
    )


def open_burst_a(page, *, mobile: bool):
    page.goto(ALBUM)
    page.wait_for_selector(".burst-tile")
    page.click(".burst-tile")
    page.wait_for_selector("#viewer:not([hidden])")
    page.wait_for_timeout(150)


def desktop_tests(browser):
    print("desktop: grid, hover preview, keyboard, wheel, deep link")
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    watch(page, "desktop")

    page.goto(f"{BASE}/")
    page.wait_for_selector(".album-card")
    caption = page.eval_on_selector(".card-date", "el => el.textContent")
    check("album caption pluralised", "серии" in caption or "серий" in caption or "серия" in caption, repr(caption))
    shot(page, "01-albums-list")

    page.click(".album-card")
    page.wait_for_selector(".burst-tile")
    check("four burst tiles", len(page.query_selector_all(".burst-tile")) == 4)
    shot(page, "02-album-grid")

    page.query_selector_all(".burst-tile")[0].hover()
    page.wait_for_timeout(150)
    check("hover swaps in preview", page.eval_on_selector(".burst-tile .preview", "el => el.style.display !== 'none'"))
    shot(page, "03-hover-preview")

    page.click(".burst-tile")
    page.wait_for_selector("#viewer:not([hidden])")
    check("filmstrip shown for multi-frame burst", page.eval_on_selector("#filmstrip", "el => !el.hidden"))

    # The photo must be letterboxed inside the stage, never cropped by it:
    # .viewer-media needs a definite height or max-height:100% on the image
    # resolves to nothing and overflow:hidden eats the top and bottom.
    fit = page.evaluate(
        """() => {
            const img = document.querySelector('#viewer-media img');
            const st = document.getElementById('viewer-stage').getBoundingClientRect();
            const r = img.getBoundingClientRect();
            return {
                fits: r.height <= st.height + 1 && r.width <= st.width + 1,
                shown: r.width / r.height,
                natural: img.naturalWidth / img.naturalHeight,
            };
        }"""
    )
    check("photo fits the stage uncropped", fit["fits"], str(fit))
    check("photo keeps its aspect ratio", abs(fit["shown"] - fit["natural"]) < 0.01, str(fit))
    check("filmstrip has 6 thumbs", len(page.query_selector_all(".filmstrip-thumb")) == 6)
    shot(page, "04-viewer-frame0")

    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(250)
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(250)
    check("ArrowDown x2 -> frame 2", page.evaluate("location.hash") == "#b0000:2", page.evaluate("location.hash"))
    check("active thumb follows", active_frame(page) == 2)
    shot(page, "05-viewer-frame2")

    page.mouse.move(640, 400)
    page.mouse.wheel(0, 100)
    page.wait_for_timeout(300)
    check("wheel down -> next frame", page.evaluate("location.hash") == "#b0000:3", page.evaluate("location.hash"))

    page.keyboard.press("ArrowUp")
    page.wait_for_timeout(250)
    check("ArrowUp clamps within burst", page.evaluate("location.hash") == "#b0000:2")

    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(250)
    check("ArrowRight -> next burst", page.evaluate("location.hash") == "#b0001:0")
    check("filmstrip shown for the 3-frame burst", page.eval_on_selector("#filmstrip", "el => !el.hidden"))

    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(250)
    check("ArrowRight -> single-photo burst", page.evaluate("location.hash") == "#b0002:0")
    check("filmstrip hidden for single frame", page.eval_on_selector("#filmstrip", "el => el.hidden"))
    shot(page, "06-viewer-single-burst")

    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(250)
    check("video burst renders <video>", page.eval_on_selector("#viewer-media", "el => !!el.querySelector('video')"))
    check("next disabled at last burst", page.eval_on_selector("#viewer-next-burst", "el => el.disabled"))
    shot(page, "07-viewer-video")

    page.keyboard.press("Escape")
    page.wait_for_timeout(250)
    check("Escape closes viewer", page.eval_on_selector("#viewer", "el => el.hidden"))
    shot(page, "08-viewer-closed")

    # Same-page hash change (someone pastes a shared link into the bar).
    page.evaluate("location.hash = '#b0003:0'")
    page.wait_for_timeout(300)
    check("same-page hash change opens that burst", page.eval_on_selector("#viewer", "el => !el.hidden"))
    page.close()

    # A shared deep link arriving as a cold page load.
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    watch(page, "deeplink")
    page.goto(f"{ALBUM}#b0003:0")
    page.wait_for_selector("#viewer:not([hidden])")
    check("cold deep link opens video burst", page.eval_on_selector("#viewer-media", "el => !!el.querySelector('video')"))
    page.go_back()
    page.wait_for_timeout(250)
    check("Back from deep link closes to grid", page.eval_on_selector("#viewer", "el => el.hidden"))
    shot(page, "09-deeplink-back")
    page.close()


def shuttle_tests(browser):
    print("mobile: filmstrip shuttle (speed control, not position)")
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    watch(page, "shuttle")

    page.goto(f"{BASE}/")
    page.wait_for_selector(".album-card")
    shot(page, "10-mobile-albums")
    page.click(".album-card")
    page.wait_for_selector(".burst-tile")
    page.wait_for_timeout(300)

    # Nothing animates until a finger is actually on a tile.
    check("no preview runs untouched", not preview_running(page, 0))

    tx0, ty0 = tile_center(page, 0)
    touch(page, "#burst-grid", "touchstart", tx0, ty0)
    page.wait_for_timeout(120)
    check("finger down starts that tile's preview", preview_running(page, 0))
    shot(page, "11-mobile-peek")

    # Dragging sideways hands the preview to the tile now under the finger.
    tx1, ty1 = tile_center(page, 1)
    touch(page, "#burst-grid", "touchmove", tx1, ty1)
    page.wait_for_timeout(120)
    check("preview follows finger to next tile", preview_running(page, 1) and not preview_running(page, 0))

    touch(page, "#burst-grid", "touchend", tx1, ty1)
    page.wait_for_timeout(120)
    check("lifting stops all previews", not preview_running(page, 0) and not preview_running(page, 1))

    # A long press peeks; it must not also open the burst.
    touch(page, "#burst-grid", "touchstart", tx0, ty0)
    page.wait_for_timeout(500)
    touch(page, "#burst-grid", "touchend", tx0, ty0)
    page.query_selector_all(".burst-tile")[0].click()  # the click a real long-press would emit
    page.wait_for_timeout(200)
    check("long press does not open the viewer", page.eval_on_selector("#viewer", "el => el.hidden"))

    # ...but a quick tap still does.
    page.query_selector_all(".burst-tile")[0].tap()
    page.wait_for_timeout(300)
    check("quick tap opens the viewer", page.eval_on_selector("#viewer", "el => !el.hidden"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(250)

    open_burst_a(page, mobile=True)
    check("starts at frame 0", active_frame(page) == 0)
    shot(page, "12-mobile-viewer")

    strip_y = page.eval_on_selector("#filmstrip", "el => el.getBoundingClientRect().top + 30")

    # Hold the finger well to the right: fast forward playback, wrapping.
    touch(page, "#filmstrip", "touchstart", 200, strip_y)
    touch(page, "#filmstrip", "touchmove", 340, strip_y)  # dx = +140 -> max speed
    page.wait_for_timeout(600)
    during = active_frame(page)
    hash_during = page.evaluate("location.hash")
    src_during = page.eval_on_selector("#viewer-media img", "el => el.getAttribute('src')")
    shot(page, "13-mobile-shuttle-fast-forward")

    page.wait_for_timeout(400)
    later = active_frame(page)
    check("holding right keeps advancing", during != later or during > 0, f"{during} -> {later}")
    check("URL not rewritten mid-shuttle", hash_during == "#b0000:0", hash_during)
    check("scrubs with low-res thumb", "thumb/" in src_during, src_during)

    touch(page, "#filmstrip", "touchend", 340, strip_y)
    page.wait_for_timeout(120)
    at_release = active_frame(page)
    page.wait_for_timeout(500)
    check("release stops the animation dead", active_frame(page) == at_release, f"{at_release} -> {active_frame(page)}")
    check("release restores display-res image",
          "display/" in page.eval_on_selector("#viewer-media img", "el => el.getAttribute('src')"))
    check("release syncs URL", page.evaluate("location.hash") == f"#b0000:{at_release}", page.evaluate("location.hash"))
    shot(page, "14-mobile-shuttle-released")

    # A tiny drag inside the deadzone must not move anything.
    before = active_frame(page)
    touch(page, "#filmstrip", "touchstart", 200, strip_y)
    touch(page, "#filmstrip", "touchmove", 206, strip_y)  # dx = +6, inside deadzone
    page.wait_for_timeout(400)
    check("deadzone: small drag does nothing", active_frame(page) == before, f"{before} -> {active_frame(page)}")
    touch(page, "#filmstrip", "touchend", 206, strip_y)

    # Leftward hold runs backwards, wrapping past frame 0.
    page.wait_for_timeout(100)
    before_back = active_frame(page)
    touch(page, "#filmstrip", "touchstart", 200, strip_y)
    touch(page, "#filmstrip", "touchmove", 60, strip_y)  # dx = -140
    page.wait_for_timeout(600)
    touch(page, "#filmstrip", "touchend", 60, strip_y)
    page.wait_for_timeout(150)
    check("holding left runs backwards", active_frame(page) != before_back, f"{before_back} -> {active_frame(page)}")
    shot(page, "15-mobile-shuttle-reverse")

    # Back to frame 0 by tapping, not by assigning location.hash: a hash
    # assignment would push an extra history entry and break the
    # single-Back-closes-the-viewer assumption further down.
    page.query_selector_all(".filmstrip-thumb")[0].tap()
    page.wait_for_timeout(200)
    seen = set()
    touch(page, "#filmstrip", "touchstart", 200, strip_y)
    touch(page, "#filmstrip", "touchmove", 340, strip_y)
    for _ in range(14):
        page.wait_for_timeout(60)
        seen.add(active_frame(page))
    touch(page, "#filmstrip", "touchend", 340, strip_y)
    check("shuttle wraps through every frame", len(seen) >= 5, f"visited {sorted(seen)}")

    # Tapping a thumbnail still selects it directly.
    page.wait_for_timeout(150)
    page.query_selector_all(".filmstrip-thumb")[4].tap()
    page.wait_for_timeout(250)
    check("tap selects that frame", active_frame(page) == 4, f"active={active_frame(page)}")
    check("tap syncs URL", page.evaluate("location.hash") == "#b0000:4", page.evaluate("location.hash"))
    shot(page, "16-mobile-tap-selects")

    # Swipe on the stage still moves between bursts, and down closes.
    touch(page, "#viewer-stage", "touchstart", 300, 400)
    touch(page, "#viewer-stage", "touchend", 50, 400)
    page.wait_for_timeout(250)
    check("stage swipe-left -> next burst", page.evaluate("location.hash") == "#b0001:0", page.evaluate("location.hash"))

    touch(page, "#viewer-stage", "touchstart", 200, 300)
    touch(page, "#viewer-stage", "touchend", 200, 500)
    page.wait_for_timeout(250)
    check("stage swipe-down closes", page.eval_on_selector("#viewer", "el => el.hidden"))
    page.close()


def zoom_tests(browser):
    print("mobile: pinch / double-tap zoom, pan, and swipe coexistence")
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    watch(page, "zoom")
    open_burst_a(page, mobile=True)

    check("starts unzoomed", stage_scale(page) == 1)

    # Double-tap zooms in around the tapped point; again zooms back out.
    for kind in ("touchstart", "touchend"):
        touch(page, "#viewer-stage", kind, 195, 420)
    page.wait_for_timeout(60)
    for kind in ("touchstart", "touchend"):
        touch(page, "#viewer-stage", kind, 195, 420)
    page.wait_for_timeout(350)
    zoomed = stage_scale(page)
    check("double-tap zooms in", zoomed > 1.5, f"scale={zoomed:.2f}")
    shot(page, "17-mobile-zoomed")

    # While zoomed, a horizontal drag pans instead of changing burst.
    before_hash = page.evaluate("location.hash")
    touch(page, "#viewer-stage", "touchstart", 300, 420)
    touch(page, "#viewer-stage", "touchmove", 180, 420)
    touch(page, "#viewer-stage", "touchend", 180, 420)
    page.wait_for_timeout(200)
    panned_x, _ = stage_translate(page)
    check("drag while zoomed pans the photo", abs(panned_x) > 1, f"tx={panned_x:.1f}")
    check("drag while zoomed does not change burst", page.evaluate("location.hash") == before_hash)
    shot(page, "18-mobile-zoom-panned")

    for kind in ("touchstart", "touchend"):
        touch(page, "#viewer-stage", kind, 195, 420)
    page.wait_for_timeout(60)
    for kind in ("touchstart", "touchend"):
        touch(page, "#viewer-stage", kind, 195, 420)
    page.wait_for_timeout(350)
    check("double-tap again resets zoom", stage_scale(page) == 1, f"scale={stage_scale(page):.2f}")

    # Back at 1x the horizontal swipe navigates bursts again.
    touch(page, "#viewer-stage", "touchstart", 300, 420)
    touch(page, "#viewer-stage", "touchend", 50, 420)
    page.wait_for_timeout(250)
    check("swipe works again once unzoomed", page.evaluate("location.hash") == "#b0001:0",
          page.evaluate("location.hash"))

    # Pinch out with two fingers.
    page.evaluate("location.hash = '#b0000:0'")
    page.wait_for_timeout(300)
    touch_multi(page, "#viewer-stage", "touchstart", [[170, 400], [220, 440]])
    touch_multi(page, "#viewer-stage", "touchmove", [[100, 330], [290, 510]])
    page.wait_for_timeout(120)
    pinched = stage_scale(page)
    touch_multi(page, "#viewer-stage", "touchend", [[100, 330], [290, 510]])
    page.wait_for_timeout(250)
    check("pinch out zooms in", pinched > 1.5, f"scale={pinched:.2f}")
    shot(page, "19-mobile-pinch")

    # Pinch back in below the threshold snaps to exactly 1x.
    touch_multi(page, "#viewer-stage", "touchstart", [[100, 330], [290, 510]])
    touch_multi(page, "#viewer-stage", "touchmove", [[192, 418], [198, 422]])
    touch_multi(page, "#viewer-stage", "touchend", [[192, 418], [198, 422]])
    page.wait_for_timeout(300)
    check("pinch in snaps back to 1x", stage_scale(page) == 1, f"scale={stage_scale(page):.2f}")

    # Changing frame always drops zoom.
    for kind in ("touchstart", "touchend"):
        touch(page, "#viewer-stage", kind, 195, 420)
    page.wait_for_timeout(60)
    for kind in ("touchstart", "touchend"):
        touch(page, "#viewer-stage", kind, 195, 420)
    page.wait_for_timeout(300)
    check("zoomed before frame change", stage_scale(page) > 1.5)
    page.query_selector_all(".filmstrip-thumb")[3].tap()
    page.wait_for_timeout(300)
    check("changing frame resets zoom", stage_scale(page) == 1, f"scale={stage_scale(page):.2f}")
    page.close()


def video_swipe_test(browser):
    print("mobile: swipe on a video burst (outside the player)")
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    watch(page, "videoswipe")
    page.goto(f"{ALBUM}#b0003:0")
    page.wait_for_selector("#viewer:not([hidden])")
    page.wait_for_timeout(300)
    check("on the video burst", page.eval_on_selector("#viewer-media", "el => !!el.querySelector('video')"))

    box = page.eval_on_selector(
        "#viewer-media video", "el => { const r = el.getBoundingClientRect(); return [r.top, r.bottom]; }"
    )
    above = max(int(box[0]) - 40, 90)  # empty stage area above the player
    touch(page, "#viewer-stage", "touchstart", 80, above)
    touch(page, "#viewer-stage", "touchend", 330, above)
    page.wait_for_timeout(250)
    check("swipe beside the player changes burst", page.evaluate("location.hash") == "#b0002:0",
          page.evaluate("location.hash"))
    page.close()


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROMIUM, headless=True)
    desktop_tests(browser)
    shuttle_tests(browser)
    zoom_tests(browser)
    video_swipe_test(browser)
    browser.close()

print()
if errors:
    print("JS errors:")
    for e in errors:
        print("  ", e)
if failures:
    print(f"{len(failures)} check(s) FAILED")
    sys.exit(1)
if errors:
    sys.exit(1)
print("all checks passed")
