import { el, isCoarsePointer, justifyRows, onResize } from "./utils.js";

// A press must be held this long before it counts as "peeking" rather
// than tapping, and a tap may wander this far before it stops counting
// as a tap.
const PEEK_HOLD_MS = 350;
const MOVE_SLOP_PX = 12;

function playIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.innerHTML = '<path d="M8 5v14l11-7z" fill="currentColor"/>';
  return svg;
}

function badgeFor(burst) {
  if (burst.type === "video") {
    return el("span", { class: "badge" }, [playIcon()]);
  }
  if (burst.count > 1) {
    return el("span", { class: "badge" }, `×${burst.count}`);
  }
  return null;
}

/**
 * Render the burst grid.
 *
 * The animated preview plays on hover with a mouse. On touch there is no
 * hover, so it follows the finger instead: exactly the tile currently
 * under the finger animates, dragging sideways moves the preview from
 * tile to tile, and lifting stops it. Only ever one preview runs at a
 * time, so a phone isn't decoding a screenful of animations at once.
 *
 * A quick tap still opens the burst; anything longer is a peek and is
 * prevented from opening it. Vertical movement means the page is being
 * scrolled, so the peek is abandoned and the scroll left alone.
 */
export function renderBurstGrid(container, bursts, { onOpen }) {
  const coarse = isCoarsePointer();
  const layoutItems = [];

  bursts.forEach((burst, index) => {
    const frame = burst.frames[burst.coverIndex];
    const cover = el("img", { class: "cover", src: frame.thumb, loading: "lazy", alt: "" });

    const tile = el(
      "button",
      { class: "burst-tile", type: "button", onClick: () => onOpen(index, 0) },
      [cover, badgeFor(burst)]
    );
    layoutItems.push({ el: tile, aspect: (burst.thumbW || 3) / (burst.thumbH || 2) });

    if (burst.preview) {
      const preview = el("img", { class: "preview", alt: "" });
      preview.style.display = "none";
      tile.append(preview);

      tile._loadPreview = () => {
        if (!preview.getAttribute("src")) preview.src = burst.preview;
        preview.style.display = "";
        cover.style.display = "none";
      };
      tile._unloadPreview = () => {
        preview.style.display = "none";
        cover.style.display = "";
        preview.removeAttribute("src");
      };

      if (!coarse) {
        tile.addEventListener("mouseenter", () => tile._loadPreview());
        tile.addEventListener("mouseleave", () => tile._unloadPreview());
      }
    }

    container.append(tile);
  });

  const relayout = () => justifyRows(container, layoutItems);
  relayout();
  onResize(relayout);

  if (!coarse) return;

  let peeking = null;
  let startX = 0;
  let startY = 0;
  let startedAt = 0;
  let abandoned = false;
  let suppressClick = false;

  const tileAt = (x, y) => document.elementFromPoint(x, y)?.closest(".burst-tile") || null;

  function peek(tile) {
    if (tile === peeking) return;
    peeking?._unloadPreview?.();
    peeking = tile;
    peeking?._loadPreview?.();
  }

  container.addEventListener(
    "touchstart",
    (event) => {
      suppressClick = false;
      abandoned = event.touches.length !== 1;
      if (abandoned) {
        peek(null);
        return;
      }
      const touch = event.touches[0];
      startX = touch.clientX;
      startY = touch.clientY;
      startedAt = performance.now();
      peek(tileAt(startX, startY));
    },
    { passive: true }
  );

  container.addEventListener(
    "touchmove",
    (event) => {
      if (abandoned || event.touches.length !== 1) return;
      const touch = event.touches[0];
      const dx = touch.clientX - startX;
      const dy = touch.clientY - startY;

      // Vertical wins => the user is scrolling the grid, not peeking.
      if (Math.abs(dy) > Math.abs(dx) && Math.abs(dy) > MOVE_SLOP_PX) {
        abandoned = true;
        suppressClick = true;
        peek(null);
        return;
      }
      if (Math.abs(dx) > MOVE_SLOP_PX) {
        suppressClick = true;
        peek(tileAt(touch.clientX, touch.clientY));
      }
    },
    { passive: true }
  );

  function endPeek() {
    if (!abandoned && performance.now() - startedAt >= PEEK_HOLD_MS) suppressClick = true;
    peek(null);
    abandoned = false;
  }

  container.addEventListener("touchend", endPeek, { passive: true });
  container.addEventListener(
    "touchcancel",
    () => {
      suppressClick = true;
      endPeek();
    },
    { passive: true }
  );

  // Swallow the click a peek would otherwise produce. Every touchstart
  // clears the flag, so an ordinary tap afterwards still opens the burst.
  container.addEventListener(
    "click",
    (event) => {
      if (!suppressClick) return;
      suppressClick = false;
      event.preventDefault();
      event.stopPropagation();
    },
    true
  );

  // Holding a finger on an image otherwise raises the "save image" sheet
  // or a selection callout, which would interrupt the peek.
  container.addEventListener("contextmenu", (event) => event.preventDefault());
}
