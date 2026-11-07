"""Subject Response Profile (SRP): learn how THIS person responds, from details we know they know.

People respond in different channels: some sweat (skin conductance), some hold their breath, some
mostly slow down on the key. A profile learned on the identity round (known answers: own name,
hometown...) re-weights the cues for the test round (the case details, answer unknown):
  * physiology + key-press RT cues: calibrate() with shrinkage toward the priors (weight x 0.25..2),
    so one short round can't overfit;
  * vision cues must EARN a weight: 0 unless the cue separated the known details by z >= 1
    (about 3 standard errors with 8 series), then 0.25 x min(z, 2);
  * channel weights for the final Stouffer combination: 1 + 0.25 z of each channel on the
    calibration round, clipped to 0.5..1.5.
Everything is fixed BEFORE the test round is scored, so the test's permutation p stays valid.

Per-trial fusion rule: cues measured on the same presentations (GSR, breathing, HR, key press,
face) are combined per presentation and tested once; only independent rounds (sensor round vs
keyboard round) are combined with Stouffer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Dict, List, Optional, Sequence

from .fusion import SLOW_CUES, stouffer
from .scoring import BlockResult, Cue, CueParams, Epoch, calibrate, default_params, score_block


def _vision_cues() -> List[Cue]:
    try:
        from vision.cit import VISION_CUES
        return list(VISION_CUES)
    except Exception:                       # vision deps missing: profile still works without it
        return []


def p_to_z(p: Optional[float]) -> Optional[float]:
    if p is None:
        return None
    p = min(max(p, 1e-6), 1 - 1e-6)
    return NormalDist().inv_cdf(1 - p)


def channel_weight(p_cal: Optional[float]) -> float:
    z = p_to_z(p_cal)
    return 1.0 if z is None else float(min(1.5, max(0.5, 1 + 0.25 * z)))


@dataclass
class Profile:
    params: Dict[str, CueParams]                 # cue weights for the test sensor round
    cue_z: Dict[str, Optional[float]]            # calibration z of the known details, per cue
    responders: Dict[str, bool]                  # cues that clearly fired on the known details
    slow_p_cal: Optional[float] = None
    rt_p_cal: Optional[float] = None
    weights: Dict[str, float] = field(default_factory=lambda: {"reaction_time": 1.0, "sensor_round": 1.0})
    notes: List[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"cue_weights": {k: {"sign": v.sign, "weight": round(v.weight, 3)} for k, v in self.params.items()},
                "cue_z": self.cue_z, "responders": [k for k, v in self.responders.items() if v],
                "slow_p_cal": self.slow_p_cal, "rt_p_cal": self.rt_p_cal, "channel_weights": self.weights,
                "notes": self.notes}


def build_profile(cal_eps: Sequence[Epoch], rt_p_cal: Optional[float] = None) -> Profile:
    """`cal_eps`: calibration sensor-round epochs (known answers labelled TRUE), with physiology,
    key press and (optionally) vision features."""
    params = calibrate(cal_eps, "TRUE", SLOW_CUES)
    cue_z = {k: v.calibration_z for k, v in params.items()}
    notes = []
    vc = _vision_cues()
    if vc and any(any(c.name in e.features for c in vc) for e in cal_eps):
        vp = calibrate(cal_eps, "TRUE", vc)
        for c in vc:
            z = vp[c.name].calibration_z
            cue_z[c.name] = z
            if z is None:
                continue
            if c.sign == 0:
                sign, zz = (1 if z > 0 else -1), abs(z)
            else:
                sign, zz = c.sign, z               # calibration_z is already signed for directional cues
            if zz >= 1.0:
                params[c.name] = CueParams(sign, 0.25 * min(zz, 2.0), z)
                notes.append(f"vision cue {c.name} earned weight {0.25 * min(zz, 2.0):.2f} (z = {z:+.2f})")
            else:
                params[c.name] = CueParams(sign, 0.0, z)
    responders = {k: (z is not None and z > 0.5) for k, z in cue_z.items()}
    # unbiased channel strength: default weights (not the fitted ones) on the calibration round
    cal = score_block(list(cal_eps), default_params(SLOW_CUES), "calibration", known_item="TRUE", n_perm=2000)
    w = {"sensor_round": channel_weight(cal.p_known), "reaction_time": channel_weight(rt_p_cal)}
    if cal.p_known is None:
        notes.append("calibration sensor round had too little data; default weights used")
    return Profile(params, cue_z, responders, cal.p_known, rt_p_cal, w, notes)


def combine_channels(ps: Dict[str, Optional[float]], weights: Dict[str, float]) -> Optional[float]:
    return stouffer(ps, weights)


def variants(rt_p_raw: Optional[float], rt_p_gated: Optional[float], slow_default_raw: Optional[float],
             slow_default_gated: Optional[float], slow_profile: Optional[float],
             slow_profile_novision: Optional[float], profile: Optional[Profile]) -> Dict[str, Optional[float]]:
    """The pre-registered comparison (evaluate.py): each step adds one idea. 'full' is the headline."""
    w = profile.weights if profile else {"reaction_time": 1.0, "sensor_round": 1.0}
    eq = {"reaction_time": 1.0, "sensor_round": 1.0}
    return {
        "rt_only": rt_p_raw,
        "rt_plus_sensors": stouffer({"reaction_time": rt_p_raw, "sensor_round": slow_default_raw}, eq),
        "plus_gating": stouffer({"reaction_time": rt_p_gated, "sensor_round": slow_default_gated}, eq),
        "plus_profile": stouffer({"reaction_time": rt_p_gated, "sensor_round": slow_profile_novision}, w),
        "full": stouffer({"reaction_time": rt_p_gated, "sensor_round": slow_profile}, w),
    }


def score_with(eps: Sequence[Epoch], params: Dict[str, CueParams], block: str, n_perm: int = 4000) -> BlockResult:
    return score_block(list(eps), params, block, known_item="TRUE", n_perm=n_perm)


def without_vision(params: Dict[str, CueParams]) -> Dict[str, CueParams]:
    names = {c.name for c in SLOW_CUES}
    return {k: v for k, v in params.items() if k in names}


def isfinite(x) -> bool:
    return x is not None and math.isfinite(x)
