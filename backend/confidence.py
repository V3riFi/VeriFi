"""Baseline-consistency confidence: weighted chi-square per signal group, then weighted Stouffer.

A second way to score a question, next to scoring.response()'s spike. For every signal it
compares a reading from the question with a reference:

    z = (mu_now - mu_base) / sqrt(sigma_base^2 + sigma_now^2)

Two kinds of reading ("measure" in CATEGORIES):
  - "mean" (default): the answer window's {mu, sigma} against the session baseline's {mu, sigma}.
  - "phasic": a short reaction, as in scoring.response(): the peak in a latency window (for skin
    conductance 1 s after Ask to 5 s after Done asking; the 1-5 s is openCV/interview/fusion.py's)
    minus the mean of the PRE_S seconds before the question, over the baseline's sigma. Slow drift
    over a session cancels.

Each signal has a "direction": +1 (a rise is suspicious), -1 (a drop is), or 0 (either way).
Directional signals use z with its sign and a one-sided p; direction 0 uses |z| and a two-sided p.

Signals are grouped into categories (Skin, Breathing, Cardiac, Voice, Blink, Lip / hand; Voice
holds pitch, loudness, spectral slope, clarity, jitter and shimmer). A category of
two-sided signals combines them into a weighted chi-square, Q = sum(w_i * z_i^2), whose tail
probability (Satterthwaite approximation) is the CATEGORY CONFIDENCE: the chance of a departure
at least this big if the subject were still in their baseline state. A category with a
directional signal uses weighted Stouffer over its signals' p-values instead (squaring z would
lose the sign). The categories are then combined with weighted Stouffer into one OVERALL
CONFIDENCE.

    high confidence (near 100%)  -> the answer looks like the baseline
    low confidence  (near 0%)    -> the answer departs from the baseline (suspicious)

`suspicion` = 1 - confidence is reported too, for ranking questions.

Data comes in the same shapes the rest of the backend uses:
  - live: lists of sample dicts ({"t": ..., "gsr": ..., ...}), e.g. server.Session.samples
    -> score_question() / score_live_question(); to_message() is the "confidence" field that
    server.py adds to each "response" for the dashboard's question summary
  - recorded: a session CSV from data/ (written by server.SessionLog)
    -> load_session_csv() + score_session()

Baselines follow scoring.Baseline: physiology and face from the silent baseline; voice signals
from the voice baseline (reading aloud), voiced samples only; the same MIN_STD floors. Breathing
is the gap between inhales: each sample gets the length of the inhale-to-inhale gap it falls in
(a held breath = a longer gap), from the same inhale events as scoring.py's breath hold. It uses the voice baseline when there is one, because
answering out loud changes breathing (the reason scoring.py keeps breath_rate out of stress), so
it is compared with breathing while reading aloud, not while silent.

Category weights are this file's own (CATEGORIES), from a research-evidence ranking. They are
independent of scoring.STRESS_WEIGHTS / SPIKE_WEIGHTS: changing one never changes the other.

No scipy: the normal and chi-square tails are computed here (statistics.NormalDist, as in
openCV/interview, plus a regularized incomplete gamma function).

Run from the repo root:
    python -m backend.confidence                      # newest session CSV in data/
    python -m backend.confidence data/<session>.csv   # one session (--json, --save, --quiet)
    python -m backend.confidence --demo               # the original worked example (synthetic sensors)
"""

from __future__ import annotations  # allows "float | None" hints on Python 3.9

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from statistics import NormalDist

import numpy as np

from backend import scoring

_NORMAL = NormalDist()
EPS = 1e-300  # p-values are clipped to [EPS, 1 - 1e-16] before turning them into Z

# ----------------------------- Statistics ----------------------------------


