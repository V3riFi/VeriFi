"""Concealed-identity test: region-matched alternatives, parts of 2 details, privacy, and
real-vs-control accuracy (simulated)."""
import pathlib
import random
import sys
import unittest
from collections import Counter

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from interview import regions  # noqa: E402
from interview.identity import (Hint, auto_hint, control_hints, foils, make_plan, question,  # noqa: E402
                                random_identity)
from interview.rtcit import build_trials, score_rt  # noqa: E402
from test_rtcit import simulate  # noqa: E402

ME = {"first_name": "Rohan", "last_name": "Sharma", "hometown": "Ludhiana", "mother_name": "Sunita"}
CANADIAN = {"first_name": "Daniel", "last_name": "Mitchell", "hometown": "Edmonton", "mother_name": "Karen"}


def run_subject(seed, effect_ms, cycles=14):
    """One simulated session over both parts. effect_ms = how much slower this person is on the
    tested details (0 = control: they don't know them)."""
    plan = make_plan(ME, random.Random(seed))
    trials = []
    for k, b in enumerate(plan.blocks(cycles)):
        trials += simulate("TRUE", effect_ms=effect_ms, seed=seed * 10 + k, block=b)
    return score_rt(trials, known_label="TRUE", n_perm=400)


class RegionTests(unittest.TestCase):
    def test_edmonton_gets_alberta_cities_of_similar_size(self):
        home = regions.find_city("Edmonton")[0]
        for s in range(10):
            alts = foils("hometown", "Edmonton", random.Random(s), hint=auto_hint("hometown", "Edmonton"))
            cities = [regions.find_city(a)[0] for a in alts]
            self.assertTrue(all(c.region == "AB" and c.tier >= 2 for c in cities), alts)
            self.assertTrue(all(regions.km(home, c) > 60 for c in cities), alts)      # not its own suburbs
            self.assertNotIn("St. Albert", alts)

    def test_names_match_style_and_gender(self):
        plan = make_plan(CANADIAN, random.Random(0))
        self.assertTrue(set(plan.alternatives["first_name"]) <= set(regions.NAMES["anglo"]["m"]))
        self.assertTrue(set(plan.alternatives["last_name"]) <= set(regions.NAMES["anglo"]["last"]))
        self.assertTrue(set(plan.alternatives["mother_name"]) <= set(regions.NAMES["anglo"]["mother"]))
        plan = make_plan(ME, random.Random(0))
        self.assertTrue(set(plan.alternatives["first_name"]) <= set(regions.NAMES["south_asian"]["m"]))
        self.assertTrue(all(regions.find_city(c)[0].region == "PB" for c in plan.alternatives["hometown"]))

    def test_unknown_values_are_asked_not_guessed(self):
        h = auto_hint("first_name", "Kavran")
        self.assertEqual(question("first_name", "Kavran", h), "style")
        h.style = "south_asian"
        self.assertEqual(question("first_name", "Kavran", h), "gender")
        h.gender = "m"
        self.assertIsNone(question("first_name", "Kavran", h))
        self.assertEqual(question("hometown", "Smallville", auto_hint("hometown", "Smallville")), "place")
        self.assertEqual(question("hometown", "London", auto_hint("hometown", "London")), "which")
        self.assertEqual(auto_hint("hometown", "London, Ontario").city.country, "CA")
        self.assertEqual(auto_hint("hometown", "Bangalore").city.name, "Bengaluru")
        self.assertEqual(regions.parse_place("Alberta, Canada"), ("CA", "AB"))
        self.assertEqual(regions.parse_place("UK"), ("GB", None))
        # a surname we don't know takes the first name's style; a town we don't have, its province
        plan = make_plan({"first_name": "Kavran", "last_name": "Zzyzx", "hometown": "Vegreville, AB",
                          "mother_name": "Meena"}, random.Random(1),
                         hints={"first_name": Hint(style="south_asian", gender="m")})
        self.assertTrue(set(plan.alternatives["last_name"]) <= set(regions.NAMES["south_asian"]["last"]))
        self.assertTrue(all(regions.find_city(c)[0].region == "AB" for c in plan.alternatives["hometown"]))

    def test_typed_alternatives_and_exclusions(self):
        alts = foils("hometown", "Smallville", random.Random(1), hint=Hint(custom=["A", "b", "C", "D", "E"]))
        self.assertEqual(sorted(alts), ["A", "B", "C", "D", "E"])
        alts = foils("first_name", "Rohan", random.Random(1), exclude=["Kabir", "Arjun"],
                     hint=Hint(style="south_asian", gender="m"))
        self.assertFalse({"Rohan", "Kabir", "Arjun"} & set(alts))

    def test_other_details_never_appear_as_alternatives(self):
        vals = dict(ME, mother_name="Priya")
        for s in range(20):
            plan = make_plan(vals, random.Random(s))
            every = [a for alts in plan.alternatives.values() for a in alts]
            self.assertFalse(set(plan.values.values()) & set(every))


