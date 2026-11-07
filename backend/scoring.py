"""Baseline, live stress indicator and per-question response scoring.

numpy only, no model training. Definitions match "Scoring definitions" in README.
Everything works on lists of sample dicts ({"t": ..., "gsr": ..., "hr": ..., ...}),
so it doesn't care whether the samples are fake, from serial, or replayed.
"""

import numpy as np

PRE_S = 2.0       # "before the question" window
WINDOW_S = 10.0   # response window after the question starts

# Smallest standard deviation we trust per signal. Stops a very flat baseline
# (or a sensor stuck on one value) from turning tiny wiggles into huge spikes.
MIN_STD = {"gsr": 1.0, "hr": 1.0, "flex": 1.0, "blink_rate": 1.0}

# spike = weighted mix of per-signal responses, weighted toward GSR.
# Missing signals are skipped and the remaining weights rescaled.
SPIKE_WEIGHTS = {"gsr": 0.6, "hr": 0.25, "breath_hold": 0.15}

# Live stress: weighted SDs above baseline, STRESS_FULL_SCALE_SD maps to 100.
STRESS_WEIGHTS = {"gsr": 0.7, "hr": 0.3}
STRESS_WINDOW_S = 2.0
STRESS_FULL_SCALE_SD = 8.0  # TUNE on real data: GSR creeps up over a session

# Breath hold: chest movement (flex range over HOLD_RANGE_S) below
# HOLD_FRACTION of the baseline's typical range. The first NORMAL_PAUSE_S of a
# hold is normal breathing; each second beyond counts as 1 SD in spike.
HOLD_RANGE_S = 1.5
HOLD_FRACTION = 0.3
NORMAL_PAUSE_S = 1.0


def column(samples: list[dict], key: str) -> np.ndarray:
    """Values of one signal as floats. Missing values (None, negative, hr of 0) become NaN."""
    out = np.full(len(samples), np.nan)
    for i, s in enumerate(samples):
        v = s.get(key)
        if v is None or v < 0 or (key == "hr" and v == 0):
            continue
        out[i] = v
    return out


def rolling_range(t: np.ndarray, x: np.ndarray, span_s: float) -> np.ndarray:
    """For each sample, max - min of x over the previous span_s seconds."""
    out = np.full(len(x), np.nan)
    start = 0
    for i in range(len(x)):
        while t[start] < t[i] - span_s:
            start += 1
        window = x[start : i + 1]
        window = window[~np.isnan(window)]
        if len(window) >= 2:
            out[i] = window.max() - window.min()
    return out


class Baseline:
    """Mean and std per signal over the calm period at the start of a session."""

    def __init__(self, samples: list[dict]) -> None:
        self.mean: dict[str, float] = {}
        self.std: dict[str, float] = {}
        for key, min_std in MIN_STD.items():
            x = column(samples, key)
            x = x[~np.isnan(x)]
            if len(x) >= 2:
                self.mean[key] = float(x.mean())
                self.std[key] = max(float(x.std()), min_std)
        t = column(samples, "t")
        ranges = rolling_range(t, column(samples, "flex"), HOLD_RANGE_S)
        ranges = ranges[~np.isnan(ranges)]
        self.breath_range = float(np.median(ranges)) if len(ranges) else None

    def z(self, key: str, value: float) -> float:
        return (value - self.mean[key]) / self.std[key]


def _weighted(parts: dict[str, float], weights: dict[str, float]) -> float | None:
    used = {k: w for k, w in weights.items() if k in parts}
    if not used:
        return None
    return sum(parts[k] * w for k, w in used.items()) / sum(used.values())


def stress(recent: list[dict], baseline: Baseline) -> float | None:
    """0-100, how far GSR and HR currently sit above baseline. Not smoothed (the session smooths it)."""
    parts = {}
    for key in STRESS_WEIGHTS:
        if key not in baseline.mean:
            continue
        x = column(recent, key)
        x = x[~np.isnan(x)]
        if len(x):
            parts[key] = baseline.z(key, float(x.mean()))
    combined = _weighted(parts, STRESS_WEIGHTS)
    if combined is None:
        return None
    return float(np.clip(combined / STRESS_FULL_SCALE_SD * 100, 0, 100))


def longest_hold(t: np.ndarray, flex: np.ndarray, in_window: np.ndarray, baseline: Baseline) -> float | None:
    """Longest stretch (seconds) inside the window where the chest barely moved."""
    if baseline.breath_range is None or baseline.breath_range <= 0:
        return None
    ranges = rolling_range(t, flex, HOLD_RANGE_S)
    holding = (ranges < HOLD_FRACTION * baseline.breath_range) & in_window
    if np.isnan(ranges[in_window]).all():
        return None
    longest, run_start = 0.0, None
    for i in range(len(t)):
        if holding[i]:
            if run_start is None:
                run_start = t[i]
            longest = max(longest, t[i] - run_start)
        else:
            run_start = None
    # A flat range at time t means no movement since t - HOLD_RANGE_S.
    return longest + HOLD_RANGE_S if longest > 0 else 0.0


def response(samples: list[dict], q_t: float, baseline: Baseline) -> tuple[float | None, dict]:
    """Score one question. samples must cover q_t - PRE_S to q_t + WINDOW_S.

    Returns (spike, signals) as in the README "response" message.
    """
    t = column(samples, "t")
    pre = (t >= q_t - PRE_S) & (t < q_t)
    post = (t >= q_t) & (t <= q_t + WINDOW_S)
    signals: dict[str, float] = {}

    for key in ("gsr", "hr"):
        if key not in baseline.mean:
            continue
        x = column(samples, key)
        before, after = x[pre & ~np.isnan(x)], x[post & ~np.isnan(x)]
        if len(before) and len(after):
            signals[key] = round(float((after.max() - before.mean()) / baseline.std[key]), 2)

    if "blink_rate" in baseline.mean:
        x = column(samples, "blink_rate")
        before, after = x[pre & ~np.isnan(x)], x[post & ~np.isnan(x)]
        if len(before) and len(after):
            # Negative = fewer blinks than just before (blink suppression).
            signals["blink"] = round(float((after.mean() - before.mean()) / baseline.std["blink_rate"]), 2)

    hold = longest_hold(t, column(samples, "flex"), post, baseline)
    if hold is not None:
        signals["breath_hold_s"] = round(float(hold), 1)

    parts = {k: signals[k] for k in ("gsr", "hr") if k in signals}
    if hold is not None:
        parts["breath_hold"] = max(0.0, hold - NORMAL_PAUSE_S)
    spike = _weighted(parts, SPIKE_WEIGHTS)
    return (round(spike, 2) if spike is not None else None), signals
