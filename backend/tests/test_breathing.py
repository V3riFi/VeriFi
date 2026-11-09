"""Breathing from inhale pulses (firmware 8791a0f): rate, baseline gap, breath hold.

Run from the repo root:  python -m pytest backend/tests
"""

import unittest

from backend import scoring

HZ = 10
Q_T = 70.0


def session(inhales, seconds=90.0):
    """Samples at HZ with flex 1 at each inhale time, 0 otherwise. Breathing only, so a
    question's spike is the breath hold alone."""
    marks = {round(t * HZ) for t in inhales}
    return [{"t": i / HZ, "flex": 1.0 if i in marks else 0.0} for i in range(int(seconds * HZ))]


def every(gap, start, end):
    t, out = start, []
    while t < end:
        out.append(t)
        t += gap
    return out


class BreathRate(unittest.TestCase):
    def test_inhale_every_4_s_is_15_per_minute(self):
        samples = session(every(4.0, 2.0, 60.0), seconds=60.0)
        history = [(s["t"], s["flex"]) for s in samples if s["t"] >= 30.0]
        self.assertAlmostEqual(scoring.breath_rate(history, 30.0), 15.0, delta=1.0)

    def test_none_until_enough_history(self):
        history = [(s["t"], s["flex"]) for s in session(every(4.0, 2.0, 10.0), seconds=10.0)]
        self.assertIsNone(scoring.breath_rate(history, 30.0))

    def test_no_inhales_is_zero(self):
        history = [(i / HZ, 0.0) for i in range(300)]
        self.assertEqual(scoring.breath_rate(history, 30.0), 0.0)


class BreathHold(unittest.TestCase):
    def baseline(self, samples):
        return scoring.Baseline([s for s in samples if s["t"] < 60.0])

    def test_baseline_stores_median_gap(self):
        self.assertAlmostEqual(self.baseline(session(every(4.0, 2.0, 90.0))).breath_gap, 4.0, places=1)

    def test_9_s_gap_after_question_is_a_hold(self):
        # Normal 4 s breathing, an inhale at the question, then nothing for 9 s.
        samples = session(every(4.0, 2.0, Q_T) + [Q_T] + every(4.0, Q_T + 9.0, 90.0))
        spike, signals = scoring.response(samples, Q_T, self.baseline(samples))
        self.assertAlmostEqual(signals["breath_hold_s"], 9.0, places=1)
        self.assertAlmostEqual(spike, 5.0, places=1)  # 9 s gap - 4 s normal gap

    def test_normal_breathing_is_no_hold(self):
        samples = session(every(4.0, 2.0, 90.0))
        spike, signals = scoring.response(samples, Q_T, self.baseline(samples))
        self.assertAlmostEqual(signals["breath_hold_s"], 4.0, places=1)
        self.assertEqual(spike, 0.0)

    def test_no_baseline_gap_means_no_hold_score(self):
        samples = session([1.0])  # one inhale: no normal gap to compare with
        _, signals = scoring.response(samples, Q_T, self.baseline(samples))
        self.assertNotIn("breath_hold_s", signals)


if __name__ == "__main__":
    unittest.main()
