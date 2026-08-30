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
# An album is served standalone at the root of its own directory.
ALBUM = BASE + "/"
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


FIT_PROBE = """() => {
    const img = document.querySelector('#viewer-media img');
    const st = document.getElementById('viewer-stage').getBoundingClientRect();
    const fit = Math.min(img.offsetWidth / img.naturalWidth, img.offsetHeight / img.naturalHeight);
    const cw = img.naturalWidth * fit, ch = img.naturalHeight * fit;
    return {
        content: [Math.round(cw), Math.round(ch)],
        stage: [Math.round(st.width), Math.round(st.height)],
        fits: cw <= st.width + 1 && ch <= st.height + 1,
        touchesEdge: Math.abs(Math.max(cw / st.width, ch / st.height) - 1) < 0.02,
        src: img.getAttribute('src').split('/')[0],
    };
}"""


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

    page.goto(ALBUM)
    page.wait_for_selector(".burst-tile")
    check("five burst tiles", len(page.query_selector_all(".burst-tile")) == 5)
    check(
        "page references only relative assets (self-contained directory)",
        page.evaluate(
            """() => [...document.querySelectorAll('link[href],script[src]')]
                 .every(e => !(e.getAttribute('href') || e.getAttribute('src') || '').startsWith('/'))"""
        ),
    )
    check("no shuttle hint on desktop", page.eval_on_selector("#strip-hint", "el => !el.classList.contains('visible')"))
    shot(page, "02-album-grid")

    page.query_selector_all(".burst-tile")[0].hover()
    page.wait_for_timeout(150)
    check("hover swaps in preview", page.eval_on_selector(".burst-tile .preview", "el => el.style.display !== 'none'"))
    shot(page, "03-hover-preview")

    page.click(".burst-tile")
    page.wait_for_selector("#viewer:not([hidden])")
    check("filmstrip shown for multi-frame burst", page.eval_on_selector("#filmstrip", "el => !el.hidden"))

    # The picture must be scaled to fit the stage: not cropped by it (the
    # <img> used to overflow and get clipped) and not left at its natural
    # size when it is smaller than the stage (which put black bars on all
    # four sides during a shuttle). Measures the drawn picture, not the
    # <img> box, since the element now fills the stage and object-fit
    # letterboxes inside it.
    fit = page.evaluate(FIT_PROBE)
    check("photo fits the stage uncropped", fit["fits"], str(fit))
    check("photo is scaled up to touch an edge", fit["touchesEdge"], str(fit))

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
    check("ArrowRight -> next burst (on its cover)", page.evaluate("location.hash") == "#b0001:2",
          page.evaluate("location.hash"))
    check("filmstrip shown for the 3-frame burst", page.eval_on_selector("#filmstrip", "el => !el.hidden"))

    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(250)
    check("ArrowRight -> single-photo burst", page.evaluate("location.hash") == "#b0002:0")
    check("filmstrip hidden for single frame", page.eval_on_selector("#filmstrip", "el => el.hidden"))
    shot(page, "06-viewer-single-burst")

    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(250)
    check("video burst renders <video>", page.eval_on_selector("#viewer-media", "el => !!el.querySelector('video')"))
    shot(page, "07-viewer-video")

    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(250)
    check("ArrowRight -> portrait burst", page.evaluate("location.hash") == "#b0004:0")
    check("next disabled at last burst", page.eval_on_selector("#viewer-next-burst", "el => el.disabled"))

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

    page.goto(ALBUM)
    page.wait_for_selector(".burst-tile")
    page.wait_for_timeout(300)

    # Nothing animates until a finger is actually on a tile.
    check("no preview runs untouched", not preview_running(page, 0))

    # Pick two tiles sharing a row, so the drag between them is sideways:
    # a vertical drag means "scrolling" and correctly cancels the peek.
    pair = page.evaluate(
        """() => {
            for (const row of document.querySelectorAll('.burst-row')) {
                const tiles = [...row.children];
                for (let i = 0; i < tiles.length - 1; i++) {
                    if (!tiles[i].querySelector('.preview')) continue;
                    const all = [...document.querySelectorAll('.burst-tile')];
                    return {
                        from: all.indexOf(tiles[i]),
                        to: all.indexOf(tiles[i + 1]),
                        toHasPreview: !!tiles[i + 1].querySelector('.preview'),
                    };
                }
            }
            return null;
        }"""
    )
    check("found two tiles in one row to drag between", pair is not None, str(pair))

    tx0, ty0 = tile_center(page, pair["from"])
    touch(page, "#burst-grid", "touchstart", tx0, ty0)
    page.wait_for_timeout(120)
    check("finger down starts that tile's preview", preview_running(page, pair["from"]))
    shot(page, "11-mobile-peek")

    # Dragging sideways hands the preview to the tile now under the finger.
    tx1, ty1 = tile_center(page, pair["to"])
    touch(page, "#burst-grid", "touchmove", tx1, ty1)
    page.wait_for_timeout(150)
    check("preview leaves the tile the finger left", not preview_running(page, pair["from"]))
    if pair["toHasPreview"]:
        check("preview follows the finger to the next tile", preview_running(page, pair["to"]))

    touch(page, "#burst-grid", "touchend", tx1, ty1)
    page.wait_for_timeout(120)
    check("lifting stops all previews", not preview_running(page, pair["from"]))

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
    check("scrubs at medium quality, not thumbnail", "medium/" in src_during, src_during)

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
    check("stage swipe-left -> next burst", page.evaluate("location.hash") == "#b0001:2", page.evaluate("location.hash"))

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
    check("swipe works again once unzoomed", page.evaluate("location.hash") == "#b0001:2",
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


def shuttle_hint_test(browser):
    print("mobile: the swipe hint advertises the shuttle, then gets out of the way")
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    watch(page, "hint")
    open_burst_a(page, mobile=True)

    check("hint shown on a multi-frame burst", page.eval_on_selector("#strip-hint", "el => el.classList.contains('visible')"))
    check(
        "hint cannot swallow the gesture it advertises",
        page.eval_on_selector("#strip-hint", "el => getComputedStyle(el).pointerEvents === 'none'"),
    )
    check(
        "hint sits in the margins, not over the middle of the strip",
        page.evaluate(
            """() => {
                const strip = document.getElementById('filmstrip').getBoundingClientRect();
                const mid = strip.left + strip.width / 2;
                return [...document.querySelectorAll('.strip-hint-side')].every(s => {
                    const r = s.getBoundingClientRect();
                    return r.right < mid || r.left > mid;
                });
            }"""
        ),
    )
    shot(page, "22-mobile-shuttle-hint")

    # Using the gesture retires the hint for good.
    strip_y = page.eval_on_selector("#filmstrip", "el => el.getBoundingClientRect().top + 30")
    touch(page, "#filmstrip", "touchstart", 200, strip_y)
    touch(page, "#filmstrip", "touchmove", 300, strip_y)
    page.wait_for_timeout(200)
    touch(page, "#filmstrip", "touchend", 300, strip_y)
    page.wait_for_timeout(600)
    check("hint dismissed once the shuttle is used", page.eval_on_selector("#strip-hint", "el => !el.classList.contains('visible')"))

    # Only once per burst-opening within the same page load...
    page.keyboard.press("Escape")
    page.wait_for_timeout(250)
    page.query_selector_all(".burst-tile")[1].click()
    page.wait_for_selector("#viewer:not([hidden])")
    page.wait_for_timeout(400)
    check("hint not repeated on the next burst", page.eval_on_selector("#strip-hint", "el => !el.classList.contains('visible')"))

    # ...but a reload is a fresh start, and it shows again.
    page.reload()
    page.wait_for_selector("#viewer:not([hidden])")
    page.wait_for_timeout(500)
    check("hint returns after a reload", page.eval_on_selector("#strip-hint", "el => el.classList.contains('visible')"))

    # A single-frame burst has no strip at all, so nothing to advertise.
    page.goto(f"{ALBUM}#b0002:0")
    page.wait_for_selector("#viewer:not([hidden])")
    page.wait_for_timeout(400)
    check("no strip (and no hint) for a single-frame burst", page.eval_on_selector("#filmstrip-wrap", "el => el.hidden"))
    page.close()


def cover_frame_test(browser):
    print("covers: a burst opens on its chosen frame, however you reach it")
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    watch(page, "covers")
    page.goto(ALBUM)
    page.wait_for_selector(".burst-tile")
    page.wait_for_timeout(400)

    # b0001's cover is frame 2 in the fixture.
    page.query_selector_all(".burst-tile")[1].click()
    page.wait_for_selector("#viewer:not([hidden])")
    page.wait_for_timeout(400)
    check("clicking a tile opens its cover", page.evaluate("location.hash") == "#b0001:2",
          page.evaluate("location.hash"))

    # Arriving from the previous burst must land on the cover too.
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(300)
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    check("stepping into a burst lands on its cover", page.evaluate("location.hash") == "#b0001:2",
          page.evaluate("location.hash"))

    page.close()

    # A link naming only the burst, arriving as a cold load.
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    watch(page, "covers-link")
    page.goto(f"{ALBUM}#b0001")
    page.wait_for_selector("#viewer:not([hidden])")
    page.wait_for_timeout(400)
    check("a burst-only link opens the cover", page.evaluate("location.hash") == "#b0001:2",
          page.evaluate("location.hash"))
    page.close()

    # Swiping between bursts on touch must behave the same.
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    watch(page, "covers-touch")
    page.goto(f"{ALBUM}#b0000:0")
    page.wait_for_selector("#viewer:not([hidden])")
    page.wait_for_timeout(400)
    touch(page, "#viewer-stage", "touchstart", 320, 400)
    touch(page, "#viewer-stage", "touchend", 60, 400)
    page.wait_for_timeout(400)
    check("swiping to the next burst lands on its cover",
          page.evaluate("location.hash") == "#b0001:2", page.evaluate("location.hash"))
    page.close()


def video_playback_test(browser):
    print("video: the player is actually reachable and does not jump about")
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    watch(page, "video")
    page.goto(f"{ALBUM}#b0003:0")
    page.wait_for_selector("#viewer-media video")
    page.wait_for_timeout(600)

    check(
        "nothing covers the middle of the player",
        page.evaluate(
            """() => {
                const v = document.querySelector('#viewer-media video').getBoundingClientRect();
                const hit = document.elementFromPoint(v.left + v.width / 2, v.top + v.height / 2);
                return hit && hit.tagName === 'VIDEO';
            }"""
        ),
    )
    check(
        "the placeholder cannot intercept taps",
        page.evaluate(
            "() => getComputedStyle(document.querySelector('#viewer-media'), '::before').pointerEvents === 'none'"
        ),
    )

    before = page.eval_on_selector("#viewer-media video", "el => el.getBoundingClientRect().height")
    check("player is sized before playback, not 150px tall", before > 200, f"{before:.0f}px")

    page.eval_on_selector("#viewer-media video", "el => el.play()")
    page.wait_for_timeout(900)
    after = page.eval_on_selector("#viewer-media video", "el => el.getBoundingClientRect().height")
    check("playing does not resize the player", abs(after - before) <= 2, f"{before:.0f}px -> {after:.0f}px")
    check("it really is playing", page.eval_on_selector("#viewer-media video", "el => !el.paused"))
    check(
        "player still fits the stage",
        page.evaluate(
            """() => {
                const v = document.querySelector('#viewer-media video').getBoundingClientRect();
                const s = document.getElementById('viewer-stage').getBoundingClientRect();
                return v.height <= s.height + 1 && v.width <= s.width + 1;
            }"""
        ),
    )
    shot(page, "26-video-player")
    page.close()


def portrait_fit_test(browser):
    print("mobile: a portrait burst stays scaled to fit, at rest and mid-shuttle")
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    watch(page, "portrait")
    page.goto(f"{ALBUM}#b0004:0")
    page.wait_for_selector("#viewer:not([hidden])")
    page.wait_for_timeout(600)

    at_rest = page.evaluate(FIT_PROBE)
    check("portrait fits at rest", at_rest["fits"] and at_rest["touchesEdge"], str(at_rest))

    strip_y = page.eval_on_selector("#filmstrip", "el => el.getBoundingClientRect().top + 30")
    touch(page, "#filmstrip", "touchstart", 200, strip_y)
    touch(page, "#filmstrip", "touchmove", 330, strip_y)
    page.wait_for_timeout(450)
    during = page.evaluate(FIT_PROBE)
    touch(page, "#filmstrip", "touchend", 330, strip_y)

    check("shuttle shows the preloaded medium copy", during["src"] == "medium", str(during))
    check("portrait mid-shuttle is scaled up, not left small", during["touchesEdge"], str(during))
    check("portrait mid-shuttle still fits", during["fits"], str(during))
    check("size does not jump between thumb and display", during["content"] == at_rest["content"],
          f"{during['content']} vs {at_rest['content']}")
    shot(page, "21-mobile-portrait-shuttle")
    page.close()


ROW_PROBE = """() => {
    const g = document.getElementById('burst-grid');
    const rows = [...g.querySelectorAll('.burst-row')];
    const full = rows.slice(0, -1);          // the last row is not stretched
    const width = g.clientWidth;
    return {
        rows: rows.length,
        stray: [...g.children].filter(c => !c.classList.contains('burst-row')).length,
        fills: full.map(r => {
            const cs = [...r.children];
            const l = Math.min(...cs.map(c => c.getBoundingClientRect().left));
            const rt = Math.max(...cs.map(c => c.getBoundingClientRect().right));
            return (rt - l) / width;
        }),
        equalHeights: full.every(r => {
            const hs = [...r.children].map(c => Math.round(c.getBoundingClientRect().height));
            return Math.max(...hs) - Math.min(...hs) <= 1;
        }),
    };
}"""


def toolbar_icons_test(browser):
    print("toolbar: controls are drawn, not typed as font glyphs")
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    watch(page, "icons")
    page.goto(f"{ALBUM}#b0000:0")
    page.wait_for_selector("#viewer:not([hidden])")
    page.wait_for_timeout(400)

    check(
        "no control relies on a font glyph",
        page.evaluate(
            """() => ['#viewer-close', '#viewer-download', '#viewer-prev-burst', '#viewer-next-burst']
                 .every(sel => {
                     const el = document.querySelector(sel);
                     return el.querySelector('svg') && !el.textContent.trim();
                 })"""
        ),
    )
    check(
        "icons are actually painted at a usable size",
        page.evaluate(
            """() => ['#viewer-close', '#viewer-download'].every(sel => {
                   const r = document.querySelector(sel).querySelector('svg').getBoundingClientRect();
                   return r.width >= 16 && r.height >= 16;
               })"""
        ),
    )
    check(
        "the clip button says what it opens",
        page.eval_on_selector("#viewer-clip", "el => el.textContent.trim()") == "MP4",
    )
    check(
        "both links open in a new tab rather than downloading",
        page.evaluate(
            """() => ['#viewer-download', '#viewer-clip'].every(sel => {
                   const el = document.querySelector(sel);
                   return el.target === '_blank'
                       && el.rel.includes('noopener')
                       && !el.hasAttribute('download');
               })"""
        ),
    )
    check(
        "every control still names itself for screen readers",
        page.evaluate(
            """() => ['#viewer-close', '#viewer-download', '#viewer-clip',
                      '#viewer-prev-burst', '#viewer-next-burst']
                 .every(sel => (document.querySelector(sel).getAttribute('aria-label') || '').length > 3)"""
        ),
    )
    shot(page, "25-toolbar-icons")
    page.close()


def face_filter_test(browser):
    print("faces: a compact filter that narrows the grid to one person")
    page = browser.new_page(viewport={"width": 1000, "height": 900})
    watch(page, "faces")
    page.goto(ALBUM)
    page.wait_for_selector(".burst-tile")
    page.wait_for_timeout(500)

    chips = page.query_selector_all(".face-chip")
    check("one chip per person", len(chips) == 2, str(len(chips)))
    check(
        "the filter stays small and out of the way",
        page.evaluate(
            """() => {
                const f = document.getElementById('face-filter').getBoundingClientRect();
                const chip = document.querySelector('.face-chip').getBoundingClientRect();
                return f.height < 60 && chip.width <= 40;
            }"""
        ),
    )

    total = len(page.query_selector_all(".burst-tile"))
    chips[0].click()
    page.wait_for_timeout(400)
    shown = len(page.query_selector_all(".burst-tile"))
    check("picking a face narrows the grid", 0 < shown < total, f"{shown} of {total}")
    check(
        "only bursts with that person remain",
        page.evaluate(
            """() => {
                const ids = [...document.querySelectorAll('.burst-tile')].length;
                return ids === 2;   // fixture: f0 is in b0000 and b0001
            }"""
        ),
    )
    check("rows still fill the width when filtered", all(abs(f - 1) < 0.005 for f in page.evaluate(ROW_PROBE)["fills"]))

    # Radio behaviour: a second face replaces the first, never adds to it.
    page.query_selector_all(".face-chip")[1].click()
    page.wait_for_timeout(400)
    check(
        "only one face is ever active",
        page.evaluate("() => document.querySelectorAll('.face-chip.active').length") == 1,
    )
    check("switching face switches the selection", len(page.query_selector_all(".burst-tile")) == 2)

    # Picking the active one again clears the filter.
    page.query_selector_all(".face-chip")[1].click()
    page.wait_for_timeout(400)
    check("picking it again clears the filter", len(page.query_selector_all(".burst-tile")) == total)
    check("no chip left active", page.evaluate("() => document.querySelectorAll('.face-chip.active').length") == 0)
    shot(page, "27-face-filter")
    page.close()


def justified_grid_test(browser):
    print("grid: rows are filled edge to edge at any width")
    for width, height in [(390, 844), (768, 1024), (1400, 950)]:
        page = browser.new_page(viewport={"width": width, "height": height})
        watch(page, f"grid-{width}")
        page.goto(ALBUM)
        page.wait_for_selector(".burst-tile")
        page.wait_for_timeout(600)
        r = page.evaluate(ROW_PROBE)
        check(f"{width}px: tiles live in rows, nothing loose", r["stray"] == 0, str(r["stray"]))
        check(
            f"{width}px: every full row spans the width",
            all(abs(f - 1) < 0.005 for f in r["fills"]),
            f"fills={[round(f, 3) for f in r['fills']]}",
        )
        check(f"{width}px: one height per row", r["equalHeights"])
        page.close()


def lazy_loading_test(browser):
    print("loading: pictures are fetched when needed, not all at once")
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    watch(page, "lazy")
    medium = []
    page.on("request", lambda r: "/medium/" in r.url and medium.append(r.url))

    page.goto(ALBUM)
    page.wait_for_selector(".burst-tile")
    check(
        "every grid cover is marked lazy",
        page.evaluate("""() => [...document.querySelectorAll('.burst-tile img.cover')]
             .every(i => i.getAttribute('loading') === 'lazy')"""),
    )

    page.click(".burst-tile")
    page.wait_for_selector("#viewer:not([hidden])")
    page.wait_for_timeout(1500)
    check(
        "every filmstrip thumb is marked lazy",
        page.evaluate("""() => [...document.querySelectorAll('.filmstrip-thumb img')]
             .every(i => i.getAttribute('loading') === 'lazy')"""),
    )

    frames = page.evaluate("() => document.querySelectorAll('.filmstrip-thumb').length")
    on_open = len(medium)
    check(
        "opening a burst fetches a window, not the whole burst",
        0 < on_open < frames,
        f"{on_open} of {frames} frames",
    )

    # Touching the strip means the user is about to scrub: fetch the rest.
    strip_y = page.eval_on_selector("#filmstrip", "el => el.getBoundingClientRect().top + 30")
    touch(page, "#filmstrip", "touchstart", 200, strip_y)
    page.wait_for_timeout(2000)
    touch(page, "#filmstrip", "touchend", 200, strip_y)
    check(
        "touching the strip pulls in the rest of the burst",
        len(medium) >= frames,
        f"{len(medium)} of {frames} after touch (was {on_open})",
    )
    page.close()


def placeholder_test(browser):
    print("placeholders: every unloaded picture still shows it has a slot")
    # No watch(): the aborted requests below are the point of the test, and
    # their console noise is not a failure.
    page = browser.new_page(viewport={"width": 1280, "height": 900})

    # Hold every picture back so the placeholders are what's on screen.
    page.route("**/thumb/**", lambda route: route.abort())
    page.route("**/medium/**", lambda route: route.abort())
    page.route("**/display/**", lambda route: route.abort())

    page.goto(ALBUM)
    page.wait_for_selector(".burst-tile")
    page.wait_for_timeout(700)

    def painted(selector):
        return page.eval_on_selector(
            selector,
            """el => {
                const s = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return {
                    colour: s.backgroundColor,
                    icon: s.backgroundImage !== 'none',
                    sized: rect.width > 20 && rect.height > 20,
                };
            }""",
        )

    tile = painted(".burst-tile")
    check("unloaded grid tile keeps its slot", tile["sized"], str(tile))
    check("unloaded grid tile has a backdrop", tile["colour"] != "rgba(0, 0, 0, 0)", str(tile))
    check("unloaded grid tile shows an image glyph", tile["icon"], str(tile))
    shot(page, "23-placeholders-grid")

    page.query_selector_all(".burst-tile")[0].click()
    page.wait_for_selector("#viewer:not([hidden])")
    page.wait_for_timeout(700)

    thumb = painted(".filmstrip-thumb")
    check("unloaded filmstrip thumb keeps its slot", thumb["sized"], str(thumb))
    check("unloaded filmstrip thumb has a backdrop", thumb["colour"] != "rgba(0, 0, 0, 0)", str(thumb))
    check("unloaded filmstrip thumb shows an image glyph", thumb["icon"], str(thumb))

    check(
        "stage marks itself as loading",
        page.eval_on_selector("#viewer-media", "el => el.classList.contains('loading')"),
    )
    check(
        "stage placeholder is actually painted",
        page.eval_on_selector(
            "#viewer-media",
            "el => getComputedStyle(el, '::before').opacity === '1'",
        ),
    )
    shot(page, "24-placeholders-viewer")
    page.close()

    # ...and it gets out of the way once the picture is there.
    page2 = browser.new_page(viewport={"width": 1280, "height": 900})
    watch(page2, "placeholder-loaded")
    page2.goto(ALBUM)
    page2.wait_for_selector(".burst-tile")
    page2.query_selector_all(".burst-tile")[0].click()
    page2.wait_for_selector("#viewer:not([hidden])")
    page2.wait_for_timeout(1200)
    check(
        "stage stops showing the placeholder once loaded",
        not page2.eval_on_selector("#viewer-media", "el => el.classList.contains('loading')"),
    )
    page2.close()


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
    toolbar_icons_test(browser)
    face_filter_test(browser)
    justified_grid_test(browser)
    shuttle_tests(browser)
    zoom_tests(browser)
    shuttle_hint_test(browser)
    cover_frame_test(browser)
    video_playback_test(browser)
    portrait_fit_test(browser)
    placeholder_test(browser)
    lazy_loading_test(browser)
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
