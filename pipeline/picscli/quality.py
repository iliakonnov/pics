"""Pick the frame of a burst worth showing as its cover.

Two signals, because measuring only one gets it wrong:

  * sharpness — variance of the Laplacian, the standard blur measure
  * blinking — MediaPipe's face blendshapes report an eyeBlink score per
    face directly, no landmark geometry needed. It is graded, not binary:
    a half-closed eye scores around 0.4, which is why the frames are put
    into bands rather than passed through one cutoff

Both are needed. Across 20 real bursts, picking purely by sharpness
landed on a frame with someone mid-blink twice out of the six bursts that
contained one — including a case where the sharpest frame of all had the
subject's eyes shut.

General image-quality metrics were measured against the same bursts and
are not a substitute: brisque, niqe, clipiqa and nima each picked a
blinking frame regularly (niqe in five of six bursts) and each picked the
*blurriest* frame of a burst several times. They score naturalness and
aesthetics, not "is this the good one of these near-identical shots".

Optional: needs `mediapipe` and `opencv-python`; without them the first
frame stays the cover.
"""

from __future__ import annotations

import threading
from pathlib import Path

from . import config

_local = threading.local()


def available() -> bool:
    try:
        import cv2  # noqa: F401
        import mediapipe  # noqa: F401
    except ImportError:
        return False
    return Path(config.FACE_LANDMARK_MODEL).is_file()


def _landmarker():
    """One detector per thread: they are not safe to share."""
    if getattr(_local, "landmarker", None) is None:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        _local.landmarker = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=config.FACE_LANDMARK_MODEL),
                num_faces=config.QUALITY_MAX_FACES,
                output_face_blendshapes=True,
                running_mode=vision.RunningMode.IMAGE,
            )
        )
    return _local.landmarker


def score_frame(path: Path) -> tuple[float, float]:
    """Return (sharpness, worst eye-blink score across the faces present)."""
    import cv2
    import mediapipe as mp

    image = cv2.imread(str(path))
    if image is None:
        return 0.0, 0.0

    sharpness = float(cv2.Laplacian(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())

    small = cv2.resize(image, (0, 0), fx=config.QUALITY_SCAN_SCALE, fy=config.QUALITY_SCAN_SCALE)
    result = _landmarker().detect(
        mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
    )
    blink = 0.0
    for shapes in result.face_blendshapes or []:
        scores = {c.category_name: c.score for c in shapes}
        blink = max(blink, scores.get("eyeBlinkLeft", 0.0), scores.get("eyeBlinkRight", 0.0))
    return sharpness, float(blink)


def pick_best(scores: list[tuple[float, float]]) -> int:
    """Sharpest frame among those with the most open eyes.

    Frames fall into bands by their blink score and the best non-empty
    band decides, so a slightly sharper frame does not win by being a
    half-blink. If every frame has someone mid-blink there is nothing
    better on offer and sharpness decides alone.
    """
    if not scores:
        return 0
    for limit in config.QUALITY_BLINK_BANDS:
        band = [i for i, (_sharp, blink) in enumerate(scores) if blink < limit]
        if band:
            return max(band, key=lambda i: scores[i][0])
    return max(range(len(scores)), key=lambda i: scores[i][0])
