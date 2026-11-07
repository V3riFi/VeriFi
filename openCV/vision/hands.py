"""Tier 2: face-touch / hand-over-face detection (self-soothing gesture).

Runs MediaPipe HandLandmarker every N frames and checks whether fingertips
fall inside the (slightly padded) face box. A hand over the face often makes
Face Mesh lose the face, so the last face box is reused for up to 1.5 s.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

import cv2
import numpy as np

from .landmarks import resolve_model

log = logging.getLogger(__name__)
FINGERTIPS = (4, 8, 12, 16, 20)
BBox = Tuple[int, int, int, int]


class FaceTouchDetector:
    def __init__(self, model_path: str = "vision/hand_landmarker.task", every_n_frames: int = 2):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
        model = resolve_model(model_path)
        if not model.exists():
            raise FileNotFoundError(f"{model} missing. Run: python download_models.py")
        self._mp = mp
        self._impl = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.VIDEO, num_hands=2,
            min_hand_detection_confidence=0.5, min_tracking_confidence=0.5))
        self.every = max(1, every_n_frames)
        self._n = 0
        self._last_ts = -1
        self._face_box: Optional[BBox] = None
        self._face_box_t = -1e9
        self.touching = 0
        self.hand_points: list = []

    def update(self, frame_bgr: np.ndarray, t: float, face_box: Optional[BBox]) -> int:
        if face_box is not None:
            self._face_box, self._face_box_t = face_box, t
        self._n += 1
        if self._n % self.every:
            return self.touching
        box = self._face_box if (t - self._face_box_t) < 1.5 else None
        if box is None:
            self.touching, self.hand_points = 0, []
            return 0
        ts = max(int(t * 1000), self._last_ts + 1)
        self._last_ts = ts
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        res = self._impl.detect_for_video(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb), ts)
        h, w = frame_bgr.shape[:2]
        x0, y0, x1, y1 = box
        self.hand_points, touch = [], 0
        for hand in res.hand_landmarks or []:
            pts = [(lm.x * w, lm.y * h) for lm in hand]
            self.hand_points.append(pts)
            inside = sum(1 for i in FINGERTIPS if x0 <= pts[i][0] <= x1 and y0 <= pts[i][1] <= y1)
            if inside >= 2:
                touch = 1
        self.touching = touch
        return touch

    def close(self) -> None:
        try:
            self._impl.close()
        except Exception:  # noqa: BLE001
            pass
