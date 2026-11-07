"""How well does VeriFi work across people? (the validation in METHOD.md section 5)

    .venv\\Scripts\\python.exe examples\\evaluate.py                    every session in data\\sessions + identity runs in data
    .venv\\Scripts\\python.exe examples\\evaluate.py --rescore          recompute each session from its files first (slow)

Mock-crime sessions (data\\sessions\\*\\session.results.json): ground truth = the case condition.
Identity runs (data\\identity*.results.json): ground truth = real (knows) vs control (doesn't).
Per variant (rt_only, rt_plus_sensors, plus_gating, plus_profile, full):
  detection rate   knowers with p < .05      (higher is better)
  false alarms     non-knowers with p < .05  (should stay near 5%)
  AUC              how well p separates the two groups (0.5 = chance, 1 = perfect)
The threshold (.05) and the headline variant ("full") were fixed before any data (pre-registered).
Left out, and listed so nothing is hidden: runs stopped early (no full result) and INVALID runs
(task not followed).
With fewer than ~10 sessions per group these numbers are anecdotes, and the script says so.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from interview.verdict import STRONG, code  # noqa: E402

VARIANTS = ["rt_only", "rt_plus_sensors", "plus_gating", "plus_profile", "full"]


def auc(pos, neg):
    """P(score of a knower beats a non-knower), score = -p. Ties count half."""
    if not pos or not neg:
        return None
    s = sum((a < b) + 0.5 * (a == b) for a in pos for b in neg)
    return s / (len(pos) * len(neg))


def load(root: pathlib.Path, rescore: bool):
    rows = []
    for f in sorted(root.glob("sessions/*/session.results.json")) + sorted(root.glob("identity*.results.json")):
        if rescore:
            import subprocess
            trials = str(f).replace(".results.json", ".trials.csv")
            if pathlib.Path(trials).exists():
                subprocess.run([sys.executable, str(pathlib.Path(__file__).parent / "fuse_identity.py"), trials],
                               capture_output=True)
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if "condition" in r:
            knows = {"guilty": True, "innocent": False}.get(r["condition"])
            kind = "mock crime"
        elif "mode" in r:
            knows = r["mode"] == "real"
            kind = "identity"
        else:
            continue
        if knows is None:
            continue
        var = r.get("variants") or {"rt_only": r.get("p"), "full": r.get("p")}   # older result files
        vc = r.get("verdict_code") or code(r.get("verdict", ""))
        # a run stopped early, or with the task not followed, has no result to count
        skip = ("stopped early" if r.get("complete") is False or vc == "incomplete" else
                "task not followed (INVALID)" if vc == "invalid" else None)
        rows.append({"file": f.relative_to(root).as_posix(), "kind": kind, "knows": knows, "variants": var,
                     "verdict": vc, "skip": skip, "p": r.get("p")})
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default="data")
    p.add_argument("--rescore", action="store_true")
    a = p.parse_args()
    rows = load(pathlib.Path(a.data), a.rescore)
    if not rows:
        sys.exit("no scored sessions found")
    for kind in ("mock crime", "identity"):
        every = [r for r in rows if r["kind"] == kind]
        if not every:
            continue
        print(f"\n{kind}: every run")
        for r in every:
            who = "knows" if r["knows"] else "doesn't know"
            p_s = "--" if r["p"] is None else f"{r['p']:.3f}"
            print(f"  {r['file']:<40} {who:<13} p = {p_s:<6} {r['verdict']:<13}"
                  + (f"  [left out: {r['skip']}]" if r["skip"] else ""))
        rs = [r for r in every if not r["skip"]]
        npos, nneg = sum(r["knows"] for r in rs), sum(not r["knows"] for r in rs)
        print(f"\n{kind}: {npos} knowers, {nneg} non-knowers counted")
        print(f"  {'variant':<16} {'detected':>10} {'false alarms':>13} {'AUC':>6}")
        for v in VARIANTS:
            pos = [r["variants"].get(v) for r in rs if r["knows"] and r["variants"].get(v) is not None]
            neg = [r["variants"].get(v) for r in rs if not r["knows"] and r["variants"].get(v) is not None]
            if not pos and not neg:
                continue
            det = f"{sum(x < STRONG for x in pos)}/{len(pos)}" if pos else "--"
            fa = f"{sum(x < STRONG for x in neg)}/{len(neg)}" if neg else "--"
            u = auc(pos, neg)
            print(f"  {v + (' *' if v == 'full' else ''):<16} {det:>10} {fa:>13} {'--' if u is None else f'{u:.2f}':>6}")
        if nneg == 0:
            print("  (no non-knower runs: false alarms can't be measured; run the --control version too)")
        if min(npos, nneg) < 10:
            print("  (fewer than 10 per group: too few to claim an accuracy; keep collecting)")
    print("\n* = pre-registered headline. Detection/false alarms at p < .05.")


if __name__ == "__main__":
    main()
