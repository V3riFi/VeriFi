"""RT-CIT design and scoring (no keyboard, no camera: simulated subjects)."""
import pathlib
import random
import sys
import unittest
from collections import Counter

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "examples"))

from interview.protocol import CARDS  # noqa: E402
from interview.rtcit import NO, YES, RtBlock, build_trials, score_rt  # noqa: E402

BLOCK = RtBlock.single("cards", [(c, c) for c in CARDS], ("Jack of Diamonds", "Jack of Diamonds"))


def simulate(probe, effect_ms=50.0, sd_ms=80.0, seed=0, err=0.05, target_miss=0.1, cycles=18, block=None):
    """Subject with ~450 ms RTs, trial SD 80 ms, probe slowed by `effect_ms`
    (group-specific base RTs added for multi-detail blocks: dates are read slower than names)."""
    rng = np.random.default_rng(seed)
    b = block or RtBlock.single(BLOCK.name, BLOCK.groups[0].candidates, BLOCK.groups[0].target, cycles=cycles)
    trials = build_trials(b, random.Random(seed))
    for t in trials:
        rt = 0.45 + 0.03 * max(t.group, 0) + rng.normal(0, sd_ms / 1000) + \
            (effect_ms / 1000 if t.label == probe and t.role == "candidate" else 0)
        miss = target_miss if t.role == "target" else err
        if rt > b.deadline_s:
            t.outcome, t.rt = "slow", None
        elif rng.random() < miss:
            t.outcome, t.rt, t.key = "wrong", max(rt, 0.2), (NO if t.correct_key == YES else YES)
        else:
            t.outcome, t.rt, t.key = "correct", max(rt, 0.2), t.correct_key
    return trials


class DesignTests(unittest.TestCase):
    def test_cycle_contents_and_keys(self):
        trials = build_trials(BLOCK, random.Random(3))
        self.assertEqual(len(trials), 18 * 9)                     # 5 cards + target + 3 fillers
        for c in range(18):
            cyc = [t for t in trials if t.cycle == c]
            self.assertEqual(sorted(t.label for t in cyc if t.role == "candidate"), sorted(CARDS))
            self.assertEqual(Counter(t.role for t in cyc),
                             {"candidate": 5, "target": 1, "filler_yes": 1, "filler_no": 2})
        for t in trials:
            self.assertEqual(t.correct_key, YES if t.role in ("target", "filler_yes") else NO)
        for a, b in zip(trials, trials[1:]):
            self.assertNotEqual(a.label, b.label)                 # never the same item twice in a row

    def test_staged_practice_sets(self):
        from interview.rtcit import practice_fillers, practice_targets
        f = practice_fillers(BLOCK, random.Random(0))
        self.assertEqual(Counter(t.correct_key for t in f), {YES: 6, NO: 6})
        self.assertTrue(all(t.role.startswith("filler") for t in f))
        p = practice_targets(BLOCK, random.Random(0))
        self.assertEqual(Counter(t.role for t in p), {"target": 2, "candidate": 2})

    def test_log_rows_hold_labels_not_text(self):
        from rt_cit_test import birthday_block
        import datetime as dt
        b = birthday_block(dt.date(2000, 3, 14), random.Random(0))
        rows = [t.log_row() for t in build_trials(b, random.Random(0))]
        self.assertTrue(all("March" not in str(r) and "text" not in r for r in rows))
        self.assertIn("TRUE", {r["label"] for r in rows})


class ScoringTests(unittest.TestCase):
    def test_finds_probe_at_literature_effect_size(self):
        hits, ps = 0, []
        for s in range(12):
            probe = CARDS[s % 5]
            r = score_rt(simulate(probe, seed=s), n_perm=500)
            hits += r.score.top_item == probe
            ps.append(r.score.p_top)
            self.assertTrue(r.valid)
        self.assertGreaterEqual(hits, 9)
        self.assertLess(np.median(ps), 0.1)

    def test_null_false_positives(self):
        ps = [score_rt(simulate(None, seed=100 + s), n_perm=300).score.p_top for s in range(30)]
        self.assertLessEqual(np.mean(np.array(ps) < 0.05), 0.15)

    def test_known_solution_and_d_cit(self):
        r = score_rt(simulate("7 of Hearts", effect_ms=60, seed=7), known_label="7 of Hearts", n_perm=500)
        self.assertEqual(r.score.known_rank, 1)
        self.assertGreater(r.d_cit, 0.4)
        self.assertGreater(r.mean_rt_ms["7 of Hearts"], max(v for k, v in r.mean_rt_ms.items() if k != "7 of Hearts"))

    def test_missing_yes_words_is_one_sided(self):
        """< 50% YES hits blocks 'no evidence' but not 'recognizes' (interview/verdict.py)."""
        from interview.verdict import verdict
        r = score_rt(simulate("Ace of Spades", seed=1, target_miss=0.8), n_perm=100)
        self.assertTrue(r.valid)
        self.assertFalse(r.engaged)
        self.assertTrue(any("YES words" in n for n in r.notes))
        self.assertTrue(verdict(0.01, r.valid, r.engaged).startswith("RECOGNIZES"))
        self.assertTrue(verdict(0.6, r.valid, r.engaged).startswith("INCONCLUSIVE"))
        self.assertTrue(verdict(0.6, True, True).startswith("NO EVIDENCE"))

    def test_pressing_yes_to_everything_is_invalid(self):
        r = score_rt(simulate("Ace of Spades", seed=2, err=0.4), n_perm=100)
        self.assertFalse(r.valid)
        self.assertTrue(any("candidates" in n for n in r.notes))

    def test_one_sided_rule_keeps_false_alarms_nominal(self):
        """Disengaged subjects who don't know the item: p < .05 must stay at ~5%."""
        ps = [score_rt(simulate(None, seed=300 + s, target_miss=0.7), known_label="Ace of Spades",
                       n_perm=300).score.p_known for s in range(40)]
        self.assertLessEqual(np.mean(np.array(ps) < 0.05), 0.125)


if __name__ == "__main__":
    unittest.main()
