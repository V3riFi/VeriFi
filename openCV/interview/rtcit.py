"""Reaction-time Concealed Information Test (RT-CIT), the primary VeriFi measure.

Why (see METHOD.md): the face cues we scored are "tells" with no validated link to recognition.
The RT-CIT measures something with a known mechanism: when you recognize an item but have to
press "NO, not familiar", the conflict slows you down. Meta-analytic d ~ 1.3, AUC ~ 0.82
(Suchotzki et al., 2017; Meijer et al., 2016). It needs only a keyboard.

Design (after Lukacs et al., 2017, "enhanced" RT-CIT with fillers):
  * one or more GROUPS (hidden details: name, birthday, ...). Each group has 5 candidates
    (in known-answer mode one is the true detail, label "TRUE") -> NO, and 1 target the subject
    is told in advance -> YES. Several groups in one block = the multiple-probe protocol: each
    detail adds evidence, which is the biggest single gain in power.
  * fillers: "FAMILIAR"/"MINE"/"RECOGNIZED" -> YES; "UNFAMILIAR"/"OTHER"/... -> NO. They keep
    the YES/NO meaning about *recognition*, which enlarges the probe effect.
  * response deadline 0.8 s, "WRONG"/"TOO SLOW" feedback, blank 0.25-0.75 s between trials.
Scoring: a series = one group in one cycle, so the true detail is only ever compared with its own
group's alternatives (names with names, dates with dates). Log RT is z-scored within series and
fed to the shared permutation scorer (interview.scoring). Only correct responses >= 150 ms count.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .scoring import BlockResult, Cue, Epoch, default_params, score_block

YES, NO = "e", "i"
FAMILIAR_FILLERS = ["FAMILIAR", "MINE", "RECOGNIZED"]
UNFAMILIAR_FILLERS = ["UNFAMILIAR", "OTHER", "FOREIGN", "THEIRS", "RANDOM", "UNKNOWN"]
RT_CUES = [Cue("log_rt", +1, 1.0)]
MIN_RT = 0.15


@dataclass
class RtGroup:
    name: str
    candidates: List[Tuple[str, str]]      # (label stored in logs, text shown on screen)
    target: Tuple[str, str]                # the YES item, told to the subject in advance
    gid: Optional[int] = None              # group id written to the log; default = position in the block.
                                           # Set it when one test is split over several blocks.


@dataclass
class RtBlock:
    name: str
    groups: List[RtGroup]
    cycles: int = 18
    deadline_s: float = 0.8
    known_label: Optional[str] = None      # set for a known-answer block
    category: str = "item"                 # used in instructions: "card", "date", "detail"
    n_yes_fillers: int = 1                 # per cycle
    n_no_fillers: int = 2
    hide: str = ""                         # what the subject hides, for the instructions (default: own details)

    @classmethod
    def single(cls, name: str, candidates, target, **kw) -> "RtBlock":
        return cls(name, [RtGroup(name, list(candidates), target)], **kw)

    @property
    def targets(self) -> List[str]:
        return [g.target[1] for g in self.groups]


@dataclass
class RtTrial:
    block: str
    cycle: int
    label: str
    text: str
    role: str                              # candidate | target | filler_yes | filler_no
    correct_key: str
    group: int = 0                         # -1 for fillers
    onset: Optional[float] = None          # time.time() when drawn (same clock as vision / backend)
    rt: Optional[float] = None             # seconds
    key: Optional[str] = None
    outcome: str = ""                      # correct | wrong | slow
    gate: str = ""                         # set by the camera check (vision/offline.py): why the trial
                                           # isn't usable (eyes closed / looked away at onset). Recomputed
                                           # from the frame CSV, so not saved in the trial log.

    def log_row(self) -> Dict[str, object]:
        """What gets saved: labels only, never the on-screen text (it can be personal)."""
        return {"block": self.block, "cycle": self.cycle, "group": self.group, "label": self.label,
                "role": self.role, "correct_key": self.correct_key, "onset": self.onset,
                "rt_ms": None if self.rt is None else round(self.rt * 1000, 1), "key": self.key,
                "outcome": self.outcome}


def load_trials(path: str) -> List[RtTrial]:
    """Read a saved trial log back (for re-scoring / fusion later)."""
    import csv
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = RtTrial(r["block"], int(r["cycle"]), r["label"], r["label"], r["role"], r["correct_key"],
                        int(r.get("group") or 0))
            t.onset = float(r["onset"]) if r.get("onset") else None
            t.rt = float(r["rt_ms"]) / 1000 if r.get("rt_ms") else None
            t.key, t.outcome = r.get("key") or None, r.get("outcome", "")
            out.append(t)
    return out


def _gid(grp: RtGroup, pos: int) -> int:
    return pos if grp.gid is None else grp.gid


def build_trials(b: RtBlock, rng: random.Random) -> List[RtTrial]:
    """Every cycle shows each candidate and target of every group once, plus fillers, shuffled.
    The same item never appears twice in a row (also across cycle boundaries)."""
    out: List[RtTrial] = []
    last = None
    for c in range(b.cycles):
        cyc: List[RtTrial] = []
        for g, grp in enumerate(b.groups):
            g = _gid(grp, g)
            cyc += [RtTrial(b.name, c, lab, txt, "candidate", NO, g) for lab, txt in grp.candidates]
            cyc.append(RtTrial(b.name, c, grp.target[0], grp.target[1], "target", YES, g))
        for k in range(b.n_yes_fillers):
            w = FAMILIAR_FILLERS[(c * b.n_yes_fillers + k) % len(FAMILIAR_FILLERS)]
            cyc.append(RtTrial(b.name, c, w, w, "filler_yes", YES, -1))
        for k in range(b.n_no_fillers):
            w = UNFAMILIAR_FILLERS[(c * b.n_no_fillers + k) % len(UNFAMILIAR_FILLERS)]
            cyc.append(RtTrial(b.name, c, w, w, "filler_no", NO, -1))
        for _ in range(200):
            rng.shuffle(cyc)
            if (cyc[0].group, cyc[0].label) != last:
                break
        out += cyc
        last = (cyc[-1].group, cyc[-1].label)
    return out


def practice_fillers(b: RtBlock, rng: random.Random) -> List[RtTrial]:
    """Stage 1: only the filler words, so the rule 'familiar-meaning word -> YES' is learned first."""
    t = [RtTrial(b.name + "_p1", 0, w, w, "filler_yes", YES, -1) for w in FAMILIAR_FILLERS * 2]
    t += [RtTrial(b.name + "_p1", 0, w, w, "filler_no", NO, -1) for w in UNFAMILIAR_FILLERS]
    rng.shuffle(t)
    return t


def practice_targets(b: RtBlock, rng: random.Random) -> List[RtTrial]:
    """Stage 2: the YES words to remember, mixed with that detail's other options (all equally often)."""
    t = []
    for g, grp in enumerate(b.groups):
        g = _gid(grp, g)
        t += [RtTrial(b.name + "_p2", 0, grp.target[0], grp.target[1], "target", YES, g) for _ in range(2)]
        t += [RtTrial(b.name + "_p2", 0, lab, txt, "candidate", NO, g) for lab, txt in rng.sample(grp.candidates, 2)]
    rng.shuffle(t)
    return t


