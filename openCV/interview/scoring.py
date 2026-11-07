"""Modality-independent CIT scoring. Vision, GSR, heart rate and breathing can all use it.

Input: a list of `Epoch`s (one per item presentation) carrying numeric cue values.
Pipeline:
  1. Within-series standardization. A "series" is one pass through all items of a block.
     Each cue is z-scored across the items of the SAME series, using a pooled within-series SD
     for the block. This cancels slow drift (fatigue, a subject who blinks 6/min in one run
     and 50/min in the next) and anything common to every item.
  2. Signed, weighted combination of cues into one score per presentation.
  3. Item score = mean over series.
  4. Permutation test: shuffle item labels within each series (the null hypothesis that
     no item is special) to get a p-value for the top item ("searching" mode) or for a
     known relevant item ("known-solution" mode).
  5. Per-subject calibration: a block with a KNOWN target (number test) shows which cues
     react for this person. Weights are adjusted, shrunk toward the priors, and the
     direction of two-sided cues is learned.
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


@dataclass(frozen=True)
class Cue:
    name: str
    sign: int            # +1: higher = more recognition-like, -1: lower, 0: direction learned per subject
    weight: float        # prior weight


@dataclass
class Epoch:
    block: str
    series: int
    item: str
    valid: bool
    features: Dict[str, Optional[float]]
    kind: str = "test"   # "test" is scored; "buffer" / "narrative" / ... are not


@dataclass
class CueParams:
    sign: int
    weight: float
    calibration_z: Optional[float] = None


@dataclass
class BlockResult:
    block: str
    items: Dict[str, float]                       # item -> combined score (higher = stood out)
    cue_z: Dict[str, Dict[str, Optional[float]]]  # item -> cue -> mean within-series signed z
    n_series: int
    top_item: Optional[str]
    lead: Optional[float]                         # top minus second
    p_top: Optional[float]                        # searching mode: chance of a top score this high
    p_known: Optional[float] = None               # known-solution mode: chance for the known item
    known_item: Optional[str] = None
    known_rank: Optional[int] = None
    label: str = "inconclusive"
    notes: List[str] = field(default_factory=list)
    series_scores: Dict[int, Dict[str, float]] = field(default_factory=dict)   # series -> item -> score

    def ranking(self) -> List[Tuple[str, float]]:
        return sorted(self.items.items(), key=lambda kv: kv[1], reverse=True)

    def series_rank(self, item: str) -> Dict[int, Optional[int]]:
        """Rank of `item` within each series (1 = stood out most); None if it wasn't valid there.
        Used for the personal-facts round, where each series is a different question."""
        out: Dict[int, Optional[int]] = {}
        for s, sc in self.series_scores.items():
            if item not in sc or len(sc) < 3:
                out[s] = None
            else:
                out[s] = 1 + sum(v > sc[item] for k, v in sc.items() if k != item)
        return out


# ---------------------------------------------------------------------- core math
def _matrix(epochs: Sequence[Epoch], cue: str) -> Tuple[np.ndarray, List[str], List[int]]:
    items = sorted({e.item for e in epochs})
    series = sorted({e.series for e in epochs})
    m = np.full((len(series), len(items)), np.nan)
    for e in epochs:
        v = e.features.get(cue)
        if e.valid and v is not None and math.isfinite(v):
            m[series.index(e.series), items.index(e.item)] = v
    return m, items, series


def series_z(m: np.ndarray, min_sd: float = 1e-6) -> np.ndarray:
    """Center each row (series) on its own mean, scale by the pooled within-series SD."""
    out = np.full_like(m, np.nan)
    rows_ok = np.sum(~np.isnan(m), axis=1) >= 3
    if not rows_ok.any():
        return out
    centered = m[rows_ok] - np.nanmean(m[rows_ok], axis=1, keepdims=True)
    pooled = float(np.sqrt(np.nanmean(centered ** 2)))
    if not math.isfinite(pooled) or pooled < min_sd:
        out[rows_ok] = 0.0 * centered      # cue never varied: carries no information
        return out
    out[rows_ok] = centered / pooled
    return out


def combined_matrix(epochs: Sequence[Epoch], params: Dict[str, CueParams], clip: float = 3.0):
    """Weighted signed z per (series, item), plus per-cue z matrices."""
    scored = [e for e in epochs if e.kind == "test"]
    if not scored:
        return None, [], [], {}
    _, items, series = _matrix(scored, next(iter(params)))
    num = np.zeros((len(series), len(items)))
    den = np.zeros_like(num)
    per_cue: Dict[str, np.ndarray] = {}
    for name, p in params.items():
        if p.weight <= 0 or p.sign == 0:
            continue
        z = np.clip(series_z(_matrix(scored, name)[0]) * p.sign, -clip, clip)
        per_cue[name] = z
        ok = ~np.isnan(z)
        num[ok] += p.weight * z[ok]
        den[ok] += p.weight
    with np.errstate(invalid="ignore", divide="ignore"):
        comb = np.where(den > 0, num / den, np.nan)
    return comb, items, series, per_cue


def permutation_p(comb: np.ndarray, n_perm: int = 4000, known_col: Optional[int] = None,
                  seed: int = 0) -> Tuple[Optional[float], Optional[float]]:
    """p for the observed max item mean (searching) and for one known item (known-solution)."""
    rng = np.random.default_rng(seed)
    obs = np.nanmean(comb, axis=0)
    if np.all(np.isnan(obs)):
        return None, None
    obs_max = np.nanmax(obs)
    obs_known = obs[known_col] if known_col is not None else None
    ge_max = ge_known = 0
    perm = comb.copy()
    for _ in range(n_perm):
        for r in range(comb.shape[0]):
            ok = np.where(~np.isnan(comb[r]))[0]
            perm[r, ok] = comb[r, rng.permutation(ok)]
        means = np.nanmean(perm, axis=0)
        ge_max += np.nanmax(means) >= obs_max - 1e-12
        if obs_known is not None and not math.isnan(obs_known):
            ge_known += means[known_col] >= obs_known - 1e-12
    p_max = (ge_max + 1) / (n_perm + 1)
    p_known = (ge_known + 1) / (n_perm + 1) if obs_known is not None and not math.isnan(obs_known) else None
    return p_max, p_known


# ---------------------------------------------------------------------- public API
def default_params(cues: Sequence[Cue]) -> Dict[str, CueParams]:
    # two-sided cues get a provisional + sign at half weight until calibration says otherwise
    return {c.name: CueParams(c.sign if c.sign else +1, c.weight * (1.0 if c.sign else 0.5)) for c in cues}


def calibrate(epochs: Sequence[Epoch], target: str, cues: Sequence[Cue]) -> Dict[str, CueParams]:
    """Per-subject cue weights from a block whose target is KNOWN (e.g. the number test).

    weight = prior x clip(1 + 0.5 * z_target, 0.25, 2): a cue that clearly fired for the known
    target counts up to double, one that went the wrong way drops to a quarter (never zero:
    one short calibration block is noisy, so we shrink toward the priors).
    """
    params = {}
    scored = [e for e in epochs if e.kind == "test"]
    for c in cues:
        m, items, _ = _matrix(scored, c.name)
        z = series_z(m)
        zt = float(np.nanmean(z[:, items.index(target)])) if target in items and not np.all(
            np.isnan(z[:, items.index(target)])) else None
        if zt is None:
            params[c.name] = CueParams(c.sign if c.sign else +1, c.weight * (1.0 if c.sign else 0.5))
            continue
        if c.sign == 0:                       # learn direction, but only if the evidence is clear
            if abs(zt) >= 0.75:
                params[c.name] = CueParams(int(np.sign(zt)), c.weight * float(np.clip(1 + 0.5 * abs(zt), 0.25, 2)), zt)
            else:
                params[c.name] = CueParams(+1, c.weight * 0.25, zt)
        else:
            s = c.sign * zt
            params[c.name] = CueParams(c.sign, c.weight * float(np.clip(1 + 0.5 * s, 0.25, 2.0)), s)
    return params


def score_block(epochs: Sequence[Epoch], params: Dict[str, CueParams], block: str = "",
                known_item: Optional[str] = None, n_perm: int = 4000) -> BlockResult:
    with warnings.catch_warnings():         # all-missing columns are expected (no sensor, item never valid)
        warnings.simplefilter("ignore", RuntimeWarning)
        return _score_block(epochs, params, block, known_item, n_perm)


def _score_block(epochs: Sequence[Epoch], params: Dict[str, CueParams], block: str,
                 known_item: Optional[str], n_perm: int) -> BlockResult:
    comb, items, series, per_cue = combined_matrix(epochs, params)
    if comb is None or len(items) < 3:
        return BlockResult(block, {}, {}, 0, None, None, None, notes=["need at least 3 items"])
    means = np.nanmean(comb, axis=0) if comb.size else np.array([])
    item_scores = {it: float(v) for it, v in zip(items, means) if not math.isnan(v)}
    cue_z = {it: {name: (None if np.all(np.isnan(z[:, j])) else float(np.nanmean(z[:, j])))
                  for name, z in per_cue.items()} for j, it in enumerate(items)}
    n_series = int(np.sum(np.sum(~np.isnan(comb), axis=1) >= 3))
    res = BlockResult(block, item_scores, cue_z, n_series, None, None, None, known_item=known_item)
    res.series_scores = {int(s): {it: float(comb[r, j]) for j, it in enumerate(items) if not math.isnan(comb[r, j])}
                         for r, s in enumerate(series)}
    if len(item_scores) < 3 or n_series < 2:
        res.notes.append("not enough valid series to score (need >= 2 with >= 3 items)")
        return res
    ranking = res.ranking()
    res.top_item, res.lead = ranking[0][0], ranking[0][1] - ranking[1][1]
    known_col = items.index(known_item) if known_item in items else None
    res.p_top, res.p_known = permutation_p(comb, n_perm, known_col)
    if known_item in item_scores:
        res.known_rank = [i for i, _ in ranking].index(known_item) + 1
    p = res.p_known if known_item else res.p_top
    res.label = "strong" if p is not None and p < 0.05 else "suggestive" if p is not None and p < 0.20 else "inconclusive"
    return res
