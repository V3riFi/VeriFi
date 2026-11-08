"""VeriFi full session: calibration on your own details, then the mock-crime test, every channel.

    .venv\\Scripts\\python.exe examples\\make_case.py p01                      (before the subject arrives)
    run_verifi.bat --case data\\cases\\p01.json --sensor COM5
    run_verifi.bat --case data\\cases\\p01.json --sensor COM5 --quick      (~15 min instead of ~25-30)
    run_verifi.bat --case data\\cases\\p01.json --physio X.csv             (sensors logged by the backend)

Pitch mode: do the calibration EARLIER with run_identity.bat --sensor COM5 on the SAME person (same
sensors, same electrode spots), then at the pitch only parts 2 and 3 run (~9 min with --quick):
    run_verifi.bat --case data\\cases\\p01.json --sensor COM5 --quick --calibration data\\identity_7

Order (each part explains itself on screen):
  1. sensor round, YOUR details (known answers): learns how this person responds  ~5.5 min
  2. sensor round, the CASE details (the test)                                   ~6 min (quick ~4)
  3. keyboard round, the CASE details, in parts of 2 details                     ~6.5 min (quick ~4.5)
  4. keyboard round, YOUR details (calibrates the keyboard channel; skipped with --quick)  ~6 min
Then: the profile, every channel, every variant, ONE pre-registered verdict (the "full" variant),
and only after that the condition from the case file (guilty / innocent).
With --calibration, parts 1 and 4 are skipped: the profile is learned from that earlier run (its own
sensor log and camera) and fixed before the case is scored.
Files go to data\\sessions\\<case>\\session.* (labels only; your details are never saved).
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from identity_test import prepare_plan  # noqa: E402
from interview import regions  # noqa: E402
from interview.analysis import analyse, mark_progress, print_report, save_vision_csv  # noqa: E402
from interview.case import case_plan, load_case  # noqa: E402
from interview.fusion import slow_items  # noqa: E402
from interview.identity import CATEGORIES, DEFAULT_CATEGORIES  # noqa: E402
from interview.verdict import code as verdict_code  # noqa: E402
from rt_cit_test import progress, run_session  # noqa: E402
from rt_cit_test import stem as trials_stem  # noqa: E402

CASE_HIDE = "anything you may know about the case"
SUFFIXES = (".trials.csv", ".results.json", ".slow.csv", ".physio.csv", ".frames.csv", ".vision.csv")


def calibration_stem(arg: str) -> pathlib.Path:
    """data\\identity_7, data\\identity_7.trials.csv or data\\identity_7.results.json -> data\\identity_7."""
    p = pathlib.Path(arg)
    for suf in SUFFIXES:
        if p.name.endswith(suf):
            return p.with_name(p.name[: -len(suf)])
    return trials_stem(p)


def check_calibration(stem: pathlib.Path) -> list:
    """Refuse a run that can't calibrate (missing, control, stopped early); return its detail categories."""
    if not pathlib.Path(f"{stem}.trials.csv").exists():
        sys.exit(f"--calibration: {stem}.trials.csv not found (give the identity run, e.g. data\\identity_7)")
    res_path = pathlib.Path(f"{stem}.results.json")
    res = json.loads(res_path.read_text(encoding="utf-8")) if res_path.exists() else {}
    if res.get("mode") == "control":
        sys.exit("--calibration: that is a --control run (a random identity); use a run with the REAL details")
    if res.get("complete") is False or "INCOMPLETE" in str(res.get("verdict", "")):
        sys.exit("--calibration: that run was stopped early; use a complete one")
    if not pathlib.Path(f"{stem}.slow.csv").exists():
        print("!! The calibration run has no sensor round (it wasn't run with --sensor). The per-person\n"
              "   profile can't be learned from it, so default weights are used.")
    return list(res.get("categories") or DEFAULT_CATEGORIES)


