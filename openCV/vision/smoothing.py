"""Temporal filters. Timestamp-based (not frame-based) so variable FPS is fine."""
from __future__ import annotations

import math
from collections import deque
from typing import Deque, Optional, Tuple


class OneEuroFilter:
    """Casiez et al. 2012: adaptive low-pass. Smooth when still, responsive when moving.

    Ideal for head pose: kills landmark jitter without making real motion laggy.
    """

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.05, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self._x: Optional[float] = None
        self._dx = 0.0
        self._t: Optional[float] = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self) -> None:
        self._x, self._t, self._dx = None, None, 0.0

    def __call__(self, x: float, t: float) -> float:
        if self._x is None or self._t is None or t <= self._t:
            self._x, self._t = x, t
            return x
        dt = t - self._t
        dx = (x - self._x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self._dx = a_d * dx + (1 - a_d) * self._dx
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        a = self._alpha(cutoff, dt)
        self._x = a * x + (1 - a) * self._x
        self._t = t
        return self._x


class Ema:
    """Time-constant EMA (tau in seconds)."""

    def __init__(self, tau: float):
        self.tau = tau
        self.value: Optional[float] = None
        self._t: Optional[float] = None

    def __call__(self, x: float, t: float) -> float:
        if self.value is None or self._t is None:
            self.value, self._t = x, t
            return x
        dt = max(t - self._t, 1e-6)
        a = 1.0 - math.exp(-dt / self.tau)
        self.value += a * (x - self.value)
        self._t = t
        return self.value


class TimeSeries:
    """(t, value) deque that forgets anything older than `horizon` seconds."""

    def __init__(self, horizon: float):
        self.horizon = horizon
        self.data: Deque[Tuple[float, float]] = deque()

    def add(self, t: float, v: float) -> None:
        self.data.append((t, v))
        cut = t - self.horizon
        while self.data and self.data[0][0] < cut:
            self.data.popleft()

    def since(self, t0: float):
        return [v for t, v in self.data if t >= t0]

    def __len__(self) -> int:
        return len(self.data)
