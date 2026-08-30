"""Group an album's photos by who is in them.

Optional: needs `insightface`, `onnxruntime` and `scikit-learn`, which are
a heavy dependency and a ~300MB model download, so an import runs without
them and simply skips this step.

Faces are detected and embedded per photo, then clustered by cosine
distance. Nothing is named or recognised across albums — an identity is
just "the same person as in these other photos of this album", which is
all the filter needs.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import config

_local = threading.local()


@dataclass(slots=True)
class Identity:
    id: str
    avatar: str  # path relative to the album dir
    photo_count: int
    burst_ids: list[str] = field(default_factory=list)


def available() -> bool:
    try:
        import cv2  # noqa: F401
        import insightface  # noqa: F401
        import sklearn  # noqa: F401
    except ImportError:
        return False
    return True


def _analyser(det_size: int):
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name=config.FACE_MODEL, providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(det_size, det_size))
    return app


def _thread_analyser():
    """One model per worker: the sessions are not safe to share, and each
    copy costs about 600MB, which is what caps the worker count."""
    if getattr(_local, "app", None) is None:
        _local.app = _analyser(config.FACE_DET_SIZE)
    return _local.app


def _read_scaled(path: Path):
    import cv2

    image = cv2.imread(str(path))
    if image is None:
        return None
    if config.FACE_SCAN_SCALE != 1.0:
        image = cv2.resize(image, (0, 0), fx=config.FACE_SCAN_SCALE, fy=config.FACE_SCAN_SCALE)
    return image


def _crop_avatar(image, box, size: int):
    import cv2

    x1, y1, x2, y2 = (int(v) for v in box)
    pad = int(0.35 * max(x2 - x1, y2 - y1))
    h, w = image.shape[:2]
    crop = image[max(0, y1 - pad) : min(h, y2 + pad), max(0, x1 - pad) : min(w, x2 + pad)]
    if crop.size == 0:
        return None
    side = min(crop.shape[:2])
    top = (crop.shape[0] - side) // 2
    left = (crop.shape[1] - side) // 2
    return cv2.resize(crop[top : top + side, left : left + side], (size, size))


def group_by_face(
    album_dir: Path,
    photos: list[tuple[str, str, Path]],
    *,
    max_identities: int = config.FACE_MAX_IDENTITIES,
    jobs: int | None = None,
    log=lambda _m: None,
) -> tuple[list[Identity], dict[str, list[str]]]:
    """Cluster the faces in `photos` [(burst_id, frame_hash, image path)].

    Returns the identities worth offering as a filter, and a mapping of
    burst id -> identity ids present in it.
    """
    import cv2
    import numpy as np
    from sklearn.cluster import AgglomerativeClustering

    workers = max(1, min(jobs or config.FACE_WORKERS, config.FACE_WORKERS))

    def scan(item):
        burst_id, _hash, path = item
        image = _read_scaled(path)
        if image is None:
            return []
        out = []
        for face in _thread_analyser().get(image):
            if float(face.det_score) < config.FACE_MIN_SCORE:
                continue
            # The image itself is deliberately not kept: only a handful of
            # crops are ever needed, and holding one array per face would
            # cost gigabytes on a large album.
            out.append((burst_id, path, tuple(float(v) for v in face.bbox),
                        float(face.det_score), face.normed_embedding))
        return out

    log(f"  scanning {len(photos)} bursts on {workers} worker(s)...")
    found = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(scan, photos):
            found.extend(result)
            done += 1
            if done % max(1, len(photos) // 8) == 0:
                log(f"  scanned {done}/{len(photos)} bursts, {len(found)} faces so far")

    embeddings = [record[4] for record in found]
    floor = max(config.FACE_MIN_PHOTOS, round(len(photos) * config.FACE_MIN_SHARE))

    if len(embeddings) < floor:
        log("  too few faces to group")
        return [], {}

    labels = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=config.FACE_CLUSTER_DISTANCE,
        metric="cosine",
        linkage="average",
    ).fit_predict(np.array(embeddings))

    clusters: dict[int, list[int]] = {}
    for position, label in enumerate(labels):
        clusters.setdefault(int(label), []).append(position)

    # Someone who appears in a handful of frames is usually a passer-by,
    # and a long row of faces would defeat the point of a compact filter.
    ranked = sorted(
        (c for c in clusters.values() if len(c) >= floor),
        key=len,
        reverse=True,
    )[:max_identities]

    identities: list[Identity] = []
    by_burst: dict[str, list[str]] = {}
    avatar_dir = album_dir / "faces"

    for rank, positions in enumerate(ranked):
        identity_id = f"f{rank}"
        bursts = []
        for position in positions:
            burst_id = found[position][0]
            if burst_id not in bursts:
                bursts.append(burst_id)
            by_burst.setdefault(burst_id, [])
            if identity_id not in by_burst[burst_id]:
                by_burst[burst_id].append(identity_id)

        # The clearest detection makes the best button; its image is read
        # back now rather than having been carried around all along.
        best = max(positions, key=lambda p: found[p][3])
        _, path, box, _score, _emb = found[best]
        image = _read_scaled(path)
        avatar = _crop_avatar(image, box, config.FACE_AVATAR_SIZE) if image is not None else None
        if avatar is None:
            continue
        avatar_dir.mkdir(parents=True, exist_ok=True)
        rel = f"faces/{identity_id}.webp"
        cv2.imwrite(str(album_dir / rel), avatar, [cv2.IMWRITE_WEBP_QUALITY, 85])

        identities.append(Identity(id=identity_id, avatar=rel, photo_count=len(positions), burst_ids=bursts))

    log(f"  {len(identities)} face group(s): {[i.photo_count for i in identities]} photos each")
    return identities, by_burst
