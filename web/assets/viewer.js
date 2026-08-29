import { clamp, el, formatDateTime, formatDuration, isCoarsePointer } from "./utils.js";

const WHEEL_LOCKOUT_MS = 180;
const WHEEL_THRESHOLD = 40;
const SWIPE_THRESHOLD = 60;
const SCROLL_SETTLE_MS = 120;

// Touch "shuttle" control on the filmstrip. The finger does NOT pick a
// frame by position; its horizontal displacement from where it landed
// sets the *speed* of playback through the burst, like a jog/shuttle
// wheel. Further right = faster forward, further left = faster backward,
// wrapping around the ends of the burst. Lifting the finger stops the
// animation immediately (no inertia) on whatever frame is showing.
// Zoom
const MAX_SCALE = 6;
const DOUBLE_TAP_SCALE = 2.5;
const DOUBLE_TAP_MS = 300;
const DOUBLE_TAP_SLOP_PX = 30;

const SHUTTLE_DEADZONE_PX = 12;
const SHUTTLE_FULL_PX = 130; // displacement at which max speed is reached
const SHUTTLE_MIN_FPS = 2;
const SHUTTLE_MAX_FPS = 24; // matches the ZV-1's own top continuous-shooting rate
const TAP_SLOP_PX = 10;

/**
 * Fullscreen burst viewer. Binds to the #viewer DOM already present in
 * album.html. Navigation model:
 *  - frames within a burst: wheel / ArrowUp / ArrowDown; on touch, drag
 *    the filmstrip as a speed control (see above) or tap a thumbnail to
 *    jump straight to it.
 *  - between bursts: ArrowLeft / ArrowRight / horizontal swipe on the
 *    main stage.
 *  - close: Escape, the close button, or a downward swipe on the stage.
 * The current burst/frame is reflected in the URL hash (#burstId:frame)
 * via pushState on open and replaceState on navigation, so a single Back
 * press always returns to the grid.
 */
