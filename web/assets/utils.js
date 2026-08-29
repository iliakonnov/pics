export async function fetchJSON(url) {
  const res = await fetch(url, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

const MONTHS_RU = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];

export function formatDateShort(isoDate) {
  const [y, m, d] = isoDate.split("-").map(Number);
  return `${d} ${MONTHS_RU[m - 1]} ${y}`;
}

export function formatDateTime(isoString) {
  const dt = new Date(isoString);
  if (Number.isNaN(dt.getTime())) return "";
  const hh = String(dt.getHours()).padStart(2, "0");
  const mm = String(dt.getMinutes()).padStart(2, "0");
  return `${dt.getDate()} ${MONTHS_RU[dt.getMonth()]} ${dt.getFullYear()}, ${hh}:${mm}`;
}

export function formatDuration(seconds) {
  if (!seconds && seconds !== 0) return "";
  const total = Math.round(seconds);
  const mm = Math.floor(total / 60);
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

export function clamp(n, min, max) {
  return Math.min(Math.max(n, min), max);
}

export function pluralRu(n, [one, few, many]) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value !== undefined && value !== null && value !== false) {
      node.setAttribute(key, value === true ? "" : value);
    }
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(child));
  }
  return node;
}

export const isCoarsePointer = () =>
  window.matchMedia && window.matchMedia("(hover: none), (pointer: coarse)").matches;

/**
 * Lay tiles out in justified rows, the way photo galleries do: every row
 * is filled edge to edge and each tile keeps its own aspect ratio.
 *
 * A plain CSS grid can't do this — with mixed portrait and landscape
 * shots, one tall frame stretches its whole row and the landscape tiles
 * beside it leave a band of empty space.
 *
 * `items` is [{ el, aspect }]; sizes are written straight onto the
 * elements, so call it again after a resize.
 */
export function justifyRows(container, items, { gap = 6, targetHeight } = {}) {
  const width = container.clientWidth;
  if (!width || !items.length) return;
  const target = targetHeight || (width < 500 ? 130 : 200);

  let row = [];
  let rowAspect = 0;

  const flush = (isLastRow) => {
    if (!row.length) return;
    const available = width - gap * (row.length - 1);
    // A last row with only a couple of frames would balloon if stretched,
    // so leave it at the target height instead.
    let height = available / rowAspect;
    if (isLastRow && height > target * 1.3) height = target;

    let used = 0;
    row.forEach((item, i) => {
      const last = i === row.length - 1;
      const w = !isLastRow && last ? available - used : Math.floor(item.aspect * height);
      used += w;
      item.el.style.width = `${w}px`;
      item.el.style.height = `${Math.round(height)}px`;
    });
    row = [];
    rowAspect = 0;
  };

  for (const item of items) {
    row.push(item);
    rowAspect += item.aspect;
    if (rowAspect * target + gap * (row.length - 1) >= width) flush(false);
  }
  flush(true);
}

export function onResize(handler) {
  let pending = null;
  window.addEventListener("resize", () => {
    if (pending) cancelAnimationFrame(pending);
    pending = requestAnimationFrame(handler);
  });
}
