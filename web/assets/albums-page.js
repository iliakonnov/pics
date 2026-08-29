import { el, fetchJSON, formatDateShort, justifyRows, onResize, pluralRu } from "./utils.js";

async function main() {
  const grid = document.getElementById("albums-grid");
  const emptyState = document.getElementById("empty-state");

  let index;
  try {
    index = await fetchJSON("/albums.json");
  } catch {
    index = { albums: [] };
  }

  const albums = (index.albums || []).slice().sort((a, b) => (a.date < b.date ? 1 : -1));

  if (albums.length === 0) {
    emptyState.hidden = false;
    return;
  }

  const layoutItems = [];

  for (const album of albums) {
    const card = el(
      "a",
      { class: "album-card", href: `albums/${album.id}/` },
      [
        album.cover
          ? el("img", { src: `/${album.cover}`, loading: "lazy", alt: album.title })
          : null,
        el("span", { class: "card-caption" }, [
          el("span", { class: "card-title" }, album.title),
          " — ",
          el(
            "span",
            { class: "card-date" },
            `${formatDateShort(album.date)} · ${album.burstCount} ${pluralRu(album.burstCount, ["серия", "серии", "серий"])}`
          ),
        ]),
      ]
    );
    layoutItems.push({ el: card, aspect: (album.coverW || 3) / (album.coverH || 2) });
    grid.append(card);
  }

  const relayout = () => justifyRows(grid, layoutItems, { targetHeight: 220 });
  relayout();
  onResize(relayout);
}

main();
