"""Per-answer verdict: backend/confidence.py's score turned into a deception likelihood.

confidence.py is the scoring engine and is used unchanged. For each answer it gives a combined
Z (weighted Stouffer over signal categories; under "no change from baseline" Z ~ N(0, 1)).
This module adds, on top of that Z:

  1. Likelihood ratio. If the answer is deceptive, Z is assumed to shift by LAMBDA with extra
     spread SIGMA:  LR = N(Z; LAMBDA, 1 + SIGMA^2) / N(Z; 0, 1).
     Posterior:     P = PRIOR*LR / (PRIOR*LR + 1 - PRIOR)    -> "likelihood", 0-1.
     Below Z_MIN = -LAMBDA/SIGMA^2 the ratio would rise again (the wider "deceptive" curve has
     fatter tails), so a very calm answer would read as suspicious. Z is floored at Z_MIN there.
  2. Interval: P at Z +- INTERVAL_Z (the answer's Z has unit noise under the model).
  3. Band: Low / Unclear / Elevated / High from P (BANDS), or "insufficient" when a quality gate
     fails (GATES). Gates only decide whether to show a reading; they never change the score.
  4. Explanation (what the dashboard's "Why this reading" plots):
       contribution of each category = w_c * z_c / sqrt(sum w^2)  -> they add up to Z exactly
       null distribution of each category's z: the same scoring run on pseudo-questions placed
       across the silent baseline (calm moments), as a histogram
       a trace slice of the raw signals from -TRACE_S to +TRACE_S around the question
  5. Session: every scored answer's log LR added up (one shared state for the whole session),
     re-run after every answer.

This measures arousal against this person's own baseline, not lying.
"""

from __future__ import annotations  # allows "float | None" hints on Python 3.9

import math

import numpy as np

from backend import confidence, scoring

# ----------------------------- Constants (TUNE on real sessions) -------------

LAMBDA = 2.0        # expected Z shift for a deceptive answer
SIGMA = 1.0         # extra spread of Z for a deceptive answer
PRIOR = 0.5         # prior chance an answer is deceptive (0.5 = no assumption either way)
INTERVAL_Z = 1.2816  # +-Z for the 80% interval
Z_MIN = -LAMBDA / SIGMA ** 2  # where the ratio is lowest; calmer answers are floored here

# (band, lowest likelihood). Checked top-down.
BANDS = (("high", 0.85), ("elevated", 0.60), ("unclear", 0.30), ("low", 0.0))
RESULT = {"high": "reaction", "elevated": "reaction", "unclear": "unclear", "low": "calm",
          "insufficient": "insufficient"}  # the coarse "result" field, kept for compatibility

# Quality gates
MIN_CATEGORIES = 2      # weighted signal groups that must be usable
MIN_COVERAGE = 0.8      # share of the answer window that must have samples
MAX_GAP_S = 2.0         # longest allowed gap between samples in the window

# Explanation
PSEUDO_STEP_S = 1.0     # spacing of pseudo-questions across the silent baseline
NULL_EDGES = tuple(np.round(np.arange(-4.0, 4.01, 0.5), 2))  # histogram bins of z (clamped)
NULL_TIE_SD = 0.05      # z values this close to a calm moment count as equal to it
TRACE_S = 10.0          # trace slice: this many seconds either side of the question
TRACE_STEP_S = 0.25     # trace resolution
TRACE_KEYS = (("hr", "Heart rate", "bpm"),
              ("breath_rate", "Breathing rate", "per min"), ("blink_rate", "Blink rate", "per min"),
              ("pitch", "Voice pitch", "Hz"))

_LOG_NORM = 0.5 * math.log(2 * math.pi)


# ----------------------------- Likelihood -----------------------------------


def _log_normal(x: float, mean: float, var: float) -> float:
    return -0.5 * (x - mean) ** 2 / var - 0.5 * math.log(var) - _LOG_NORM


