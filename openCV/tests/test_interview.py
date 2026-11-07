"""Interview protocol, runner and generic scoring (pure Python, no camera)."""
import datetime
import pathlib
import random
import sys
import threading
import unittest
from collections import Counter

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from interview.protocol import (CARDS, build_steps, card_interview, mock_crime_interview,  # noqa: E402
                                series_orders, total_seconds)
from interview.facts import OPEN_QUESTIONS, TARGET, PersonalFacts, facts_steps, parse_birthday  # noqa: E402
from interview.runner import InterviewRunner  # noqa: E402
from interview.scoring import Cue, Epoch, calibrate, default_params, score_block, series_z  # noqa: E402

ITEMS = ["A", "B", "C", "D", "E"]
FACTS = PersonalFacts("Daniel", "green", datetime.date(1999, 6, 21), "Lakeview College",
                      today=datetime.date(2026, 11, 6))


def make_epochs(target, n_series=4, effect=1.2, seed=0, drift=0.0, cue="x", block="b"):
    rng = np.random.default_rng(seed)
    eps = []
    for s in range(n_series):
        for it in ITEMS:
            v = rng.normal(0, 1) + drift * s + (effect if it == target else 0.0)
            eps.append(Epoch(block, s, it, True, {cue: v}))
    return eps


class ScoringTests(unittest.TestCase):
    def test_series_z_removes_drift(self):
        m = np.array([[1, 2, 3, 4, 5], [11, 12, 13, 14, 15]], float)
        z = series_z(m)
        np.testing.assert_allclose(z[0], z[1])

    def test_drift_between_series_changes_nothing(self):
        cues = [Cue("x", +1, 1.0)]
        a = score_block(make_epochs("C", effect=1.5, drift=0.0), default_params(cues), n_perm=500)
        b = score_block(make_epochs("C", effect=1.5, drift=5.0), default_params(cues), n_perm=500)
        self.assertEqual(a.ranking()[0][0], b.ranking()[0][0])
        for k in a.items:
            self.assertAlmostEqual(a.items[k], b.items[k], places=9)
        self.assertAlmostEqual(a.p_top, b.p_top)

    def test_strong_effect_is_strong(self):
        cues = [Cue("x", +1, 1.0)]
        res = score_block(make_epochs("C", effect=2.5, drift=5.0), default_params(cues), n_perm=1000)
        self.assertEqual(res.top_item, "C")
        self.assertLess(res.p_top, 0.05)
        self.assertEqual(res.label, "strong")

    def test_false_positive_rate_under_null(self):
        cues = [Cue("x", +1, 1.0)]
        ps = [score_block(make_epochs(None, seed=s), default_params(cues), n_perm=300).p_top for s in range(40)]
        self.assertLessEqual(np.mean(np.array(ps) < 0.05), 0.15)     # ~5% expected

    def test_known_solution_mode(self):
        cues = [Cue("x", +1, 1.0)]
        res = score_block(make_epochs("D", effect=2.0), default_params(cues), known_item="D", n_perm=1000)
        self.assertEqual(res.known_rank, 1)
        self.assertLess(res.p_known, 0.05)

    def test_negative_sign_cue(self):
        cues = [Cue("x", -1, 1.0)]
        res = score_block(make_epochs("B", effect=-2.0), default_params(cues), n_perm=500)
        self.assertEqual(res.top_item, "B")

    def test_calibration_reweights_and_learns_sign(self):
        cues = [Cue("good", +1, 1.0), Cue("bad", +1, 1.0), Cue("twosided", 0, 1.0)]
        rng = np.random.default_rng(0)
        eps = []
        for s in range(3):
            for it in ITEMS:
                tgt = it == "C"
                eps.append(Epoch("cal", s, it, True, {
                    "good": rng.normal(0, .3) + (2.0 if tgt else 0),
                    "bad": rng.normal(0, .3) - (1.5 if tgt else 0),
                    "twosided": rng.normal(0, .3) - (2.0 if tgt else 0)}))
        p = calibrate(eps, "C", cues)
        self.assertGreater(p["good"].weight, 1.5)
        self.assertLessEqual(p["bad"].weight, 0.3)
        self.assertEqual(p["twosided"].sign, -1)
        self.assertGreater(p["twosided"].weight, 1.0)

    def test_buffer_and_invalid_epochs_ignored(self):
        cues = [Cue("x", +1, 1.0)]
        eps = make_epochs("A", effect=2.0)
        eps.append(Epoch("b", -1, "A", True, {"x": -100.0}, kind="buffer"))
        eps.append(Epoch("b", 0, "E", False, {"x": 1000.0}))
        res = score_block(eps, default_params(cues), n_perm=200)
        self.assertEqual(res.top_item, "A")


