"""VeriFi concealed-identity test (reaction-time CIT, several hidden details).

    run_identity.bat                 REAL: your own details (should be detected)
    run_identity.bat --control       CONTROL: a random identity of the same kind (should NOT be)
    run_identity.bat --quick         shorter (~6 min of words instead of ~8.5): 10 cycles instead of 14

Run both. A test is only convincing if it fires on the real details AND stays quiet on the control.
The details are typed in the console and only ever shown on screen. The saved files hold labels
(TRUE / F1-F4 / YES), never the values. In a control run your real details are typed too, but only
to pick the same kind of words and to make sure none of them appears.

Task: words flash one at a time. E = YES, I = NO, as FAST as you can.
  YES: the 2 words you're told to remember (per part), plus FAMILIAR / MINE / RECOGNIZED.
  NO: everything else, INCLUDING your own name, hometown and mother's name.
Two parts of ~3.5 min with practice before each. Esc = quit.
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from typing import Dict, List, Optional  # noqa: E402

from interview import regions  # noqa: E402
from interview.analysis import analyse, mark_progress, print_report, save_vision_csv  # noqa: E402
from interview.fusion import slow_items  # noqa: E402
from interview.identity import (CATEGORIES, DEFAULT_CATEGORIES, NAME_CATS, Hint, IdentityPlan,  # noqa: E402
                                auto_hint, control_hints, make_plan, normalize, question, random_identity,
                                summary_line)
from interview.verdict import code as verdict_code  # noqa: E402
from rt_cit_test import progress, run_session, save_results, unique_path  # noqa: E402
from rt_cit_test import stem as session_stem  # noqa: E402


# ---------------------------------------------------------------------- console
def ask_values(cats, control: bool = False) -> dict:
    if control:
        print("CONTROL run. Type YOUR real details: they're only used to pick the same kind of words\n"
              "(name style, province) and to make sure none of them shows up. Never shown, never saved.")
    else:
        print("Type the details (shown on screen only, never saved):")
    out = {}
    for c in cats:
        while True:
            v = input(f"  {CATEGORIES[c].prompt}: ").strip()
            if not v:
                continue
            try:
                normalize(c, v)
                out[c] = v
                break
            except ValueError as e:
                print("   ", e)
    return out


def _choose(prompt: str, options: List[str]) -> int:
    for i, o in enumerate(options, 1):
        print(f"      {i}  {o}")
    while True:
        a = input(f"    {prompt} [1-{len(options)}]: ").strip()
        if a.isdigit() and 1 <= int(a) <= len(options):
            return int(a) - 1


def _typed(cat: str, n: int) -> List[str]:
    while True:
        a = input(f"    Type {n} {CATEGORIES[cat].label} alternatives of the same kind (similar size / style), "
                  "comma-separated: ")
        xs = [x.strip() for x in a.split(",") if x.strip()]
        if len(xs) >= n:
            return xs
        print(f"    need {n}, got {len(xs)}")


def ask_hints(cats, values: dict, name_style: Optional[str] = None, control: bool = False) -> Dict[str, Hint]:
    """Look each value up in our lists; ask once about anything we can't place."""
    n_custom = 6 if control else 5          # a control needs one more: the control value itself
    styles = list(regions.STYLES)
    hints: Dict[str, Hint] = {}
    first_style = None
    for c in cats:
        h = auto_hint(c, values[c], first_style)
        if c in NAME_CATS and h.style is None:
            if name_style:
                h.style = name_style
            elif c != "first_name" and first_style:
                h.style = first_style           # unknown surname / mother's name: same style as the first name
        q = question(c, values[c], h)
        while q:
            label = CATEGORIES[c].label
            if q == "style":
                print(f"  I don't have that {label} in my lists. Which list fits it best?")
                i = _choose("choice", [regions.STYLES[s] for s in styles] + ["none of these: I'll type alternatives"])
                if i < len(styles):
                    h.style = styles[i]
                else:
                    h.custom = _typed(c, n_custom)
            elif q == "gender":
                i = _choose("the alternatives should be", ["male names", "female names", "either"])
                h.gender = "mfx"[i]
            elif q == "which":
                hits = regions.find_city(values[c])
                i = _choose("which one", [f"{x.name}, {regions.REGIONS.get(x.country, {}).get(x.region, '')} "
                                          f"{regions.COUNTRIES[x.country]}".replace("  ", " ") for x in hits])
                h.city = hits[i]
            elif q == "place":
                a = input(f"  I don't have that {label} in my list. Where is it? (province/state, country, "
                          "e.g. 'Alberta, Canada'; Enter = type alternatives yourself): ").strip()
                cc, reg = regions.parse_place(a) if a else (None, None)
                if cc:
                    h.country, h.region = cc, reg
                    print(f"    OK: alternatives from {summary_line(c, h).replace('places in ', '')}")
                else:
                    if a:
                        print("    I don't have towns for that place.")
                    h.custom = _typed(c, n_custom)
            q = question(c, values[c], h)
        if c == "first_name":
            first_style = h.style
        hints[c] = h
    return hints


