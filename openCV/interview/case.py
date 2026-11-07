"""Mock-crime case files: the validated CIT use with a ground truth we control.

A case = 3 crime details (what was taken, the code word, where it was hidden), each with 4
alternatives and a YES word. make_case.py writes the case key (JSON, fictional, no personal data) and
an ENVELOPE.txt for the subject: GUILTY (memorize the details) or INNOCENT (you know nothing),
chosen at random so the operator can stay blind. The interview tests whether the subject
recognizes the crime details; the condition is only revealed after scoring.
"""
from __future__ import annotations

import json
import random
import time
from typing import Dict, Optional

from .identity import IdentityPlan

CASE_POOLS = {
    "item": ("stolen item", ["USB drive", "gold ring", "passport", "car keys", "wristwatch", "lanyard badge",
                             "sunglasses", "wallet", "headphones", "hard drive", "SIM card", "notebook"]),
    "codeword": ("code word", ["ORCHID", "FALCON", "COBALT", "SPARROW", "GRANITE", "LANTERN", "VELVET",
                               "HARBOR", "CIPHER", "TUNDRA", "MAPLE", "QUARTZ"]),
    "place": ("hiding place", ["blue locker", "desk drawer", "filing cabinet", "mailroom", "supply closet",
                               "back stairwell", "server room", "coat rack", "recycling bin", "lab fridge"]),
}
CASE_CATS = list(CASE_POOLS)


def new_case(rng: random.Random, case_id: str, condition: Optional[str] = None) -> dict:
    details = {}
    for cat, (_, pool) in CASE_POOLS.items():
        six = rng.sample(pool, 6)
        details[cat] = {"true": six[0], "alternatives": six[1:]}   # 4 irrelevants + 1 YES word (last)
    return {"id": case_id, "created": time.strftime("%Y-%m-%d %H:%M"), "details": details,
            "condition": condition or rng.choice(["guilty", "innocent"])}


def envelope(case: dict) -> str:
    if case["condition"] == "innocent":
        return ("VeriFi mock-crime study: ENVELOPE\n\n"
                "You are INNOCENT.\n"
                "Something was stolen from the lab today, but you had nothing to do with it and you\n"
                "don't know any details. Just do the interview honestly.\n\n"
                "Don't tell the operator what this envelope says. Fold it and keep it.\n")
    d = case["details"]
    return ("VeriFi mock-crime study: ENVELOPE\n\n"
            "You are the THIEF (a role-play: nothing is really stolen).\n"
            f"  What you took:      {d['item']['true']}\n"
            f"  The code word:      {d['codeword']['true']}\n"
            f"  Where you hid it:   {d['place']['true']}\n\n"
            "Read these out loud to yourself 3 times, picture doing it, then fold this paper.\n"
            "In the interview, HIDE that you know: answer NO to every option like an innocent person.\n"
            "Don't tell the operator what this envelope says.\n")


def save_case(case: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(case, f, indent=2)


def load_case(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def case_plan(case: dict) -> IdentityPlan:
    cats = [c for c in CASE_CATS if c in case["details"]]
    return IdentityPlan(cats, {c: case["details"][c]["true"] for c in cats},
                        {c: list(case["details"][c]["alternatives"]) for c in cats},
                        labels={c: CASE_POOLS[c][0] for c in cats})


def condition(case: dict) -> Optional[str]:
    return case.get("condition")


def summary(case: dict) -> Dict[str, str]:
    return {c: CASE_POOLS[c][0] for c in case["details"]}