class ProtocolTests(unittest.TestCase):
    def test_series_orders_constraints(self):
        rng = random.Random(1)
        for _ in range(50):
            orders = series_orders(CARDS, 4, rng, avoid_first="Jack of Diamonds")
            for o in orders:
                self.assertEqual(sorted(o), sorted(CARDS))
            for a, b in zip(orders, orders[1:]):
                self.assertNotEqual(a[-1], b[0])

    def test_card_interview_structure(self):
        steps = build_steps(card_interview(FACTS), seed=3)
        kinds = [s.kind for s in steps]
        self.assertEqual(kinds[:3], ["setup_check", "instructions", "rest"])
        self.assertEqual(kinds[-2:], ["post_test", "results"])
        self.assertEqual([s.title for s in steps if s.block == "open_questions"], OPEN_QUESTIONS)
        for block, n_series in (("facts", 5), ("cards", 4)):
            items = [s for s in steps if s.block == block]
            self.assertEqual(items[0].marker, "buffer")             # buffer first, never scored
            tests = [s for s in items if s.marker == "test"]
            self.assertEqual(len(tests), 5 * n_series)
            self.assertEqual(set(Counter(s.item for s in tests).values()), {n_series})
            self.assertTrue(all(6.6 <= s.duration <= 8.0 for s in items))
        self.assertLess(total_seconds(build_steps(card_interview(FACTS, quick=True))), total_seconds(steps))
        self.assertLess(total_seconds(steps), 11 * 60)

    def test_facts_round_layout_and_privacy(self):
        for seed in range(30):
            steps = facts_steps(FACTS, random.Random(seed))
            for s in range(5):
                q = [st for st in steps if st.series == s]
                self.assertEqual(q[0].marker, "buffer")              # each question opens with a buffer foil
                self.assertNotEqual(q[1].item, TARGET)               # true answer never first
                self.assertEqual(sorted(st.item for st in q[1:]), sorted([TARGET, "F1", "F2", "F3", "F4"]))
                titles = [st.title for st in q]
                self.assertEqual(len(set(titles)), 6)                # foils differ from each other and the truth
            true_titles = {st.title for st in steps if st.item == TARGET}
            self.assertEqual(true_titles, {"Is your first name Daniel?", "Is your T-shirt green?",
                                           "Is today November 6?", "Is your birthday June 21?",
                                           "Are you at Lakeview College?"})
            # labels / blocks / markers (what gets logged) never contain an answer
            for st in steps:
                for v in ("Daniel", "green", "June", "Lakeview", "November"):
                    self.assertNotIn(v, f"{st.item}{st.block}{st.marker}{st.subtitle}")

    def test_parse_birthday(self):
        for txt in ("March 14", "Mar 14", "14 March", "2004-03-14", "March 14, 2004"):
            d = parse_birthday(txt)
            self.assertEqual((d.month, d.day), (3, 14))
        self.assertEqual(parse_birthday("Feb 29").day, 29)
        with self.assertRaises(ValueError):
            parse_birthday("someday")

    def test_series_rank_per_question(self):
        eps = make_epochs("C", n_series=5, effect=3.0, seed=2)
        res = score_block(eps, default_params([Cue("x", +1, 1.0)]), known_item="C", n_perm=200)
        ranks = res.series_rank("C")
        self.assertEqual(sorted(ranks), [0, 1, 2, 3, 4])
        self.assertGreaterEqual(sum(r == 1 for r in ranks.values()), 3)

    def test_mock_crime_has_yes_no_and_three_questions(self):
        steps = build_steps(mock_crime_interview(FACTS), seed=0)
        markers = Counter(s.marker for s in steps)
        self.assertEqual(markers["known_truth"], 3)
        self.assertEqual(markers["directed_lie"], 2)
        self.assertEqual({s.block for s in steps if s.marker == "test"}, {"facts", "object", "codeword", "room"})


class RunnerTests(unittest.TestCase):
    def test_runner_plays_steps_and_reports_onsets(self):
        from interview.protocol import Step
        steps = [Step("setup_check", 5.0, "Setup"),
                 Step("instructions", 5.0, "Hi", wait_for_key=True),
                 Step("rest", 0.1, "+", block="rest", item="rest", marker="rest"),
                 Step("item", 0.2, "Q?", block="b", item="A", series=0, marker="test", display_s=0.05),
                 Step("results", 0, "Done")]
        shown, onsets = [], []
        r = InterviewRunner(steps, lambda a, b: shown.append(a), lambda s, t: onsets.append(s.item),
                            check=lambda: (True, "ok"))
        r.SETUP_HOLD_S = 0.3
        th = threading.Thread(target=r.run)
        th.start()
        while "Hi" not in shown:
            pass
        r.press_key()
        th.join(5)
        self.assertTrue(r.finished.is_set())
        self.assertEqual(onsets, ["rest", "A"])
        self.assertIn("+", shown)          # fixation after display_s
        self.assertEqual(shown[-1], "Done")


if __name__ == "__main__":
    unittest.main()
