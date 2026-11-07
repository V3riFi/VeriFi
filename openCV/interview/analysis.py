"""Score a saved session from its files. Everything the live run prints can be recomputed here,
so a session can be re-analysed later (new sensor log, fixed bug) with the same code.

Files of a session <stem> (labels only, never the shown words):
    <stem>.trials.csv    keyboard (RT) round(s)             interview/rtcit.py
    <stem>.slow.csv      sensor-round onsets + key presses  interview/fusion.py
    <stem>.frames.csv    camera, one row per frame          vision/output.py
    <stem>.physio.csv    sensors, t,gsr,hr,resp[,ppg]       sensors/bridge.py (or the backend's logger)
    <stem>.vision.csv    face cues per sensor-round word    (exploratory; only used if they earn weight)
Blocks: "identity" / "identity_slow" = known answers (own details), "case" / "case_slow" = the test.
"""
from __future__ import annotations

import csv
import math
import pathlib
from typing import Dict, Optional, Sequence

from .fusion import SLOW_CUES, Physio, default_params, load_slow, score_slow, slow_epochs
from .profile import Profile, build_profile, score_with, variants, without_vision
from .rtcit import RtResult, RtTrial, load_trials, score_rt
from .verdict import verdict


def paths(stem: str) -> Dict[str, str]:
    return {k: f"{stem}.{k}.csv" for k in ("trials", "slow", "frames", "physio", "vision")} | \
        {"results": f"{stem}.results.json"}


def _exists(p: Optional[str]) -> bool:
    return bool(p) and pathlib.Path(p).exists()


# ---------------------------------------------------------------------- vision cue file
def save_vision_csv(path: str, vision_epochs: Dict[tuple, Dict[str, Optional[float]]]) -> None:
    if not vision_epochs:
        return
    cues = sorted({k for f in vision_epochs.values() for k in f})
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["block", "series", "label", *cues])
        for (b, s, lab), feats in vision_epochs.items():
            w.writerow([b, s, lab] + ["" if feats.get(c) is None else f"{feats[c]:.6g}" for c in cues])


def load_vision_csv(path: str) -> Dict[tuple, Dict[str, Optional[float]]]:
    out = {}
    if not _exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            feats = {k: (float(v) if v not in ("", None) else None) for k, v in r.items()
                     if k not in ("block", "series", "label")}
            out[(r["block"], int(r["series"]), r["label"])] = feats
    return out


# ---------------------------------------------------------------------- camera gating
def gate_trials(trials: Sequence[RtTrial], frames) -> Dict[str, object]:
    """Mark RT trials the camera says weren't seen (in place). Returns a summary."""
    if frames is None:
        return {"applied": False}
    from vision.offline import countermeasure_check, gate_rt, summary
    for t in trials:
        t.gate = ""
    if not trials:
        return {"applied": False}
    if frames.face_rate < 0.7:
        return {"applied": False, "summary": f"not applied: camera saw the face only {frames.face_rate:.0%} of the time"}
    g = gate_rt(frames, [t.onset for t in trials])
    cand = [i for i, t in enumerate(trials) if t.role == "candidate"]
    cm = countermeasure_check([trials[i].label for i in cand], {k: v for k, v in enumerate(cand) if v in g})
    if len(g) > MAX_GATED * len(trials):
        # far more than blinks / glances can explain: the camera or the seating is off, not the trials
        return {"applied": False, "countermeasure": cm,
                "summary": f"NOT applied: the camera check flagged {len(g)} of {len(trials)} trials ({summary(g, len(trials))}); "
                           "too many to be real artifacts. Check the camera faces the subject."}
    for i, why in g.items():
        trials[i].gate = why
    return {"applied": True, "summary": summary(g, len(trials)), "countermeasure": cm}


MAX_GATED = 0.25   # a camera check that would drop more than this is distrusted, not obeyed


