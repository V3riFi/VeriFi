"""Rolling-window aggregation + personal baseline (the part the fusion team actually consumes).

Every `window_s` seconds: summarize the last window of per-frame records into
~35 numbers, then (once calibrated) add z-scores against this person's own
calm baseline and one composite `vision_anomaly_score`.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from .blink import BlinkDetector
from .config import AggregationConfig, HeadPoseConfig

Record = Dict[str, Optional[float]]

# anomaly feature -> (window key it is computed from, sign so that + = more stress-like)
ANOMALY_SOURCES: Dict[str, Tuple[str, int]] = {
    "blink_rate": ("blink_rate", +1),
    "head_motion_energy": ("head_motion_energy", +1),
    "facial_motion_energy": ("facial_motion_energy", +1),
    "lip_compression": ("lip_thickness_mean", -1),
    "brow_furrow": ("brow_inner_gap_mean", -1),
    "gaze_aversion_ratio": ("gaze_aversion_ratio", +1),
    "head_reversal_rate": ("head_reversal_rate", +1),
    "face_touch_ratio": ("face_touch_ratio", +1),
}


def _arr(rows: List[Record], key: str) -> np.ndarray:
    return np.array([r[key] for r in rows if r.get(key) is not None], dtype=np.float64)


def _mean(rows, key) -> Optional[float]:
    a = _arr(rows, key)
    return float(a.mean()) if a.size else None


def _std(rows, key) -> Optional[float]:
    a = _arr(rows, key)
    return float(a.std()) if a.size >= 2 else None


def _reversals(rows: List[Record], key: str, deadband: float) -> int:
    """Direction changes of an angle whose speed exceeds `deadband` deg/s (nods/shakes/fidgets)."""
    vals =[(r["t"], r[key]) for r in rows if r.get(key) is not None]
    if len(vals) < 3:
        return 0
    t = np.array([x[0] for x in vals])
    v = np.array([x[1] for x in vals])
    dt = np.diff(t)
    ok = dt > 1e-4
    vel = np.diff(v)[ok] / dt[ok]
    signs = np.sign(vel[np.abs(vel) > deadband])
    return int(np.count_nonzero(signs[1:] != signs[:-1])) if signs.size > 1 else 0


class BaselineCalibrator:
    """Mean/std of each window feature over the first `baseline_s` seconds of good data."""

    def __init__(self, cfg: AggregationConfig):
        self.cfg = cfg
        self.windows: List[Dict[str, Optional[float]]] = []
        self.mean: Dict[str, float] = {}
        self.std: Dict[str, float] = {}
        self.ready = False

    @property
    def progress(self) -> float:
        if self.ready:
            return 1.0
        return min(1.0, len(self.windows) * self.cfg.window_s / self.cfg.baseline_s)

    def reset(self) -> None:
        self.windows.clear()
        self.mean.clear()
        self.std.clear()
        self.ready = False

    def offer(self, wf: Dict[str, Optional[float]]) -> None:
        if self.ready or (wf.get("face_presence_ratio") or 0) < self.cfg.min_presence_for_baseline:
            return
        self.windows.append(wf)
        if self.progress >= 1.0 and len(self.windows) >= 3:
            keys = {k for w in self.windows for k, v in w.items() if v is not None}
            for k in keys:
                vals = np.array([w[k] for w in self.windows if w.get(k) is not None], dtype=np.float64)
                if vals.size < 2:
                    continue
                mu = float(vals.mean())
                self.mean[k] = mu
                self.std[k] = max(float(vals.std()), 0.1 * abs(mu), 1e-3)
            self.ready = True

    def z(self, key: str, value: Optional[float]) -> Optional[float]:
        if not self.ready or value is None or key not in self.mean:
            return None
        return (value - self.mean[key]) / self.std[key]


class WindowAggregator:
    def __init__(self, cfg: AggregationConfig, head_cfg: HeadPoseConfig, weights: Dict[str, float]):
        self.cfg, self.head_cfg, self.weights = cfg, head_cfg, weights
        self.frames: Deque[Record] = deque()
        self.baseline = BaselineCalibrator(cfg)
        self._next_emit: Optional[float] = None
        self.last: Dict[str, Optional[float]] = {}

    def add(self, rec: Record) -> None:
        self.frames.append(rec)
        cut = rec["t"] - self.cfg.window_s - 1.0
        while self.frames and self.frames[0]["t"] < cut:
            self.frames.popleft()

    def due(self, t: float) -> bool:
        if self._next_emit is None:
            self._next_emit = t + self.cfg.window_s
            return False
        if t >= self._next_emit:
            self._next_emit += self.cfg.window_s
            if self._next_emit < t:          # we stalled; don't burst-emit to catch up
                self._next_emit = t + self.cfg.window_s
            return True
        return False

    def compute(self, t: float, blink: BlinkDetector) -> Dict[str, Optional[float]]:
        W = self.cfg.window_s
        rows = [r for r in self.frames if r["t"] >= t - W]
        face = [r for r in rows if r.get("face_present")]
        wf: Dict[str, Optional[float]] = {
            "window_seconds": W,
            "frames": float(len(rows)),
            "fps": len(rows) / W,
            "face_presence_ratio": (len(face) / len(rows)) if rows else 0.0,
        }

        # --- eyes
        recent_durs = blink.blinks_since(t - blink.cfg.rate_window_s)
        closures = blink.long_closures_since(t - W)
        wf.update({
            "blink_rate": blink.blink_rate_per_minute(t),
            "blink_count": float(len(blink.blinks_since(t - W))),
            "blink_duration_ms": float(np.mean(recent_durs)) if recent_durs else None,
            "perclos": _mean(face, "eye_closed"),
            "long_closure_count": float(len(closures)),
            "max_closure_ms": float(max(closures)) if closures else 0.0,
            "ear_mean": _mean(face, "ear"),
        })

        # --- head
        for a in ("pitch", "yaw", "roll"):
            wf[f"head_{a}_mean"] = _mean(face, f"head_{a}")
            wf[f"head_{a}_std"] = _std(face, f"head_{a}")
        wf["head_motion_energy"] = _mean(face, "head_speed")
        db = self.head_cfg.reversal_deadband_dps
        wf["head_reversal_rate"] = (_reversals(face, "head_yaw", db) + _reversals(face, "head_pitch", db)) / W
        tx, ty, tz = _arr(face, "head_tx"), _arr(face, "head_ty"), _arr(face, "head_tz")
        wf["head_sway"] = (float(np.hypot(tx.std(), ty.std()) / tz.mean() * 100.0)
                           if tz.size >= 2 and tz.mean() > 0 else None)

        # --- mouth / brows / face
        wf.update({
            "mouth_open_mean": _mean(face, "mouth_open"),
            "mouth_movement": _std(face, "mouth_open"),
            "lip_thickness_mean": _mean(face, "lip_thickness"),
            "brow_raise_mean": _mean(face, "brow_raise"),
            "brow_movement": _std(face, "brow_raise"),
            "brow_inner_gap_mean": _mean(face, "brow_inner_gap"),
            "facial_motion_energy": _mean(face, "facial_motion_energy"),
            "yawn_count": float(sum(r.get("yawn") or 0 for r in face)),
        })

        # --- gaze + hands
        wf["gaze_aversion_ratio"] = _mean(face, "gaze_offaxis")
        wf["gaze_shift_rate"] = float(sum(r.get("gaze_shift") or 0 for r in face)) / W
        wf["gaze_h_std"] = _std(face, "gaze_h")
        wf["face_touch_ratio"] = _mean(rows, "face_touch")

        # --- optional pre-trained blendshapes (tasks backend only)
        for k in sorted({k for r in face for k in r if k.startswith("bs_")}):
            wf[f"{k}_mean"] = _mean(face, k)

        self._add_baseline_features(wf)
        self.last = wf
        return wf

    def _add_baseline_features(self, wf: Dict[str, Optional[float]]) -> None:
        bl = self.baseline
        bl.offer(wf)
        wf["baseline_ready"] = 1.0 if bl.ready else 0.0
        wf["baseline_progress"] = bl.progress
        if not bl.ready:
            wf["vision_anomaly_score"] = None
            return
        def rel_drop(key: str) -> Optional[float]:
            v, mu = wf.get(key), bl.mean.get(key)
            return None if v is None or not mu else 1.0 - v / mu
        wf["lip_compression"] = rel_drop("lip_thickness_mean")
        wf["brow_furrow"] = rel_drop("brow_inner_gap_mean")

        clip = self.cfg.z_clip
        num = den = 0.0
        for name, (src, sign) in ANOMALY_SOURCES.items():
            z = bl.z(src, wf.get(src))
            if z is None:
                wf[f"{name}_z"] = None
                continue
            z = float(np.clip(sign * z, -clip, clip))
            wf[f"{name}_z"] = z
            w = self.weights.get(name, 0.0)
            num += w * z
            den += w
        wf["vision_anomaly_score"] = num / den if den else None