def log_lr(z: float) -> float:
    """log N(z; LAMBDA, 1 + SIGMA^2) - log N(z; 0, 1), with z floored at Z_MIN."""
    z = max(z, Z_MIN)
    return _log_normal(z, LAMBDA, 1 + SIGMA ** 2) - _log_normal(z, 0.0, 1.0)


def posterior(total_log_lr: float, prior: float = PRIOR) -> float:
    """P = prior*LR / (prior*LR + 1 - prior), computed in log space so it never overflows."""
    logit = math.log(prior / (1 - prior)) + total_log_lr
    return 1 / (1 + math.exp(-logit)) if logit >= 0 else math.exp(logit) / (1 + math.exp(logit))


def band_of(likelihood: float | None) -> str:
    if likelihood is None:
        return "insufficient"
    return next(name for name, low in BANDS if likelihood >= low)


# ----------------------------- Engine ---------------------------------------


def _between(samples: list[dict], start: float, end: float) -> list[dict]:
    return [s for s in samples if start <= s["t"] <= end]


def _histogram(values: list[float]) -> dict:
    edges = np.array(NULL_EDGES)
    x = np.clip(np.array(values, dtype=float), edges[0], edges[-1])
    counts, _ = np.histogram(x, bins=edges)
    return {"edges": [float(e) for e in edges], "counts": [int(c) for c in counts]}


class Engine:
    """Everything that only depends on the baseline, built once per session.

    baselines: confidence.py's baselines. null: per category (and "_total"), the z values of
    pseudo-questions across the silent baseline.
    """

    def __init__(self, samples: list[dict], silent: tuple[float, float],
                 voice: tuple[float, float] | None = None) -> None:
        silent_samples = _between(samples, *silent)
        voice_samples = _between(samples, *voice) if voice else None
        self.baselines, self.skipped = confidence.baselines_from_samples(silent_samples, voice_samples)
        self.ready = any(cat["sensors"] for cat in self.baselines.values())
        self.null: dict[str, list[float]] = {}
        start, end = silent
        t = start + scoring.PRE_S
        while t + scoring.WINDOW_S <= end:
            result = confidence.score_question(samples, self.baselines, t, t + scoring.WINDOW_S)
            for name, cat in result["categories"].items():
                self.null.setdefault(name, []).append(cat["z"])
            if result["z_total"] is not None:
                self.null.setdefault("_total", []).append(result["z_total"])
            t += PSEUDO_STEP_S

    def null_summary(self, name: str, value: float | None) -> dict | None:
        values = self.null.get(name)
        if not values:
            return None
        x = np.array(values, dtype=float)
        out = {"n": len(x), "mean": round(float(x.mean()), 3),
               "sd": round(float(x.std(ddof=1)), 3) if len(x) > 1 else 0.0, **_histogram(values)}
        if value is not None:
            # Share of calm moments below it, ties (within NULL_TIE_SD) counted half: a flat or
            # perfectly regular signal sits mid-pack, not on top.
            ties = np.isclose(x, value, rtol=0, atol=NULL_TIE_SD)
            out["percentile"] = round(float(np.mean((x < value) & ~ties) + 0.5 * np.mean(ties)), 3)
        return out


# ----------------------------- One answer -----------------------------------


def _coverage(samples: list[dict], start: float, end: float) -> tuple[float, float]:
    """(share of [start, end] with samples, longest gap in seconds)."""
    times = [s["t"] for s in samples if start <= s["t"] <= end]
    if end <= start:
        return 0.0, 0.0
    if len(times) < 2:
        return 0.0, end - start
    points = [start, *times, end]
    gaps = [b - a for a, b in zip(points, points[1:])]
    return (times[-1] - times[0]) / (end - start), max(gaps)


