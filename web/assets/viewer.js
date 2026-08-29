import { clamp, el, formatDateTime, formatDuration } from "./utils.js";

const WHEEL_LOCKOUT_MS = 180;
const WHEEL_THRESHOLD = 40;
const SWIPE_THRESHOLD = 60;
const SCROLL_SETTLE_MS = 120;

/**
 * Fullscreen burst viewer. Binds to the #viewer DOM already present in
 * album.html. Navigation model:
 *  - wheel / ArrowUp / ArrowDown / scrolling or swiping the filmstrip
 *    move between frames of the current burst.
 *  - ArrowLeft / ArrowRight / horizontal swipe on the main stage move
 *    between bursts.
 *  - vertical swipe-down on the stage, Escape, or the close button close
 *    the viewer.
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

  let burstIndex = 0;
  let frameIndex = 0;
  let isOpen = false;
  let wheelLocked = false;
  let wheelAccum = 0;
  let scrollSettleTimer = null;
  let ignoreNextFilmstripScroll = false;

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

  function updateMedia() {
    const burst = bursts[burstIndex];
    const frame = burst.frames[frameIndex];
    mediaHost.innerHTML = "";

    if (frame.video) {
      mediaHost.append(el("video", { src: frame.video, poster: frame.thumb, controls: true, playsinline: true }));
    } else {
      mediaHost.append(el("img", { src: frame.display || frame.thumb, alt: "" }));
    }

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

    const frames = burst.frames;
    if (frameIndex > 0) preload(frames[frameIndex - 1].display || frames[frameIndex - 1].thumb);
    if (frameIndex < frames.length - 1) preload(frames[frameIndex + 1].display || frames[frameIndex + 1].thumb);
    if (burstIndex > 0) {
      const b = bursts[burstIndex - 1];
      const f = b.frames[Math.min(frameIndex, b.frames.length - 1)];
      preload(f.display || f.thumb);
    }
    if (burstIndex < bursts.length - 1) {
      const b = bursts[burstIndex + 1];
      const f = b.frames[Math.min(frameIndex, b.frames.length - 1)];
      preload(f.display || f.thumb);
    }
  }

  function updateFilmstripActive() {
    filmstrip.querySelectorAll(".filmstrip-thumb").forEach((node, i) => {
      node.classList.toggle("active", i === frameIndex);
    });
  }

  function scrollActiveThumbIntoView() {
    filmstrip.querySelector(".filmstrip-thumb.active")?.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
  }

  function buildFilmstrip() {
    const burst = bursts[burstIndex];
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

  function pushOrReplace(push) {
    const hash = "#" + hashFor(burstIndex, frameIndex);
    const state = { viewer: true };
    if (push) history.pushState(state, "", hash);
    else history.replaceState(state, "", hash);
  }

  function goTo(bi, fi, { push = false } = {}) {
    const burstChanged = bi !== burstIndex;
    burstIndex = clamp(bi, 0, bursts.length - 1);
    frameIndex = clamp(fi, 0, bursts[burstIndex].frames.length - 1);
    updateMedia();
    ignoreNextFilmstripScroll = true;
    if (burstChanged) buildFilmstrip();
    else updateFilmstripActive();
    scrollActiveThumbIntoView();
    pushOrReplace(push);
  }

  function setFrameFromFilmstripScroll(fi) {
    if (fi === frameIndex) return;
    frameIndex = fi;
    updateMedia();
    updateFilmstripActive();
    pushOrReplace(false);
  }

  function showAt(bi, fi) {
    isOpen = true;
    root.hidden = false;
    document.body.style.overflow = "hidden";
    burstIndex = clamp(bi, 0, bursts.length - 1);
    frameIndex = clamp(fi, 0, bursts[burstIndex].frames.length - 1);
    updateMedia();
    buildFilmstrip();
    scrollActiveThumbIntoView();
  }

  function hide() {
    isOpen = false;
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
    pushOrReplace(false);
    return true;
  }

  function close() {
    if (isOpen) history.back();
  }

  const nextFrame = () => goTo(burstIndex, frameIndex + 1);
  const prevFrame = () => goTo(burstIndex, frameIndex - 1);
  const nextBurst = () => goTo(burstIndex + 1, 0);
  const prevBurst = () => goTo(burstIndex - 1, 0);

  // -- wiring --

  closeBtn.addEventListener("click", close);
  prevBurstBtn.addEventListener("click", prevBurst);
  nextBurstBtn.addEventListener("click", nextBurst);

  window.addEventListener("popstate", (event) => {
    if (event.state && event.state.viewer) {
      const target = findFromHash(location.hash);
      if (target) showAt(target.bi, target.fi);
    } else {
      hide();
    }
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

  filmstrip.addEventListener(
    "scroll",
    () => {
      if (ignoreNextFilmstripScroll) {
        ignoreNextFilmstripScroll = false;
      }
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

  // horizontal swipe on the stage -> burst nav; swipe down -> close.
  // Skipped while a video is showing so scrubbing its native controls
  // doesn't get misread as a swipe.
  let touchStartX = 0;
  let touchStartY = 0;
  let tracking = false;

  stage.addEventListener(
    "touchstart",
    (event) => {
      if (event.touches.length !== 1 || mediaHost.querySelector("video")) {
        tracking = false;
        return;
      }
      tracking = true;
      touchStartX = event.touches[0].clientX;
      touchStartY = event.touches[0].clientY;
    },
    { passive: true }
  );

  stage.addEventListener(
    "touchend",
    (event) => {
      if (!tracking) return;
      tracking = false;
      const touch = event.changedTouches[0];
      const dx = touch.clientX - touchStartX;
      const dy = touch.clientY - touchStartY;
      if (Math.abs(dx) > Math.abs(dy) * 1.5 && Math.abs(dx) > SWIPE_THRESHOLD) {
        if (dx < 0) nextBurst();
        else prevBurst();
      } else if (dy > Math.abs(dx) * 1.5 && dy > 80) {
        close();
      }
    },
    { passive: true }
  );

  return { open, openFromHash };
}
