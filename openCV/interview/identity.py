"""Concealed-identity test: does this person recognize these identity details?

Scenario (Security & Defense): someone claims a false identity; investigators have a candidate
real identity (from a document, a database). The RT-CIT checks whether the person RECOGNIZES the
candidate's details. This is the known-answer use the CIT literature validates, and own name /
hometown / mother's name are high-importance items, which give much larger effects than an item
picked a minute ago (see METHOD.md).

Several details in one test (multiple-probe protocol): each adds evidence. The keyboard round runs
them in parts of 2 details, so only 2 YES words have to be remembered at a time (with 4 at once,
YES accuracy collapsed in our first runs).
Control condition: the same test with a random identity of the same kind (same name style, gender,
province) that the person has never had. A working test must say "no evidence" there.

Rules for good hidden details: known to the real person and to nobody else in the room (so not
shirt colour, today's date or, at a hackathon, the university), and the 4 alternatives must be
equally plausible (interview/regions.py), so the true one doesn't stand out by looks alone.
Privacy: values are only shown on screen. Logs use labels: TRUE, F1-F4, YES (the target).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from . import regions
from .facts import _date_foils, fmt_day, parse_birthday
from .regions import City, norm
from .rtcit import RtBlock, RtGroup

UNI_POOL = ["University of Alberta", "University of Calgary", "University of Toronto", "UBC", "McGill",
            "University of Waterloo", "Simon Fraser University", "MacEwan University", "University of Ottawa",
            "Queen's University", "Western University", "University of Saskatchewan", "University of Manitoba",
            "Dalhousie University"]


@dataclass(frozen=True)
class Category:
    key: str
    prompt: str          # console question
    label: str           # used in the report and on screen


CATEGORIES = {
    "first_name": Category("first_name", "First name", "first name"),
    "last_name": Category("last_name", "Last name (family name)", "last name"),
    "hometown": Category("hometown", "Hometown (city you grew up in; add ', province' if unusual)", "hometown"),
    "mother_name": Category("mother_name", "Mother's first name", "mother's first name"),
    "birthday": Category("birthday", "Date of birth (e.g. March 14)", "birthday"),
    "university": Category("university", "University", "university"),
}
NAME_CATS = ("first_name", "last_name", "mother_name")
# birthday is optional: it ranked 3rd and 4th of 5 in both first real runs (dates are hard to tell
# apart at 0.8 s). University is out: in a hackathon room most people share it.
DEFAULT_CATEGORIES = ["first_name", "last_name", "hometown", "mother_name"]


@dataclass
class Hint:
    """How to build alternatives for one detail. Filled from our lists, or asked once in the console."""
    style: Optional[str] = None            # names: key of regions.STYLES
    gender: Optional[str] = None           # first name: "m" / "f" / "x" (either); None = not known yet
    city: Optional[City] = None            # hometown found in our table
    country: Optional[str] = None          # hometown not in the table: where it is
    region: Optional[str] = None
    custom: Optional[List[str]] = None     # alternatives typed by the operator (5 for a real test)


def _tidy(v: str) -> str:
    v = " ".join(v.strip().split())
    return v.title() if (v.islower() or v.isupper()) else v


def normalize(cat: str, value: str) -> str:
    if cat == "birthday":
        return fmt_day(parse_birthday(value))
    if cat == "hometown":
        return regions.city_display(value)
    if cat == "university":
        return " ".join(value.strip().split())
    return _tidy(value)


def auto_hint(cat: str, value: str, prefer_style: Optional[str] = None) -> Hint:
    """What our lists know about this value. Missing fields are asked by `question()`."""
    if cat in NAME_CATS:
        style, gender = regions.name_style(cat, value, prefer_style)
        return Hint(style=style, gender=gender)
    if cat == "hometown":
        hits = regions.find_city(value)
        if len(hits) == 1:
            return Hint(city=hits[0])
        if not hits and "," in value:                 # 'Vegreville, Alberta': we know the place, not the town
            cc, reg = regions.parse_place(value.partition(",")[2])
            return Hint(country=cc, region=reg)
        return Hint()
    return Hint()


def question(cat: str, value: str, h: Hint) -> Optional[str]:
    """What still has to be asked before alternatives can be built, or None."""
    if cat == "first_name":
        if h.style is None:
            return "style"
        if h.gender is None and h.custom is None:
            return "gender"
    if cat in ("last_name", "mother_name") and h.style is None and h.custom is None:
        return "style"
    if cat == "hometown" and h.city is None and h.country is None and h.custom is None:
        return "which" if len(regions.find_city(value)) > 1 else "place"
    return None


def foils(cat: str, true_value: str, rng: random.Random, n: int = 5, exclude: Iterable[str] = (),
          hint: Optional[Hint] = None) -> List[str]:
    """n alternatives (4 irrelevants + 1 YES target): never the true value or anything the subject
    flagged as personally meaningful; same style / place / size as the true value."""
    h = hint or Hint()
    exclude = list(exclude)
    ex = {norm(x) for x in exclude} | {norm(true_value)}
    if h.custom:
        out = [x for x in dict.fromkeys(normalize(cat, c) for c in h.custom) if norm(x) not in ex]
        if len(out) < n:
            raise ValueError(f"need {n} typed alternatives for {cat}, got {len(out)} usable")
        return rng.sample(out, n)
    if cat == "birthday":
        bd = parse_birthday(true_value)
        out: List[str] = []
        for _ in range(50):
            for d in _date_foils(bd, n, rng, same_month=False):
                if norm(d) not in ex and d not in out and len(out) < n:
                    out.append(d)
            if len(out) >= n:
                break
        return out
    if cat in NAME_CATS:
        return regions.name_foils(cat, true_value, rng, n, exclude, h.style or "anglo", h.gender)
    if cat == "hometown":
        return regions.city_foils(true_value, rng, n, exclude, h.city, h.country or "CA", h.region)
    if cat == "university":
        pool = [x for x in UNI_POOL if norm(x) not in ex]
        return rng.sample(pool, n)
    raise KeyError(cat)


@dataclass
class IdentityPlan:
    """What gets shown. `values` stays in memory only."""
    cats: List[str]
    values: Dict[str, str]                       # cat -> true (or control) value, as shown
    alternatives: Dict[str, List[str]]           # cat -> 4 irrelevants + 1 YES target (last)
    hints: Dict[str, Hint] = field(default_factory=dict)
    labels: Dict[str, str] = field(default_factory=dict)   # cat -> label, for non-identity plans (cases)

    def label(self, cat: str) -> str:
        return self.labels.get(cat) or CATEGORIES[cat].label

    def _group(self, g: int) -> RtGroup:
        cat = self.cats[g]
        alts = self.alternatives[cat]
        cands = [("TRUE", self.values[cat])] + [(f"F{i + 1}", v) for i, v in enumerate(alts[:4])]
        return RtGroup(self.label(cat), cands, ("YES", alts[4]), gid=g)

    def block(self, cycles: int = 14) -> RtBlock:
        """All details in one block (4 YES words at once). Kept for tests; the session uses blocks()."""
        return RtBlock("identity", [self._group(g) for g in range(len(self.cats))], cycles=cycles,
                       known_label="TRUE", category="detail", n_yes_fillers=2, n_no_fillers=4)

    def parts(self, per_part: int = 2) -> List[List[int]]:
        """Detail indices per part, interleaved so one part doesn't hold two similar lists
        (first + last name): 4 details -> [0, 2], [1, 3]."""
        n = math.ceil(len(self.cats) / per_part)
        return [list(range(len(self.cats)))[i::n] for i in range(n)]

    def blocks(self, cycles: int = 14, per_part: int = 2, name: str = "identity", hide: str = "") -> List[RtBlock]:
        """The keyboard round in parts of `per_part` details. Same trials per detail as one big block
        (so the same power), but only 2 YES words to keep in mind. Group ids stay global, so the
        parts score together as one test. Fillers keep YES at 20% of trials."""
        out = []
        for idx in self.parts(per_part):
            k = len(idx)
            out.append(RtBlock(name, [self._group(g) for g in idx], cycles=cycles, known_label="TRUE",
                               category="detail", n_yes_fillers=max(1, k // 2), n_no_fillers=k, hide=hide))
        return out

    def shown_before_test(self, control: bool) -> Dict[str, List[str]]:
        """Items the subject looks at in advance to flag personally meaningful ones.
        Real test: only the alternatives (the true value is theirs anyway).
        Control: all 6, shuffled, so the subject can flag their own name etc. if the random identity
        hit it, and every item gets the same exposure (no item becomes extra familiar)."""
        out = {}
        for c in self.cats:
            items = list(self.alternatives[c]) + ([self.values[c]] if control else [])
            random.Random(len(items)).shuffle(items)
            out[c] = items
        return out


def make_plan(values: Dict[str, str], rng: random.Random, exclude: Iterable[str] = (),
              cats: Sequence[str] = DEFAULT_CATEGORIES, hints: Optional[Dict[str, Hint]] = None) -> IdentityPlan:
    """`hints` come from auto_hint() plus any console answers. Missing hints are looked up here;
    an unknown name defaults to the first name's style (or English), an unknown city to Alberta."""
    hints = dict(hints or {})
    exclude = list(exclude)
    vals = {c: normalize(c, values[c]) for c in cats}
    first_style = None
    if "first_name" in cats:
        hints.setdefault("first_name", auto_hint("first_name", values["first_name"]))
        first_style = hints["first_name"].style
    for c in cats:
        h = hints.get(c)
        empty = h is None or not (h.style or h.gender or h.city or h.country or h.custom)
        if empty:                                   # nothing known yet: look the value up
            h = hints[c] = auto_hint(c, values[c], first_style)
        if c in NAME_CATS and h.style is None:
            h.style = first_style or "anglo"
    # the other details' true values must never show up as an alternative (a mother called Priya
    # would make "Priya" a personally meaningful word in the first-name list)
    own = list(vals.values())
    alts = {c: foils(c, vals[c], rng, 5, exclude + own, hints[c]) for c in cats}
    return IdentityPlan(list(cats), vals, alts, hints)


