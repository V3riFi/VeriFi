"""Head pose from 6 Face Mesh landmarks via OpenCV solvePnP.

The 3D reference model is a generic face in OpenCV camera convention
(x right, y DOWN, z AWAY from camera; nose tip at origin). Because it already
matches the image axes, a frontal face gives R ~= identity and the Euler angles
are ~0 - no 180-degree pitch flip, which is the #1 solvePnP head-pose bug.

Conventions of the output (degrees, verified by tests/test_headpose.py):
    pitch > 0 : chin up / looking up
    yaw   > 0 : head turned toward the image's right side
    roll  > 0 : head tilted clockwise in the image
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

from . import geometry as G
from .config import HeadPoseConfig
from .smoothing import OneEuroFilter

# Order MUST match geometry.POSE_LANDMARKS: nose, chin, R-eye outer, L-eye outer, R-mouth, L-mouth.
# Points are defined by IMAGE side: x<0 = image-left (subject's right in a non-mirrored frame).
MODEL_POINTS = np.array([
    (0.0, 0.0, 0.0),          # nose tip (1)
    (0.0, 330.0, 65.0),       # chin (152)
    (-225.0, -170.0, 135.0),  # right eye outer corner (33)
    (225.0, -170.0, 135.0),   # left eye outer corner (263)
    (-150.0, 150.0, 125.0),   # right mouth corner (61)
    (150.0, 150.0, 125.0),    # left mouth corner (291)
], dtype=np.float64)


@dataclass
class HeadPose:
    pitch: float
    yaw: float
    roll: float
    rvec: np.ndarray
    tvec: np.ndarray
    angular_speed: float = 0.0   # deg/s, from filtered angles

    @property
    def translation(self) -> Tuple[float, float, float]:
        x, y, z = np.asarray(self.tvec, dtype=np.float64).ravel()[:3]
        return float(x), float(y), float(z)


def rotation_to_euler(R: np.ndarray) -> Tuple[float, float, float]:
    """R = Rz(roll) @ Ry(yaw) @ Rx(pitch)  ->  (pitch, yaw, roll) in degrees, with our sign conventions."""
    sy = math.hypot(R[0, 0], R[1, 0])
    if sy > 1e-6:
        x = math.atan2(R[2, 1], R[2, 2])
        y = math.atan2(-R[2, 0], sy)
        z = math.atan2(R[1, 0], R[0, 0])
    else:  # gimbal lock - never happens for a face the mesh can still track
        x = math.atan2(-R[1, 2], R[1, 1])
        y = math.atan2(-R[2, 0], sy)
        z = 0.0
    # Camera frame is x-right, y-down, z-away, so raw +x rotation = chin down and raw +y = nose
    # toward image-left. Flip both so that "up" and "toward image-right" are positive.
    return -math.degrees(x), -math.degrees(y), math.degrees(z)


class HeadPoseEstimator:
    def __init__(self, cfg: Optional[HeadPoseConfig] = None):
        self.cfg = cfg or HeadPoseConfig()
        self._cam: Optional[np.ndarray] = None
        self._cam_size: Tuple[int, int] = (0, 0)
        self._rvec: Optional[np.ndarray] = None
        self._tvec: Optional[np.ndarray] = None
        mk = lambda: OneEuroFilter(self.cfg.one_euro_min_cutoff, self.cfg.one_euro_beta)
        self._filters = (mk(), mk(), mk())
        self._prev: Optional[Tuple[float, float, float, float]] = None   # (t, p, y, r)

    def _camera_matrix(self, w: int, h: int) -> np.ndarray:
        if self._cam is None or self._cam_size != (w, h):
            f = float(w)   # ~60 deg horizontal FOV approximation; good enough for relative pose
            self._cam = np.array([[f, 0, w / 2.0], [0, f, h / 2.0], [0, 0, 1]], dtype=np.float64)
            self._cam_size = (w, h)
        return self._cam

    def reset(self) -> None:
        self._rvec = self._tvec = None
        self._prev = None
        for f in self._filters:
            f.reset()

    def estimate(self, points: np.ndarray, w: int, h: int, t: float) -> Optional[HeadPose]:
        idx = list(G.POSE_LANDMARKS)
        if points[idx[2], 0] > points[idx[3], 0]:
            # Mirrored (selfie) frame: anatomical right is on the image right. The model is
            # defined by IMAGE side, so swap L/R correspondences (a reflection can't be solved
            # as a rotation and would give garbage angles).
            idx = [idx[0], idx[1], idx[3], idx[2], idx[5], idx[4]]
        img = points[idx, :2].astype(np.float64)
        cam = self._camera_matrix(w, h)
        dist = np.zeros((4, 1))
        use_guess = self._rvec is not None
        ok, rvec, tvec = cv2.solvePnP(
            MODEL_POINTS, img, cam, dist,
            rvec=self._rvec.copy() if use_guess else None,
            tvec=self._tvec.copy() if use_guess else None,
            useExtrinsicGuess=use_guess, flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok or tvec[2, 0] <= 0:          # face "behind" the camera = bad solution
            self.reset()
            return None
        self._rvec, self._tvec = rvec, tvec
        R, _ = cv2.Rodrigues(rvec)
        raw = rotation_to_euler(R)
        pitch, yaw, roll = (f(v, t) for f, v in zip(self._filters, raw))

        speed = 0.0
        if self._prev is not None:
            dt = t - self._prev[0]
            if dt > 1e-4:
                speed = math.sqrt((pitch - self._prev[1]) ** 2 + (yaw - self._prev[2]) ** 2
                                  + (roll - self._prev[3]) ** 2) / dt
        self._prev = (t, pitch, yaw, roll)
        return HeadPose(pitch, yaw, roll, rvec, tvec, speed)

    def project_axes(self, pose: HeadPose, w: int, h: int, length: float = 200.0) -> np.ndarray:
        """2D endpoints [origin, x, y, z] for drawing the pose axes on the nose."""
        pts3 = np.array([[0, 0, 0], [length, 0, 0], [0, -length, 0], [0, 0, -length]], dtype=np.float64)
        pts2, _ = cv2.projectPoints(pts3, pose.rvec, pose.tvec, self._camera_matrix(w, h), np.zeros((4, 1)))
        return pts2.reshape(-1, 2)
