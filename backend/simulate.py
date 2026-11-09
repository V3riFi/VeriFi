"""Simulated sessions through VeriFi's real scoring (backend/verdict.py + confidence.py).

Run from the repo root:
    python -m backend.simulate            # 100 sessions per condition (~3 min)
    python -m backend.simulate 20         # quicker
    IDEAL=1 python -m backend.simulate    # the unit tests' clean noise instead

These are numbers on synthetic data: they show the scoring behaves as designed (calm answers
rarely flagged, clear reactions caught and ranked first), not how accurate it is on people.

Session: 60 s silent baseline, 25 s read-aloud voice baseline, then N_Q questions 25 s apart.
The interrogator asks for 2 s; the subject answers for ~3 s. One random question per session
gets a reaction (or none, for the calm condition); the others are calm controls.

Noise is deliberately rougher than the unit tests: heart rate wanders (AR(1), sd ~1.6 bpm,
plus a slow drift), breaths are irregular (gap CV 15%), blink rate and voice vary per sample.
Breathing rate is computed from the inhale pulses exactly as the server does.
"""
import math
import os
import sys
import time
from collections import Counter, deque

import numpy as np

from backend import scoring, verdict

HZ = 10
BASE = (0.0, 60.0)
VOICE = (60.0, 85.0)
N_Q = 6
Q0, Q_STEP = 100.0, 25.0
ASK_S, ANSWER_TALK = 2.0, (2.5, 5.5)     # interrogator asks 0-2 s; subject talks 2.5-5.5 s after Ask
BREATH_WINDOW_S = 30.0                   # server.py
IDEAL = os.environ.get("IDEAL") == "1"   # the unit tests' clean noise instead of the realistic one
N = dict(hr_ar=0.15, hr_drift=0.0, gap_sd=0.0, blink=1.5, lip=0.01, pitch=8) if IDEAL else \
    dict(hr_ar=0.5, hr_drift=2.5, gap_sd=0.6, blink=3.0, lip=0.03, pitch=10)
FLAGGED = {"elevated", "high"}


def make_session(rng, reaction=None, rq=None, drop=None):
    """reaction: dict of effects applied to question index rq. drop: (start, end) with no samples."""
    qs = [Q0 + i * Q_STEP for i in range(N_Q)]
    end = qs[-1] + 20
    r = reaction or {}
    rt = qs[rq] if rq is not None else None

    def active(t, a=1.0, b=10.0):
        return rt is not None and rt + a <= t <= rt + b

    hr_noise, samples, hist = 0.0, [], deque()
    next_breath = rng.uniform(0.5, 4.0)
    for i in range(int(end * HZ)):
        t = i / HZ
        talking = VOICE[0] <= t <= VOICE[1] or any(q + ANSWER_TALK[0] <= t <= q + ANSWER_TALK[1] for q in qs)
        # Heart rate: drift + AR(1) wander; reaction ramps up over 2 s.
        hr_noise = 0.95 * hr_noise + rng.normal(0, N["hr_ar"])
        hr = 72 + N["hr_drift"] * math.sin(t / 23) + hr_noise
        if "hr" in r and active(t):
            hr += r["hr"] * min(1.0, (t - rt - 1.0) / 2.0)
        # Breathing: inhale pulses, gaps ~4 s (CV 15%); a hold delays the next inhale.
        flex = 0
        if t >= next_breath:
            flex = 1
            gap = rng.normal(4.0, N["gap_sd"]) if N["gap_sd"] else 4.0
            if "hold" in r and rt is not None and rt - 1.0 <= t < rt + 2.0:
                gap = r["hold"]
            next_breath = t + max(1.5, gap)
        hist.append((t, flex))
        while hist[0][0] < t - BREATH_WINDOW_S:
            hist.popleft()
        blink = 17 + rng.normal(0, N["blink"]) + (r["blink"] if "blink" in r and active(t) else 0.0)
        s = {"t": t, "hr": round(hr, 1), "flex": flex,
             "breath_rate": scoring.breath_rate(list(hist), BREATH_WINDOW_S),
             "blink_rate": max(0.0, blink), "lip_compression": max(0.0, 0.08 + rng.normal(0, N["lip"])),
             "hand_on_face": bool(r.get("hand") and active(t, 3, 6))}
        if talking:
            dp = r["pitch"] if "pitch" in r and active(t) else 0.0
            s.update({"pitch": 120 + dp + rng.normal(0, N["pitch"]), "jitter": 1.5 + rng.normal(0, 0.4),
                      "shimmer": 9 + rng.normal(0, 1.5), "voice_db": 70 + rng.normal(0, 3),
                      "hnr": 12 + rng.normal(0, 2), "spectral_slope": -15 + rng.normal(0, 1.5)})
        if drop and drop[0] <= t < drop[1]:
            continue
        samples.append(s)
    return samples, qs


