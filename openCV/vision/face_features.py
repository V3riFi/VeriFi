"""Non-blink facial signals: mouth, lips, brows, non-rigid motion energy, gaze, yawns.

All distances are normalized by inter-ocular distance (IOD) after removing
roll, so values are invariant to distance-from-camera and head tilt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

from . import geometry as G
from .config import GazeConfig
from .landmarks import FaceObservation


@dataclass
class FacialFeatures:
    mouth_open: float              # inner-lip gap / mouth width (MAR)
    lip_thickness: float           # visible red-lip height / IOD; drops when lips are pressed
    brow_raise: float              # brow-to-upper-lid height / IOD
    brow_inner_gap: float          # inner-brow distance / IOD; drops when brows furrow
    facial_motion_energy: float    # non-rigid landmark motion, IOD/s
    gaze_h: Optional[float] = None         # 0.5 ~ centered; +/- = right/left in image
    gaze_v: Optional[float] = None
    gaze_offaxis: Optional[int] = None     # 1 = eyes or head pointed away from camera
    gaze_shift: int = 0                    # 1 on the frame a rapid gaze shift is detected
    yawn: int = 0                          # 1 on the frame a yawn completes


class FacialFeatureExtractor:
    YAWN_MAR = 0.55
    YAWN_MIN_S = 1.2

    def __init__(self, gaze_cfg: Optional[GazeConfig] = None):
        self.gcfg = gaze_cfg or GazeConfig()
        self._prev_aligned: Optional[np.ndarray] = None
        self._prev_t: Optional[float] = None
        self._gaze_center_samples: list = []
        self.gaze_center: Optional[float] = None
        self._prev_gaze: Optional[tuple] = None
        self._last_shift_t = -1e9
        self._yawn_start: Optional[float] = None

    def reset_motion(self) -> None:
        self._prev_aligned = None
        self._prev_t = None
        self._prev_gaze = None

    def update(self, obs: FaceObservation, yaw: float, eyes_open: bool) -> FacialFeatures:
        pts, t = obs.points, obs.timestamp
        a = G.align_face(pts)          # IOD-normalized, roll-free

        mouth_w = max(G.dist(a[G.MOUTH_INNER_CORNER_R], a[G.MOUTH_INNER_CORNER_L]), 1e-6)
        mouth_open = G.dist(a[G.UPPER_LIP_INNER], a[G.LOWER_LIP_INNER]) / mouth_w
        lip_thickness = (G.dist(a[G.UPPER_LIP_OUTER], a[G.UPPER_LIP_INNER])
                         + G.dist(a[G.LOWER_LIP_INNER], a[G.LOWER_LIP_OUTER]))

        # y grows downward: brow raise = upper-lid y minus brow y (positive, bigger when raised)
        r_raise = a[G.RIGHT_EYE_LIDS[0], 1] - a[list(G.RIGHT_BROW), 1].mean()
        l_raise = a[G.LEFT_EYE_LIDS[0], 1] - a[list(G.LEFT_BROW), 1].mean()
        brow_raise = float(0.5 * (r_raise + l_raise))
        brow_inner_gap = G.dist(a[G.RIGHT_BROW_INNER], a[G.LEFT_BROW_INNER])

        energy = 0.0
        if self._prev_aligned is not None and self._prev_t is not None:
            dt = t - self._prev_t
            if 1e-3 < dt < 0.5:
                energy = G.procrustes_residual(self._prev_aligned, a, G.RIGID, G.EXPRESSIVE) / dt
        self._prev_aligned, self._prev_t = a, t

        feats = FacialFeatures(mouth_open, lip_thickness, brow_raise, brow_inner_gap, energy)
        feats.yawn = self._yawn(mouth_open, t)
        if obs.has_iris:
            self._gaze(pts, t, yaw, eyes_open, feats)
        return feats

    # ------------------------------------------------------------------ helpers
    def _yawn(self, mar: float, t: float) -> int:
        if mar > self.YAWN_MAR:
            if self._yawn_start is None:
                self._yawn_start = t
            return 0
        done = self._yawn_start is not None and (t - self._yawn_start) >= self.YAWN_MIN_S
        self._yawn_start = None
        return int(done)

    def _gaze(self, pts: np.ndarray, t: float, yaw: float, eyes_open: bool, f: FacialFeatures) -> None:
        if not eyes_open:              # iris position is garbage mid-blink
            self._prev_gaze = None
            return
        # corners ordered image-left -> image-right for both eyes so h grows to the right
        rh, rv = G.iris_ratio(pts, G.RIGHT_IRIS_CENTER, G.RIGHT_EYE_CORNERS, G.RIGHT_EYE_LIDS)
        lh, lv = G.iris_ratio(pts, G.LEFT_IRIS_CENTER, G.LEFT_EYE_CORNERS[::-1], G.LEFT_EYE_LIDS)
        if pts[G.RIGHT_EYE_CORNERS[0], 0] > pts[G.RIGHT_EYE_CORNERS[1], 0]:   # mirrored frame
            rh, lh = 1 - rh, 1 - lh
        h, v = 0.5 * (rh + lh), 0.5 * (rv + lv)
        f.gaze_h, f.gaze_v = h, v

        if self.gaze_center is None:
            if abs(yaw) < 10:
                self._gaze_center_samples.append(h)
            if len(self._gaze_center_samples) >= self.gcfg.center_samples:
                self.gaze_center = float(np.median(self._gaze_center_samples))
        center = self.gaze_center if self.gaze_center is not None else 0.5
        f.gaze_offaxis = int(abs(h - center) > self.gcfg.aversion_threshold
                             or abs(yaw) > self.gcfg.aversion_yaw_deg)

        if self._prev_gaze is not None:
            dt = t - self._prev_gaze[0]
            if 1e-3 < dt < 0.5:
                vel = abs(h - self._prev_gaze[1]) / dt
                if vel > self.gcfg.shift_velocity and (t - self._last_shift_t) * 1000 > self.gcfg.shift_refractory_ms:
                    f.gaze_shift = 1
                    self._last_shift_t = t
        self._prev_gaze = (t, h)


def blendshape_subset(blend: Optional[Dict[str, float]]) -> Dict[str, float]:
    """Pre-trained (no training needed) ARKit-style coefficients that map to stress cues."""
    if not blend:
        return {}
    pick = lambda *names: float(np.mean([blend.get(n, 0.0) for n in names]))
    return {
        "bs_brow_down": pick("browDownLeft", "browDownRight"),
        "bs_brow_inner_up": pick("browInnerUp"),
        "bs_mouth_press": pick("mouthPressLeft", "mouthPressRight"),
        "bs_lip_roll": pick("mouthRollLower", "mouthRollUpper"),
        "bs_eye_squint": pick("eyeSquintLeft", "eyeSquintRight"),
        "bs_eye_blink": pick("eyeBlinkLeft", "eyeBlinkRight"),   # model's own eye-closure score 0..1
        "bs_jaw_open": pick("jawOpen"),
        "bs_mouth_frown": pick("mouthFrownLeft", "mouthFrownRight"),
    }
