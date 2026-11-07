"""Make a mock-crime case for one subject (do this BEFORE the subject arrives).

    .venv\\Scripts\\python.exe examples\\make_case.py p01            random condition (guilty or innocent), operator stays blind
    .venv\\Scripts\\python.exe examples\\make_case.py p01 --guilty   / --innocent to force it

Writes data\\cases\\p01.json (the investigator's key: crime details + condition; fictional, no
personal data) and data\\cases\\p01_ENVELOPE.txt. Print or show the envelope to the SUBJECT only,
without reading it yourself. A guilty subject memorizes the details; an innocent one knows nothing.
Then run:  run_verifi.bat --case data\\cases\\p01.json
"""
from __future__ import annotations

import argparse
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from interview.case import envelope, new_case, save_case  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("case_id", help="subject / case id, e.g. p01")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--guilty", action="store_true")
    g.add_argument("--innocent", action="store_true")
    p.add_argument("--dir", default="data/cases")
    a = p.parse_args()
    d = pathlib.Path(a.dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{a.case_id}.json"
    if path.exists():
        sys.exit(f"{path} exists already; pick another id (cases are never overwritten)")
    cond = "guilty" if a.guilty else "innocent" if a.innocent else None
    case = new_case(random.SystemRandom(), a.case_id, cond)
    save_case(case, str(path))
    env = d / f"{a.case_id}_ENVELOPE.txt"
    env.write_text(envelope(case), encoding="utf-8")
    print(f"Case key:  {path}   (don't open it before scoring if you want to stay blind)")
    print(f"Envelope:  {env}   (give it to the subject unread; they read it alone, then fold it)")
    print(f"Then:      run_verifi.bat --case {path}")


if __name__ == "__main__":
    main()
