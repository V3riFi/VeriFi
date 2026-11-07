"""Re-score a finished session from its files, e.g. once the sensor log is available.

    .venv\\Scripts\\python.exe examples\\fuse_identity.py data\\identity.trials.csv --physio data\\arduino_log.csv
    .venv\\Scripts\\python.exe examples\\fuse_identity.py data\\sessions\\p01\\session.trials.csv          (full VeriFi session)

Finds the session's other files by name (<stem>.slow.csv, .frames.csv, .physio.csv, .vision.csv),
recomputes every channel with the current code, prints the report and updates <stem>.results.json.
The sensor CSV: t,gsr,hr,resp (t = time.time() on the interview laptop). --physio overrides the
session's own <stem>.physio.csv.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from interview.analysis import analyse, mark_progress, print_report  # noqa: E402
from interview.case import CASE_POOLS  # noqa: E402
from interview.identity import CATEGORIES, DEFAULT_CATEGORIES  # noqa: E402
from interview.rtcit import load_trials  # noqa: E402
from interview.verdict import code  # noqa: E402
from rt_cit_test import stem  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("trials", help="the session's .trials.csv")
    p.add_argument("--physio", help="sensor CSV: t,gsr,hr,resp (default: <session>.physio.csv if it exists)")
    p.add_argument("--gsr-inverted", action="store_true")
    a = p.parse_args()
    path = pathlib.Path(a.trials)
    if not path.exists():
        sys.exit(f"no such file: {path}")
    base = str(stem(path))
    res_path = pathlib.Path(base + ".results.json")
    saved = json.loads(res_path.read_text(encoding="utf-8")) if res_path.exists() else {}
    blocks = {t.block for t in load_trials(str(path))}
    if "case" in blocks:
        cats = saved.get("case_categories", list(CASE_POOLS))
        cal = saved.get("calibration_categories", DEFAULT_CATEGORIES)
        res = analyse(base, physio_path=a.physio, gsr_inverted=a.gsr_inverted)
        mark_progress(res, saved.get("progress"), saved.get("complete"))
        print_report(res, [CASE_POOLS[c][0] for c in cats], [CATEGORIES[c].label for c in cal],
                     f"VeriFi session (re-scored): {base}")
    else:
        cats = saved.get("categories", DEFAULT_CATEGORIES)
        res = analyse(base, "identity", "identity_slow", "-", "-", physio_path=a.physio, gsr_inverted=a.gsr_inverted)
        mark_progress(res, saved.get("progress"), saved.get("complete"))
        print_report(res, [CATEGORIES[c].label if c in CATEGORIES else c for c in cats],
                     title=f"Concealed-identity test (re-scored): {base}")
    keep = {k: saved[k] for k in ("mode", "case", "condition", "categories", "case_categories",
                                  "calibration_categories", "quality", "quick", "cycles") if k in saved}
    res.update(keep)
    res["verdict_code"] = code(res["verdict"])
    res["rescored"] = True
    res_path.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    print(f"\nUpdated {res_path}")


if __name__ == "__main__":
    main()
