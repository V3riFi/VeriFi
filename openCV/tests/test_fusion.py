"""Slow sensor round, physiology scoring and the combined verdict (simulated sensor streams)."""
import math
import pathlib
import random
import sys
import tempfile
import unittest
from collections import Counter

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from interview.fusion import Physio, combine, physio_features, score_physio, slow_items, stouffer  # noqa: E402
from interview.identity import make_plan  # noqa: E402

ME = {"first_name": "Rohan", "last_name": "Sharma", "hometown": "Ludhiana", "mother_name": "Sunita"}


def session(seed, true_scr=1.0, other_scr=0.4, reps=3, hz=20.0, gsr_inverted=False):
    """Slow round with onsets, plus a simulated sensor stream: GSR drift + a skin-conductance response
    1-4 s after each word (bigger for the tested detail if the person knows it), noisy breathing
    that gets shallower after recognized words, noisy heart rate."""
    rng = np.random.default_rng(seed)
    plan = make_plan(ME, random.Random(seed))
    items = slow_items(plan, random.Random(seed), reps)
    t0, t = 1000.0, 1000.0
    for it in items:
        it.onset = t
        t += it.total_s
    ts = np.arange(t0 - 5, t + 5, 1 / hz)
    gsr = 5 + 0.02 * (ts - t0) / 60 + rng.normal(0, 0.05, ts.size)
    resp_amp = np.ones_like(ts)
    for it in items:
        amp = (true_scr if it.label == "TRUE" else other_scr) * rng.lognormal(0, 0.4)
        d = ts - it.onset
        k = (d > 1) & (d < 8)
        gsr[k] += amp * (1 - np.exp(-(d[k] - 1) / 0.8)) * np.exp(-(d[k] - 1) / 3.0)
        if it.label == "TRUE" and true_scr > other_scr:
            resp_amp[(d > 0) & (d < 6)] *= 0.8
    resp = resp_amp * np.sin(2 * math.pi * 0.25 * ts) + rng.normal(0, 0.05, ts.size)
    hr = 72 + rng.normal(0, 1.5, ts.size)
    if gsr_inverted:
        gsr = -gsr
    rows = [{**it.log_row(), "onset": it.onset} for it in items]
    return plan, rows, Physio(list(ts), {"gsr": list(gsr), "hr": list(hr), "resp": list(resp)})


class SlowRoundTests(unittest.TestCase):
    def test_layout(self):
        plan = make_plan(ME, random.Random(0))
        items = slow_items(plan, random.Random(0), reps=3)
        self.assertEqual(len(items), 4 * (1 + 3 * 5))
        for g in range(4):
            gi = [i for i in items if i.group == g]
            self.assertEqual(gi[0].kind, "buffer")
            self.assertEqual(Counter(i.label for i in gi if i.kind == "test"),
                             {"TRUE": 3, "F1": 3, "F2": 3, "F3": 3, "F4": 3})
            for s in {i.series for i in gi if i.kind == "test"}:
                self.assertEqual(len([i for i in gi if i.series == s]), 5)
        for a, b in zip(items, items[1:]):
            if a.group == b.group:
                self.assertNotEqual(a.label, b.label)
        self.assertTrue(all(6.6 <= i.total_s <= 8.0 and i.display_s == 3.0 for i in items))
        logged = " ".join(str(i.log_row()) for i in items)
        for v in ("Rohan", "Sharma", "Ludhiana", "Sunita"):
            self.assertNotIn(v, logged)


class PhysioTests(unittest.TestCase):
    def test_features_from_known_response(self):
        ts = list(np.arange(0, 20, 0.05))
        gsr = [5 + (1.0 if 11.5 < x < 14 else 0) for x in ts]
        p = Physio(ts, {"gsr": gsr})
        f = physio_features(p, 10.0)
        self.assertAlmostEqual(f["scr_amp"], 1.0, places=6)
        self.assertIsNone(f["hr_change"])
        self.assertIsNone(physio_features(p, 100.0)["scr_amp"])       # no data -> None, not 0

    def test_knower_detected_by_physiology(self):
        hits = 0
        for s in range(10):
            _, rows, ph = session(s)
            r = score_physio(rows, ph, n_perm=500)
            hits += r.known_rank == 1 and r.p_known < 0.05
        self.assertGreaterEqual(hits, 8)

    def test_control_quiet(self):
        ps = [score_physio(session(50 + s, true_scr=0.4)[1], session(50 + s, true_scr=0.4)[2], n_perm=300).p_known
              for s in range(20)]
        self.assertLessEqual(np.mean(np.array(ps) < 0.05), 0.15)

    def test_csv_roundtrip_and_inverted_gsr(self):
        _, rows, ph = session(3, gsr_inverted=True)
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "phys.csv"
            with open(path, "w") as f:
                f.write("t,gsr,hr,resp\n")
                for i, t in enumerate(ph.t):
                    f.write(f"{t},{ph.cols['gsr'][i]},{ph.cols['hr'][i]},{ph.cols['resp'][i]}\n")
            wrong = score_physio(rows, Physio.from_csv(str(path)), n_perm=300)
            right = score_physio(rows, Physio.from_csv(str(path), gsr_inverted=True), n_perm=300)
        self.assertEqual(right.known_rank, 1)
        self.assertGreater(right.items["TRUE"], wrong.items["TRUE"])


class FusionTests(unittest.TestCase):
    def test_stouffer(self):
        self.assertLess(stouffer({"a": 0.05, "b": 0.05}, {"a": 1, "b": 1}), 0.02)
        self.assertAlmostEqual(stouffer({"a": 0.05, "b": None}, {"a": 1, "b": 1}), 0.05, places=6)
        self.assertAlmostEqual(stouffer({"a": 0.3, "v": 0.0001}, {"a": 1, "v": 0}), 0.3, places=6)
        self.assertIsNone(stouffer({"a": None}, {"a": 1}))
        self.assertGreater(stouffer({"a": 0.9, "b": 0.9}, {"a": 1, "b": 1}), 0.9)

    def test_combined_verdict_and_null_rate(self):
        c = combine({"reaction_time": 0.08, "physiology": 0.06})
        self.assertEqual(c["used"], ["reaction_time", "physiology"])
        self.assertTrue(c["verdict"].startswith("RECOGNIZES"))            # two weak hints agree -> strong
        rng = np.random.default_rng(0)
        null = [combine({"reaction_time": rng.uniform(), "physiology": rng.uniform()})["p"] for _ in range(4000)]
        self.assertAlmostEqual(np.mean(np.array(null) < 0.05), 0.05, delta=0.012)   # valid under the null


if __name__ == "__main__":
    unittest.main()
