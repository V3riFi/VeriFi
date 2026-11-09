"""Calibration on simulated sessions with realistic noise (backend/simulate.py).

Calm answers must rarely be flagged and a clear reaction must be caught. Guards against the
scoring drifting back to "everything reads low" (or "everything reads high").
"""

import unittest

import numpy as np

from backend import simulate


class Calibration(unittest.TestCase):
    def test_calm_rarely_flagged_and_reaction_caught(self):
        rng = np.random.default_rng(5)
        sessions = [simulate.run_session(rng, {"hr": 10}) for _ in range(12)]
        calm = [a for s in sessions for a in s if not a["react"]]
        react = [a for s in sessions for a in s if a["react"]]
        calm_flagged = np.mean([a["band"] in simulate.FLAGGED for a in calm])
        react_flagged = np.mean([a["band"] in simulate.FLAGGED for a in react])
        calm_z = np.mean([a["z"] for a in calm])
        self.assertLess(calm_flagged, 0.10)
        self.assertGreater(react_flagged, 0.6)
        self.assertLess(abs(calm_z), 0.6, "calm answers should centre near z = 0")


if __name__ == "__main__":
    unittest.main()