def _gates(engine: Engine | None, raw: dict | None, samples: list[dict], q_t: float, until: float,
           provisional: bool) -> list[dict]:
    """Each quality gate: {key, label, pass, detail}."""
    gates = []
    ok = engine is not None and engine.ready
    gates.append({"key": "baseline", "label": "Calm baseline recorded", "pass": ok,
                  "detail": "Baseline analyzed" if ok else "No usable baseline to compare with"})

    share, gap = _coverage(samples, q_t, until)
    if provisional:
        ok, detail = gap <= MAX_GAP_S, f"Longest gap {gap:.1f} s so far"
    else:
        ok = share >= MIN_COVERAGE and gap <= MAX_GAP_S
        detail = f"{share:.0%} of the window covered, longest gap {gap:.1f} s"
    gates.append({"key": "coverage", "label": "Sensors kept streaming", "pass": ok, "detail": detail})

    usable = [name for name, cat in (raw or {}).get("categories", {}).items() if cat["weight"] > 0]
    ok = len(usable) >= MIN_CATEGORIES
    gates.append({"key": "signals", "label": f"At least {MIN_CATEGORIES} signal groups", "pass": ok,
                  "detail": f"{len(usable)} usable: {', '.join(usable) or 'none'}"})
    return gates


def _signal(s: dict) -> dict:
    return {"key": s["key"], "name": s["name"], "weight": s["weight"], "direction": s["direction"],
            "phasic": "pre_mu" in s, "base": round(s["base_mu"], 3), "base_sd": round(s["base_sigma"], 3),
            "now": round(s["mu"], 3), "now_sd": round(s["sigma"], 3), "z": round(s["z"], 3),
            "p": round(s["p"], 4), "n": s.get("n")}


def _trace(samples: list[dict], q_t: float, q_end: float | None, until: float) -> dict:
    """Raw signals around the question, averaged into TRACE_STEP_S bins, times relative to q_t."""
    near = _between(samples, q_t - TRACE_S, min(until, q_t + TRACE_S))
    t = np.array([s["t"] - q_t for s in near])
    bins = np.floor((t + TRACE_S) / TRACE_STEP_S).astype(int) if len(t) else np.array([], dtype=int)
    series = {}
    for key, label, unit in TRACE_KEYS:
        x = scoring.column(near, key)
        points = []
        for b in np.unique(bins):
            sel = (bins == b) & ~np.isnan(x)
            if sel.any():
                points.append([round(float(t[bins == b].mean()), 2), round(float(x[sel].mean()), 3)])
        if points:
            series[key] = {"label": label, "unit": unit, "points": points}
    windows = [
        {"key": "pre", "label": "Reference: just before the question", "from": -scoring.PRE_S, "to": 0.0},
        {"key": "answer", "label": "Answer window", "from": 0.0, "to": round(until - q_t, 2)},
    ]
    if q_end is not None:
        windows.append({"key": "voice", "label": "Spoken answer (after Done asking)",
                        "from": round(q_end - q_t, 2), "to": round(until - q_t, 2)})
    return {"from": -TRACE_S, "to": TRACE_S, "series": series, "windows": windows,
            "end": None if q_end is None else round(q_end - q_t, 2)}