def practice_trials(b: RtBlock, rng: random.Random, n: int = 24) -> List[RtTrial]:
    p = RtBlock(b.name + "_practice", b.groups, cycles=2, n_yes_fillers=b.n_yes_fillers,
                n_no_fillers=b.n_no_fillers)
    return build_trials(p, rng)[:n]


@dataclass
class RtResult:
    block: str
    score: BlockResult
    mean_rt_ms: Dict[str, float]           # candidate label -> mean correct RT (single-group blocks)
    error_rate: Dict[str, float]           # candidate label -> share wrong or too slow
    target_hit_rate: float
    candidate_accuracy: float
    filler_accuracy: float
    valid: bool
    d_cit: Optional[float]                 # (RT focus - RT others) / SD others, centred per group
    groups: Dict[int, dict] = field(default_factory=dict)   # group -> {rank, p, top} (known mode)
    slowing_ms: Optional[float] = None     # focus item minus same-group alternatives, mean ms
    notes: List[str] = field(default_factory=list)
    engaged: bool = True                   # YES words hit >= 50% (see interview/verdict.py)


def _ok(t: RtTrial) -> bool:
    return t.outcome == "correct" and t.rt is not None and t.rt >= MIN_RT and not t.gate


def to_epochs(trials: Sequence[RtTrial]) -> List[Epoch]:
    cand = [t for t in trials if t.role == "candidate"]
    n_groups = max((t.group for t in cand), default=0) + 1
    return [Epoch(t.block, t.cycle * n_groups + t.group, t.label, True,
                  {"log_rt": math.log(t.rt * 1000) if _ok(t) else None}) for t in cand]


