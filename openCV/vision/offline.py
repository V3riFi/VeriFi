"""Vision as a data-quality channel: per-trial checks from the saved frame CSV (<session>.frames.csv).

Why this, not face "tells": the face cues we scored were at chance (METHOD.md). What the camera
CAN reliably tell is whether a trial is usable:
  * reaction-time trials: was the word actually SEEN?  eyes open at onset, face toward the screen.
    A blink or a glance away at onset delays the key press for reasons that have nothing to do
    with recognition.
  * sensor-round trials: did the person MOVE, TALK, TOUCH their face or shift posture? Those
    produce skin-conductance, breathing and heart-rate changes of their own (movement artifacts).
  * countermeasures: deliberate movement or breathing on chosen items shows up as artifacts piling
    up on those items.
Every rule is blind to which item was shown (same thresholds for all), so dropping trials can lower
sensitivity but can't create a false "recognizes": the permutation test stays valid.
Thresholds: absolute floors plus robust outliers (median + 3.5 MAD over the session), so a fidgety
person isn't thrown out wholesale.
"""
from __future__ import annotations

import bisect
import csv
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

FLOAT_COLS = ("face_present", "eye_state", "eye_openness", "head_yaw", "head_pitch", "head_speed", "mouth_open", "face_touch",
              "gaze_offaxis", "head_ty", "head_tz", "yawn", "blink_event")


class Frames:
    def __init__(self, t: Sequence[float], cols: Dict[str, Sequence[float]]):
        self.t = np.asarray(t, dtype=float)
        self.cols = {k: np.asarray(v, dtype=float) for k, v in cols.items()}
        face = self.col("face_present")
        self.face_rate = float(np.nanmean(face)) if face is not None and face.size else 0.0
        ok = (face == 1) if face is not None else np.zeros(len(self.t), bool)
        self.med = {}
        for k in ("head_yaw", "head_pitch", "head_ty", "head_tz"):
            c = self.col(k)
            if c is not None and np.any(ok & np.isfinite(c)):
                self.med[k] = float(np.nanmedian(c[ok]))

    def col(self, k: str) -> Optional[np.ndarray]:
        return self.cols.get(k)

    def idx(self, a: float, b: float) -> slice:
        return slice(bisect.bisect_left(self.t, a), bisect.bisect_right(self.t, b))

    @property
    def span(self):
        return (float(self.t[0]), float(self.t[-1])) if len(self.t) else (0.0, 0.0)


def load_frames(path: str) -> Frames:
    t, cols = [], {k: [] for k in FLOAT_COLS}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                t.append(float(r["t"]))
            except (KeyError, ValueError, TypeError):
                continue
            for k in FLOAT_COLS:
                v = r.get(k)
                try:
                    cols[k].append(float(v) if v not in (None, "") else math.nan)
                except ValueError:
                    cols[k].append(math.nan)
    present = {k: v for k, v in cols.items() if any(math.isfinite(x) for x in v)}
    return Frames(t, present)


@dataclass
class Window:
    n: int                              # frames in the window
    face: float                         # share with a face
    eyes_open: Optional[float] = None   # share with eye state OPEN (of frames with a known state)
    on_screen: Optional[float] = None   # share facing the screen (head near session median, gaze on axis)
    motion: Optional[float] = None      # peak head angular speed, deg/s
    talk: Optional[float] = None        # SD of mouth opening
    touch: Optional[float] = None       # share with a hand on the face
    posture: Optional[float] = None     # head distance / height change, relative to session median distance
    yawn: bool = False


def window(fr: Frames, a: float, b: float, yaw_tol: float = 20.0, pitch_tol: float = 15.0) -> Window:
    s = fr.idx(a, b)
    n = s.stop - s.start
    if n == 0:
        return Window(0, 0.0)
    face = fr.col("face_present")[s] == 1
    w = Window(n, float(face.mean()))
    if not face.any():
        return w
    # eyes: the opening relative to this person's open eye (< 0.5 = closed), which doesn't depend
    # on the blink detector's state machine; the state is only a fallback for old CSVs
    op, st = fr.col("eye_openness"), fr.col("eye_state")
    if op is not None and np.isfinite(op[s][face]).any():
        v = op[s][face]
        v = v[np.isfinite(v)]
        w.eyes_open = float((v >= 0.5).mean())
    elif st is not None:
        known = st[s][face & np.isfinite(st[s]) & (st[s] >= 0)]
        w.eyes_open = float((known == 1).mean()) if known.size else None
    yaw, pitch = fr.col("head_yaw"), fr.col("head_pitch")
    if yaw is not None and pitch is not None and "head_yaw" in fr.med:
        dy = np.abs(yaw[s][face] - fr.med["head_yaw"])
        dp = np.abs(pitch[s][face] - fr.med.get("head_pitch", 0.0))
        facing = (dy <= yaw_tol) & (dp <= pitch_tol)
        g = fr.col("gaze_offaxis")
        if g is not None:
            gg = g[s][face]
            facing &= ~(gg == 1)
        w.on_screen = float(np.nanmean(facing)) if facing.size else None
    # motion = 95th percentile, not the max: single-frame pose jitter makes spikes; a real movement lasts
    # several frames. talk = SD of mouth opening. touch = share of frames.
    for k, attr, fn in (("head_speed", "motion", lambda v: np.nanpercentile(v, 95)), ("mouth_open", "talk", np.nanstd),
                        ("face_touch", "touch", np.nanmean)):
        c = fr.col(k)
        if c is not None:
            v = c[s][face]
            v = v[np.isfinite(v)]
            if v.size:
                setattr(w, attr, float(fn(v)))
    tz, ty = fr.col("head_tz"), fr.col("head_ty")
    if tz is not None and "head_tz" in fr.med and abs(fr.med["head_tz"]) > 1e-6:
        z = tz[s][face]
        z = z[np.isfinite(z)]
        y = ty[s][face] if ty is not None else np.array([])
        y = y[np.isfinite(y)]
        if z.size > 1:
            dz = (z.max() - z.min()) / abs(fr.med["head_tz"])
            dy = (y.max() - y.min()) / abs(fr.med["head_tz"]) if y.size > 1 else 0.0
            w.posture = float(max(dz, dy))
    yw = fr.col("yawn")
    w.yawn = bool(yw is not None and np.nanmax(np.nan_to_num(yw[s], nan=0.0)) > 0)
    return w


