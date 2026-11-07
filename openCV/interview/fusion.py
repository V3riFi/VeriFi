"""Slow sensor round + combined verdict.

Two independent channels look at the SAME identity details:
  1. reaction time (fast round, interview/rtcit.py): response conflict when denying a known detail
  2. physiology (slow round, this file): the orienting response to a recognized detail:
       skin conductance rises 1-5 s after onset   (scr_amp, +)   the best-established CIT measure
       breathing gets shallower                    (resp_length, -)
       heart rate slows briefly                    (hr_change, -)
     These are slow, so items come every 6.8-7.8 s (an RT round is ~1 per second).
The slow round runs FIRST: skin conductance habituates with repeated exposure, and the RT round
shows every item 14 times.

Combined verdict: Stouffer's weighted Z over the channels' one-sided known-answer p-values. The
channels come from different rounds, so under "doesn't know the details" their noise is independent
and the combined p is valid. Vision face cues are recorded in the slow round but are exploratory
(weight 0) until validated (METHOD.md).

Physiology input contract (for the backend / Arduino logger), one CSV:
    t,gsr,hr,resp          t = time.time() on this laptop when the sample arrived; any column may be missing
Onsets come from the slow round log (<name>.slow.csv) or live over UDP (--udp).
"""
from __future__ import annotations

import bisect
import csv
import math
import random
from dataclasses import dataclass
from statistics import NormalDist
from typing import Dict, List, Optional, Sequence

from .identity import IdentityPlan
from .protocol import series_orders
from .scoring import BlockResult, Cue, Epoch, default_params, score_block
from .verdict import verdict

PHYSIO_CUES = [Cue("scr_amp", +1, 1.0), Cue("resp_length", -1, 0.75), Cue("hr_change", -1, 0.5)]
# The one key press per word (I = NO) in the slow round is a slow-paced RT-CIT trial of its own:
# denying a recognized detail takes longer. Free extra evidence from the same presentations.
KP_CUE = Cue("kp_log_rt", +1, 0.75)
SLOW_CUES = PHYSIO_CUES + [KP_CUE]
SLOW_BLOCK = "identity_slow"


# ---------------------------------------------------------------------- slow round
@dataclass
class SlowItem:
    series: int
    group: int
    label: str                  # TRUE / F1-F4 / BUFFER
    text: str
    kind: str                   # test | buffer
    display_s: float
    total_s: float
    onset: Optional[float] = None
    key: Optional[str] = None
    rt: Optional[float] = None  # seconds from onset to the (first) key press
    block: str = SLOW_BLOCK

    def log_row(self) -> Dict[str, object]:
        return {"block": self.block, "series": self.series, "group": self.group, "label": self.label,
                "kind": self.kind, "onset": self.onset, "display_s": self.display_s, "key": self.key,
                "rt_ms": None if self.rt is None else round(self.rt * 1000, 1)}


def slow_items(plan: IdentityPlan, rng: random.Random, reps: int = 3, isi=(6.8, 7.8),
               display_s: float = 3.0, block: str = SLOW_BLOCK) -> List[SlowItem]:
    """Detail by detail: one unscored buffer (topic change causes its own response), then `reps`
    shuffled passes over the 5 options. Series = (detail, pass)."""
    out: List[SlowItem] = []
    for g, cat in enumerate(plan.cats):
        alts = plan.alternatives[cat]
        out.append(SlowItem(-1, g, "BUFFER", alts[4], "buffer", display_s, rng.uniform(*isi), block=block))
        texts = {"TRUE": plan.values[cat], **{f"F{i + 1}": v for i, v in enumerate(alts[:4])}}
        for r, order in enumerate(series_orders(list(texts), reps, rng, avoid_first="BUFFER")):
            for lab in order:
                out.append(SlowItem(g * reps + r, g, lab, texts[lab], "test", display_s, rng.uniform(*isi),
                                    block=block))
    return out


