import { el, isCoarsePointer } from "./utils.js";

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
 * Render the burst grid. On fine-pointer (mouse) devices the animated
 * preview plays on hover; on coarse-pointer (touch) devices it autoplays
 * whenever the tile is in the viewport, since there is no hover there.
 */
export function renderBurstGrid(container, bursts, { onOpen }) {
  const coarse = isCoarsePointer();
  const observer = coarse
    ? new IntersectionObserver(
        (entries) => {
          for (const entry of entries) {
            const tile = entry.target;
            if (entry.isIntersecting) tile._loadPreview?.();
            else tile._unloadPreview?.();
          }
        },
        { rootMargin: "100px" }
      )
    : null;

  bursts.forEach((burst, index) => {
    const frame = burst.frames[burst.coverIndex];
    const cover = el("img", { class: "cover", src: frame.thumb, loading: "lazy", alt: "" });
    const children = [cover, badgeFor(burst)];

    const tile = el(
      "button",
      {
        class: "burst-tile",
        type: "button",
        style: `--tile-w:${burst.thumbW};--tile-h:${burst.thumbH}`,
        onClick: () => onOpen(index, 0),
      },
      children
    );

    if (burst.preview) {
      const preview = el("img", { class: "preview", loading: "lazy", alt: "" });
      preview.style.display = "none";
      tile.append(preview);

      const load = () => {
        if (!preview.getAttribute("src")) preview.src = burst.preview;
        preview.style.display = "";
        cover.style.display = "none";
      };
      const unload = () => {
        preview.style.display = "none";
        cover.style.display = "";
      };

      if (coarse) {
        tile._loadPreview = load;
        tile._unloadPreview = unload;
        observer.observe(tile);
      } else {
        tile.addEventListener("mouseenter", load);
        tile.addEventListener("mouseleave", () => {
          unload();
          preview.removeAttribute("src");
        });
      }
    }

    container.append(tile);
  });
}