def random_identity(rng: random.Random, exclude: Iterable[str] = (), cats: Sequence[str] = DEFAULT_CATEGORIES,
                    hints: Optional[Dict[str, Hint]] = None) -> Dict[str, str]:
    """Control condition: an identity the subject has never had, of the SAME kind as theirs (name
    style and gender, province, size of town), so both conditions show the same kind of words.
    `exclude` should hold the subject's real values; `hints` are those of the real values."""
    hints = hints or {}
    exclude = list(exclude)
    out: Dict[str, str] = {}
    for c in cats:
        h = hints.get(c) or Hint()
        if h.custom:                               # operator typed the alternatives: the control is one of them
            taken = {norm(e) for e in exclude + list(out.values())}
            out[c] = rng.choice([x for x in h.custom if norm(x) not in taken])
        elif c in NAME_CATS:
            style = h.style or (hints.get("first_name") or Hint()).style or "anglo"
            out[c] = regions.random_name(c, rng, exclude + list(out.values()), style, h.gender)
        elif c == "hometown":
            out[c] = regions.random_city(rng, exclude, h.city, h.country or "CA",
                                         h.region if (h.city or h.country) else "AB")
        elif c == "birthday":
            while True:
                d = fmt_day(parse_birthday(f"{rng.choice(['January', 'March', 'May', 'July', 'September', 'November'])} "
                                           f"{rng.randint(1, 28)}"))
                if norm(d) not in {norm(e) for e in exclude}:
                    break
            out[c] = d
        elif c == "university":
            out[c] = rng.choice([x for x in UNI_POOL if norm(x) not in {norm(e) for e in exclude}])
    return out