def meaningful_check(plan: IdentityPlan, control: bool) -> list:
    print("\nBefore the test: do any of these words mean something personal to you?")
    print("(a friend's name, a town you lived in, a relative's name...). Such words would react too.")
    for c, items in plan.shown_before_test(control).items():
        print(f"  {plan.label(c):<20} " + ", ".join(items))
    ans = input("Type any that are personal, separated by commas, or just press Enter: ").strip()
    return [x.strip() for x in ans.split(",") if x.strip()]


def prepare_plan(cats, control: bool, rng: random.Random, name_style: Optional[str] = None) -> IdentityPlan:
    """Console part shared with verifi_session.py: details, hints, plan, personal-word check."""
    values = ask_values(cats, control)
    hints = ask_hints(cats, values, name_style, control)
    real = list(values.values())
    exclude: list = []

    def build() -> IdentityPlan:
        if control:
            cv = random_identity(rng, real + exclude, cats, hints)
            return make_plan(cv, rng, real + exclude, cats, control_hints(hints))
        return make_plan(values, rng, exclude, cats, hints)

    plan = build()
    for _ in range(4):
        flagged = meaningful_check(plan, control)
        if not flagged:
            break
        exclude += flagged
        plan = build()
        print("  OK, replaced those.")
    print("\nAlternatives chosen from: " + "; ".join(f"{CATEGORIES[c].label}: {summary_line(c, plan.hints[c])}"
                                                    for c in cats))
    return plan


# ---------------------------------------------------------------------- report
def score_identity(stem: str, plan: Optional[IdentityPlan], control: bool, physio: Optional[str],
                   gsr_inverted: bool, quality: Optional[dict] = None, shown: Optional[dict] = None) -> dict:
    """Keyboard round + sensor round (if any) on the identity details, from the saved files.
    `shown` = words shown vs. planned (rt_cit_test.progress); a run stopped early is INCOMPLETE."""
    res = analyse(stem, test_rt="identity", test_slow="identity_slow", cal_rt="-", cal_slow="-",
                  physio_path=physio, gsr_inverted=gsr_inverted)
    mark_progress(res, shown)
    cats = plan.cats if plan else res.get("categories", DEFAULT_CATEGORIES)
    labels = [plan.label(c) if plan else CATEGORIES[c].label for c in cats]
    mode = "CONTROL (random identity, never had)" if control else "REAL details"
    print_report(res, labels, title=f"VeriFi concealed-identity test: {mode}")
    if quality:
        print(f"  camera: face visible {quality.get('face', 0):.0%} of the time")
    if control:
        print("  Expected for a control: NO EVIDENCE. A 'recognizes' here is a false alarm (should happen <5%).")
    res.update({"mode": "control" if control else "real", "categories": cats, "quality": quality or {},
                "verdict_code": verdict_code(res["verdict"])})
    return res

