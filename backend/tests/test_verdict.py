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


def make_session(seconds=90.0, seed=1, gsr=None, hr=None, breath_gap=None, talking=None, drop=None,
                 only=None):
    """Samples at HZ. gsr/hr/talking: functions of t that change the calm signal.

    Breathing is inhale pulses, as the firmware sends them (flex 1 where an inhale starts, else
    0). breath_gap: function of t giving the seconds until the next inhale (default 4 s).

    drop: (start, end) with no samples. only: keep just these fields (plus t).
    """
    rng = np.random.default_rng(seed)
    samples = []
    next_inhale = 1.0
    for i in range(int(seconds * HZ)):
        t = i / HZ
        if drop and drop[0] <= t < drop[1]:
            continue
        inhale = t >= next_inhale - 1e-9
        if inhale:
            next_inhale = t + (breath_gap(t) if breath_gap else 4.0)
        s = {
            "t": t,
            "gsr": 450 + rng.normal(0, 1.5) + (gsr(t) if gsr else 0.0),
            "hr": 72 + 3 * math.sin(t / 7) + rng.normal(0, 0.5) + (hr(t) if hr else 0.0),
            # The "+ 0 * rng" keeps the old breathing noise draw, so every other signal gets the
            # same random noise the tests were tuned with.
            "flex": float(inhale) + 0 * rng.normal(0, 1),
            "blink_rate": 17 + rng.normal(0, 1.5),
            "lip_compression": 0.05 + rng.normal(0, 0.01),
            "hand_on_face": False,
        }
        if talking and talking(t):
            s.update({"pitch": 120 + rng.normal(0, 8), "jitter": 1.5 + rng.normal(0, 0.3),
                      "shimmer": 9 + rng.normal(0, 1), "voice_db": 72 + rng.normal(0, 2),
                      "hnr": 12 + rng.normal(0, 1.5), "spectral_slope": -15 + rng.normal(0, 1)})
        if only:
            s = {k: v for k, v in s.items() if k == "t" or k in only}
        samples.append(s)
    return samples


