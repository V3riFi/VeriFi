"""Baseline, live stress indicator and per-question response scoring.

numpy only, no model training. Definitions match "Scoring definitions" in README.
Everything works on lists of sample dicts ({"t": ..., "gsr": ..., "hr": ..., ...}),
so it doesn't care whether the samples are fake, from serial, or replayed.
"""

from __future__ import annotations  # allows "float | None" hints on Python 3.9

import numpy as np

PRE_S = 2.0       # "before the question" window
WINDOW_S = 10.0   # response window after the question starts

# Smallest standard deviation we trust per signal. Stops a very flat baseline
# (or a sensor stuck on one value) from turning tiny wiggles into huge spikes.
MIN_STD = {
    # breath_rate and blink_rate swing a lot naturally (talking, looking around);
    # a tight floor made the dashboard flag normal variation as a reaction.
    "gsr": 1.0, "hr": 1.0, "flex": 1.0, "breath_rate": 2.0, "blink_rate": 3.0, "lip_compression": 0.05,
    "spo2": 0.5, "temp": 0.1,  # baseline only (drawn on the dashboard), not part of stress
    # Voice: only present while the subject talks, so it gets its own baseline from the
    # voice phase (subject reads VOICE_SENTENCES) instead of the silent one. Drawn on
    # the dashboard and used for vocal tension, not part of stress or spike.
    "pitch": 5.0, "jitter": 0.2, "shimmer": 0.5, "voice_db": 2.0, "hnr": 1.0, "spectral_slope": 1.0,
}
VOICE_KEYS = ("pitch", "jitter", "shimmer", "voice_db", "hnr", "spectral_slope")
SENSOR_KEYS = tuple(k for k in MIN_STD if k not in VOICE_KEYS)
MIN_VOICE_SAMPLES = 20  # voiced samples needed for a voice baseline (~2 s of speech at 10 Hz)

# spike = weighted mix of per-signal responses, weighted toward GSR.
# Missing signals are skipped and the remaining weights rescaled.
SPIKE_WEIGHTS = {"gsr": 0.6, "hr": 0.25, "breath_hold": 0.15}

# Live stress: every signal's distance from baseline in SDs, mixed by how much
# we trust each signal (STRESS_WEIGHTS). A signal that is an outlier right now
# also gets extra weight: its weight is multiplied by its distance once that
# passes 1 SD, so one strongly reacting signal isn't averaged away by quiet ones.
# STRESS_FULL_SCALE_SD maps to 100.
# breath_rate is NOT in stress: answering out loud changes breathing, and with it
# in, talking alone pushed stress to ~94 with GSR and heart rate flat. Breath
# holds still count, per question, in the spike score (SPIKE_WEIGHTS).
STRESS_WEIGHTS = {"gsr": 0.5, "hr": 0.3, "blink_rate": 0.13, "lip_compression": 0.07}
# GSR and heart rate only count when above baseline. These count a change either way.
STRESS_TWO_SIDED = {"blink_rate"}
STRESS_WINDOW_S = 2.0
STRESS_FULL_SCALE_SD = 8.0  # TUNE on real data: GSR creeps up over a session

# A signal (or the combined stress) is flagged once it is ALERT_SD above baseline.
ALERT_SD = 2.0
STRESS_ALERT = round(ALERT_SD / STRESS_FULL_SCALE_SD * 100)

# Breathing arrives as inhale events (firmware 8791a0f): "flex" (the sketch's
# "breath") is 1 on the sample where an inhale starts and 0 on every other one.
# Breath hold = the longest gap between inhales that overlaps a question's
# response window; each second beyond the person's normal gap (the median gap
# in their baseline) counts as 1 SD in spike.
BREATH_LOOKBACK_S = 15.0    # history kept before a question, to find the inhale that opened its gap
MIN_BREATH_WINDOW_S = 15.0  # need this much history before reporting a breathing rate


def inhale_times(t: np.ndarray, flex: np.ndarray) -> np.ndarray:
    """Times of the samples where an inhale starts (flex is 1)."""
    return t[~np.isnan(flex) & (flex >= 0.5)]


def breath_rate(history: list[tuple[float, float]], window_s: float) -> float | None:
    """Breaths per minute: inhales counted over the last window_s seconds of (t, flex) pairs.

    Uses all the history while it is shorter than window_s, and None until it covers
    MIN_BREATH_WINDOW_S. 0 means no inhale in the window (a long hold).
    """
    if not history:
        return None
    end = history[-1][0]
    span = min(window_s, end - history[0][0])
    if span < MIN_BREATH_WINDOW_S:
        return None
    count = sum(1 for t, x in history if t > end - span and x is not None and x >= 0.5)
    return round(count / span * 60, 1)


# Signals that are negative for real. For every other one, a negative value is a sensor's "no reading".
SIGNED = {"hnr", "spectral_slope", "vocal_tension"}


