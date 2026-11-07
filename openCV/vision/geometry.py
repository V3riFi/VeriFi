"""MediaPipe Face Mesh landmark indices + pure geometry helpers.

"Right"/"left" are the SUBJECT's right/left (subject's right eye appears on the
image left in a non-mirrored frame). All helpers take an (N, 2+) array of pixel
coordinates so they never care about which backend produced them.
"""
from __future__ import annotations

from typing import Sequence, Tuple

import numpy as np

# ---- Eyes: 6-point EAR layout  p1(outer/inner corner), p2, p3 (upper lid), p4 (other corner), p5, p6 (lower lid)
RIGHT_EYE_EAR = (33, 160, 158, 133, 153, 144)
LEFT_EYE_EAR = (362, 385, 387, 263, 373, 380)

RIGHT_EYE_CORNERS = (33, 133)    # outer, inner
LEFT_EYE_CORNERS = (263, 362)    # outer, inner
RIGHT_EYE_LIDS = (159, 145)      # top, bottom
LEFT_EYE_LIDS = (386, 374)

# Iris (only present with refine_landmarks / FaceLandmarker -> 478 points)
RIGHT_IRIS_CENTER = 468
LEFT_IRIS_CENTER = 473
RIGHT_IRIS = (469, 470, 471, 472)
LEFT_IRIS = (474, 475, 476, 477)

# Eye contours for drawing
RIGHT_EYE_CONTOUR = (33, 246, 161, 160, 159, 158, 157, 173, 133, 155, 154, 153, 145, 144, 163, 7)
LEFT_EYE_CONTOUR = (263, 466, 388, 387, 386, 385, 384, 398, 362, 382, 381, 380, 374, 373, 390, 249)

# ---- Mouth
UPPER_LIP_OUTER = 0
UPPER_LIP_INNER = 13
LOWER_LIP_INNER = 14
LOWER_LIP_OUTER = 17
MOUTH_CORNER_R = 61
MOUTH_CORNER_L = 291
MOUTH_INNER_CORNER_R = 78
MOUTH_INNER_CORNER_L = 308
LIPS_OUTER_CONTOUR = (61, 185, 40, 39, 37, 0, 267, 269, 270, 409, 291, 375, 321, 405, 314, 17, 84, 181, 91, 146)
LIPS_INNER_CONTOUR = (78, 191, 80, 81, 82, 13, 312, 311, 310, 415, 308, 324, 318, 402, 317, 14, 87, 178, 88, 95)

# ---- Brows
RIGHT_BROW = (70, 63, 105, 66, 107)   # outer -> inner
LEFT_BROW = (300, 293, 334, 296, 336)  # outer -> inner
RIGHT_BROW_INNER = 107
LEFT_BROW_INNER = 336

# ---- Head pose (solvePnP) correspondences
NOSE_TIP = 1
CHIN = 152
POSE_LANDMARKS = (NOSE_TIP, CHIN, RIGHT_EYE_CORNERS[0], LEFT_EYE_CORNERS[0], MOUTH_CORNER_R, MOUTH_CORNER_L)

# Landmarks that carry expression (used for non-rigid facial motion energy)
EXPRESSIVE = tuple(sorted(set(
    RIGHT_BROW + LEFT_BROW + LIPS_OUTER_CONTOUR + LIPS_INNER_CONTOUR
    + RIGHT_EYE_CONTOUR + LEFT_EYE_CONTOUR + (CHIN,)
)))
# Landmarks that barely move with expression (used as the rigid reference)
RIGID = (33, 133, 263, 362, 1, 4, 6, 168, 197, 195, 5, 234, 454, 10)


def dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a[:2] - b[:2]))


def eye_aspect_ratio(pts: np.ndarray, idx: Sequence[int]) -> float:
    """Soukupova & Cech EAR = (|p2-p6| + |p3-p5|) / (2 |p1-p4|)."""
    p1, p2, p3, p4, p5, p6 = (pts[i] for i in idx)
    horizontal = dist(p1, p4)
    if horizontal < 1e-6:
        return 0.0
    return (dist(p2, p6) + dist(p3, p5)) / (2.0 * horizontal)


def inter_ocular(pts: np.ndarray) -> float:
    """Outer-corner to outer-corner distance; our scale unit for everything."""
    return max(dist(pts[RIGHT_EYE_CORNERS[0]], pts[LEFT_EYE_CORNERS[0]]), 1e-6)


def eye_width(pts: np.ndarray, corners: Tuple[int, int]) -> float:
    return dist(pts[corners[0]], pts[corners[1]])


def align_face(pts: np.ndarray) -> np.ndarray:
    """Remove translation, in-plane rotation (roll) and scale.

    Returns 2D points where the eye midpoint is the origin, the eye line is
    horizontal, and the inter-ocular distance is 1.0. Positive y = down.
    """
    xy = pts[:, :2].astype(np.float64)
    a, b = xy[RIGHT_EYE_CORNERS[0]], xy[LEFT_EYE_CORNERS[0]]
    if a[0] > b[0]:            # mirrored frame: order by image x so we never rotate 180 deg
        a, b = b, a
    center = (a + b) / 2.0
    v = b - a
    scale = max(np.linalg.norm(v), 1e-6)
    ang = np.arctan2(v[1], v[0])
    c, s = np.cos(-ang), np.sin(-ang)
    rot = np.array([[c, -s], [s, c]])
    return (xy - center) @ rot.T / scale


def procrustes_residual(prev: np.ndarray, cur: np.ndarray, idx_fit: Sequence[int],
                        idx_eval: Sequence[int]) -> float:
    """Mean displacement of idx_eval points after best rigid 2D fit on idx_fit points."""
    a, b = prev[list(idx_fit)], cur[list(idx_fit)]
    ma, mb = a.mean(0), b.mean(0)
    h = (b - mb).T @ (a - ma)
    u, _, vt = np.linalg.svd(h)
    r = u @ vt
    if np.linalg.det(r) < 0:
        u[:, -1] *= -1
        r = u @ vt
    moved = (cur[list(idx_eval)] - mb) @ r + ma
    return float(np.linalg.norm(moved - prev[list(idx_eval)], axis=1).mean())


def iris_ratio(pts: np.ndarray, iris: int, corners: Tuple[int, int], lids: Tuple[int, int]) -> Tuple[float, float]:
    """Iris position inside the eye: (horizontal 0..1 from corner[0] to corner[1], vertical 0..1 top->bottom)."""
    c0, c1 = pts[corners[0], :2], pts[corners[1], :2]
    axis = c1 - c0
    n2 = float(axis @ axis)
    if n2 < 1e-6:
        return 0.5, 0.5
    h = float((pts[iris, :2] - c0) @ axis / n2)
    top, bot = pts[lids[0], :2], pts[lids[1], :2]
    vaxis = bot - top
    vn2 = float(vaxis @ vaxis)
    v = float((pts[iris, :2] - top) @ vaxis / vn2) if vn2 > 1e-6 else 0.5
    return h, v
