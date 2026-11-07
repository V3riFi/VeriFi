"""Face landmark backend. Hides MediaPipe API differences behind one class.

Backends:
  * "tasks"  - mediapipe.tasks FaceLandmarker (current MediaPipe; 478 pts + 52 blendshapes)
  * "legacy" - mp.solutions.face_mesh (older MediaPipe <= 0.10.x; 478 pts with refine_landmarks)
"auto" picks tasks if the model file exists, else legacy if available.
"""
from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

log = logging.getLogger(__name__)
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


@dataclass
class FaceObservation:
    """One face in one frame. Pixel coordinates; z is in the same (pixel) scale as x."""
    timestamp: float
    points: np.ndarray                      # (N, 3) float32 pixels
    width: int
    height: int
    blendshapes: Optional[Dict[str, float]] = None

    @property
    def has_iris(self) -> bool:
        return self.points.shape[0] >= 478

    def bbox(self, pad: float = 0.0) -> Tuple[int, int, int, int]:
        x0, y0 = self.points[:, 0].min(), self.points[:, 1].min()
        x1, y1 = self.points[:, 0].max(), self.points[:, 1].max()
        px, py = (x1 - x0) * pad, (y1 - y0) * pad
        return int(x0 - px), int(y0 - py), int(x1 + px), int(y1 + py)


def resolve_model(path: str) -> pathlib.Path:
    p = pathlib.Path(path)
    if p.is_absolute() or p.exists():
        return p
    return PROJECT_ROOT / p


def _legacy_available() -> bool:
    try:
        import mediapipe as mp
        return hasattr(mp, "solutions") and hasattr(mp.solutions, "face_mesh")
    except ImportError:
        return False


class FaceMeshDetector:
    def __init__(self, backend: str = "auto", model_path: str = "models/face_landmarker.task",
                 min_detection_confidence: float = 0.5, min_tracking_confidence: float = 0.5):
        model = resolve_model(model_path)
        if backend == "auto":
            backend = "tasks" if model.exists() else ("legacy" if _legacy_available() else "tasks")
        self.backend = backend
        self._last_ts_ms = -1
        if backend == "tasks":
            if not model.exists():
                raise FileNotFoundError(
                    f"FaceLandmarker model not found at {model}. Run: python download_models.py")
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision
            self._mp = mp
            opts = vision.FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(model)),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=1,
                min_face_detection_confidence=min_detection_confidence,
                min_face_presence_confidence=min_detection_confidence,
                min_tracking_confidence=min_tracking_confidence,
                output_face_blendshapes=True,
            )
            self._impl = vision.FaceLandmarker.create_from_options(opts)
        elif backend == "legacy":
            import mediapipe as mp
            self._impl = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=False, max_num_faces=1, refine_landmarks=True,
                min_detection_confidence=min_detection_confidence,
                min_tracking_confidence=min_tracking_confidence)
        else:
            raise ValueError(f"unknown backend {backend!r}")
        log.info("Face mesh backend: %s", self.backend)

    def process(self, frame_bgr: np.ndarray, timestamp: float) -> Optional[FaceObservation]:
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        blend: Optional[Dict[str, float]] = None
        if self.backend == "tasks":
            ts_ms = int(timestamp * 1000)
            if ts_ms <= self._last_ts_ms:          # VIDEO mode demands strictly increasing timestamps
                ts_ms = self._last_ts_ms + 1
            self._last_ts_ms = ts_ms
            image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
            res = self._impl.detect_for_video(image, ts_ms)
            if not res.face_landmarks:
                return None
            lms = res.face_landmarks[0]
            if res.face_blendshapes:
                blend = {c.category_name: float(c.score) for c in res.face_blendshapes[0]}
        else:
            rgb.flags.writeable = False
            res = self._impl.process(rgb)
            if not res.multi_face_landmarks:
                return None
            lms = res.multi_face_landmarks[0].landmark
        pts = np.array([(lm.x * w, lm.y * h, lm.z * w) for lm in lms], dtype=np.float32)
        return FaceObservation(timestamp, pts, w, h, blend)

    def close(self) -> None:
        try:
            self._impl.close()
        except Exception:  # noqa: BLE001 - best effort on shutdown
            pass


def tesselation_edges() -> List[Tuple[int, int]]:
    """Mesh edges for the overlay (empty list if unavailable)."""
    try:
        from mediapipe.tasks.python import vision
        return [(c.start, c.end) for c in vision.FaceLandmarksConnections.FACE_LANDMARKS_TESSELATION]
    except Exception:  # noqa: BLE001
        pass
    try:
        import mediapipe as mp
        return list(mp.solutions.face_mesh.FACEMESH_TESSELATION)
    except Exception:  # noqa: BLE001
        return []