def norm_sf(z: float) -> float:
    """P(Z > z) for a standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def norm_isf(p: float) -> float:
    """Inverse of norm_sf. Written as -inv_cdf(p) so tiny p keeps its precision."""
    return -_NORMAL.inv_cdf(p)


def _gammaincc(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x) (series below a+1, continued fraction above)."""
    if x <= 0:
        return 1.0
    log_front = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1:
        term = total = 1.0 / a
        ap = a
        for _ in range(10000):
            ap += 1
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return max(0.0, 1.0 - total * math.exp(log_front))
    tiny = 1e-300
    b = x + 1 - a
    c, d = 1 / tiny, 1 / b
    h = d
    for i in range(1, 10000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        step = d * c
        h *= step
        if abs(step - 1) < 1e-15:
            break
    return math.exp(log_front) * h


def chi2_sf(x: float, df: float) -> float:
    """P(X > x) for a chi-square with df (any positive real) degrees of freedom."""
    return _gammaincc(df / 2, x / 2)


def stats_from_data(data) -> dict:
    """Collapse raw data into {mu, sigma} (sample std, ddof=1). NaNs are ignored."""
    d = np.asarray(data, dtype=float)
    d = d[~np.isnan(d)]
    return {"mu": float(np.mean(d)), "sigma": float(np.std(d, ddof=1))}


def weighted_chi2_sf(q: float, weights) -> tuple[float, float]:
    """Satterthwaite approximation for Q = sum(w_i * X_i), X_i ~ chi2(1).

    Returns (tail probability, effective degrees of freedom).
    """
    w = np.asarray(weights, dtype=float)
    g = float(np.sum(w ** 2) / np.sum(w))
    h = float(np.sum(w) ** 2 / np.sum(w ** 2))
    return chi2_sf(q / g, h), h


# ----------------------------- Core algorithm -------------------------------
# Generic: works on any {category: {"weight", "sensors": [{"name", "mu", "sigma", "weight"}]}}
# with a matching baselines dict ({category: {"sensors": [{"name", "mu", "sigma"}]}}).


def baseline_lookup(baselines: dict) -> dict:
    """{(category, sensor name): (mu, sigma)}."""
    return {(cat_name, s["name"]): (s["mu"], s["sigma"])
            for cat_name, cat in baselines.items() for s in cat["sensors"]}


def validate(baselines: dict, categories: dict) -> list:
    """Every sensor needs a baseline (raises). Returns the baselines with no matching sensor."""
    lookup = baseline_lookup(baselines)
    expected = {(c, s["name"]) for c, cat in categories.items() for s in cat["sensors"]}
    missing = expected - set(lookup)
    if missing:
        raise ValueError(f"Sensors missing from baselines: {sorted(missing)}")
    return sorted(set(lookup) - expected)


def evaluate_category(cat_name: str, sensors: list, lookup: dict) -> dict:
    """Per-sensor Z and point confidence, combined into the category confidence.

    All sensors two-sided (direction 0, the default): weighted chi-square (method "chi2").
    Any sensor one-sided (direction +1 / -1): weighted Stouffer over the sensors' p-values
    (method "stouffer"), since squaring z would throw its sign away.
    """
    rows = []
    for pt in sensors:
        mu_b, sigma_b = lookup[(cat_name, pt["name"])]
        sigma_combined = math.sqrt(sigma_b ** 2 + pt["sigma"] ** 2)
        diff = (pt["mu"] - mu_b) / sigma_combined
        direction = pt.get("direction", 0)
        if direction:
            z, p = direction * diff, norm_sf(direction * diff)
        else:
            z, p = abs(diff), 2 * norm_sf(abs(diff))
        rows.append({**pt, "direction": direction, "base_mu": mu_b, "base_sigma": sigma_b, "z": z, "p": p})
    weights = np.array([pt["weight"] for pt in sensors], dtype=float)
    if not any(r["direction"] for r in rows):
        chi2_stat = float(np.sum(weights * np.array([r["z"] for r in rows]) ** 2))
        conf, df = weighted_chi2_sf(chi2_stat, weights)
        return {"sensors": rows, "method": "chi2", "chi2": chi2_stat, "df": df, "confidence": conf}
    z_s = np.array([norm_isf(float(np.clip(r["p"], EPS, 1 - 1e-16))) for r in rows])
    z_cat = float(np.sum(weights * z_s) / np.sqrt(np.sum(weights ** 2)))
    return {"sensors": rows, "method": "stouffer", "chi2": None, "df": None, "confidence": norm_sf(z_cat)}


def combine(cat_results: dict) -> tuple[float | None, float | None]:
    """Weighted Stouffer over the categories' confidences. Returns (Z total, overall confidence)."""
    if not cat_results:
        return None, None
    w_c = np.array([r["weight"] for r in cat_results.values()], dtype=float)
    p_c = np.array([r["confidence"] for r in cat_results.values()], dtype=float)
    z_c = [norm_isf(float(p)) for p in np.clip(p_c, EPS, 1 - 1e-16)]
    for r, z in zip(cat_results.values(), z_c):
        r["z"] = z
    if not np.any(w_c > 0):  # only weight-0 categories had data: nothing counts toward the total
        return None, None
    z_total = float(np.sum(w_c * z_c) / np.sqrt(np.sum(w_c ** 2)))
    return z_total, norm_sf(z_total)


def evaluate(baselines: dict, categories: dict) -> dict:
    """Runs every category and combines them. The whole algorithm in one call."""
    unused = validate(baselines, categories)
    lookup = baseline_lookup(baselines)
    cats = {}
    for cat_name, cat in categories.items():
        if cat["sensors"]:
            cats[cat_name] = {"weight": cat["weight"], **evaluate_category(cat_name, cat["sensors"], lookup)}
    z_total, overall = combine(cats)
    return {"categories": cats, "z_total": z_total, "confidence": overall,
            "suspicion": None if overall is None else 1 - overall, "unused_baselines": unused}


# ----------------------------- VeriFi signals -------------------------------
# Which recorded signals feed which category. "key" is the sample / CSV column.
# "phase": which baseline the signal is compared with ("silent" or "voice").
# "voiced": only samples where the subject is speaking (pitch present) count.
# "direction": +1 a rise is suspicious, -1 a drop is, 0 (default) either way.
# "phasic": (from, to) seconds after the question: peak there minus the PRE_S before (see top).
# "breath_gap" is derived, not a column: the inhale-to-inhale gap each sample falls in (seconds).
# "min_std": this signal's baseline sigma floor, when it isn't in MIN_STD.

def _voice(key: str, name: str, weight: float, direction: int = 0) -> dict:
    return {"key": key, "name": name, "weight": weight, "phase": "voice", "voiced": True, "direction": direction}


# Category weights: evidence ranking (effect size d where known). Only their ratios matter.
#   1 GSR .30 (d~1.55)   2 respiration .20 (d~1.11)   3 heart rate .15 (d~0.89)
#   4 voice pitch + response latency .13 (d~0.2-0.27)   5 blink .12   6 lip / hand-to-face .05
#   7 gaze aversion .05 -> dropped: no gaze signal is recorded, and the evidence calls it a myth.
# Response latency is one number per answer (no mu/sigma over a window), so it isn't included.
# Voice keeps row 4's .13 as one category; the other voice features share it, weighted inside
# the category (see "Voice" below).
CATEGORIES = {
    "Skin": {"weight": 0.30, "sensors": [
        # Phasic response: rises 1-5 s after the question (window from openCV/interview/fusion.py).
        {"key": "gsr", "name": "Skin conductance", "weight": 1.0, "direction": +1, "phasic": (1.0, 5.0)},
    ]},
    "Breathing": {"weight": 0.20, "sensors": [
        # Respiration suppression: breath held = a longer gap until the next inhale.
        {"key": "breath_gap", "name": "Breath gap", "weight": 1.0, "phase": "voice", "direction": +1,
         "min_std": 0.5},
    ]},
    "Cardiac": {"weight": 0.15, "sensors": [
        # spo2 and temp are left out, as in stress: they barely move, or drift over a session.
        # Either direction for now: deceleration (recognition) vs acceleration (stress) depends
        # on the question type.
        {"key": "hr", "name": "Heart rate", "weight": 1.0},
    ]},
    # Signal weights inside Voice (ratios only):
    #   pitch 1.0 (+)     the most consistent vocal stress finding (pitch rises)
    #   loudness 0.5 (+)  louder under arousal, but also depends on distance to the mic
    #   spectral slope 0.4 (either way)  energy shifts toward high frequencies under arousal,
    #                     but the direction is disputed (see TENSION_PARTS in scoring.py)
    #   clarity 0.3, jitter 0.2, shimmer 0.2 (either way)  mixed findings, noisy on short answers.
    #   These three overlap (all measure voice irregularity), so low weights keep them from
    #   counting the same evidence three times.
    "Voice": {"weight": 0.13, "sensors": [
        _voice("pitch", "Pitch", 1.0, direction=+1),
        _voice("voice_db", "Loudness", 0.5, direction=+1),
        _voice("spectral_slope", "Spectral slope", 0.4),
        _voice("hnr", "Voice clarity (HNR)", 0.3),
        _voice("jitter", "Jitter", 0.2),
        _voice("shimmer", "Shimmer", 0.2),
    ]},
    "Blink": {"weight": 0.12, "sensors": [
        {"key": "blink_rate", "name": "Blink rate", "weight": 1.0},
    ]},
    "Lip / hand": {"weight": 0.05, "sensors": [
        {"key": "lip_compression", "name": "Lip compression", "weight": 1.0},
        {"key": "hand_on_face", "name": "Hand on face", "weight": 1.0},
    ]},
}

# Smallest baseline sigma per signal: scoring's floors, plus hand_on_face (a 0/1 signal that is
# usually 0 for the whole baseline, which would make any single touch infinitely significant).
MIN_STD = {**scoring.MIN_STD, "hand_on_face": 0.1, "chest_range": scoring.MIN_STD["flex"]}
MIN_BASELINE_SAMPLES = 2                        # ddof=1 needs two
MIN_VOICE_BASELINE = scoring.MIN_VOICE_SAMPLES  # as scoring.Baseline.add_voice
MIN_WINDOW_SAMPLES = 3                          # values needed in the answer window
MIN_VOICE_WINDOW = 5                            # voiced samples in the answer (~0.5 s of speech at 10 Hz)


def _values(samples: list[dict], key: str, voiced: bool,
            start: float = -math.inf, end: float = math.inf) -> np.ndarray:
    """One signal's values with start <= t <= end, NaNs dropped.

    scoring.column rules: None/negative/hr=0 are missing. "breath_gap" is computed from the inhales
    in all of samples first, so pass samples from scoring.BREATH_LOOKBACK_S before start to find
    the inhale that opened the first gap.
    """
    if voiced:
        samples = [s for s in samples if s.get("pitch") is not None]
    t = scoring.column(samples, "t")
    if key == "breath_gap":
        x = _breath_gaps(t, scoring.column(samples, "flex"))
    else:
        x = scoring.column(samples, key)
    return x[(t >= start) & (t <= end) & ~np.isnan(x)]


def _breath_gaps(t: np.ndarray, flex: np.ndarray) -> np.ndarray:
    """For each sample, the length of the inhale-to-inhale gap it falls in. The gap still open
    at the end is measured to the last sample; samples before the first inhale are NaN."""
    out = np.full(len(t), np.nan)
    have = ~np.isnan(flex)
    inhales = scoring.inhale_times(t, flex)
    if not len(inhales) or not have.any():
        return out
    edges = np.append(inhales, t[have][-1])
    which = np.searchsorted(inhales, t, side="right") - 1  # index of the inhale that opened the gap
    ok = which >= 0
    out[ok] = edges[which[ok] + 1] - edges[which[ok]]
    return out


def _between(samples: list[dict], start: float, end: float) -> list[dict]:
    return [s for s in samples if start <= s["t"] <= end]


def baselines_from_samples(silent: list[dict], voice: list[dict] | None = None,
                           categories: dict = CATEGORIES) -> tuple[dict, list]:
    """Baselines in the algorithm's format from the silent (and voice) baseline samples.

    A "voice" phase signal falls back to the silent baseline when there was no voice baseline,
    except voiced-only signals (the silent baseline has no speech). Returns (baselines, skipped).
    """
    out, skipped = {}, []
    for cat_name, cat in categories.items():
        sensors = []
        for s in cat["sensors"]:
            voiced = s.get("voiced", False)
            source = voice if s.get("phase") == "voice" and voice else silent
            if voiced and not voice:
                skipped.append((cat_name, s["name"], "no voice baseline"))
                continue
            x = _values(source, s["key"], voiced)
            need = MIN_VOICE_BASELINE if voiced else MIN_BASELINE_SAMPLES
            if len(x) < need:
                skipped.append((cat_name, s["name"], f"baseline has {len(x)} values (needs {need})"))
                continue
            st = stats_from_data(x)
            st["sigma"] = max(st["sigma"], s.get("min_std", MIN_STD.get(s["key"], 0.0)))
            sensors.append({"name": s["name"], "key": s["key"], **st, "n": len(x)})
        out[cat_name] = {"sensors": sensors}
    return out, skipped


def readings_from_samples(samples: list[dict], baselines: dict, q_t: float, until: float,
                          q_end: float | None = None, categories: dict = CATEGORIES) -> tuple[dict, list]:
    """One question's readings in the algorithm's format, for the signals that have a baseline.

    "mean" signals: the samples from the question's start (q_t) to when it was scored (until);
    voiced signals only from "Done asking" (q_end) when it was clicked, so the interrogator's
    voice doesn't count. "phasic" signals: the peak in their latency window (sigma 0), plus
    "pre_mu", the mean of the PRE_S seconds before q_t, which score_question() compares it with.
    Returns (categories, skipped).
    """
    has_base = {(c, s["name"]) for c, cat in baselines.items() for s in cat["sensors"]}
    before = np.nextafter(q_t, -math.inf)  # strictly before the question
    out, skipped = {}, []
    for cat_name, cat in categories.items():
        sensors = []
        for s in cat["sensors"]:
            if (cat_name, s["name"]) not in has_base:
                continue
            voiced = s.get("voiced", False)
            reading = {"name": s["name"], "key": s["key"], "weight": s["weight"],
                       "direction": s.get("direction", 0)}
            if "phasic" in s:
                # From lo after Ask to hi after Done asking: asking takes a few seconds, and the
                # reaction can come at any point in it or just after.
                lo, hi = s["phasic"]
                anchor = q_end if q_end is not None else q_t
                pre = _values(samples, s["key"], voiced, q_t - scoring.PRE_S, before)
                post = _values(samples, s["key"], voiced, q_t + lo, min(anchor + hi, until))
                if not len(pre) or len(post) < MIN_WINDOW_SAMPLES:
                    skipped.append((cat_name, s["name"], f"{len(pre)} values before the question and "
                                    f"{len(post)} at {lo:g}-{hi:g} s (needs 1 and {MIN_WINDOW_SAMPLES})"))
                    continue
                sensors.append({**reading, "mu": float(post.max()), "sigma": 0.0,
                                "pre_mu": float(pre.mean()), "n": len(post)})
                continue
            start = q_end if voiced and q_end is not None else q_t
            x = _values(samples, s["key"], voiced, start, until)
            need = MIN_VOICE_WINDOW if voiced else MIN_WINDOW_SAMPLES
            if len(x) < need:
                skipped.append((cat_name, s["name"], f"window has {len(x)} values (needs {need})"))
                continue
            sensors.append({**reading, **stats_from_data(x), "n": len(x)})
        out[cat_name] = {"weight": cat["weight"], "sensors": sensors}
    return out, skipped


def score_question(samples: list[dict], baselines: dict, q_t: float, until: float,
                   q_end: float | None = None, categories: dict = CATEGORIES) -> dict:
    """Confidence for one question.

    "mean" signals are compared with the session baseline. "phasic" ones with the seconds just
    before the question, using the session baseline's sigma (as scoring.response does).
    """
    lookback = max(scoring.PRE_S, scoring.BREATH_LOOKBACK_S)
    nearby = _between(samples, q_t - lookback, until)
    current, skipped = readings_from_samples(nearby, baselines, q_t, until, q_end, categories)
    lookup_base = {}
    for c, cat in baselines.items():
        read = {r["name"]: r for r in current[c]["sensors"]}
        lookup_base[c] = {"sensors": [{**s, "mu": read[s["name"]]["pre_mu"]} if "pre_mu" in read[s["name"]] else s
                                      for s in cat["sensors"] if s["name"] in read]}
    result = evaluate(lookup_base, current)
    result["skipped"] = skipped
    return result


def score_live_question(session, q: dict, until: float | None = None) -> dict:
    """score_question() for a running server.Session and one of its session.questions dicts."""
    silent = _between(session.samples, session.started, session.baseline_end)
    voice = (_between(session.samples, session.voice_start, session.voice_end)
             if session.voice_start is not None and session.voice_done else None)
    baselines, skipped = baselines_from_samples(silent, voice)
    result = score_question(session.samples, baselines, q["t"], q["due"] if until is None else until, q["end"])
    result["skipped"] = skipped + result["skipped"]
    return result


def to_message(result: dict, categories: dict = CATEGORIES) -> dict:
    """A result in the compact, JSON-ready form the dashboard shows ("confidence" in "response").

    Everything is suspicion = 1 - confidence (higher = further from baseline). Every category is
    listed, in CATEGORIES order; one without usable data has suspicion None and says why in "missing".
    """
    missing: dict[str, list[str]] = {}
    for cat_name, name, why in result.get("skipped", []):
        missing.setdefault(cat_name, []).append(f"{name}: {why}")
    cats = []
    for cat_name, cat in categories.items():
        r = result["categories"].get(cat_name)
        cats.append({
            "name": cat_name,
            "weight": cat["weight"],
            "suspicion": None if r is None else round(1 - r["confidence"], 4),
            "signals": [] if r is None else [
                {"key": s["key"], "name": s["name"], "weight": s["weight"], "direction": s["direction"],
                 "phasic": "pre_mu" in s, "base": round(s["base_mu"], 3), "now": round(s["mu"], 3),
                 "z": round(s["z"], 2), "suspicion": round(1 - s["p"], 4)}
                for s in r["sensors"]],
            "missing": missing.get(cat_name, []),
        })
    overall = result["suspicion"]
    return {"suspicion": None if overall is None else round(overall, 4), "categories": cats}


# ----------------------------- Recorded sessions ----------------------------


def _parse(value: str):
    if value == "":
        return None
    if value in ("True", "False"):
        return value == "True"
    try:
        return float(value)
    except ValueError:
        return value


def load_session_csv(path: Path) -> tuple[list[dict], list[dict]]:
    """A session CSV from data/ (server.SessionLog) as (samples, events).

    Sample rows have no "event"; event rows (baseline_start, question, response, ...) do.
    Values come back as floats, booleans (hand_on_face) or None, like live samples.
    """
    samples, events = [], []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("event"):
                events.append({"t": float(row["t"]), "event": row["event"], "label": row.get("label") or "",
                               "spike": _parse(row.get("spike", "")), "text": row.get("text") or ""})
            else:
                samples.append({k: (float(v) if k == "t" else _parse(v)) for k, v in row.items()})
    return samples, events


def _first(events: list[dict], name: str) -> float | None:
    return next((e["t"] for e in events if e["event"] == name), None)


def score_session(samples: list[dict], events: list[dict]) -> dict:
    """Every question in a recorded session, plus a ranking (most suspicious first)."""
    base_start, base_end = _first(events, "baseline_start"), _first(events, "baseline_done")
    if base_start is None or base_end is None:
        raise ValueError("This session never finished its baseline, so there is nothing to compare with.")
    voice_start, voice_end = _first(events, "voice_baseline_start"), _first(events, "voice_baseline_done")
    voice = _between(samples, voice_start, voice_end) if voice_start and voice_end else None
    baselines, base_skipped = baselines_from_samples(_between(samples, base_start, base_end), voice)

    last_t = samples[-1]["t"] if samples else base_end
    questions = []
    for i, e in enumerate(events):
        if e["event"] != "question":
            continue
        later = events[i + 1:]
        end = next((x["t"] for x in later if x["event"] == "question_end" and x["label"] == e["label"]), None)
        resp = next((x for x in later if x["event"] == "response" and x["label"] == e["label"]), None)
        until = resp["t"] if resp else min(e["t"] + scoring.WINDOW_S, last_t)
        result = score_question(samples, baselines, e["t"], until, end)
        questions.append({"label": e["label"], "text": e["text"], "t": e["t"], "end": end, "until": until,
                          "spike": resp["spike"] if resp else None, **result})

    ranked = sorted((q for q in questions if q["confidence"] is not None), key=lambda q: q["confidence"])
    return {"baselines": baselines, "baseline_skipped": base_skipped, "voice_baseline": voice is not None,
            "questions": questions, "ranked": [q["label"] for q in ranked]}


# ----------------------------- Output ---------------------------------------


def print_baselines(baselines: dict) -> None:
    print("BASELINES")
    print(f"{'Category':<10} | {'Signal':<22} | {'mu':>9} | {'sigma':>8} | {'n':>5}")
    print("-" * 66)
    for cat_name, cat in baselines.items():
        for s in cat["sensors"]:
            print(f"{cat_name:<10} | {s['name']:<22} | {s['mu']:>9.3f} | {s['sigma']:>8.3f} | {s.get('n', ''):>5}")
    print()


def print_result(result: dict, title: str = "") -> None:
    if title:
        print(f"##### {title}")
    for cat_name, r in result["categories"].items():
        print(f"=== {cat_name} (category weight {r['weight']:g}) ===")
        print(f"{'Data Stream':<22} | {'Dir':>3} | {'Wt':>5} | {'Base mu':>9} | {'Base sd':>8} | {'Now mu':>9} | "
              f"{'Now sd':>8} | {'Z':>7} | {'Point Conf.':>11}")
        print("-" * 104)
        for s in r["sensors"]:
            arrow = {1: "+", -1: "-"}.get(s.get("direction", 0), "+/-")
            print(f"{s['name']:<22} | {arrow:>3} | {s['weight']:>5.2f} | {s['base_mu']:>9.3f} | "
                  f"{s['base_sigma']:>8.3f} | {s['mu']:>9.3f} | {s['sigma']:>8.3f} | {s['z']:>7.3f} | {s['p']:>11.2%}")
            if "pre_mu" in s:
                print(f"{'':<22}   (phasic: Base mu = mean of the {scoring.PRE_S:g} s before the question, "
                      f"Now mu = peak in the latency window)")
        if r.get("method", "chi2") == "chi2":
            print(f"-> Weighted Chi-Sq: {r['chi2']:.4f} (df {r['df']:.2f}) | "
                  f"CATEGORY CONFIDENCE: {r['confidence']:.2%}\n")
        else:
            print(f"-> Weighted Stouffer (one-sided) | CATEGORY CONFIDENCE: {r['confidence']:.2%}\n")
    for cat_name, name, why in result.get("skipped", []):
        print(f"   (skipped {cat_name} / {name}: {why})")
    for key in result.get("unused_baselines", []):
        print(f"   Warning: baseline with no matching sensor: {key}")
    print("=" * 55)
    print(f"{'Category':<14} | {'Cat. Weight':<11} | {'Confidence':<11} | {'Z':<8}")
    print("-" * 55)
    for cat_name, r in result["categories"].items():
        print(f"{cat_name:<14} | {r['weight']:<11.2f} | {r['confidence']:<11.2%} | {r['z']:<8.3f}")
    print("-" * 55)
    if result["confidence"] is None:
        print("OVERALL CONFIDENCE: no usable signals")
    else:
        print(f"Combined Z (weighted Stouffer): {result['z_total']:.4f}")
        print(f"OVERALL CONFIDENCE:             {result['confidence']:.2%}")
    print("=" * 55 + "\n")


def print_session(report: dict, quiet: bool = False) -> None:
    if not quiet:
        print_baselines(report["baselines"])
        if not report["voice_baseline"]:
            print("(no voice baseline in this session: voice signals skipped)\n")
        for cat_name, name, why in report["baseline_skipped"]:
            print(f"   (no baseline for {cat_name} / {name}: {why})")
        print()
        for q in report["questions"]:
            print_result(q, f"{q['label']}  {q['text']}".strip())
    by_label = {q["label"]: q for q in report["questions"]}
    print("SUMMARY (lowest confidence = biggest departure from baseline)")
    print(f"{'Question':<10} | {'Confidence':>10} | {'Suspicion':>9} | {'Z total':>8} | {'Spike':>6}")
    print("-" * 56)
    for label in report["ranked"] + [q["label"] for q in report["questions"] if q["confidence"] is None]:
        q = by_label[label]
        conf = "-" if q["confidence"] is None else f"{q['confidence']:.2%}"
        susp = "-" if q["suspicion"] is None else f"{q['suspicion']:.2%}"
        z = "-" if q["z_total"] is None else f"{q['z_total']:.3f}"
        spike = "-" if q["spike"] is None else f"{q['spike']:.2f}"
        print(f"{label:<10} | {conf:>10} | {susp:>9} | {z:>8} | {spike:>6}")


SAVE_COLUMNS = ["label", "text", "t", "category", "signal", "direction", "weight", "base_mu", "base_sigma",
                "mu", "sigma", "z", "p", "method", "chi2", "df", "confidence", "suspicion", "spike"]


def save_session(report: dict, path: Path) -> None:
    """One row per signal, per category and per question (category/signal blank on summary rows)."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SAVE_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for q in report["questions"]:
            head = {"label": q["label"], "text": q["text"], "t": round(q["t"], 3)}
            for cat_name, r in q["categories"].items():
                for s in r["sensors"]:
                    w.writerow({**head, "category": cat_name, "signal": s["key"], "direction": s["direction"],
                                "weight": s["weight"], "base_mu": s["base_mu"], "base_sigma": s["base_sigma"],
                                "mu": s["mu"], "sigma": s["sigma"], "z": s["z"], "p": s["p"]})
                w.writerow({**head, "category": cat_name, "weight": r["weight"], "method": r["method"],
                            "chi2": r["chi2"],
                            "df": r["df"], "z": r["z"], "confidence": r["confidence"],
                            "suspicion": 1 - r["confidence"]})
            w.writerow({**head, "z": q["z_total"], "confidence": q["confidence"],
                        "suspicion": q["suspicion"], "spike": q["spike"]})


def _json_safe(obj):
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float):
        return None if math.isnan(obj) else round(obj, 6)
    return obj


def demo() -> dict:
    """The original worked example: synthetic sensors, fixed seed."""
    rng = np.random.default_rng(42)
    baselines = {
        "Thermal": {"sensors": [
            {"name": "Sensor 1 (Normal)", **stats_from_data(rng.normal(10.0, 1.5, size=500))},
            {"name": "Sensor 2 (Low-Weight Drift)", "mu": 10.0, "sigma": 2.0},
        ]},
        "Pressure": {"sensors": [
            {"name": "Sensor 3 (Normal)", "mu": 10.0, "sigma": 1.0},
            {"name": "Sensor 4 (Critical Focus)", "mu": 11.0, "sigma": 0.5},
        ]},
        "Vibration": {"sensors": [
            {"name": "Sensor 5 (Mild Drift)", "mu": 10.0, "sigma": 1.0},
            {"name": "Sensor 6 (Normal)", **stats_from_data(rng.normal(10.0, 0.8, size=300))},
            {"name": "Sensor 7 (Normal)", "mu": 10.0, "sigma": 0.9},
        ]},
    }
    categories = {
        "Thermal": {"weight": 1.0, "sensors": [
            {"name": "Sensor 1 (Normal)", "mu": 10.1, "sigma": 0.4, "weight": 1.0},
            {"name": "Sensor 2 (Low-Weight Drift)", "mu": 14.5, "sigma": 1.2, "weight": 0.2},
        ]},
        "Pressure": {"weight": 3.0, "sensors": [
            {"name": "Sensor 3 (Normal)", "mu": 9.8, "sigma": 0.5, "weight": 1.0},
            {"name": "Sensor 4 (Critical Focus)", "mu": 11.2, "sigma": 0.3, "weight": 2.5},
        ]},
        "Vibration": {"weight": 0.5, "sensors": [
            {"name": "Sensor 5 (Mild Drift)", "mu": 12.0, "sigma": 0.6, "weight": 1.0},
            {"name": "Sensor 6 (Normal)", "mu": 10.0, "sigma": 0.4, "weight": 1.0},
            {"name": "Sensor 7 (Normal)", "mu": 9.9, "sigma": 0.5, "weight": 1.0},
        ]},
    }
    print_baselines(baselines)
    result = evaluate(baselines, categories)
    print_result(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.confidence", description=__doc__.split("\n")[0])
    parser.add_argument("csv", nargs="?", type=Path, help="session CSV (default: newest in data/)")
    parser.add_argument("--demo", action="store_true", help="run the synthetic worked example")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument("--save", action="store_true", help="write <session>.confidence.csv next to the session")
    parser.add_argument("--quiet", action="store_true", help="summary table only")
    args = parser.parse_args(argv)

    if args.demo:
        demo()
        return 0
    path = args.csv
    if path is None:
        found = sorted((p for p in Path("data").glob("*.csv") if not p.name.endswith(".confidence.csv")),
                       key=lambda p: p.stat().st_mtime)
        if not found:
            print("No session CSVs in data/. Record a session, or try --demo.", file=sys.stderr)
            return 1
        path = found[-1]
    samples, events = load_session_csv(path)
    try:
        report = score_session(samples, events)
    except ValueError as e:
        print(f"{path}: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(_json_safe(report), indent=2))
    else:
        print(f"Session: {path}\n")
        print_session(report, args.quiet)
    if args.save:
        out = path.with_name(path.stem + ".confidence.csv")
        save_session(report, out)
        print(f"\nsaved {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
