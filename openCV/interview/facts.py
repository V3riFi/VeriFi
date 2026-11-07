"""Personal-facts round: the subject's own answers to 5 basic questions, used two ways.

1. Pre-interview (open, answered truthfully out loud):
     What is your first name? / What colour T-shirt are you wearing? / What is today's date?
     What is your date of birth? / Which university are you at right now?
2. Calibration (known-answer CIT): each question becomes 5 yes/no options, one of them true.
   The subject answers NO to all, including the true one. Own name and birthdate are the
   classic strongest items in concealed-information research, so this is a much better
   calibration than one picked number: 5 known answers instead of 1.

Scoring trick: inside the calibration block the true option is labelled "TARGET" and the foils
"F1".."F4", and each QUESTION is one series. One block of 5 series then tells the scorer which
cues separate the true answer from the foils for this person.
Privacy: the real answers only appear in on-screen text, never in item labels, logs or results.
"""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

TARGET = "TARGET"
OPEN_QUESTIONS = [
    "What is your first name?",
    "What colour T-shirt are you wearing?",
    "What is today's date?",
    "What is your date of birth?",
    "Which university are you at right now?",
]
NAMES = ["Daniel", "Priya", "Omar", "Sophie", "Ryan", "Mei", "Lucas", "Aisha", "Ethan", "Hannah", "Arjun",
         "Chloe", "Mateo", "Fatima", "Noah", "Ananya", "Liam", "Sara", "Kenji", "Olivia"]
COLOURS = ["black", "white", "blue", "red", "green", "grey", "yellow", "navy", "purple", "orange", "pink", "brown"]
UNIVERSITIES = ["University of Alberta", "University of Calgary", "University of Toronto", "UBC", "McGill",
                "University of Waterloo", "Simon Fraser University", "MacEwan University", "University of Ottawa",
                "Queen's University", "Western University", "University of Saskatchewan"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def fmt_day(d: dt.date) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}"


def parse_birthday(text: str) -> dt.date:
    """'March 14', 'Mar 14', '14 March', '2004-03-14'. The year is ignored."""
    s = " ".join(text.replace(",", " ").split())
    with_year = ("%Y-%m-%d", "%d/%m/%Y", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y")
    no_year = ("%B %d", "%b %d", "%d %B", "%d %b")
    for fmts, src in ((with_year, s), (no_year, None)):
        for f in fmts:
            try:   # year-less formats get a leap year appended so Feb 29 parses
                d = dt.datetime.strptime(src or f"{s} 2000", src and f or f + " %Y")
                return dt.date(2000, d.month, d.day)
            except ValueError:
                continue
    raise ValueError(f"couldn't read birthday {text!r}; use e.g. 'March 14' or '2004-03-14'")


@dataclass
class PersonalFacts:
    first_name: str
    shirt_colour: str
    birthday: dt.date
    university: str
    today: dt.date = field(default_factory=dt.date.today)


def _pick(pool: Sequence[str], true: str, n: int, rng: random.Random) -> List[str]:
    t = true.strip().lower()
    cands = [p for p in pool if p.lower() != t and p.lower() not in t and t not in p.lower()]
    return rng.sample(cands, n)


def _date_foils(true: dt.date, n: int, rng: random.Random, same_month: bool) -> List[str]:
    out, seen = [], {(true.month, true.day)}
    while len(out) < n:
        if same_month:
            day = rng.randint(1, 28)
            d = dt.date(2000, true.month, day)
            if abs(day - true.day) < 3:
                continue
        else:
            m = rng.choice([m for m in range(1, 13) if m != true.month])
            d = dt.date(2000, m, rng.randint(1, 28))
        if (d.month, d.day) not in seen:
            seen.add((d.month, d.day))
            out.append(fmt_day(d))
    return out


def fact_questions(f: PersonalFacts, rng: random.Random, n_foils: int = 4) -> List[Tuple[str, str, List[str]]]:
    """(yes/no template, true value, foils) for each of the 5 questions, in a fixed order.
    One extra foil per question is used as that question's unscored buffer."""
    k = n_foils + 1
    return [
        ("Is your first name {v}?", f.first_name.strip().title(), _pick(NAMES, f.first_name, k, rng)),
        ("Is your T-shirt {v}?", f.shirt_colour.strip().lower(), _pick(COLOURS, f.shirt_colour, k, rng)),
        ("Is today {v}?", fmt_day(f.today), _date_foils(f.today, k, rng, same_month=True)),
        ("Is your birthday {v}?", fmt_day(f.birthday), _date_foils(f.birthday, k, rng, same_month=False)),
        ("Are you at {v}?", f.university.strip(), _pick(UNIVERSITIES, f.university, k, rng)),
    ]


def facts_steps(f: PersonalFacts, rng: random.Random, isi=(6.6, 7.2), display_s: float = 3.0,
                block: str = "facts", step_cls=None) -> list:
    """Calibration block. Each question = one series: buffer foil, then true + 4 foils in random
    order with the true answer never first (the topic switch itself causes an orienting response)."""
    from .protocol import Step
    step_cls = step_cls or Step
    steps = []
    for s, (tmpl, true, foils) in enumerate(fact_questions(f, rng)):
        buffer, scored = foils[0], foils[1:]
        steps.append(step_cls("item", rng.uniform(*isi), tmpl.format(v=buffer), "Answer NO", block=block,
                              item="BUFFER", series=s, marker="buffer", display_s=display_s))
        opts = [(TARGET, true)] + [(f"F{i + 1}", v) for i, v in enumerate(scored)]
        for _ in range(50):
            rng.shuffle(opts)
            if opts[0][0] != TARGET:
                break
        for label, v in opts:
            steps.append(step_cls("item", rng.uniform(*isi), tmpl.format(v=v), "Answer NO", block=block,
                                  item=label, series=s, marker="test", display_s=display_s))
    return steps