# ---------------------------------------------------------------------- gating
def gate_rt(fr: Frames, onsets: Sequence[Optional[float]], min_session_face: float = 0.7) -> Dict[int, str]:
    """Reaction-time trials to drop: {index: reason}. Window -0.05..+0.15 s around onset: the moment
    the word is read, before any key press (a blink right AFTER the press is normal and harmless).
    Not applied (empty) when the camera saw the face < 70% of the session: then 'no face' says more
    about the camera than about the person."""
    if fr.face_rate < min_session_face:
        return {}
    a0, a1 = fr.span
    out = {}
    for i, on in enumerate(onsets):
        if on is None or on < a0 or on > a1:
            continue
        w = window(fr, on - 0.05, on + 0.15)
        if w.n < 3:
            continue                                  # dropped frames: unknown, keep
        if w.face < 0.5:
            out[i] = "no_face"
        elif w.eyes_open is not None and w.eyes_open < 0.4:
            out[i] = "eyes_closed"
        elif w.on_screen is not None and w.on_screen < 0.5:
            out[i] = "looked_away"
    return out


def _robust_hi(vals: List[float], floor: float, k: float = 3.5) -> float:
    v = np.asarray([x for x in vals if x is not None and math.isfinite(x)])
    if v.size < 5:
        return floor
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med))) * 1.4826
    return max(floor, med + k * mad)


def gate_physio(fr: Frames, onsets: Sequence[Optional[float]], min_session_face: float = 0.7) -> Dict[int, str]:
    """Sensor-round presentations to drop: {index: reason}. Window -1..+6 s (baseline + response).
    Reasons: motion (head speed spike), talk (mouth moving), touch (hand on face: often the sensor
    hand), posture (leaning / sinking: moves the breathing belt and the electrodes)."""
    if fr.face_rate < min_session_face:
        return {}
    a0, a1 = fr.span
    ws: Dict[int, Window] = {}
    for i, on in enumerate(onsets):
        if on is not None and a0 <= on <= a1:
            w = window(fr, on - 1.0, on + 6.0)
            if w.n >= 10 and w.face >= 0.5:
                ws[i] = w
    if not ws:
        return {}
    thr = {"motion": _robust_hi([w.motion for w in ws.values()], 60.0),       # deg/s
           "talk": _robust_hi([w.talk for w in ws.values()], 0.08),
           "posture": _robust_hi([w.posture for w in ws.values()], 0.10)}       # 10% of distance
    out = {}
    for i, w in ws.items():
        if w.touch is not None and w.touch > 0.2:
            out[i] = "touch"
        elif w.motion is not None and w.motion > thr["motion"]:
            out[i] = "motion"
        elif w.talk is not None and w.talk > thr["talk"]:
            out[i] = "talk"
        elif w.posture is not None and w.posture > thr["posture"]:
            out[i] = "posture"
    return out


def countermeasure_check(labels: Sequence[str], gated: Dict[int, str], focus: str = "TRUE") -> Optional[str]:
    """Artifacts piling up on the tested item (a known RT/physiology countermeasure: move, breathe or
    bite the tongue on chosen items). One-sided hypergeometric test; flag at p < .01."""
    n = len(labels)
    k_f = sum(1 for lab in labels if lab == focus)
    g = len(gated)
    g_f = sum(1 for i in gated if labels[i] == focus)
    if n == 0 or k_f == 0 or g_f < 3:
        return None
    p = sum(math.comb(k_f, x) * math.comb(n - k_f, g - x) for x in range(g_f, min(k_f, g) + 1)) / math.comb(n, g)
    if p < 0.01:
        return (f"possible countermeasure: {g_f} of {k_f} presentations of the tested details had artifacts vs "
                f"{g - g_f} of {n - k_f} of the alternatives (p = {p:.4f})")
    return None


def summary(gated: Dict[int, str], n: int) -> str:
    if not gated:
        return f"0 of {n} dropped"
    by: Dict[str, int] = {}
    for r in gated.values():
        by[r] = by.get(r, 0) + 1
    return f"{len(gated)} of {n} dropped (" + ", ".join(f"{k} {v}" for k, v in sorted(by.items())) + ")"