# ---------------------------------------------------------------------- main
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--control", action="store_true", help="test a random identity the subject has never had")
    p.add_argument("--details", default=",".join(DEFAULT_CATEGORIES),
                   help=f"comma list from {list(CATEGORIES)} (default: {','.join(DEFAULT_CATEGORIES)})")
    p.add_argument("--name-style", choices=list(regions.STYLES),
                   help="style of alternative names when a name isn't in our lists (otherwise you're asked)")
    p.add_argument("--quick", action="store_true",
                   help="shorter run for practice / data collection: 10 cycles (~6 min) and 2 sensor passes. "
                        "Less sensitive: weaker responders may only show up with the full 14")
    p.add_argument("--cycles", type=int, default=None,
                   help="presentations of each item (default 14, ~8.5 min; 10 with --quick)")
    p.add_argument("--out", default="data/identity.trials.csv")
    p.add_argument("--no-camera", action="store_true")
    p.add_argument("--source", default="0")
    p.add_argument("--slow", action="store_true",
                   help="add the slow sensor round first (GSR / heart rate / breathing), +~8 min")
    p.add_argument("--slow-reps", type=int, default=None,
                   help="passes per detail in the sensor round (default 3, ~8 min; 2 with --quick, ~5.5 min)")
    p.add_argument("--sensor", help="record the sensors here: COM5 (Arduino, 115200 baud), COM5@9600, "
                                    "udp:0.0.0.0:5008 (backend forwards samples), or sim. Implies --slow")
    p.add_argument("--sensor-columns", default="gsr,hr,resp",
                   help="order of plain comma-separated numbers per line (default gsr,hr,resp; t = device time)")
    p.add_argument("--physio", help="OR: sensor CSV (t,gsr,hr,resp) recorded by another program during this session")
    p.add_argument("--gsr-inverted", action="store_true", help="the GSR column goes DOWN when sweating increases")
    p.add_argument("--udp", help="also send each sensor-round onset to the backend, e.g. 127.0.0.1:5007")
    a = p.parse_args()
    if a.cycles is None:                      # an explicit --cycles / --slow-reps always wins over --quick
        a.cycles = 10 if a.quick else 14
    if a.slow_reps is None:
        a.slow_reps = 2 if a.quick else 3
    if a.cycles < 1 or a.slow_reps < 1:
        sys.exit("--cycles and --slow-reps must be at least 1")
    logging.basicConfig(level=logging.WARNING)
    print(__doc__)
    if a.quick:
        print(f"--quick: {a.cycles} cycles per word (full test: 14). Fine for practice and data collection; "
              "use the full length for the demo.")
    cats = [c.strip() for c in a.details.split(",") if c.strip()]
    bad = [c for c in cats if c not in CATEGORIES]
    if bad:
        sys.exit(f"unknown details {bad}; choose from {list(CATEGORIES)}")
    if a.sensor:
        a.slow = True
        if a.sensor.lower() == "sim":
            print("!! --sensor sim: SIMULATED sensors. Tests the software only; the physiology result is noise.")
    rng = random.Random()
    plan = prepare_plan(cats, a.control, rng, a.name_style)
    print("\nA window will open. Rest two fingers on E and I.")

    out = pathlib.Path(a.out)
    if a.control:
        out = out.with_name(out.name.replace(".trials.csv", "_control.trials.csv"))
    path = unique_path(out)
    slow = slow_items(plan, rng, a.slow_reps) if a.slow else None
    if slow:
        print(f"Sensor round: {len(slow)} words, ~{sum(i.total_s for i in slow) / 60 + 0.4:.1f} min. "
              "Start the sensor logger now if it isn't running.")
    blocks = plan.blocks(a.cycles)
    trials, quality, extras = run_session(blocks, rng, path, a.no_camera, a.source, slow, a.udp,
                                          a.sensor, [c.strip() for c in a.sensor_columns.split(",")])
    if not trials:
        print("No trials completed.")
        return
    stem = str(session_stem(path))
    save_vision_csv(stem + ".vision.csv", extras.get("vision_epochs") or {})
    res = score_identity(stem, plan, a.control, a.physio or extras.get("physio"), a.gsr_inverted, quality,
                         progress(blocks, trials, slow))
    res["cycles"] = a.cycles
    save_results(path, res)


if __name__ == "__main__":
    main()
