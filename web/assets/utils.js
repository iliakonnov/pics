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
 * Lay tiles out in justified rows: fill each row with as many pictures as
 * fit at no less than `minHeight`, then scale the row up so it spans the
 * full width. Every picture keeps its own aspect ratio, and every picture
 * in a row shares one height.
 *
 * Pure CSS cannot do this. The usual `flex-grow: <aspect>` trick fills the
 * width but leaves rows at inconsistent heights and needs a hack for the
 * last row, because CSS has no way to solve "scale this set of aspect
 * ratios to exactly this width".
 *
 * Rows are real elements rather than flex-wrap: when the browser does the
 * wrapping it can disagree with the arithmetic — a scrollbar appearing
 * mid-layout narrows the container, one tile drops to the next line, and
 * the whole grid shifts out of step. Explicit rows cannot drift, and
 * flex-shrink absorbs any leftover fraction of a pixel.
 *
 * `items` is [{ el, aspect }]; call it again after a resize.
 */
export function justifyRows(container, items, { gap = 6, minHeight } = {}) {
  const width = container.clientWidth;
  if (!width || !items.length) return;
  const floor = minHeight || (width < 700 ? 120 : 190);
  // A row that cannot be filled (the last one, or a single panorama)
  // should not balloon to fill the screen.
  const ceiling = floor * 2.2;

  const rows = [];
  let row = [];
  let rowAspect = 0;

  for (const item of items) {
    const aspect = item.aspect > 0 ? item.aspect : 1.5;
    if (row.length) {
      const heightIfAdded = (width - gap * row.length) / (rowAspect + aspect);
      if (heightIfAdded < floor) {
        rows.push({ row, rowAspect, full: true });
        row = [];
        rowAspect = 0;
      }
    }
    row.push({ ...item, aspect });
    rowAspect += aspect;
  }
  if (row.length) rows.push({ row, rowAspect, full: false });

  rows.forEach(({ row: cells, rowAspect: total, full }, index) => {
    const available = width - gap * (cells.length - 1);
    let height = available / total;
    if (!full) height = Math.min(height, ceiling);

    let rowEl = container.children[index];
    if (!rowEl || !rowEl.classList.contains("burst-row")) {
      rowEl = document.createElement("div");
      rowEl.className = "burst-row";
      container.append(rowEl);
    }

    let used = 0;
    cells.forEach((cell, i) => {
      const last = i === cells.length - 1;
      const w = full && last ? available - used : Math.floor(cell.aspect * height);
      used += w;
      cell.el.style.width = `${w}px`;
      cell.el.style.height = `${Math.round(height)}px`;
      rowEl.append(cell.el);
    });
  });

  while (container.children.length > rows.length) container.lastElementChild.remove();
}

export function onResize(handler) {
  let pending = null;
  window.addEventListener("resize", () => {
    if (pending) cancelAnimationFrame(pending);
    pending = requestAnimationFrame(handler);
  });
}
