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
    page.evaluate(
        """([selector, kind, x, y]) => {
            const el = document.querySelector(selector);
            const t = new Touch({ identifier: 1, target: el, clientX: x, clientY: y });
            el.dispatchEvent(new TouchEvent(kind, {
                touches: kind === 'touchend' ? [] : [t],
                changedTouches: [t], bubbles: true, cancelable: true,
            }));
        }""",
        [selector, kind, x, y],
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
    check("three burst tiles", len(page.query_selector_all(".burst-tile")) == 3)
    shot(page, "02-album-grid")

    page.query_selector_all(".burst-tile")[0].hover()
    page.wait_for_timeout(150)
    check("hover swaps in preview", page.eval_on_selector(".burst-tile .preview", "el => el.style.display !== 'none'"))
    shot(page, "03-hover-preview")

    page.click(".burst-tile")
    page.wait_for_selector("#viewer:not([hidden])")
    check("filmstrip shown for multi-frame burst", page.eval_on_selector("#filmstrip", "el => !el.hidden"))
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
    page.evaluate("location.hash = '#b0002:0'")
    page.wait_for_timeout(300)
    check("same-page hash change opens that burst", page.eval_on_selector("#viewer", "el => !el.hidden"))
    page.close()

    # A shared deep link arriving as a cold page load.
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    watch(page, "deeplink")
    page.goto(f"{ALBUM}#b0002:0")
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
    check(
        "touch autoplays preview in viewport",
        page.eval_on_selector(".burst-tile .preview", "el => el && el.style.display !== 'none'"),
    )
    shot(page, "11-mobile-grid")

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


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROMIUM, headless=True)
    desktop_tests(browser)
    shuttle_tests(browser)
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
