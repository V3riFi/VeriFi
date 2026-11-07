"""One place for the verdict wording and thresholds (fixed BEFORE the validation data, METHOD.md §5).

One-sided task check: a subject who doesn't read the items (misses the YES words) shows no
recognition effect whether or not they know the details. So low YES accuracy can hide a real effect,
but it can't create a false one. It therefore only blocks the "no evidence" verdict, never
"recognizes". The p-value itself stays valid either way (labels are exchangeable under the null).
"""
from __future__ import annotations

from typing import Optional

STRONG = 0.05      # "recognizes"
LEAN = 0.20        # below this: "inconclusive" (about 1 in 4 innocents land in .05-.2 in simulation)


def verdict(p: Optional[float], valid: bool = True, engaged: bool = True) -> str:
    if not valid:
        return "INVALID (task not followed; see checks above)"
    if p is None:
        return "NO RESULT (not enough usable data in any channel)"
    if p < STRONG:
        return "RECOGNIZES these details (strong)"
    if p < LEAN:
        return "INCONCLUSIVE (a slight lean, but innocents land here ~25% of the time)"
    if not engaged:
        return "INCONCLUSIVE (no effect, but too many YES words were missed to rule recognition out)"
    return "NO EVIDENCE of recognition"


def code(v: str) -> str:
    """Short machine label of a verdict string:
    recognizes / inconclusive / no_evidence / invalid / incomplete / none."""
    if v.startswith("NO EVIDENCE"):
        return "no_evidence"
    head = v.split(" ", 1)[0]
    return {"RECOGNIZES": "recognizes", "INCONCLUSIVE": "inconclusive", "INVALID": "invalid",
            "INCOMPLETE": "incomplete"}.get(head, "none")
