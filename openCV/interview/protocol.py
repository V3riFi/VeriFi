"""VeriFi interview protocol: a structured interview modelled on real investigative practice.

Phases (in order):
  1. setup_check  - automatic: face centred and well tracked before anything is recorded
  2. instructions - what will happen; this is a demo, not a lie detector (operator presses SPACE)
  3. rest         - resting baseline: look at the cross, don't talk
  4. narrative    - pre-test interview: open questions answered out loud. Gives each sensor a
                    "talking" baseline, and the rapport phase of investigative interviews.
  5. known_truth / directed_lie (full script) - questions with a KNOWN answer, to get this
                    person's truthful-answer vs. instructed-false-answer reaction template.
  6. calibration  - personal-facts round (polygraph "acquaintance test"): the 5 open questions
                    come back as yes/no options, one true, all answered NO. The answers are KNOWN,
                    so this mini CIT learns which cues react for this person (interview/facts.py).
                    The old number test (`calibration=CitBlock(...)`) still works.
  7. review       - every test item is shown in advance. No surprise questions (standard practice:
                    surprise itself causes a reaction).
  8. main CIT     - one or more questions, each with 5 alternatives: a buffer item first (absorbs
                    the start-of-block orienting response, never scored), then N randomized series.
                    Jittered timing so the next item can't be anticipated.
  9. post_test    - the subject can explain anything that stood out (real examiners do this)
 10. results

`build_steps()` turns a spec into a timed list of Steps that any front end (OpenCV self-test,
web frontend, backend) can play. Every scored step carries the metadata the scorer needs:
block, series, item, kind.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .facts import OPEN_QUESTIONS, PersonalFacts, facts_steps


@dataclass
class Step:
    kind: str                     # setup_check | instructions | rest | narrative | question | item | review | post_test | results
    duration: float               # seconds (max wait for setup_check / instructions)
    title: str
    subtitle: str = ""
    block: str = ""
    item: Optional[str] = None
    series: int = -1
    marker: str = ""              # epoch kind for the scorer: test | buffer | narrative | rest | known_truth | directed_lie
    display_s: Optional[float] = None   # show the title only this long, then a fixation cross
    wait_for_key: bool = False

    @property
    def scored_epoch(self) -> bool:
        return bool(self.marker)


@dataclass
class CitBlock:
    name: str
    question: str                 # e.g. "Was your card the {item}?"
    items: List[str]
    series: int = 4
    buffer_item: Optional[str] = None
    known_target: Optional[str] = None   # calibration (operator knows it) or known-solution mode
    role: str = "main"            # "calibration" | "main"
    isi: tuple = (6.8, 7.8)       # jittered onset-to-onset interval, seconds (>= 6.6 for the 5 s window)
    display_s: float = 3.0


@dataclass
class InterviewSpec:
    title: str
    rest_s: float = 60.0
    narrative: List[str] = field(default_factory=list)
    narrative_s: float = 20.0
    known_truth: List[str] = field(default_factory=list)
    directed_lie: List[str] = field(default_factory=list)
    calibration: Optional[CitBlock] = None
    main: List[CitBlock] = field(default_factory=list)
    post_test: str = "Is there anything about any of the items that stood out to you for another reason?"
    post_test_s: float = 20.0
    review_s: float = 10.0
    # personal-facts round (interview/facts.py): asked openly first, then the known-answer practice CIT.
    # Replaces `calibration` when set. The answers live only in on-screen text, never in item labels.
    facts: Optional[PersonalFacts] = None
    open_question_s: float = 8.0


# ---------------------------------------------------------------------- randomization
def series_orders(items: Sequence[str], n_series: int, rng: random.Random, avoid_first: Optional[str] = None):
    """n random orders; an item never appears twice in a row across a series boundary."""
    orders, last = [], avoid_first
    for _ in range(n_series):
        for _attempt in range(100):
            o = list(items)
            rng.shuffle(o)
            if o[0] != last:
                break
        orders.append(o)
        last = o[-1]
    return orders


def _cit_steps(b: CitBlock, rng: random.Random) -> List[Step]:
    steps = []
    if b.buffer_item:
        steps.append(Step("item", rng.uniform(*b.isi), b.question.format(item=b.buffer_item), block=b.name,
                          item=b.buffer_item, series=-1, marker="buffer", display_s=b.display_s))
    for s, order in enumerate(series_orders(b.items, b.series, rng, b.buffer_item)):
        for it in order:
            steps.append(Step("item", rng.uniform(*b.isi), b.question.format(item=it),
                              subtitle="Answer NO", block=b.name, item=it, series=s, marker="test",
                              display_s=b.display_s))
    return steps


# ---------------------------------------------------------------------- build
def build_steps(spec: InterviewSpec, seed: Optional[int] = None) -> List[Step]:
    rng = random.Random(seed)
    st: List[Step] = [
        Step("setup_check", 45.0, "Setting up", "Sit centred in front of the camera, face well lit"),
        Step("instructions", 120.0, spec.title,
             "You'll be asked some questions. This is a demo of stress signals, not a lie detector. "
             "Answer the questions out loud. When a single item appears, answer NO, even if it's yours. "
             "Keep your head still. Operator: press SPACE to start.", wait_for_key=True),
        Step("rest", spec.rest_s, "+", "Resting baseline: look at the cross. Don't talk.",
             block="rest", item="rest", marker="rest"),
    ]
    if spec.facts:
        for k, q in enumerate(OPEN_QUESTIONS):
            st.append(Step("narrative", spec.open_question_s, q, "Answer out loud, truthfully",
                           block="open_questions", item=f"Q{k + 1}", series=0, marker="narrative"))
    for k, q in enumerate(spec.narrative):
        st.append(Step("narrative", spec.narrative_s, q, f"Answer out loud for about {spec.narrative_s:.0f} seconds",
                       block="narrative", item=f"N{k + 1}", series=0, marker="narrative"))
    qs = [(q, "known_truth", "Answer truthfully") for q in spec.known_truth] + \
         [(q, "directed_lie", "For this one, answer NO") for q in spec.directed_lie]
    rng.shuffle(qs)
    for k, (q, kind, how) in enumerate(qs):
        st.append(Step("question", 8.0, q, how, block="yes_no", item=f"{kind}:{k + 1}", series=k, marker=kind,
                       display_s=4.0))
    if spec.facts:
        st.append(Step("review", 12.0, "Practice round: the same 5 questions",
                       "Each question now has 5 options, one of them true. Answer NO to every option, "
                       "including the true one."))
        st += facts_steps(spec.facts, rng, step_cls=Step)
    elif spec.calibration:
        c = spec.calibration
        st.append(Step("review", 12.0, "Practice round",
                       "Think of your number. Answer NO to every number, including yours.", wait_for_key=False))
        st += _cit_steps(c, rng)
    for b in spec.main:
        st.append(Step("review", spec.review_s, "Next you'll be asked about: " + ", ".join(b.items),
                       "Answer NO to every item. Keep your head still and look at the screen."))
        st += _cit_steps(b, rng)
    st.append(Step("post_test", spec.post_test_s, spec.post_test, "Answer out loud",
                   block="post_test", item="post_test", series=0, marker="narrative"))
    st.append(Step("results", 0.0, "Done", "Results are in the console"))
    return st


def total_seconds(steps: Sequence[Step]) -> float:
    return sum(s.duration for s in steps if s.kind not in ("setup_check", "instructions", "results"))


# ---------------------------------------------------------------------- ready-made scripts
CARDS = ["Ace of Spades", "7 of Hearts", "Queen of Clubs", "3 of Diamonds", "King of Hearts"]


def card_interview(facts: Optional[PersonalFacts], quick: bool = False) -> InterviewSpec:
    """Hackathon demo: 5 open personal questions + personal-facts practice round + card CIT
    (~8.5 min, ~7 min quick)."""
    return InterviewSpec(
        title="VeriFi interview: card test",
        rest_s=20.0 if quick else 45.0,
        narrative=[] if quick else ["Describe what you did this morning."],
        narrative_s=20.0,
        facts=facts,
        main=[CitBlock("cards", "Was your card the {item}?", list(CARDS), series=3 if quick else 4,
                       buffer_item="Jack of Diamonds")],
        post_test_s=15.0 if quick else 20.0,
    )


def mock_crime_interview(facts: Optional[PersonalFacts], known: Optional[dict] = None) -> InterviewSpec:
    """Full demo for judges: before the interview the subject secretly takes one envelope and
    memorizes its object, codeword and room. Three independent questions = three chances to detect.
    `known` (block -> correct item) switches to known-solution mode, like a real investigation."""
    known = known or {}
    return InterviewSpec(
        title="VeriFi interview: mock investigation",
        rest_s=60.0,
        narrative=["Describe your journey here today.", "Describe what you did in the last hour.",
                   "Tell me about a time you had to keep a secret."],
        known_truth=["Are you sitting down?", "Is it daytime right now?", "Are you in Canada right now?"],
        directed_lie=["Are you sitting down?", "Are you in Canada right now?"],
        facts=facts,
        main=[
            CitBlock("object", "Was the object a {item}?", ["USB drive", "key", "ring", "coin", "SIM card"],
                     buffer_item="watch", known_target=known.get("object")),
            CitBlock("codeword", "Was the codeword {item}?", ["FALCON", "MERIDIAN", "COBALT", "HARBOR", "SPHINX"],
                     buffer_item="ORBIT", known_target=known.get("codeword")),
            CitBlock("room", "Was it hidden in the {item}?", ["library", "cafeteria", "lab", "parking lot", "gym"],
                     buffer_item="lobby", known_target=known.get("room")),
        ],
    )