def session_dir(base: pathlib.Path, case_id: str) -> pathlib.Path:
    d = base / case_id
    n = 2
    while d.exists():
        d = base / f"{case_id}_{n}"
        n += 1
    d.mkdir(parents=True)
    return d


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--case", required=True, help="case file from make_case.py, e.g. data\\cases\\p01.json")
    p.add_argument("--quick", action="store_true", help="~15 min: fewer passes, no keyboard calibration round")
    p.add_argument("--details", default=",".join(DEFAULT_CATEGORIES), help="own details for the calibration")
    p.add_argument("--name-style", choices=list(regions.STYLES))
    p.add_argument("--sensor", help="COM5 / COM5@9600 / udp:0.0.0.0:5008 / sim (records <session>.physio.csv)")
    p.add_argument("--sensor-columns", default="gsr,hr,resp")
    p.add_argument("--physio", help="OR a sensor CSV logged by another program during the session")
    p.add_argument("--gsr-inverted", action="store_true", help="GSR value goes DOWN with sweating (see sensor check)")
    p.add_argument("--no-camera", action="store_true")
    p.add_argument("--source", default="0")
    p.add_argument("--udp", help="send each sensor-round onset to the backend, e.g. 127.0.0.1:5007")
    p.add_argument("--calibration", help="an EARLIER identity run of the SAME person with sensors "
                                         "(run_identity.bat --sensor COM5), e.g. data\\identity_7: parts 1 and 4 "
                                         "are skipped here")
    p.add_argument("--out", default="data/sessions")
    a = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    print(__doc__)
    case = load_case(a.case)
    cats = [c.strip() for c in a.details.split(",") if c.strip()]
    bad = [c for c in cats if c not in CATEGORIES]
    if bad:
        sys.exit(f"unknown details {bad}; choose from {list(CATEGORIES)}")
    if not (a.sensor or a.physio):
        print("!! No sensors (--sensor / --physio): the sensor rounds still score the key presses, "
              "but without physiology.")
    if a.sensor and a.sensor.lower() == "sim":
        print("!! --sensor sim: SIMULATED sensors. Software test only; physiology is left out of the scores.")

    rng = random.Random()
    cal = calibration_stem(a.calibration) if a.calibration else None
    if cal:
        cal_cats = check_calibration(cal)
        plan_id = None
        print(f"\nCALIBRATION: from the earlier run {cal} (must be the same person, same sensors). "
              "Parts 1 and 4 are skipped.")
    else:
        print("\nCALIBRATION: your own details (the system knows these answers).")
        plan_id = prepare_plan(cats, False, rng, a.name_style)
        cal_cats = plan_id.cats
    plan_case = case_plan(case)
    folder = session_dir(pathlib.Path(a.out), case["id"])
    path = folder / "session.trials.csv"

    reps_test = 2 if a.quick else 3
    rounds = [] if cal else [
        {"items": slow_items(plan_id, rng, 2, block="identity_slow"), "title": "Part 1: sensor round, your details",
         "own": "your own details", "next": "part 2, the case details"}]
    rounds.append({"items": slow_items(plan_case, rng, reps_test, block="case_slow"),
                   "title": ("Part 1" if cal else "Part 2") + ": sensor round, the case",
                   "own": CASE_HIDE, "next": "the fast keyboard round"})
    blocks = plan_case.blocks(10 if a.quick else 14, name="case", hide=CASE_HIDE)
    if not a.quick and not cal:
        blocks += plan_id.blocks(10, name="identity")
    n_slow = sum(len(r["items"]) for r in rounds)
    print(f"\nSensor rounds: {n_slow} words (~{sum(i.total_s for r in rounds for i in r['items']) / 60 + 0.6:.0f} min), "
          f"then {len(blocks)} keyboard parts. Start the sensor logger now if the backend records it.")
    print("A window will open. Rest two fingers on E and I.")
    trials, quality, extras = run_session(blocks, rng, path, a.no_camera, a.source, rounds, a.udp, a.sensor,
                                          [c.strip() for c in a.sensor_columns.split(",")])
    stem = str(folder / "session")
    save_vision_csv(stem + ".vision.csv", extras.get("vision_epochs") or {})
    if not trials and not any(it.onset for it in extras["slow"]):
        print("Nothing completed.")
        return
    res = analyse(stem, physio_path=a.physio or extras.get("physio"), gsr_inverted=a.gsr_inverted,
                  cal_stem=str(cal) if cal else None)
    mark_progress(res, progress(blocks, trials, rounds))
    cal_labels = [plan_id.label(c) for c in cal_cats] if plan_id else [CATEGORIES[c].label for c in cal_cats]
    print_report(res, [plan_case.label(c) for c in plan_case.cats], cal_labels,
                 f"VeriFi session: case {case['id']}" + ("  (quick)" if a.quick else ""))
    cond = case.get("condition")
    vc = verdict_code(res["verdict"])
    print("\n" + "-" * 78)
    print(f"Case file says: {str(cond).upper()}   (verdict: {vc})")
    if cond in ("guilty", "innocent") and vc in ("recognizes", "no_evidence"):
        hit = (cond == "guilty") == (vc == "recognizes")
        print("  -> " + ("CORRECT" if hit else "WRONG") + " for this session. Accuracy only means something over "
              "many sessions: .venv\\Scripts\\python.exe examples\\evaluate.py")
    res.update({"case": case["id"], "condition": cond, "verdict_code": vc, "quick": a.quick, "quality": quality,
                "calibration_categories": cal_cats, "calibration_run": str(cal) if cal else None,
                "case_categories": plan_case.cats,
                "sensor_source": a.sensor or ("file" if a.physio else None), "gsr_inverted": a.gsr_inverted})
    out = pathlib.Path(stem + ".results.json")
    out.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved the session in {folder}")


if __name__ == "__main__":
    main()