def load_slow(path: str) -> List[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["series"], r["group"] = int(r["series"]), int(r["group"])
        r["onset"] = float(r["onset"]) if r.get("onset") else None
        r["rt"] = float(r["rt_ms"]) / 1000 if r.get("rt_ms") else None
        r["key"] = r.get("key") or None
        r.setdefault("block", SLOW_BLOCK)
    return rows


def slow_rows(items: Sequence[SlowItem]) -> List[dict]:
    """In-memory equivalent of load_slow() for the items shown in this session."""
    return [{**it.log_row(), "onset": it.onset, "rt": it.rt} for it in items if it.onset]


# ---------------------------------------------------------------------- physiology
def hr_from_ppg(t: Sequence[float], ppg: Sequence[float]) -> List[float]:
    """Heart rate (bpm) per sample from a raw pulse waveform: detrend with a 0.75 s moving mean,
    find peaks at least 0.33 s apart (<= 180 bpm) above +0.3 SD, keep beat intervals for 40-180 bpm,
    and give each sample the rate of the most recent beat interval (NaN before the 2nd beat)."""
    n = len(t)
    if n < 10:
        return [math.nan] * n
    hz = (n - 1) / (t[-1] - t[0]) if t[-1] > t[0] else 0
    if hz <= 0:
        return [math.nan] * n
    w = max(1, int(0.75 * hz))
    x = [v if math.isfinite(v) else 0.0 for v in ppg]
    cs = [0.0]
    for v in x:
        cs.append(cs[-1] + v)
    d = [x[i] - (cs[min(n, i + w // 2 + 1)] - cs[max(0, i - w // 2)]) / (min(n, i + w // 2 + 1) - max(0, i - w // 2))
         for i in range(n)]
    sd = math.sqrt(sum(v * v for v in d) / n) or 1.0
    beats: List[float] = []
    for i in range(1, n - 1):
        if d[i] > 0.3 * sd and d[i] >= d[i - 1] and d[i] > d[i + 1] and (not beats or t[i] - beats[-1] >= 0.33):
            beats.append(t[i])
    out, k, cur = [], 0, math.nan
    for ti in t:
        while k + 1 < len(beats) and beats[k + 1] <= ti:
            ibi = beats[k + 1] - beats[k]
            cur = 60.0 / ibi if 1 / 3 <= ibi <= 1.5 else cur
            k += 1
        out.append(cur)
    return out


class Physio:
    def __init__(self, t: Sequence[float], cols: Dict[str, Sequence[float]], simulated: bool = False):
        order = sorted(range(len(t)), key=lambda i: t[i])
        self.t = [t[i] for i in order]
        self.cols = {k: [v[i] for i in order] for k, v in cols.items()}
        self.simulated = simulated

    @classmethod
    def from_csv(cls, path: str, gsr_inverted: bool = False) -> "Physio":
        """Reads t,gsr,hr,resp[,ppg][,sim]. Empty cells are gaps. hr = 0 means 'no finger' (gap).
        Without an hr column, heart rate is derived from ppg."""
        t, cols, sim = [], {}, False
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    t.append(float(r["t"]))
                except (KeyError, ValueError, TypeError):
                    continue
                sim = sim or (r.get("sim") or "").strip() == "1"
                for k in ("gsr", "hr", "resp", "ppg"):
                    if k in r:
                        try:
                            v = float(r[k])
                        except (TypeError, ValueError):
                            v = math.nan
                        if k == "hr" and (v <= 0 or v > 250):
                            v = math.nan
                        cols.setdefault(k, []).append(-v if (k == "gsr" and gsr_inverted) else v)
        cols = {k: v for k, v in cols.items() if len(v) == len(t) and any(math.isfinite(x) for x in v)}
        ph = cls(t, cols, sim)
        if "hr" not in ph.cols and "ppg" in ph.cols:
            ph.cols["hr"] = hr_from_ppg(ph.t, ph.cols["ppg"])
        return ph

    def window(self, col: str, a: float, b: float) -> List[float]:
        if col not in self.cols:
            return []
        i, j = bisect.bisect_left(self.t, a), bisect.bisect_right(self.t, b)
        return [v for v in self.cols[col][i:j] if not math.isnan(v)]

    def rate(self) -> float:
        return (len(self.t) - 1) / (self.t[-1] - self.t[0]) if len(self.t) > 1 and self.t[-1] > self.t[0] else 0.0


def physio_features(p: Physio, onset: float, min_cover: float = 0.5) -> Dict[str, Optional[float]]:
    """Per-presentation cues. Windows: baseline -1..0 s; SCR peak 1-5 s; HR 0-5 s; breathing 0-6 s."""
    hz = p.rate()
    enough = lambda xs, secs: hz > 0 and len(xs) >= min_cover * hz * secs
    mean = lambda xs: sum(xs) / len(xs)
    f: Dict[str, Optional[float]] = {"scr_amp": None, "hr_change": None, "resp_length": None}
    base, post = p.window("gsr", onset - 1, onset), p.window("gsr", onset + 1, onset + 5)
    if enough(base, 1) and enough(post, 4):
        f["scr_amp"] = max(post) - mean(base)
    base, post = p.window("hr", onset - 1, onset), p.window("hr", onset, onset + 5)
    if base and enough(post, 5) and len(post) >= 2:
        f["hr_change"] = mean(post) - mean(base)
    post = p.window("resp", onset, onset + 6)
    if enough(post, 6) and len(post) > 2:
        f["resp_length"] = sum(abs(b - a) for a, b in zip(post, post[1:])) / 6.0
    return f


def keypress_feature(r: dict, min_rt: float = 0.15) -> Optional[float]:
    """log RT (ms) of a correct NO press in the slow round; None if no / wrong / implausible press."""
    rt = r.get("rt")
    if rt is None or r.get("key") != "i" or rt < min_rt or rt > float(r.get("display_s") or 3.0):
        return None
    return math.log(rt * 1000)


def slow_epochs(rows: Sequence[dict], p: Optional[Physio] = None, gated: Optional[Dict[int, str]] = None,
                vision: Optional[Dict[tuple, Dict[str, Optional[float]]]] = None,
                keypress: bool = True) -> List[Epoch]:
    """One Epoch per presentation with every per-trial cue: physiology (if a sensor log is given),
    the key-press RT, and vision cues (`vision` maps (block, series, label) -> features).
    `gated` = {row index: reason} from the camera check: those presentations are left out."""
    eps = []
    for i, r in enumerate(rows):
        if r.get("onset") is None:
            continue
        feats: Dict[str, Optional[float]] = (physio_features(p, float(r["onset"])) if p is not None
                                             else {"scr_amp": None, "hr_change": None, "resp_length": None})
        if keypress:
            feats[KP_CUE.name] = keypress_feature(r)
        if vision:
            feats.update(vision.get((r.get("block", SLOW_BLOCK), int(r["series"]), r["label"]), {}))
        valid = any(v is not None for v in feats.values()) and not (gated and i in gated)
        eps.append(Epoch(r.get("block", SLOW_BLOCK), int(r["series"]), r["label"], valid, feats, kind=r["kind"]))
    return eps


def physio_epochs(rows: Sequence[dict], p: Physio) -> List[Epoch]:
    return slow_epochs(rows, p, keypress=False)


def score_physio(rows: Sequence[dict], p: Physio, n_perm: int = 4000) -> BlockResult:
    """Physiology only (default cue weights)."""
    return score_block(physio_epochs(rows, p), default_params(PHYSIO_CUES), SLOW_BLOCK, known_item="TRUE",
                       n_perm=n_perm)


def score_slow(rows: Sequence[dict], p: Optional[Physio], params=None, n_perm: int = 4000,
               gated: Optional[Dict[int, str]] = None, vision=None) -> BlockResult:
    """The whole sensor round as ONE channel: every cue is combined per presentation (weighted
    within-series z), then one permutation test. Cues measured on the same presentations must be
    combined this way, never by multiplying their separate p-values (they aren't independent).
    `params` = per-person weights from the calibration round (interview/profile.py); default priors."""
    params = params or default_params(SLOW_CUES)
    eps = slow_epochs(rows, p, gated, vision)
    block = eps[0].block if eps else SLOW_BLOCK
    return score_block(eps, params, block, known_item="TRUE", n_perm=n_perm)


# ---------------------------------------------------------------------- fusion
def stouffer(ps: Dict[str, Optional[float]], weights: Dict[str, float]) -> Optional[float]:
    """One-sided p-values -> combined p (weighted Stouffer). Channels with no p are skipped."""
    nd = NormalDist()
    num = den = 0.0
    for k, p in ps.items():
        w = weights.get(k, 0.0)
        if p is None or w <= 0:
            continue
        p = min(max(p, 1e-6), 1 - 1e-6)
        num += w * nd.inv_cdf(1 - p)
        den += w * w
    return None if den == 0 else 1 - nd.cdf(num / math.sqrt(den))


DEFAULT_WEIGHTS = {"reaction_time": 1.0, "physiology": 1.0, "vision": 0.0}


def combine(channel_p: Dict[str, Optional[float]], weights: Optional[Dict[str, float]] = None,
            engaged: bool = True) -> dict:
    """`engaged` = False when a used channel's task check failed one-sidedly (interview/verdict.py)."""
    w = dict(DEFAULT_WEIGHTS, **(weights or {}))
    p = stouffer(channel_p, w)
    used = [k for k, v in channel_p.items() if v is not None and w.get(k, 0) > 0]
    return {"p": p, "verdict": verdict(p, True, engaged), "channels": channel_p, "weights": w, "used": used}