def score(engine: Engine | None, samples: list[dict], q_t: float, until: float,
          q_end: float | None = None, provisional: bool = False) -> tuple[dict, dict | None]:
    """The verdict for one answer (the "verdict" in "response" / "verdict_progress").

    Returns (verdict, confidence.py's raw result or None). provisional: a partial window
    (until = now): a lighter verdict without the null distributions and the trace.
    """
    raw = None
    if engine is not None and engine.ready:
        raw = confidence.score_question(samples, engine.baselines, q_t, until, q_end)
    gates = _gates(engine, raw, samples, q_t, until, provisional)
    failed = next((g for g in gates if not g["pass"]), None)

    z = raw["z_total"] if raw else None
    categories = []
    if raw:
        weights = np.array([c["weight"] for c in raw["categories"].values()], dtype=float)
        norm = float(np.sqrt(np.sum(weights ** 2))) or 1.0
        missing: dict[str, list[str]] = {}
        for cat_name, name, why in raw["skipped"]:
            missing.setdefault(cat_name, []).append(f"{name}: {why}")
        for name, cat in confidence.CATEGORIES.items():
            r = raw["categories"].get(name)
            entry = {"name": name, "weight": cat["weight"], "missing": missing.get(name, [])}
            if r is None:
                entry.update({"z": None, "contribution": None, "suspicion": None})
            else:
                entry.update({"z": round(r["z"], 3), "contribution": round(r["weight"] * r["z"] / norm, 3),
                              "suspicion": round(1 - r["confidence"], 4), "method": r["method"]})
                if not provisional:
                    entry["signals"] = [_signal(s) for s in r["sensors"]]
                    entry["null"] = engine.null_summary(name, r["z"])
            categories.append(entry)
    else:
        norm = None

    verdict: dict = {"provisional": provisional, "categories": categories, "gates": gates,
                     "z": None if z is None else round(z, 3), "norm": None if norm is None else round(norm, 3),
                     "model": {"lambda": LAMBDA, "sigma": SIGMA, "prior": PRIOR, "interval_z": INTERVAL_Z}}
    if failed or z is None:
        reason = failed["detail"] if failed else "No signal group could be scored"
        verdict.update({"band": "insufficient", "result": RESULT["insufficient"], "likelihood": None,
                        "interval": None, "reason": reason, "log_lr": None, "z_eff": None})
    else:
        lr = log_lr(z)
        likelihood = posterior(lr)
        lo, hi = posterior(log_lr(z - INTERVAL_Z)), posterior(log_lr(z + INTERVAL_Z))
        band = band_of(likelihood)
        verdict.update({"band": band, "result": RESULT[band], "likelihood": round(likelihood, 4),
                        "interval": [round(min(lo, hi), 4), round(max(lo, hi), 4)], "reason": None,
                        "log_lr": round(lr, 4), "z_eff": round(max(z, Z_MIN), 3)})
    if not provisional:
        verdict["null_total"] = engine.null_summary("_total", z) if engine and engine.ready else None
        verdict["trace"] = _trace(samples, q_t, q_end, until)
    return verdict, raw


# ----------------------------- Session --------------------------------------


def session_summary(answers: list[tuple[str, dict]]) -> dict:
    """All scored answers so far, combined: their log LRs added up (re-run after every answer).

    answers: (label, final verdict) in the order asked. Answers that failed a gate are listed
    but add no evidence. Also gives the Stouffer Z over the answers' Zs (confidence.py's way of
    combining), as an intermediate value.
    """
    total = lo_total = hi_total = 0.0
    history, zs = [], []
    for label, v in answers:
        if v.get("log_lr") is not None:
            z = v["z"]
            total += v["log_lr"]
            lo_total += log_lr(z - INTERVAL_Z)
            hi_total += log_lr(z + INTERVAL_Z)
            zs.append(z)
        history.append({"label": label, "band": v["band"], "log_lr": v.get("log_lr"),
                        "cum_log_lr": round(total, 4), "likelihood": round(posterior(total), 4) if zs else None,
                        "interval": [round(posterior(lo_total), 4), round(posterior(hi_total), 4)] if zs else None})
    counts = {name: 0 for name in RESULT}
    for _, v in answers:
        counts[v["band"]] += 1
    if not zs:
        return {"likelihood": None, "interval": None, "band": "insufficient", "log_lr": None, "z": None,
                "answers": 0, "history": history, "counts": counts}
    likelihood = posterior(total)
    return {"likelihood": round(likelihood, 4), "band": band_of(likelihood),
            "interval": [round(posterior(lo_total), 4), round(posterior(hi_total), 4)],
            "log_lr": round(total, 4), "z": round(float(sum(zs) / math.sqrt(len(zs))), 3),
            "answers": len(zs), "history": history, "counts": counts}