def run_session(rng, reaction=None, drop_on_reactive=None):
    rq = int(rng.integers(N_Q)) if reaction is not None or drop_on_reactive else None
    drop = None
    if drop_on_reactive and rq is not None:
        q = Q0 + rq * Q_STEP
        drop = (q + drop_on_reactive[0], q + drop_on_reactive[1])
    samples, qs = make_session(rng, reaction, rq, drop)
    engine = verdict.Engine(samples, BASE, VOICE)
    out = []
    for i, q in enumerate(qs):
        t0 = time.perf_counter()
        v, _ = verdict.score(engine, samples, q, q + ASK_S + 10.0, q + ASK_S)
        out.append({"i": i, "react": i == rq, "band": v["band"], "lik": v.get("likelihood"), "z": v.get("z"),
                     "ms": (time.perf_counter() - t0) * 1000})
    return out


def summarise(name, sessions, has_reaction=True):
    react = [a for s in sessions for a in s if a["react"]]
    calm = [a for s in sessions for a in s if not a["react"]]
    flag = lambda xs: 100 * sum(a["band"] in FLAGGED for a in xs) / len(xs)  # noqa: E731
    ins = lambda xs: 100 * sum(a["band"] == "insufficient" for a in xs) / len(xs)  # noqa: E731
    cz = [a["z"] for a in calm if a["z"] is not None]
    line = {"condition": name, "calm_flagged_%": round(flag(calm), 1), "calm_insufficient_%": round(ins(calm), 1),
            "calm_Z_mean": round(float(np.mean(cz)), 2), "calm_Z_sd": round(float(np.std(cz)), 2)}
    if has_reaction and react:
        ranks = []
        for s in sessions:
            scored = sorted([a for a in s if a["lik"] is not None], key=lambda a: -a["lik"])
            r = next((k + 1 for k, a in enumerate(scored) if a["react"]), None)
            if r:
                ranks.append(r)
        line.update({"reaction_flagged_%": round(flag(react), 1),
                     "reaction_insufficient_%": round(ins(react), 1),
                     "ranked_first_%": round(100 * sum(r == 1 for r in ranks) / max(1, len(ranks)), 1),
                     "ranked_top2_%": round(100 * sum(r <= 2 for r in ranks) / max(1, len(ranks)), 1),
                     "mean_rank": round(float(np.mean(ranks)), 2) if ranks else None,
                     "reaction_Z_mean": round(float(np.mean([a["z"] for a in react if a["z"] is not None] or [np.nan])), 2),
                     "bands_reaction": dict(Counter(a["band"] for a in react))})
    return line


CONDITIONS = [
    ("no reaction (all calm)", None),
    ("heart rate +5 bpm", {"hr": 5}),
    ("heart rate +10 bpm", {"hr": 10}),
    ("breath hold (~9 s gap)", {"hold": 9.0}),
    ("blink rate -8/min", {"blink": -8}),
    ("voice pitch +15 Hz", {"pitch": 15}),
    ("hand on face", {"hand": True}),
    ("combined moderate (HR +6, hold 9 s, pitch +10 Hz)", {"hr": 6, "hold": 9.0, "pitch": 10}),
]

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    rng = np.random.default_rng(2026)
    results, timing = [], []
    for name, reaction in CONDITIONS:
        sessions = [run_session(rng, reaction if reaction is not None else None) for _ in range(n)]
        if reaction is None:
            results.append(summarise(name, sessions, has_reaction=False))
        else:
            results.append(summarise(name, sessions))
        timing += [a["ms"] for s in sessions for a in s]
        print(results[-1], flush=True)
    # Quality gate: the combined reaction, but 3 s of samples lost mid-answer (loose cable).
    sessions = [run_session(rng, CONDITIONS[-1][1], drop_on_reactive=(3.0, 6.0)) for _ in range(n)]
    print(summarise("combined + 3 s data dropout on that answer", sessions), flush=True)
    print({"answers_scored": len(timing), "score_ms_median": round(float(np.median(timing)), 1),
           "score_ms_p95": round(float(np.percentile(timing, 95)), 1)})
