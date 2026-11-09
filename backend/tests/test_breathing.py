"""Breathing estimated from the Arduino's breath events, and the simulated GSR.

Run from the repo root: python -m unittest discover -s backend/tests -t .
"""

import unittest

import numpy as np

from backend import confidence, scoring
from backend.server import SimulatedGSR


def pulses(seconds, every=4.0, hold=None):
    """(t, flex) at 10 Hz with a 1 every `every` seconds, none inside hold=(start, end)."""
    history, next_breath = [], 1.0
    for i in range(int(seconds * 10)):
        t = i / 10
        flex = 0
        if hold and hold[0] <= t < hold[1]:
            next_breath = hold[1]
        elif t >= next_breath:
            flex, next_breath = 1, t + every
        history.append((t, flex))
    return history


def as_samples(history):
    return [{"t": t, "flex": flex} for t, flex in history]


class BreathRate(unittest.TestCase):
    def test_normal_events_read_as_the_typical_rate(self):
        self.assertAlmostEqual(scoring.breath_rate(pulses(30), 30), 15.0, delta=1)

    def test_more_events_read_higher_but_only_partly(self):
        slow = scoring.breath_rate(pulses(30, every=8.0), 30)
        normal = scoring.breath_rate(pulses(30), 30)
        fast = scoring.breath_rate(pulses(30, every=2.0), 30)
        self.assertLess(slow, normal)
        self.assertLess(normal, fast)
        self.assertLess(fast, 25)  # events arrive at 30/min

    def test_one_missed_event_barely_moves_it(self):
        history = pulses(30)
        full = scoring.breath_rate(history, 30)
        first = next(i for i, (_, flex) in enumerate(history) if flex == 1)
        history[first] = (history[first][0], 0)
        self.assertLess(abs(scoring.breath_rate(history, 30) - full), 1.0)

    def test_noise_bursts_are_capped(self):
        noisy = [(i / 10, 1) for i in range(300)]
        self.assertLessEqual(scoring.breath_rate(noisy, 30), 25)

    def test_needs_enough_history_and_some_events(self):
        self.assertIsNone(scoring.breath_rate(pulses(10), 30))
        self.assertIsNone(scoring.breath_rate([(i / 10, 0) for i in range(300)], 30))

    def test_rate_falls_during_a_hold(self):
        steady = scoring.breath_rate(pulses(30), 30)
        held = scoring.breath_rate(pulses(30, hold=(18, 30)), 30)
        self.assertLess(held, steady)

    def test_old_continuous_values_are_not_breaths(self):
        self.assertIsNone(scoring.breath_rate([(i / 10, 120 + i) for i in range(300)], 30))


class BreathHold(unittest.TestCase):
    def setUp(self):
        self.baseline = scoring.Baseline(as_samples(pulses(40)), keys=())

    def hold_in(self, history):
        t = np.array([h[0] for h in history], dtype=float)
        flex = np.array([h[1] for h in history], dtype=float)
        return scoring.longest_hold(t, flex, t >= 40, self.baseline)

    def test_baseline_learns_the_typical_gap(self):
        self.assertAlmostEqual(self.baseline.breath_interval, 4.0, places=1)

    def test_one_missed_event_is_not_a_hold(self):
        history = [(t, 0 if 44 <= t < 46 else flex) for t, flex in pulses(50)]
        self.assertLessEqual(self.hold_in(history), scoring.HOLD_TOLERANCE * self.baseline.breath_interval)

    def test_normal_breathing_is_not_a_hold(self):
        self.assertLessEqual(self.hold_in(pulses(50)), scoring.HOLD_TOLERANCE * self.baseline.breath_interval)

    def test_a_long_gap_is_a_hold(self):
        self.assertGreater(self.hold_in(pulses(50, hold=(40, 49))), 8)


class SimulatedSkin(unittest.TestCase):
    def test_stays_at_the_average_and_inside_the_alert_lines(self):
        gsr = SimulatedGSR()
        values = [gsr.next() for _ in range(5000)]
        self.assertAlmostEqual(float(np.mean(values)), SimulatedGSR.AVERAGE, delta=0.2)
        limit = scoring.ALERT_SD * scoring.MIN_STD["gsr"]
        self.assertLess(max(abs(v - SimulatedGSR.AVERAGE) for v in values), limit)

    def test_has_no_weight_in_any_score(self):
        self.assertNotIn("gsr", scoring.STRESS_WEIGHTS)
        self.assertNotIn("gsr", scoring.SPIKE_WEIGHTS)
        keys = [s["key"] for cat in confidence.CATEGORIES.values() for s in cat["sensors"]]
        self.assertNotIn("gsr", keys)


if __name__ == "__main__":
    unittest.main()
