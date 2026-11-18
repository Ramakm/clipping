"""--detect-facecam: find the streamer's face in a sample frame and suggest a crop box."""

from __future__ import annotations

import logging
from pathlib import Path

from config import FacecamBox
from media import run_ffmpeg

log = logging.getLogger(__name__)


def grab_frame(video: Path, out_png: Path, at: float = 0.0) -> Path:
    run_ffmpeg(["-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", str(out_png)])
    return out_png


def _detect_faces(image_path: Path) -> list[tuple[int, int, int, int]]:
    """Return face boxes (x, y, w, h) in pixels. MediaPipe first, OpenCV Haar fallback."""
    import cv2  # type: ignore[import-untyped]  # installed with mediapipe

    img = cv2.imread(str(image_path))
    if img is None:
        raise RuntimeError(f"could not read {image_path}")
    h, w = img.shape[:2]
    try:
        import mediapipe as mp  # type: ignore[import-untyped]
        with mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5) as fd:
            res = fd.process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        boxes = []
        for d in res.detections or []:
            b = d.location_data.relative_bounding_box
            boxes.append((int(b.xmin * w), int(b.ymin * h), int(b.width * w), int(b.height * h)))
        return boxes
    except (ImportError, AttributeError):
        log.info("mediapipe face detection unavailable — falling back to OpenCV Haar cascade")
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return [tuple(map(int, f)) for f in cascade.detectMultiScale(gray, 1.1, 5)]  # type: ignore[misc]


def suggest_box(face: tuple[int, int, int, int], src_w: int, src_h: int, aspect: float,
                expand: float) -> FacecamBox:
    """Grow the face box by `expand`, fit it to `aspect` (w/h), clamp inside the frame."""
    fx, fy, fw, fh = face
    cx, cy = fx + fw / 2, fy + fh / 2
    h = min(fh * expand, src_h)
    w = h * aspect
    if w > src_w:
        w, h = src_w, src_w / aspect
    x = min(max(cx - w / 2, 0), src_w - w)
    y = min(max(cy - h / 2, 0), src_h - h)
    return FacecamBox(int(x), int(y), int(w) // 2 * 2, int(h) // 2 * 2)


def detect_facecam(video: Path, workdir: Path, src_w: int, src_h: int, aspect: float,
                   expand: float) -> FacecamBox | None:
    workdir.mkdir(parents=True, exist_ok=True)
    frame = grab_frame(video, workdir / "facecam_sample.png")
    try:
        faces = _detect_faces(frame)
    except ImportError:
        log.error("--detect-facecam needs mediapipe: pip install mediapipe")
        return None
    finally:
        frame.unlink(missing_ok=True)
    if not faces:
        log.warning("no face found in sample frame")
        return None
    largest = max(faces, key=lambda f: f[2] * f[3])
    return suggest_box(largest, src_w, src_h, aspect, expand)