def rise(start, size, over=3.0):
    """A skin conductance rise starting at `start`, reaching `size` after `over` seconds."""
    return lambda t: 0.0 if t < start else size * min(1.0, (t - start) / over)


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
        samples = make_session(gsr=rise(Q_T + 2, 40), hr=lambda t: 8.0 if Q_T + 1 <= t <= Q_T + 9 else 0.0)
        v, _, _ = score(samples)
        self.assertIn(v["band"], ("elevated", "high"))
        self.assertGreater(v["likelihood"], 0.6)
        self.assertEqual(v["result"], "reaction")
        skin = category(v, "Skin")
        self.assertEqual(max(v["categories"], key=lambda c: c["contribution"] or -9)["name"], "Skin")
        self.assertGreater(skin["z"], 3)
        lo, hi = v["interval"]
        self.assertLessEqual(lo, v["likelihood"])
        self.assertGreaterEqual(hi, v["likelihood"])

    def test_calm_answer_reads_low(self):
        v, _, _ = score(make_session())
        self.assertIn(v["band"], ("low", "unclear"))
        self.assertLess(v["likelihood"], 0.6)
        self.assertNotEqual(v["result"], "reaction")

    def test_done_asking_moves_the_skin_window(self):
        # The question takes 6 s to ask, and the skin reacts 2 s after it ends: outside
        # 1-5 s after Ask, inside 5 s after Done asking.
        q_end = Q_T + 6
        samples = make_session(seconds=100, gsr=rise(q_end + 1, 40))
        with_end, _, _ = score(samples, q_end=q_end)
        without_end, _, _ = score(samples, q_end=None, until=q_end + 10)
        # Without the rise the peak-minus-before measure still sits ~2 SD up (the peak of noise),
        # so "missed" means near that, far below the caught rise.
        self.assertGreater(category(with_end, "Skin")["z"], 6)
        self.assertLess(category(without_end, "Skin")["z"], 3)
        self.assertGreater(with_end["likelihood"], without_end["likelihood"])
        peak = next(w for w in with_end["trace"]["windows"] if w["key"] == "peak")
        self.assertAlmostEqual(peak["to"], 11.0, places=1)

    def test_breathing_is_scored_while_talking(self):
        # Talking slows breathing (longer gaps between inhales). The answer is compared with
        # breathing while reading aloud (the voice baseline), so it is scored, and talking alone
        # doesn't look like a held breath.
        talking = lambda t: VOICE[0] <= t <= VOICE[1] or t >= Q_T + 1  # noqa: E731
        slower = lambda t: 5.0 if talking(t) else 4.0  # noqa: E731
        samples = make_session(breath_gap=slower, talking=talking)
        v, raw, engine = score(samples, voice=VOICE)
        breathing = category(v, "Breathing")
        self.assertIsNotNone(breathing["z"], "breathing must not be skipped while the subject talks")
        self.assertLess(breathing["suspicion"], 0.8)
        self.assertNotIn("Breathing", [c for c, _, _ in raw["skipped"]])
        self.assertIsNotNone(category(v, "Voice")["z"])

    def test_breath_hold_after_question_is_suspicious(self):
        # A ~9 s gap between inhales starting at the question, against a normal 4 s.
        hold = lambda t: 9.0 if Q_T - 1 <= t < Q_T + 3 else 4.0  # noqa: E731
        held, _, _ = score(make_session(breath_gap=hold))
        calm, _, _ = score(make_session())
        self.assertGreater(category(held, "Breathing")["z"], 1.0)
        self.assertLess(abs(category(calm, "Breathing")["z"]), 1.0)

    def test_contributions_add_up_to_z(self):
        v, _, _ = score(make_session(gsr=rise(Q_T + 2, 15)))
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

    def test_skin_sensor_reads_zero(self):
        samples = make_session()
        for s in samples:
            s["gsr"] = 0.0
        v, _, _ = score(samples)
        self.assert_fails(v, "skin")
        self.assertIn("not touching", v["reason"])

    def test_gap_in_the_window(self):
        v, _, _ = score(make_session(drop=(Q_T + 3, Q_T + 8)))
        self.assert_fails(v, "coverage")

    def test_too_few_signal_groups(self):
        v, _, _ = score(make_session(only={"gsr"}))
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
        v, _, engine = score(make_session(gsr=rise(Q_T + 2, 40)))
        skin = category(v, "Skin")
        null = skin["null"]
        self.assertEqual(sum(null["counts"]), null["n"])
        self.assertEqual(len(null["edges"]), len(null["counts"]) + 1)
        self.assertGreater(null["n"], 20)  # pseudo-questions across the 40 s baseline
        self.assertGreaterEqual(null["percentile"], 0.95)
        self.assertIn("gsr", v["trace"]["series"])
        times = [p[0] for p in v["trace"]["series"]["gsr"]["points"]]
        self.assertLessEqual(min(times), -9)
        self.assertGreaterEqual(max(times), 9)
        signals = skin["signals"][0]
        self.assertTrue(signals["phasic"])
        self.assertEqual(signals["direction"], 1)

    def test_provisional_is_light(self):
        samples = make_session(gsr=rise(Q_T + 2, 40))
        v, _, _ = score(samples, until=Q_T + 4, provisional=True)
        self.assertTrue(v["provisional"])
        self.assertNotIn("trace", v)
        self.assertNotIn("signals", category(v, "Skin"))


class Session(unittest.TestCase):
    def test_session_adds_up_the_answers(self):
        calm, _, _ = score(make_session())
        hot, _, _ = score(make_session(seed=2, gsr=rise(Q_T + 2, 40)))
        failed, _, _ = score(make_session(only={"gsr"}))
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