def column(samples: list[dict], key: str) -> np.ndarray:
    """Values of one signal as floats. Missing values (None, negative sensor values, hr of 0) become NaN."""
    out = np.full(len(samples), np.nan)
    signed = key in SIGNED
    for i, s in enumerate(samples):
        v = s.get(key)
        if v is None or (v < 0 and not signed) or (key == "hr" and v == 0):
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

    def __init__(self, samples: list[dict], keys: tuple[str, ...] = SENSOR_KEYS) -> None:
        self.mean: dict[str, float] = {}
        self.std: dict[str, float] = {}
        self.add(samples, keys)
        gaps = np.diff(inhale_times(column(samples, "t"), column(samples, "flex")))
        # This person's normal time between inhales (needs at least 3 inhales).
        self.breath_gap = float(np.median(gaps)) if len(gaps) >= 2 else None

    def add(self, samples: list[dict], keys: tuple[str, ...], min_count: int = 2) -> int:
        """Adds (or replaces) these signals' mean and std from samples. Returns the fewest values any key had."""
        fewest = len(samples)
        for key in keys:
            x = column(samples, key)
            x = x[~np.isnan(x)]
            fewest = min(fewest, len(x))
            if len(x) >= max(min_count, 2):
                self.mean[key] = float(x.mean())
                self.std[key] = max(float(x.std()), MIN_STD[key])
        return fewest

    def add_voice(self, samples: list[dict]) -> int:
        """Voice baseline from the voice phase. Returns how many voiced samples it had.

        Only samples where the subject is speaking (pitch present) count, so the
        silences between sentences don't pull loudness down.
        """
        voiced = [s for s in samples if s.get("pitch") is not None]
        self.add(voiced, VOICE_KEYS, MIN_VOICE_SAMPLES)
        return len(voiced)

    def z(self, key: str, value: float) -> float:
        return (value - self.mean[key]) / self.std[key]

    def as_message(self) -> dict:
        """Per-signal mean and std for the dashboard, which draws them on every chart."""
        return {
            "mean": {k: round(v, 3) for k, v in self.mean.items()},
            "std": {k: round(v, 3) for k, v in self.std.items()},
            "alert_sd": ALERT_SD,
            "stress_alert": STRESS_ALERT,
        }


def _weighted(parts: dict[str, float], weights: dict[str, float]) -> float | None:
    used = {k: w for k, w in weights.items() if k in parts}
    if not used:
        return None
    return sum(parts[k] * w for k, w in used.items()) / sum(used.values())


def stress(recent: list[dict], baseline: Baseline) -> float | None:
    """0-100, how far the signals currently sit from baseline. Not smoothed (the session smooths it)."""
    weighted_sum = total_weight = 0.0
    for key, weight in STRESS_WEIGHTS.items():
        if key not in baseline.mean:
            continue
        x = column(recent, key)
        x = x[~np.isnan(x)]
        if not len(x):
            continue
        z = baseline.z(key, float(x.mean()))
        distance = abs(z) if key in STRESS_TWO_SIDED else max(z, 0.0)
        effective = weight * max(1.0, distance)  # outliers count for more
        weighted_sum += effective * distance
        total_weight += effective
    if total_weight == 0:
        return None
    return float(np.clip(weighted_sum / total_weight / STRESS_FULL_SCALE_SD * 100, 0, 100))


# Vocal tension: louder, more jitter and shimmer, steeper spectral slope, each in SDs
# from the voice baseline, averaged. Steeper = more negative slope, hence the -1.
# TUNE: much of the research finds a tense (pressed) voice has a FLATTER slope; flip
# spectral_slope to +1 if real data agrees.
TENSION_PARTS = {"voice_db": 1.0, "jitter": 1.0, "shimmer": 1.0, "spectral_slope": -1.0}


def vocal_tension(sample: dict, baseline: Baseline) -> float | None:
    """SDs above the voice baseline, while the subject is speaking. None when silent or no voice baseline."""
    if sample.get("pitch") is None:
        return None
    parts = [sign * baseline.z(key, sample[key]) for key, sign in TENSION_PARTS.items()
             if key in baseline.mean and sample.get(key) is not None]
    return float(np.mean(parts)) if len(parts) >= 2 else None


def longest_hold(t: np.ndarray, flex: np.ndarray, in_window: np.ndarray, baseline: Baseline) -> float | None:
    """Longest gap between inhales (seconds) that overlaps the window.

    Gaps run from one inhale to the next. A gap still open at either end of the data
    is measured to the first / last sample, so pass samples from BREATH_LOOKBACK_S
    before the window. None without breathing data in the window, or without a
    normal gap (baseline.breath_gap) to compare it with.
    """
    have = ~np.isnan(flex)
    if baseline.breath_gap is None or not (have & in_window).any():
        return None
    w0, w1 = t[in_window].min(), t[in_window].max()
    th = t[have]
    edges = np.concatenate([[th[0]], inhale_times(t, flex), [th[-1]]])
    longest = 0.0
    for a, b in zip(edges[:-1], edges[1:]):
        if a < w1 and b > w0:
            longest = max(longest, float(b - a))
    return longest


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
        parts["breath_hold"] = max(0.0, hold - baseline.breath_gap)
    spike = _weighted(parts, SPIKE_WEIGHTS)
    return (round(spike, 2) if spike is not None else None), signals
