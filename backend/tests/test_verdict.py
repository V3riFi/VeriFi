"""Per-answer verdicts (backend/verdict.py) on synthetic sessions. No hardware needed.

Run from the repo root:  python -m unittest discover -s backend/tests -t . -v
"""

import math
import unittest

import numpy as np

from backend import verdict

HZ = 10
BASELINE = (0.0, 40.0)        # silent baseline
VOICE = (40.0, 60.0)          # reading aloud (only in the voice tests)
Q_T = 70.0                    # the question


def make_session(seconds=90.0, seed=1, hr=None, blink=None, flex_amp=None, talking=None, drop=None,
                 only=None):
    """Samples at HZ. hr/blink/flex_amp/talking: functions of t that change the calm signal.

    drop: (start, end) with no samples. only: keep just these fields (plus t).
    """
    rng = np.random.default_rng(seed)
    samples = []
    for i in range(int(seconds * HZ)):
        t = i / HZ
        if drop and drop[0] <= t < drop[1]:
            continue
        amp = flex_amp(t) if flex_amp else 25.0
        s = {
            "t": t,
            "hr": 72 + 3 * math.sin(t / 7) + rng.normal(0, 0.5) + (hr(t) if hr else 0.0),
            "flex": 330 + amp * math.sin(2 * math.pi * 0.25 * t) + rng.normal(0, 1),
            "blink_rate": 17 + rng.normal(0, 1.5) + (blink(t) if blink else 0.0),
            "lip_compression": 0.05 + rng.normal(0, 0.01),
        }
        if talking and talking(t):
            s.update({"pitch": 120 + rng.normal(0, 8), "jitter": 1.5 + rng.normal(0, 0.3),
                      "shimmer": 9 + rng.normal(0, 1), "voice_db": 72 + rng.normal(0, 2),
                      "hnr": 12 + rng.normal(0, 1.5), "spectral_slope": -15 + rng.normal(0, 1)})
        if only:
            s = {k: v for k, v in s.items() if k == "t" or k in only}
        samples.append(s)
    return samples


def bump(size, start=Q_T + 1, end=Q_T + 9):
    """A change of `size` from `start` to `end`."""
    return lambda t: size if start <= t <= end else 0.0


def step(size, start=Q_T + 1):
    """A change of `size` from `start` onward."""
    return lambda t: size if t >= start else 0.0


def score(samples, q_end=None, until=None, voice=None, provisional=False):
    engine = verdict.Engine(samples, BASELINE, voice)
    until = until if until is not None else (q_end if q_end is not None else Q_T) + 10
    v, raw = verdict.score(engine, samples, Q_T, until, q_end, provisional)
    return v, raw, engine


def category(v, name):
    return next(c for c in v["categories"] if c["name"] == name)


def gate(v, key):
    return next(g for g in v["gates"] if g["key"] == key)


class Reactions(unittest.TestCase):
    def test_reaction_reads_high(self):
        # Two signal groups reacting together: one alone isn't enough to read as a reaction.
        samples = make_session(hr=step(20), blink=step(30))
        v, _, _ = score(samples)
        self.assertIn(v["band"], ("elevated", "high"))
        self.assertGreater(v["likelihood"], 0.6)
        self.assertEqual(v["result"], "reaction")
        self.assertGreater(category(v, "Cardiac")["z"], 2)
        lo, hi = v["interval"]
        self.assertLessEqual(lo, v["likelihood"])
        self.assertGreaterEqual(hi, v["likelihood"])

    def test_calm_answer_reads_low(self):
        v, _, _ = score(make_session())
        self.assertIn(v["band"], ("low", "unclear"))
        self.assertLess(v["likelihood"], 0.6)
        self.assertNotEqual(v["result"], "reaction")

    def test_breathing_is_scored_while_talking(self):
        # Talking makes breathing shallower. The answer is compared with breathing while reading
        # aloud (the voice baseline), so it is scored, and talking alone doesn't look suppressed.
        talking = lambda t: VOICE[0] <= t <= VOICE[1] or t >= Q_T + 1  # noqa: E731
        shallow = lambda t: 12.0 if talking(t) else 25.0  # noqa: E731
        samples = make_session(flex_amp=shallow, talking=talking)
        v, raw, engine = score(samples, voice=VOICE)
        breathing = category(v, "Breathing")
        self.assertIsNotNone(breathing["z"], "breathing must not be skipped while the subject talks")
        self.assertLess(breathing["suspicion"], 0.8)
        self.assertNotIn("Breathing", [c for c, _, _ in raw["skipped"]])
        self.assertIsNotNone(category(v, "Voice")["z"])

    def test_contributions_add_up_to_z(self):
        v, _, _ = score(make_session(hr=bump(10)))
        total = sum(c["contribution"] for c in v["categories"] if c["contribution"] is not None)
        self.assertAlmostEqual(total, v["z"], places=2)


