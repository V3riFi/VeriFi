"""Blink detection: adaptive-threshold EAR state machine with hysteresis.

False-positive defences, in order:
  1. Personal threshold: closed = EAR < 72% of THIS person's open-eye EAR
     (80th percentile of recent open-eye samples), clamped to [0.12, 0.28].
  2. Hysteresis: must rise above 82% to count as reopened -> no chatter.
  3. Duration gate: 50-500 ms = blink; <50 ms = jitter; >500 ms = long closure.
  4. Refractory period: blinks closer than 80 ms are merged/rejected.
  5. Pose gate: |pitch| > 25 deg or |yaw| > 35 deg -> UNKNOWN (looking down
     shrinks EAR and fakes blinks). A closure can't start from UNKNOWN.
  6. Near-eye selection: at |yaw| > 15 deg use only the eye facing the camera.
"""
from __future__ import annotations

import enum
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

import numpy as np

from .config import BlinkConfig


class EyeState(enum.IntEnum):
    UNKNOWN = -1
    CLOSED = 0
    OPEN = 1


@dataclass
class BlinkUpdate:
    ear: float
    state: EyeState
    close_threshold: float
    open_ear: float
    blink_completed: Optional[float] = None          # duration ms if a blink just ended
    long_closure_completed: Optional[float] = None   # duration ms if a long closure just ended
    current_closure_ms: float = 0.0


class BlinkDetector:
    def __init__(self, cfg: Optional[BlinkConfig] = None):
        self.cfg = cfg = cfg or BlinkConfig()
        self.state = EyeState.UNKNOWN
        self.open_ear = cfg.default_open_ear
        self._open_samples: Deque[Tuple[float, float]] = deque()
        self._close_start: Optional[float] = None
        self._last_blink_t = -1e9
        self._frames_since_baseline = 0
        self.blinks: Deque[Tuple[float, float]] = deque()          # (t_end, duration_ms)
        self.long_closures: Deque[Tuple[float, float]] = deque()
        self._t_start: Optional[float] = None
        self._warmup: list = []
        self._warmed_up = False

    WARMUP_FRAMES = 30
    STUCK_MS = 2000.0          # a "closure" this long with the eye still half open is a new resting level
    STUCK_EAR_RATIO = 0.45     # really closed eyes sit well below this fraction of the open level

    # ------------------------------------------------------------------ thresholds
    @property
    def close_threshold(self) -> float:
        c = self.cfg
        return float(np.clip(self.open_ear * c.close_ratio, c.min_close_threshold, c.max_close_threshold))

    @property
    def reopen_threshold(self) -> float:
        return max(self.close_threshold + 0.01, self.open_ear * self.cfg.reopen_ratio)

    def _update_open_baseline(self, t: float, ear: float) -> None:
        self._open_samples.append((t, ear))
        cut = t - self.cfg.baseline_window_s
        while self._open_samples and self._open_samples[0][0] < cut:
            self._open_samples.popleft()
        self._frames_since_baseline += 1
        if self._frames_since_baseline >= 10 and len(self._open_samples) >= 30:
            self._frames_since_baseline = 0
            vals = np.fromiter((e for _, e in self._open_samples), dtype=np.float64)
            self.open_ear = float(np.percentile(vals, self.cfg.baseline_percentile))

    # ------------------------------------------------------------------ main update
    def combine_ears(self, ear_r: float, ear_l: float, width_r: float, width_l: float, yaw: float) -> float:
        if abs(yaw) > self.cfg.near_eye_yaw_deg:
            return ear_r if width_r > width_l else ear_l   # wider eye = facing the camera
        return 0.5 * (ear_r + ear_l)

    def update(self, t: float, ear: float, pitch: float = 0.0, yaw: float = 0.0) -> BlinkUpdate:
        c = self.cfg
        if self._t_start is None:
            self._t_start = t
        self._prune(t)
        out = BlinkUpdate(ear, self.state, self.close_threshold, self.open_ear)

        if abs(pitch) > c.max_abs_pitch_deg or abs(yaw) > c.max_abs_yaw_deg:
            self.mark_unknown()
            out.state = self.state
            return out

        if not self._warmed_up:
            # Bootstrap the personal open-eye level from the first ~1 s instead of trusting a
            # population default (a 0.22-EAR person would otherwise never register as "open").
            self._warmup.append(ear)
            if len(self._warmup) >= self.WARMUP_FRAMES:
                self.open_ear = float(np.percentile(self._warmup, c.baseline_percentile))
                self._warmed_up = True
            return out

        close_thr, reopen_thr = self.close_threshold, self.reopen_threshold
        if self.state == EyeState.UNKNOWN:
            if ear > reopen_thr:
                self.state = EyeState.OPEN
        elif self.state == EyeState.OPEN:
            if ear < close_thr:
                self.state = EyeState.CLOSED
                self._close_start = t
            else:
                self._update_open_baseline(t, ear)
        elif self.state == EyeState.CLOSED:
            dur_ms = (t - self._close_start) * 1000.0
            if dur_ms > self.STUCK_MS and ear > self.STUCK_EAR_RATIO * self.open_ear:
                # Not a closure: the resting eye opening dropped (looking down at the keyboard, tired
                # eyes) and now sits between the close and reopen thresholds, where hysteresis would
                # keep it "closed" forever. Seen in real runs: "closed" for up to 20 s at 75% of
                # the open level. Re-learn the open level from the next ~1 s and count nothing.
                self.mark_unknown()
                self._warmup, self._warmed_up = [], False
                out.state = self.state
                return out
            if ear > reopen_thr:
                self.state = EyeState.OPEN
                self._close_start = None
                if dur_ms < c.min_blink_ms:
                    pass                                         # jitter
                elif dur_ms <= c.max_blink_ms:
                    if (t - self._last_blink_t) * 1000.0 >= c.refractory_ms:
                        self.blinks.append((t, dur_ms))
                        self._last_blink_t = t
                        out.blink_completed = dur_ms
                else:
                    self.long_closures.append((t, dur_ms))
                    out.long_closure_completed = dur_ms
            else:
                out.current_closure_ms = dur_ms

        out.state = self.state
        out.close_threshold = close_thr
        out.open_ear = self.open_ear
        return out

    def mark_unknown(self) -> None:
        """Face lost or pose out of range: abandon any closure in progress."""
        self.state = EyeState.UNKNOWN
        self._close_start = None

    # ------------------------------------------------------------------ statistics
    def _prune(self, t: float) -> None:
        cut = t - max(self.cfg.rate_window_s, 120.0)
        for dq in (self.blinks, self.long_closures):
            while dq and dq[0][0] < cut:
                dq.popleft()

    def blink_rate_per_minute(self, t: float, window_s: Optional[float] = None) -> Optional[float]:
        """Blinks/min over the trailing window. None during the first 10 s (too noisy)."""
        window_s = window_s or self.cfg.rate_window_s
        if self._t_start is None:
            return None
        effective = min(window_s, t - self._t_start)
        if effective < 10.0:
            return None
        n = sum(1 for te, _ in self.blinks if te >= t - effective)
        return 60.0 * n / effective

    def blinks_since(self, t0: float):
        return [d for te, d in self.blinks if te >= t0]

    def long_closures_since(self, t0: float):
        return [d for te, d in self.long_closures if te >= t0]

    @property
    def current_eye_state(self) -> str:
        return self.state.name.lower()
