"""Concealed Information Test (CIT) support: per-item face responses locked to item onset.

The backend or interview runner calls `mark_item(item, t, meta, duration)` when something
appears on screen. For every onset we cut a response window [onset, onset + duration]
(default 5 s) and a short reference window [onset - pre_s, onset), then compute face cues:

  eye_closure_early     mean eyelid closure 0..2 s (continuous; recognition -> SUPPRESSED blinking)
  first_blink_latency_s time to the first blink (recognition -> LONGER), censored at the window end
  blinks_early/late     blink counts 0..2 s / 2..5 s (reported; the counts are too coarse to score)
  lip_compression       drop in lip thickness vs. the reference window
  bs_mouth_press_change rise in the model's "mouth press" blendshape vs. the reference window
  brow_furrow           drop in inner-brow gap vs. the reference window
  head_motion_change    head speed after minus before. Two-sided: the direction is learned per person
  face_touch_ratio      fraction of the window with a hand on the face
  blink_rate_post, mouth_open_max, gaze_aversion_post, facial_motion_post: context, not scored

Scoring lives in `interview.scoring` (shared with the other sensors): within-series z-scores,
per-subject calibration, permutation p-values.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from interview.scoring import BlockResult, Cue, CueParams, Epoch, calibrate, default_params, score_block

from .blink import BlinkDetector
from .config import CitConfig

# Scored vision cues: (name, expected direction for the recognized item, prior weight).
# Priors favour the cues with the best published support (blink suppression / latency).
VISION_CUES: List[Cue] = [
    Cue("eye_closure_early", -1, 1.0),
    Cue("first_blink_latency_s", +1, 1.0),
    Cue("lip_compression", +1, 0.75),
    Cue("bs_mouth_press_change", +1, 0.5),
    Cue("brow_furrow", +1, 0.5),
    Cue("head_motion_change", 0, 0.5),
    Cue("face_touch_ratio", +1, 0.5),
]
LEGACY_BLOCK = "cit"


@dataclass
class _Pending:
    item: str
    onset: float
    seq: int
    post_s: float
    meta: Dict[str, object]


@dataclass
class ItemResponse:
    item: str
    onset: float
    seq: int
    valid: bool
    features: Dict[str, Optional[float]] = field(default_factory=dict)
    meta: Dict[str, object] = field(default_factory=dict)


def _mean(rows, key) -> Optional[float]:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return float(np.mean(vals)) if vals else None


def _diff(pre: Optional[float], post: Optional[float]) -> Optional[float]:
    return None if pre is None or post is None else post - pre


def _rel_drop(pre: Optional[float], post: Optional[float]) -> Optional[float]:
    if pre is None or post is None or abs(pre) < 1e-9:
        return None
    return 1.0 - post / pre


class CitSession:
    """Collects onsets, computes per-presentation responses, scores blocks."""

    def __init__(self, cfg: Optional[CitConfig] = None):
        self.cfg = cfg or CitConfig()
        self._lock = threading.Lock()
        self._pending: List[_Pending] = []
        self._history: Deque[Dict[str, Optional[float]]] = deque()
        self.responses: List[ItemResponse] = []
        self._seq = 0
        self._occurrences: Dict[Tuple[str, str], int] = {}
        self.last_onset: Optional[Tuple[str, float, float]] = None   # (item, onset, duration)
        self.calibration: Optional[Dict[str, CueParams]] = None

    # ------------------------------------------------------------------ inputs
    def mark_item(self, item: str, t: float, meta: Optional[Dict[str, object]] = None,
                  duration: Optional[float] = None) -> None:
        """Thread-safe. `meta` may carry block / series / kind (see interview.protocol.Step)."""
        meta = dict(meta or {})
        block = str(meta.get("block") or LEGACY_BLOCK)
        meta["block"] = block
        meta.setdefault("kind", "test")
        with self._lock:
            if "series" not in meta or meta["series"] is None:
                key = (block, str(item))                 # legacy: n-th showing of an item = series n
                meta["series"] = self._occurrences.get(key, 0)
                self._occurrences[key] = meta["series"] + 1
            self._seq += 1
            post = float(duration) if duration else self.cfg.post_s
            self._pending.append(_Pending(str(item), float(t), self._seq, post, meta))
            self.last_onset = (str(item), float(t), post)

    def reset(self) -> None:
        with self._lock:
            self._pending.clear()
            self.responses.clear()
            self._occurrences.clear()
            self.last_onset = None
            self.calibration = None

    def add_frame(self, rec: Dict[str, Optional[float]]) -> None:
        self._history.append(rec)
        horizon = rec["t"] - (self.cfg.pre_s + 120.0)       # long enough for narrative answers
        while self._history and self._history[0]["t"] < horizon:
            self._history.popleft()

    # ------------------------------------------------------------------ epochs
    def collect_ready(self, t: float, blink: BlinkDetector) -> List[ItemResponse]:
        """Finish every epoch whose window has fully elapsed (+0.6 s so blinks in progress end)."""
        with self._lock:
            ready = [p for p in self._pending if t >= p.onset + p.post_s + 0.6]
            self._pending = [p for p in self._pending if t < p.onset + p.post_s + 0.6]
        out = [self._compute(p, blink) for p in ready]
        with self._lock:
            self.responses.extend(out)
        return out

    def _compute(self, p: _Pending, blink: BlinkDetector) -> ItemResponse:
        c = self.cfg
        end = p.onset + p.post_s
        pre = [r for r in self._history if p.onset - c.pre_s <= r["t"] < p.onset]
        post_all = [r for r in self._history if p.onset <= r["t"] <= end]
        post = [r for r in post_all if r.get("face_present")]
        early = [r for r in post if r["t"] < p.onset + c.early_s]
        pre_face = [r for r in pre if r.get("face_present")]
        presence = len(post) / len(post_all) if post_all else 0.0

        starts = [te - d / 1000.0 for te, d in blink.blinks]
        rel = sorted(s - p.onset for s in starts if p.onset <= s < end)
        closure = [max(0.0, 1.0 - r["eye_openness"]) for r in early if r.get("eye_openness") is not None]
        f: Dict[str, Optional[float]] = {
            "face_presence_ratio": presence,
            "eye_closure_early": float(np.mean(closure)) if closure else None,
            "first_blink_latency_s": rel[0] if rel else p.post_s,
            "blinks_early": float(sum(1 for x in rel if x < c.early_s)),
            "blinks_late": float(sum(1 for x in rel if x >= c.early_s)),
            "blink_rate_post": 60.0 * len(rel) / p.post_s,
            "lip_compression": _rel_drop(_mean(pre_face, "lip_thickness"), _mean(post, "lip_thickness")),
            "bs_mouth_press_change": _diff(_mean(pre_face, "bs_mouth_press"), _mean(post, "bs_mouth_press")),
            "brow_furrow": _rel_drop(_mean(pre_face, "brow_inner_gap"), _mean(post, "brow_inner_gap")),
            "head_motion_post": _mean(post, "head_speed"),
            "head_motion_pre": _mean(pre_face, "head_speed"),
            "head_motion_change": _diff(_mean(pre_face, "head_speed"), _mean(post, "head_speed")),
            "facial_motion_post": _mean(post, "facial_motion_energy"),
            "gaze_aversion_post": _mean(post, "gaze_offaxis"),
            "face_touch_ratio": _mean(post_all, "face_touch"),
            "mouth_open_max": max((r["mouth_open"] for r in post if r.get("mouth_open") is not None), default=None),
            "head_yaw_post": _mean(post, "head_yaw"),
        }
        valid = presence >= c.min_presence
        if not valid:   # "0 blinks" with no face visible is missing data, not a real response
            f = {k: (v if k == "face_presence_ratio" else None) for k, v in f.items()}
        return ItemResponse(p.item, p.onset, p.seq, valid, f, p.meta)

    # ------------------------------------------------------------------ scoring
    def epochs(self, block: Optional[str] = None) -> List[Epoch]:
        with self._lock:
            rs = list(self.responses)
        return [Epoch(str(r.meta.get("block")), int(r.meta.get("series", 0)), r.item, r.valid, r.features,
                      str(r.meta.get("kind", "test")))
                for r in rs if block is None or r.meta.get("block") == block]

    def params(self) -> Dict[str, CueParams]:
        base = default_params(VISION_CUES)
        for name, p in base.items():                       # config weights scale the priors
            p.weight *= self.cfg.weights.get(name, 1.0)
        if self.calibration:
            for name, p in self.calibration.items():
                base[name] = CueParams(p.sign, p.weight * self.cfg.weights.get(name, 1.0), p.calibration_z)
        return base

    def calibrate(self, block: str, target: str) -> Dict[str, CueParams]:
        """Learn this subject's cue weights from a block with a KNOWN target (number test)."""
        self.calibration = calibrate(self.epochs(block), str(target), VISION_CUES)
        return self.calibration

    def block_result(self, block: str = LEGACY_BLOCK, known_item: Optional[str] = None,
                     n_perm: int = 4000) -> BlockResult:
        return score_block(self.epochs(block), self.params(), block, known_item, n_perm)

    def scores(self, block: str = LEGACY_BLOCK) -> Dict[str, Dict[str, Optional[float]]]:
        """Live, cheap (no permutation test): per item {vision_cit_score, <cue>_z, repetitions}."""
        res = score_block(self.epochs(block), self.params(), block, n_perm=0) if self.epochs(block) else None
        reps: Dict[str, int] = {}
        for e in self.epochs(block):
            if e.kind == "test" and e.valid:
                reps[e.item] = reps.get(e.item, 0) + 1
        out: Dict[str, Dict[str, Optional[float]]] = {}
        for item in {e.item for e in self.epochs(block) if e.kind == "test"}:
            row: Dict[str, Optional[float]] = {"repetitions": float(reps.get(item, 0)),
                                               "vision_cit_score": res.items.get(item) if res else None}
            for cue, z in ((res.cue_z.get(item, {}) if res else {}).items()):
                row[f"{cue}_z"] = z
            out[item] = row
        return out

    def ranking(self, block: str = LEGACY_BLOCK) -> List[Tuple[str, float]]:
        s = self.scores(block)
        ranked = [(i, v["vision_cit_score"]) for i, v in s.items() if v.get("vision_cit_score") is not None]
        return sorted(ranked, key=lambda x: x[1], reverse=True)

    def current_item(self, t: float) -> Optional[Tuple[str, float]]:
        """(item, seconds since onset) while the latest window is open."""
        lo = self.last_onset
        if lo and 0 <= t - lo[1] <= lo[2]:
            return lo[0], t - lo[1]
        return None