def gate_rows(rows: Sequence[dict], frames) -> Dict[str, object]:
    if frames is None or not rows:
        return {"applied": False, "gated": {}}
    from vision.offline import countermeasure_check, gate_physio, summary
    if frames.face_rate < 0.7:
        return {"applied": False, "gated": {},
                "summary": f"not applied: camera saw the face only {frames.face_rate:.0%} of the time"}
    g = gate_physio(frames, [r.get("onset") for r in rows])
    test = [i for i, r in enumerate(rows) if r.get("kind") == "test"]
    cm = countermeasure_check([rows[i]["label"] for i in test], {k: g[i] for k, i in enumerate(test) if i in g})
    if len(g) > MAX_GATED * len(rows):
        return {"applied": False, "gated": {}, "countermeasure": cm,
                "summary": f"NOT applied: the camera check flagged {len(g)} of {len(rows)} words ({summary(g, len(rows))}); "
                           "too many to be real artifacts. Check the camera faces the subject."}
    return {"applied": True, "gated": g, "summary": summary(g, len(rows)), "countermeasure": cm}


# ---------------------------------------------------------------------- per-detail helpers
def rt_by_detail(r: RtResult) -> Dict[int, dict]:
    return r.groups


def slow_by_detail(rows: Sequence[dict], physio, gated, vision, params, n_perm: int = 1000) -> Dict[int, dict]:
    out = {}
    for g in sorted({r["group"] for r in rows}):
        idx = [i for i, r in enumerate(rows) if r["group"] == g]
        sub = [rows[i] for i in idx]
        sub_g = {k: gated[i] for k, i in enumerate(idx) if i in gated}
        res = score_with(slow_epochs(sub, physio, sub_g, vision), params, "detail", n_perm)
        out[g] = {"rank": res.known_rank, "p": res.p_known}
    return out


def _rt_p(r: Optional[RtResult]) -> Optional[float]:
    return r.score.p_known if (r is not None and r.valid) else None