class QualityGates(unittest.TestCase):
    def assert_fails(self, v, key):
        self.assertEqual(v["band"], "insufficient")
        self.assertIsNone(v["likelihood"])
        self.assertEqual(v["result"], "insufficient")
        self.assertFalse(gate(v, key)["pass"])
        self.assertEqual(v["reason"], gate(v, key)["detail"])

    def test_no_baseline(self):
        samples = make_session()
        v, _ = verdict.score(None, samples, Q_T, Q_T + 10)
        self.assert_fails(v, "baseline")

    def test_gap_in_the_window(self):
        v, _, _ = score(make_session(drop=(Q_T + 3, Q_T + 8)))
        self.assert_fails(v, "coverage")

    def test_too_few_signal_groups(self):
        v, _, _ = score(make_session(only={"hr"}))
        self.assert_fails(v, "signals")

    def test_all_gates_pass_on_a_clean_answer(self):
        v, _, _ = score(make_session())
        self.assertTrue(all(g["pass"] for g in v["gates"]))
        self.assertIsNone(v["reason"])


class Model(unittest.TestCase):
    def test_posterior_matches_the_formula(self):
        for z in (-1.0, 0.0, 1.0, 2.5):
            lr = math.exp(verdict.log_lr(z))
            n1 = math.exp(-0.5 * (z - verdict.LAMBDA) ** 2 / (1 + verdict.SIGMA ** 2)) / math.sqrt(1 + verdict.SIGMA ** 2)
            n0 = math.exp(-0.5 * z ** 2)
            self.assertAlmostEqual(lr, n1 / n0, places=6)
            p = verdict.PRIOR
            self.assertAlmostEqual(verdict.posterior(verdict.log_lr(z)), p * lr / (p * lr + 1 - p), places=6)

    def test_likelihood_never_rises_for_calmer_answers(self):
        zs = np.linspace(-10, 6, 200)
        likelihoods = [verdict.posterior(verdict.log_lr(z)) for z in zs]
        self.assertTrue(all(b >= a - 1e-12 for a, b in zip(likelihoods, likelihoods[1:])))

    def test_bands(self):
        self.assertEqual(verdict.band_of(0.9), "high")
        self.assertEqual(verdict.band_of(0.7), "elevated")
        self.assertEqual(verdict.band_of(0.4), "unclear")
        self.assertEqual(verdict.band_of(0.1), "low")
        self.assertEqual(verdict.band_of(None), "insufficient")


class Explanation(unittest.TestCase):
    def test_null_distribution_and_trace(self):
        v, _, engine = score(make_session(hr=bump(20)))
        cardiac = category(v, "Cardiac")
        null = cardiac["null"]
        self.assertEqual(sum(null["counts"]), null["n"])
        self.assertEqual(len(null["edges"]), len(null["counts"]) + 1)
        self.assertGreater(null["n"], 20)  # pseudo-questions across the 40 s baseline
        self.assertGreaterEqual(null["percentile"], 0.95)
        self.assertIn("hr", v["trace"]["series"])
        times = [p[0] for p in v["trace"]["series"]["hr"]["points"]]
        self.assertLessEqual(min(times), -9)
        self.assertGreaterEqual(max(times), 9)
        signals = cardiac["signals"][0]
        self.assertFalse(signals["phasic"])
        self.assertEqual(signals["direction"], 0)

    def test_provisional_is_light(self):
        samples = make_session(hr=bump(20))
        v, _, _ = score(samples, until=Q_T + 4, provisional=True)
        self.assertTrue(v["provisional"])
        self.assertNotIn("trace", v)
        self.assertNotIn("signals", category(v, "Cardiac"))


class Session(unittest.TestCase):
    def test_session_adds_up_the_answers(self):
        calm, _, _ = score(make_session())
        hot, _, _ = score(make_session(seed=2, hr=bump(20)))
        failed, _, _ = score(make_session(only={"hr"}))
        summary = verdict.session_summary([("Q1", calm), ("Q2", hot), ("Q3", failed)])
        self.assertEqual(summary["answers"], 2)
        self.assertAlmostEqual(summary["log_lr"], calm["log_lr"] + hot["log_lr"], places=3)
        self.assertEqual(len(summary["history"]), 3)
        self.assertEqual(summary["history"][-1]["cum_log_lr"], summary["history"][-2]["cum_log_lr"])
        self.assertEqual(summary["counts"]["insufficient"], 1)
        self.assertAlmostEqual(summary["z"], (calm["z"] + hot["z"]) / math.sqrt(2), places=2)

    def test_empty_session(self):
        summary = verdict.session_summary([])
        self.assertIsNone(summary["likelihood"])
        self.assertEqual(summary["band"], "insufficient")


if __name__ == "__main__":
    unittest.main()
