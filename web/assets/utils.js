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