export function initViewer(bursts) {
  const root = document.getElementById("viewer");
  const closeBtn = document.getElementById("viewer-close");
  const info = document.getElementById("viewer-info");
  const downloadLink = document.getElementById("viewer-download");
  const stage = document.getElementById("viewer-stage");
  const prevBurstBtn = document.getElementById("viewer-prev-burst");
  const nextBurstBtn = document.getElementById("viewer-next-burst");
  const mediaHost = document.getElementById("viewer-media");
  const filmstrip = document.getElementById("filmstrip");

  const coarse = isCoarsePointer();

  let burstIndex = 0;
  let frameIndex = 0;
  let isOpen = false;
  let wheelLocked = false;
  let wheelAccum = 0;
  let scrollSettleTimer = null;
  let programmaticScrollUntil = 0;
  let scale = 1;
  let tx = 0;
  let ty = 0;

  const currentBurst = () => bursts[burstIndex];
  const currentFrame = () => currentBurst().frames[frameIndex];
  const hashFor = (bi, fi) => `${bursts[bi].id}:${fi}`;

  function findFromHash(hash) {
    const raw = hash.replace(/^#/, "");
    if (!raw) return null;
    const [burstId, frameStr] = raw.split(":");
    const bi = bursts.findIndex((b) => b.id === burstId);
    if (bi === -1) return null;
    const fi = clamp(parseInt(frameStr || "0", 10) || 0, 0, bursts[bi].frames.length - 1);
    return { bi, fi };
  }

  function preload(url) {
    if (!url) return;
    new Image().src = url;
  }

  // -- rendering ------------------------------------------------------

  /**
   * Swap the stage media to the current frame. During a shuttle drag
   * `lowRes` reuses the (already decoded) filmstrip thumbnail so frames
   * can change at up to 24fps without waiting on 2560px display JPEGs;
   * the sharp image is restored when the finger lifts.
   */
  function renderMedia({ lowRes = false } = {}) {
    const frame = currentFrame();

    // Whatever is shown next starts unzoomed.
    scale = 1;
    tx = 0;
    ty = 0;

    if (frame.video) {
      mediaHost.innerHTML = "";
      mediaHost.append(el("video", { src: frame.video, poster: frame.thumb, controls: true, playsinline: true }));
      return;
    }

    const src = lowRes ? frame.thumb : frame.display || frame.thumb;
    let img = mediaHost.querySelector("img");
    if (!img) {
      mediaHost.innerHTML = "";
      img = el("img", { alt: "" });
      mediaHost.append(img);
    }
    img.style.transition = "";
    img.style.transform = "";
    img.classList.remove("zoomed");
    if (img.getAttribute("src") !== src) img.setAttribute("src", src);
  }

  function updateChrome() {
    const burst = currentBurst();
    const frame = currentFrame();
    const exif = frame.exif || {};

    const bits = [formatDateTime(burst.capturedAt)];
    if (exif.exposureTime) bits.push(exif.exposureTime + " с");
    if (exif.fNumber) bits.push("f/" + exif.fNumber);
    if (exif.iso) bits.push("ISO " + exif.iso);
    if (exif.focalLength) bits.push(exif.focalLength);
    if (exif.durationSeconds) bits.push(formatDuration(exif.durationSeconds));

    info.innerHTML = "";
    info.append(
      el("strong", {}, burst.frames.length > 1 ? `${frameIndex + 1}/${burst.frames.length} — ` : ""),
      bits.filter(Boolean).join(" · ")
    );

    downloadLink.href = frame.original;
    downloadLink.setAttribute("download", frame.original.split("/").pop());

    prevBurstBtn.disabled = burstIndex === 0;
    nextBurstBtn.disabled = burstIndex === bursts.length - 1;
  }

  function preloadNeighbors() {
    const frames = currentBurst().frames;
    if (frameIndex > 0) preload(frames[frameIndex - 1].display || frames[frameIndex - 1].thumb);
    if (frameIndex < frames.length - 1) preload(frames[frameIndex + 1].display || frames[frameIndex + 1].thumb);
    for (const bi of [burstIndex - 1, burstIndex + 1]) {
      if (bi < 0 || bi >= bursts.length) continue;
      const b = bursts[bi];
      const f = b.frames[Math.min(frameIndex, b.frames.length - 1)];
      preload(f.display || f.thumb);
    }
  }

  function updateFilmstripActive() {
    filmstrip.querySelectorAll(".filmstrip-thumb").forEach((node, i) => {
      node.classList.toggle("active", i === frameIndex);
    });
  }

  function centerActiveThumb(smooth) {
    const node = filmstrip.querySelector(".filmstrip-thumb.active");
    if (!node) return;
    const left = node.offsetLeft - (filmstrip.clientWidth - node.offsetWidth) / 2;
    // Mark the scroll as ours so the desktop scroll-settle handler below
    // doesn't mistake it for the user browsing the strip by hand.
    programmaticScrollUntil = performance.now() + (smooth ? 700 : 120);
    filmstrip.scrollTo({ left, behavior: smooth ? "smooth" : "auto" });
  }

  function buildFilmstrip() {
    const burst = currentBurst();
    filmstrip.innerHTML = "";
    if (burst.frames.length < 2) {
      filmstrip.hidden = true;
      return;
    }
    filmstrip.hidden = false;
    burst.frames.forEach((frame, fi) => {
      filmstrip.append(
        el(
          "button",
          { class: "filmstrip-thumb", type: "button", onClick: () => goTo(burstIndex, fi) },
          [el("img", { src: frame.thumb, alt: "", loading: "lazy" })]
        )
      );
    });
    updateFilmstripActive();
  }

  // -- navigation -----------------------------------------------------

  function pushOrReplace(push) {
    const hash = "#" + hashFor(burstIndex, frameIndex);
    const state = { viewer: true };
    if (push) history.pushState(state, "", hash);
    else history.replaceState(state, "", hash);
  }

  function goTo(bi, fi, { push = false } = {}) {
    const burstChanged = bi !== burstIndex;
    burstIndex = clamp(bi, 0, bursts.length - 1);
    frameIndex = clamp(fi, 0, currentBurst().frames.length - 1);
    renderMedia();
    updateChrome();
    preloadNeighbors();
    if (burstChanged) buildFilmstrip();
    else updateFilmstripActive();
    centerActiveThumb(true);
    pushOrReplace(push);
  }

  function setFrameFromFilmstripScroll(fi) {
    if (fi === frameIndex) return;
    frameIndex = fi;
    renderMedia();
    updateChrome();
    updateFilmstripActive();
    pushOrReplace(false);
  }

  function showAt(bi, fi) {
    isOpen = true;
    root.hidden = false;
    document.body.style.overflow = "hidden";
    burstIndex = clamp(bi, 0, bursts.length - 1);
    frameIndex = clamp(fi, 0, currentBurst().frames.length - 1);
    renderMedia();
    updateChrome();
    preloadNeighbors();
    buildFilmstrip();
    centerActiveThumb(false);
  }

  function hide() {
    isOpen = false;
    stopShuttle({ settle: false });
    root.hidden = true;
    document.body.style.overflow = "";
    mediaHost.innerHTML = "";
  }

  function open(bi, fi = 0) {
    showAt(bi, fi);
    pushOrReplace(true);
  }

  function openFromHash() {
    const target = findFromHash(location.hash);
    if (!target) return false;
    showAt(target.bi, target.fi);
    // Arriving straight at a deep link means the viewer is the first thing
    // in this tab's history for the page. Insert a non-viewer entry below
    // it so a single Back press closes the viewer (back to the grid)
    // instead of leaving the page with nowhere to land.
    history.replaceState(null, "", location.pathname + location.search);
    pushOrReplace(true);
    return true;
  }

  function close() {
    if (isOpen) history.back();
  }

  const nextFrame = () => goTo(burstIndex, frameIndex + 1);
  const prevFrame = () => goTo(burstIndex, frameIndex - 1);
  const nextBurst = () => goTo(burstIndex + 1, 0);
  const prevBurst = () => goTo(burstIndex - 1, 0);

  // -- touch shuttle --------------------------------------------------

  let shuttleActive = false;
  let shuttleStartX = 0;
  let shuttleDx = 0;
  let shuttleRaf = null;
  let shuttleAccum = 0;
  let shuttleLastTs = 0;
  let shuttleDragged = false;

  function shuttleFps(dx) {
    const magnitude = Math.abs(dx);
    if (magnitude <= SHUTTLE_DEADZONE_PX) return 0;
    const t = clamp((magnitude - SHUTTLE_DEADZONE_PX) / (SHUTTLE_FULL_PX - SHUTTLE_DEADZONE_PX), 0, 1);
    // Quadratic ramp: fine control just outside the deadzone, full speed
    // only when the finger is pushed well out to the side.
    return SHUTTLE_MIN_FPS + (SHUTTLE_MAX_FPS - SHUTTLE_MIN_FPS) * t * t;
  }

  function shuttleStep(delta) {
    const length = currentBurst().frames.length;
    frameIndex = (((frameIndex + delta) % length) + length) % length; // wraps both ways
    renderMedia({ lowRes: true });
    updateChrome();
    updateFilmstripActive();
    centerActiveThumb(false);
  }

  function shuttleTick(ts) {
    if (!shuttleActive) return;
    const dt = shuttleLastTs ? Math.min((ts - shuttleLastTs) / 1000, 0.1) : 0;
    shuttleLastTs = ts;

    const fps = shuttleFps(shuttleDx);
    if (fps > 0) {
      shuttleAccum += fps * dt;
      const whole = Math.floor(shuttleAccum);
      if (whole > 0) {
        shuttleAccum -= whole;
        shuttleStep(Math.sign(shuttleDx) * whole);
      }
    } else {
      shuttleAccum = 0;
    }
    shuttleRaf = requestAnimationFrame(shuttleTick);
  }

  function startShuttle(x) {
    shuttleActive = true;
    shuttleStartX = x;
    shuttleDx = 0;
    shuttleAccum = 0;
    shuttleLastTs = 0;
    shuttleDragged = false;
    filmstrip.classList.add("shuttling");
    shuttleRaf = requestAnimationFrame(shuttleTick);
  }

  function stopShuttle({ settle = true } = {}) {
    if (!shuttleActive) return;
    shuttleActive = false;
    if (shuttleRaf !== null) cancelAnimationFrame(shuttleRaf);
    shuttleRaf = null;
    shuttleDx = 0;
    filmstrip.classList.remove("shuttling");
    if (!settle) return;
    // Animation stops dead on the current frame; restore full resolution.
    renderMedia();
    updateChrome();
    preloadNeighbors();
    centerActiveThumb(true);
    pushOrReplace(false);
  }

  if (coarse) {
    filmstrip.addEventListener(
      "touchstart",
      (event) => {
        if (event.touches.length !== 1) {
          stopShuttle();
          return;
        }
        startShuttle(event.touches[0].clientX);
      },
      { passive: true }
    );

    filmstrip.addEventListener(
      "touchmove",
      (event) => {
        if (!shuttleActive || event.touches.length !== 1) return;
        event.preventDefault();
        shuttleDx = event.touches[0].clientX - shuttleStartX;
        if (Math.abs(shuttleDx) > TAP_SLOP_PX) shuttleDragged = true;
      },
      { passive: false }
    );

    const endShuttle = () => stopShuttle();
    filmstrip.addEventListener("touchend", endShuttle, { passive: true });
    filmstrip.addEventListener("touchcancel", endShuttle, { passive: true });

    // A drag must not also register as a tap on whichever thumbnail the
    // finger happened to lift over. Fresh taps are unaffected: every
    // touchstart resets shuttleDragged.
    filmstrip.addEventListener(
      "click",
      (event) => {
        if (!shuttleDragged) return;
        event.preventDefault();
        event.stopPropagation();
      },
      true
    );
  } else {
    // Desktop only: browsing the strip by trackpad/scrollbar selects the
    // frame nearest the centre once the scrolling settles.
    filmstrip.addEventListener(
      "scroll",
      () => {
        if (performance.now() < programmaticScrollUntil) return;
        clearTimeout(scrollSettleTimer);
        scrollSettleTimer = setTimeout(() => {
          const rect = filmstrip.getBoundingClientRect();
          const center = rect.left + rect.width / 2;
          let closest = 0;
          let closestDist = Infinity;
          filmstrip.querySelectorAll(".filmstrip-thumb").forEach((node, i) => {
            const r = node.getBoundingClientRect();
            const dist = Math.abs(r.left + r.width / 2 - center);
            if (dist < closestDist) {
              closestDist = dist;
              closest = i;
            }
          });
          setFrameFromFilmstripScroll(closest);
        }, SCROLL_SETTLE_MS);
      },
      { passive: true }
    );
  }

  // -- other input ----------------------------------------------------

  closeBtn.addEventListener("click", close);
  prevBurstBtn.addEventListener("click", prevBurst);
  nextBurstBtn.addEventListener("click", nextBurst);

  // Decide from the hash rather than from our own history state, so that
  // manually editing the URL to a #burst:frame works the same as history
  // navigation between viewer entries. No hash (or an unknown burst id)
  // means the grid, so close.
  window.addEventListener("popstate", () => {
    const target = findFromHash(location.hash);
    if (target) showAt(target.bi, target.fi);
    else hide();
  });

  window.addEventListener("keydown", (event) => {
    if (!isOpen) return;
    if (event.key === "Escape") close();
    else if (event.key === "ArrowUp") { event.preventDefault(); prevFrame(); }
    else if (event.key === "ArrowDown") { event.preventDefault(); nextFrame(); }
    else if (event.key === "ArrowLeft") prevBurst();
    else if (event.key === "ArrowRight") nextBurst();
  });

  root.addEventListener(
    "wheel",
    (event) => {
      if (!isOpen) return;
      if (filmstrip.contains(event.target)) {
        filmstrip.scrollLeft += event.deltaY;
        event.preventDefault();
        return;
      }
      event.preventDefault();
      wheelAccum += event.deltaY;
      if (wheelLocked) return;
      if (Math.abs(wheelAccum) > WHEEL_THRESHOLD) {
        if (wheelAccum > 0) nextFrame();
        else prevFrame();
        wheelAccum = 0;
        wheelLocked = true;
        setTimeout(() => (wheelLocked = false), WHEEL_LOCKOUT_MS);
      }
    },
    { passive: false }
  );

  // -- stage gestures: zoom, pan, swipe --------------------------------
  //
  // At 1x a horizontal drag moves between bursts and a downward drag
  // closes. Zoomed in, the same drag pans the photo instead, so the two
  // never fight: to leave a zoomed photo you zoom back out (pinch in or
  // double-tap). Pinch and double-tap zoom around the point being
  // touched, the way a photo viewer is expected to behave.

  const zoomTarget = () => mediaHost.querySelector("img");
  const distance = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
  const midpoint = (a, b) => ({ x: (a.clientX + b.clientX) / 2, y: (a.clientY + b.clientY) / 2 });

  function applyTransform(animate = false) {
    const img = zoomTarget();
    if (!img) return;
    img.style.transition = animate ? "transform .18s ease-out" : "";
    img.style.transform =
      scale === 1 && tx === 0 && ty === 0 ? "" : `translate(${tx}px, ${ty}px) scale(${scale})`;
    img.classList.toggle("zoomed", scale > 1);
  }

  /**
   * Size of the picture as actually drawn, which is not the size of the
   * <img> box: the element fills the stage and object-fit letterboxes the
   * image inside it. Panning must be bounded by the picture, otherwise a
   * portrait shot could be dragged out into the empty side margins.
   */
  function renderedImageSize(img) {
    const boxW = img.offsetWidth;
    const boxH = img.offsetHeight;
    const { naturalWidth: nw, naturalHeight: nh } = img;
    if (!nw || !nh) return [boxW, boxH];
    const fit = Math.min(boxW / nw, boxH / nh);
    return [nw * fit, nh * fit];
  }

  function clampPan() {
    const img = zoomTarget();
    if (!img) {
      tx = 0;
      ty = 0;
      return;
    }
    const [shownW, shownH] = renderedImageSize(img);
    const maxX = Math.max(0, (shownW * scale - stage.clientWidth) / 2);
    const maxY = Math.max(0, (shownH * scale - stage.clientHeight) / 2);
    tx = clamp(tx, -maxX, maxX);
    ty = clamp(ty, -maxY, maxY);
  }

  function zoomAround(nextScale, px, py, { animate = false, from = null } = {}) {
    const base = from || { scale, tx, ty };
    const rect = stage.getBoundingClientRect();
    const relX = px - (rect.left + rect.width / 2);
    const relY = py - (rect.top + rect.height / 2);
    scale = clamp(nextScale, 1, MAX_SCALE);
    const ratio = scale / base.scale;
    tx = relX - (relX - base.tx) * ratio;
    ty = relY - (relY - base.ty) * ratio;
    if (scale === 1) {
      tx = 0;
      ty = 0;
    }
    clampPan();
    applyTransform(animate);
  }

  function toggleZoom(px, py) {
    if (!zoomTarget()) return;
    if (scale > 1) {
      scale = 1;
      tx = 0;
      ty = 0;
      applyTransform(true);
    } else {
      zoomAround(DOUBLE_TAP_SCALE, px, py, { animate: true });
    }
  }

  let gesture = null;
  let lastTapAt = 0;
  let lastTapX = 0;
  let lastTapY = 0;

  stage.addEventListener(
    "touchstart",
    (event) => {
      if (event.touches.length === 2) {
        const m = midpoint(event.touches[0], event.touches[1]);
        gesture = {
          mode: "pinch",
          startDistance: distance(event.touches[0], event.touches[1]),
          from: { scale, tx, ty },
          anchorX: m.x,
          anchorY: m.y,
        };
        return;
      }
      if (event.touches.length !== 1) {
        gesture = null;
        return;
      }
      // Touches that land on the video itself belong to its controls.
      if (event.target.closest("video")) {
        gesture = null;
        return;
      }
      const touch = event.touches[0];
      gesture = {
        mode: scale > 1 ? "pan" : "swipe",
        x0: touch.clientX,
        y0: touch.clientY,
        from: { scale, tx, ty },
        startedAt: performance.now(),
        moved: false,
      };
    },
    { passive: true }
  );

  stage.addEventListener(
    "touchmove",
    (event) => {
      if (!gesture) return;

      if (gesture.mode === "pinch") {
        if (event.touches.length !== 2) return;
        const spread = distance(event.touches[0], event.touches[1]);
        const m = midpoint(event.touches[0], event.touches[1]);
        zoomAround(gesture.from.scale * (spread / gesture.startDistance), m.x, m.y, { from: gesture.from });
        event.preventDefault();
        return;
      }

      if (event.touches.length !== 1) return;
      const touch = event.touches[0];
      const dx = touch.clientX - gesture.x0;
      const dy = touch.clientY - gesture.y0;
      if (Math.abs(dx) > 8 || Math.abs(dy) > 8) gesture.moved = true;

      if (gesture.mode === "pan") {
        tx = gesture.from.tx + dx;
        ty = gesture.from.ty + dy;
        clampPan();
        applyTransform();
        event.preventDefault();
      }
    },
    { passive: false }
  );

  stage.addEventListener(
    "touchend",
    (event) => {
      if (!gesture) return;

      if (gesture.mode === "pinch") {
        if (event.touches.length > 0) return; // second finger still down
        gesture = null;
        if (scale <= 1.05) {
          scale = 1;
          tx = 0;
          ty = 0;
          applyTransform(true);
        }
        return;
      }

      const finished = gesture;
      gesture = null;
      const touch = event.changedTouches[0];
      const dx = touch.clientX - finished.x0;
      const dy = touch.clientY - finished.y0;

      // Decide tap vs drag from where the finger actually ended up, not
      // from whether a touchmove happened to fire.
      const travelled = Math.hypot(dx, dy);
      if (!finished.moved && travelled < 10 && performance.now() - finished.startedAt < 300) {
        const now = performance.now();
        const isDouble =
          now - lastTapAt < DOUBLE_TAP_MS &&
          Math.hypot(touch.clientX - lastTapX, touch.clientY - lastTapY) < DOUBLE_TAP_SLOP_PX;
        if (isDouble) {
          lastTapAt = 0;
          toggleZoom(touch.clientX, touch.clientY);
        } else {
          lastTapAt = now;
          lastTapX = touch.clientX;
          lastTapY = touch.clientY;
        }
        return;
      }

      if (finished.mode === "pan" || scale > 1) return; // dragging a zoomed photo never navigates

      if (Math.abs(dx) > Math.abs(dy) * 1.5 && Math.abs(dx) > SWIPE_THRESHOLD) {
        if (dx < 0) nextBurst();
        else prevBurst();
      } else if (dy > Math.abs(dx) * 1.5 && dy > 80) {
        close();
      }
    },
    { passive: true }
  );

  // Mouse equivalents: double-click toggles zoom, drag pans while zoomed.
  stage.addEventListener("dblclick", (event) => {
    if (event.target.closest("video")) return;
    toggleZoom(event.clientX, event.clientY);
  });

  let mousePan = null;
  stage.addEventListener("mousedown", (event) => {
    if (event.button !== 0 || scale === 1 || !zoomTarget()) return;
    mousePan = { x0: event.clientX, y0: event.clientY, tx, ty };
    event.preventDefault();
  });
  window.addEventListener("mousemove", (event) => {
    if (!mousePan) return;
    tx = mousePan.tx + (event.clientX - mousePan.x0);
    ty = mousePan.ty + (event.clientY - mousePan.y0);
    clampPan();
    applyTransform();
  });
  window.addEventListener("mouseup", () => {
    mousePan = null;
  });

  return { open, openFromHash };
}