def control_hints(real_hints: Dict[str, Hint]) -> Dict[str, Hint]:
    """Hints for building the control identity's alternatives: same style/gender, and the control
    hometown is placed fresh (it's a different town). Custom city lists are reused minus the pick."""
    out = {}
    for c, h in real_hints.items():
        if h.custom:
            out[c] = Hint(custom=h.custom)
        elif c in NAME_CATS:
            out[c] = Hint(style=h.style, gender=h.gender)
        # otherwise nothing: the control town comes from our table, so make_plan looks it up
    return out


def summary_line(cat: str, h: Hint) -> str:
    """Console summary of how the alternatives were chosen (no values)."""
    if h.custom:
        return "alternatives typed by the operator"
    if cat in NAME_CATS:
        g = {"m": ", male", "f": ", female"}.get(h.gender or "", "")
        return f"{regions.STYLES.get(h.style or '', h.style)} names{g}"
    if cat == "hometown":
        if h.city:
            return (f"{regions.REGIONS.get(h.city.country, {}).get(h.city.region, regions.COUNTRIES[h.city.country])}"
                    f" places of similar size, > 60 km away")
        return f"places in {h.region or ''} {regions.COUNTRIES.get(h.country or '', h.country or '')}".strip()
    return "similar dates" if cat == "birthday" else ""