# ---------------------------------------------------------------------- whole session
def analyse(stem: str, test_rt: str = "case", test_slow: str = "case_slow", cal_rt: str = "identity",
            cal_slow: str = "identity_slow", physio_path: Optional[str] = None, gsr_inverted: bool = False,
            n_perm: int = 4000) -> dict:
    """Calibrate on the known-answer blocks, score the test blocks, every variant + the verdict."""
    p = paths(stem)
    trials = load_trials(p["trials"]) if _exists(p["trials"]) else []
    rows = load_slow(p["slow"]) if _exists(p["slow"]) else []
    frames = None
    if _exists(p["frames"]):
        from vision.offline import load_frames
        frames = load_frames(p["frames"])
    physio_path = physio_path or (p["physio"] if _exists(p["physio"]) else None)
    physio = Physio.from_csv(physio_path, gsr_inverted) if physio_path else None
    out: dict = {"stem": stem, "physio_file": physio_path, "simulated_sensors": bool(physio and physio.simulated),
                 "notes": []}
    if physio is not None and physio.simulated:
        out["notes"].append("SIMULATED sensors: physiology left out of every score")
        physio = None
    if physio is not None and len(physio.t) < 10:
        out["notes"].append("sensor file has (almost) no samples: physiology left out")
        physio = None
    vision = load_vision_csv(p["vision"])

    # --- keyboard rounds
    t_test = [t for t in trials if t.block == test_rt]
    t_cal = [t for t in trials if t.block == cal_rt]
    rt_raw = score_rt(t_test, known_label="TRUE", n_perm=n_perm) if t_test else None
    out["rt_gating"] = gate_trials(t_test + t_cal, frames)
    rt = score_rt(t_test, known_label="TRUE", n_perm=n_perm) if t_test else None
    rt_cal = score_rt(t_cal, known_label="TRUE", n_perm=n_perm) if t_cal else None

    # --- sensor rounds
    r_cal = [r for r in rows if r.get("block") == cal_slow]
    r_test = [r for r in rows if r.get("block") == test_slow]
    g_cal, g_test = gate_rows(r_cal, frames), gate_rows(r_test, frames)
    profile: Optional[Profile] = None
    if r_cal:
        profile = build_profile(slow_epochs(r_cal, physio, g_cal["gated"], vision), _rt_p(rt_cal))
    slow_raw = score_slow(r_test, physio, n_perm=n_perm) if r_test else None
    slow_gated = score_slow(r_test, physio, n_perm=n_perm, gated=g_test["gated"]) if r_test else None
    slow_prof = slow_prof_nv = None
    if r_test and profile:
        eps = slow_epochs(r_test, physio, g_test["gated"], vision)
        slow_prof = score_with(eps, profile.params, test_slow, n_perm)
        slow_prof_nv = score_with(eps, without_vision(profile.params), test_slow, n_perm)
    pk = lambda b: b.p_known if b is not None else None
    var = variants(_rt_p(rt_raw), _rt_p(rt), pk(slow_raw), pk(slow_gated), pk(slow_prof) if profile else pk(slow_gated),
                   pk(slow_prof_nv) if profile else pk(slow_gated), profile)
    head = var["full"]
    engaged = rt.engaged if (rt is not None and rt.valid) else True
    # INVALID only when the keyboard task wasn't followed and nothing else could be scored;
    # too little data with the task followed is "NO RESULT"
    task_ok = head is not None or rt is None or rt.valid
    out.update({
        "rt": None if rt is None else {"p": rt.score.p_known, "p_ungated": _rt_p(rt_raw), "valid": rt.valid,
                                       "engaged": rt.engaged, "slowing_ms": rt.slowing_ms, "d_cit": rt.d_cit,
                                       "yes_hit": rt.target_hit_rate, "no_acc": rt.candidate_accuracy,
                                       "per_detail": rt.groups, "notes": rt.notes + rt.score.notes},
        "rt_calibration": None if rt_cal is None else {"p": rt_cal.score.p_known, "valid": rt_cal.valid,
                                                       "slowing_ms": rt_cal.slowing_ms, "per_detail": rt_cal.groups},
        "sensor_round": None if slow_gated is None else {
            "p_default": slow_gated.p_known, "p_profile": pk(slow_prof), "rank": (slow_prof or slow_gated).known_rank,
            "cue_z": (slow_prof or slow_gated).cue_z.get("TRUE", {}), "gating": {k: v for k, v in g_test.items()
                                                                                  if k != "gated"},
            "per_detail": slow_by_detail(r_test, physio, g_test["gated"], vision,
                                         profile.params if profile else default_params(SLOW_CUES)),
            "notes": (slow_prof or slow_gated).notes},
        "calibration_sensor_round": None if not r_cal else {
            "gating": {k: v for k, v in g_cal.items() if k != "gated"},
            "per_detail": slow_by_detail(r_cal, physio, g_cal["gated"], vision, default_params(SLOW_CUES))},
        "profile": profile.to_json() if profile else None,
        "variants": var, "p": head,
        "verdict": verdict(head, task_ok, engaged),
        "channels_used": [k for k, v in {"reaction_time": _rt_p(rt), "sensor_round": pk(slow_prof or slow_gated)}.items()
                          if v is not None],
        "has_physiology": physio is not None,
    })
    return out


INCOMPLETE = "INCOMPLETE (stopped early: the scores above are partial, not a result)"


def mark_progress(res: dict, progress: Optional[dict] = None, complete: Optional[bool] = None) -> dict:
    """Record how much of the session was shown, e.g. {"keyboard": [75, 420], "sensor": [0, 0]}
    (words shown, words planned). A session stopped early (Esc) gets the INCOMPLETE verdict, so a
    partial run is never counted as a 'no evidence' result (examples/evaluate.py skips it).
    `complete=False` marks an older results file that has no counts."""
    short = []
    if progress:
        res["progress"] = progress
        short = [f"{k} {d} of {n} words" for k, (d, n) in progress.items() if n and d < n]
    res["complete"] = not short and complete is not False
    if not res["complete"]:
        res["notes"].append("stopped early" + (": only " + ", ".join(short) + " shown" if short else ""))
        res["verdict"] = INCOMPLETE
    return res