def score_rt(trials: Sequence[RtTrial], known_label: Optional[str] = None, n_perm: int = 4000) -> RtResult:
    trials = [t for t in trials if t.outcome]
    block = trials[0].block if trials else ""
    cand = [t for t in trials if t.role == "candidate"]
    tgt = [t for t in trials if t.role == "target"]
    fil = [t for t in trials if t.role.startswith("filler")]
    acc = lambda ts: sum(t.outcome == "correct" for t in ts) / len(ts) if ts else 0.0
    eps = to_epochs(trials)
    res = score_block(eps, default_params(RT_CUES), block, known_item=known_label, n_perm=n_perm)
    labels = sorted({t.label for t in cand})
    group_ids = sorted({t.group for t in cand})
    good = {lab: [t.rt * 1000 for t in cand if t.label == lab and _ok(t)] for lab in labels}
    mean_rt = {lab: sum(v) / len(v) for lab, v in good.items() if v} if len(group_ids) == 1 else {}
    err = {lab: sum(t.outcome != "correct" for t in cand if t.label == lab) /
           max(1, sum(t.label == lab for t in cand)) for lab in labels}
    r = RtResult(block, res, mean_rt, err, acc(tgt), acc(cand), acc(fil), True, None)
    # Task checks. The literature excludes subjects with < 50% YES hits. Here it's one-sided
    # (interview/verdict.py): missing YES words can hide an effect but can't create one, so it only
    # blocks a "no evidence" verdict.
    if r.target_hit_rate < 0.5:
        r.engaged = False
        r.notes.append(f"YES words hit {r.target_hit_rate:.0%} < 50%: a 'no evidence' result can't be trusted "
                       "(a 'recognizes' still can)")
    if r.candidate_accuracy < 0.75:
        r.valid = False
        r.notes.append(f"accuracy on the candidates {r.candidate_accuracy:.0%} < 75%")
    # per-group breakdown (which hidden details stood out)
    if known_label and len(group_ids) > 1:
        n_groups = max(group_ids) + 1
        for g in group_ids:
            sub = [e for e in eps if e.series % n_groups == g]
            gr = score_block(sub, default_params(RT_CUES), block, known_item=known_label, n_perm=min(n_perm, 1000))
            r.groups[g] = {"rank": gr.known_rank, "p": gr.p_known, "top": gr.top_item}
    # d_CIT on RTs centred within each group (names and dates have different base RTs)
    focus = known_label or res.top_item
    if focus:
        resid_f, resid_o = [], []
        for g in group_ids:
            gt = [t for t in cand if t.group == g and _ok(t)]
            others = [t.rt * 1000 for t in gt if t.label != focus]
            if not others:
                continue
            m = sum(others) / len(others)
            resid_f += [t.rt * 1000 - m for t in gt if t.label == focus]
            resid_o += [x - m for x in others]
        if resid_f and len(resid_o) > 2:
            r.slowing_ms = sum(resid_f) / len(resid_f)
            sd = math.sqrt(sum(x * x for x in resid_o) / (len(resid_o) - 1))
            r.d_cit = (sum(resid_f) / len(resid_f)) / sd if sd > 0 else None
    return r
