import { fetchJSON, formatDateShort } from "./utils.js";
import { renderBurstGrid, renderFaceFilter } from "./gallery.js";
import { initViewer } from "./viewer.js";

async function main() {
  const grid = document.getElementById("burst-grid");
  const emptyState = document.getElementById("empty-state");
  const titleEl = document.getElementById("album-title");

  let album;
  try {
    album = await fetchJSON("./album.json");
  } catch (err) {
    titleEl.textContent = "Альбом не найден";
    emptyState.hidden = false;
    emptyState.textContent = "Не удалось загрузить альбом.";
    return;
  }

  document.title = album.title;
  titleEl.textContent = `${album.title} — ${formatDateShort(album.date)}`;

  if (!album.bursts || album.bursts.length === 0) {
    emptyState.hidden = false;
    return;
  }

  const viewer = initViewer(album.bursts, album.diskUrl);
  const gallery = renderBurstGrid(grid, album.bursts, { onOpen: (index, frame) => viewer.open(index, frame) });
  renderFaceFilter(document.getElementById("face-filter"), album.faces, {
    onPick: (faceId) => gallery.filterByFace(faceId),
  });

  viewer.openFromHash();
}

main();