def fmt(v, spec: str) -> str:
    return "--" if v is None or (isinstance(v, float) and math.isnan(v)) else format(v, spec)


def print_report(res: dict, labels: Sequence[str], cal_labels: Sequence[str] = (), title: str = "") -> None:
    print("\n" + "=" * 78 + f"\n{title or 'VeriFi session'}\n" + "=" * 78)
    for n in res["notes"]:
        print("  !! " + n)
    pc = res.get("calibration_sensor_round")
    prof = res.get("profile")
    if prof:
        print("\nCalibration (your own details, known answers): how you respond")
        if pc:
            for g, lab in enumerate(cal_labels):
                d = pc["per_detail"].get(g, {})
                print(f"  sensor round  {lab:<20} rank {fmt(d.get('rank'), 'd')} of 5   p = {fmt(d.get('p'), '.3f')}")
        rc = res.get("rt_calibration")
        if rc:
            print(f"  keyboard round: own details {fmt(rc['slowing_ms'], '+.0f')} ms slower, p = {fmt(rc['p'], '.3f')}")
        z = ", ".join(f"{k} {fmt(v, '+.2f')}" for k, v in prof["cue_z"].items() if v is not None)
        print(f"  cue response to known details (z): {z or 'no data'}")
        print(f"  responds through: {', '.join(prof['responders']) or 'nothing clear (weights stay near defaults)'}")
        print(f"  channel weights for the test: {prof['channel_weights']}")
        for n in prof["notes"]:
            print("  - " + n)
    print("\nTest details")
    rt, sr = res.get("rt"), res.get("sensor_round")
    for g, lab in enumerate(labels):
        a = (rt or {}).get("per_detail", {}).get(g, {})
        b = (sr or {}).get("per_detail", {}).get(g, {})
        print(f"  {lab:<20} keyboard rank {fmt(a.get('rank'), 'd')} p {fmt(a.get('p'), '.3f')}   |   "
              f"sensor round rank {fmt(b.get('rank'), 'd')} p {fmt(b.get('p'), '.3f')}")
    if rt:
        print(f"\n  keyboard round: {fmt(rt['slowing_ms'], '+.0f')} ms slower on the tested details, p = {fmt(rt['p'], '.3f')}"
              f"   (YES words {rt['yes_hit']:.0%}, NO items {rt['no_acc']:.0%})" + ("" if rt["valid"] else "  INVALID"))
        for n in rt["notes"]:
            print("    ! " + n)
    g = res.get("rt_gating") or {}
    if g.get("summary"):
        print(f"  camera check, keyboard trials: {g['summary']}")
        if g.get("countermeasure"):
            print("    !! " + g["countermeasure"])
    if sr:
        print(f"  sensor round: p = {fmt(sr['p_profile'] if sr['p_profile'] is not None else sr['p_default'], '.3f')}"
              f"  (default weights p = {fmt(sr['p_default'], '.3f')})" +
              ("" if res["has_physiology"] else "   [key presses only: no sensor data]"))
        z = ", ".join(f"{k} {fmt(v, '+.2f')}" for k, v in sr["cue_z"].items() if v is not None)
        print(f"    tested-detail response (z): {z}")
        sg = sr["gating"]
        if sg.get("summary"):
            print(f"    camera check, sensor-round words: {sg['summary']}")
            if sg.get("countermeasure"):
                print("    !! " + sg["countermeasure"])
    print("\n  Every variant (p): " + "   ".join(f"{k} {fmt(v, '.3f')}" for k, v in res["variants"].items()))
    print(f"\n  Channels: {', '.join(res['channels_used']) or 'none'}    combined p = {fmt(res['p'], '.3f')}")
    print(f"  VERDICT: {res['verdict']}")
    print("\n  A reaction means recognition of the details, not lying in general.")