class PlanTests(unittest.TestCase):
    def test_parts_layout_and_privacy(self):
        plan = make_plan(ME, random.Random(2))
        blocks = plan.blocks(10)
        self.assertEqual([[g.gid for g in b.groups] for b in blocks], [[0, 2], [1, 3]])
        trials = [t for b in blocks for t in build_trials(b, random.Random(2))]
        self.assertEqual(len(trials), 2 * 10 * (2 * 6 + 3))
        self.assertEqual(Counter(t.group for t in trials if t.label == "TRUE"), {0: 10, 1: 10, 2: 10, 3: 10})
        self.assertAlmostEqual(sum(t.correct_key == "e" for t in trials) / len(trials), 0.2)
        rows = " ".join(str(t.log_row()) for t in trials)
        for v in ("Rohan", "Sharma", "Ludhiana", "Sunita"):
            self.assertNotIn(v, rows)
        for alts in plan.alternatives.values():
            for v in alts:
                self.assertNotIn(v, rows)

    def test_control_is_same_kind_and_never_real(self):
        for s in range(15):
            rng = random.Random(s)
            real = make_plan(CANADIAN, rng)
            cv = random_identity(rng, list(CANADIAN.values()), real.cats, real.hints)
            plan = make_plan(cv, rng, list(CANADIAN.values()), real.cats, control_hints(real.hints))
            shown = [plan.values[c] for c in plan.cats] + [a for v in plan.alternatives.values() for a in v]
            self.assertFalse(set(CANADIAN.values()) & set(shown))
            self.assertIn(cv["first_name"], regions.NAMES["anglo"]["m"])
            self.assertEqual(regions.find_city(cv["hometown"])[0].region, "AB")
            self.assertTrue(all(len(v) == 5 and len(set(v)) == 5 for v in plan.alternatives.values()))
            sh = plan.shown_before_test(control=True)
            self.assertTrue(all(len(sh[c]) == 6 and plan.values[c] in sh[c] for c in plan.cats))


class AccuracyTests(unittest.TestCase):
    def test_real_detected_control_quiet(self):
        """Realistic per-person effect: ~28 ms (Lukacs & Ansorge's autobiographical RT-CIT), varying
        between people (SD 15 ms), trial SD 80 ms, 4 details x 14 presentations in 2 parts (the
        default). Measured ~62% detected at p < .05 vs ~2-8% false alarms (METHOD.md)."""
        rng = np.random.default_rng(0)
        real = [run_subject(s, max(0.0, rng.normal(28, 15))) for s in range(30)]
        ctrl = [run_subject(100 + s, 0.0) for s in range(30)]
        hit = np.mean([r.score.p_known < 0.05 for r in real])
        fa = np.mean([r.score.p_known < 0.05 for r in ctrl])
        self.assertGreaterEqual(hit, 0.45)
        self.assertLessEqual(fa, 0.15)
        self.assertTrue(all(r.valid and r.engaged for r in real + ctrl))


if __name__ == "__main__":
    unittest.main()
