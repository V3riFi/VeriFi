"""Whole-session analysis from files (interview/analysis.py) on simulated sessions:
calibration on own details + mock-crime test, keyboard + sensor rounds, profile, verdict.
Checks the plumbing and the logic (guilty detected, innocent quiet, sim data never counted,
camera check capped), not real-world accuracy."""
import csv
import pathlib
import random
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from interview.analysis import analyse  # noqa: E402
from interview.case import case_plan, new_case  # noqa: E402
from interview.fusion import slow_items  # noqa: E402
from interview.identity import make_plan  # noqa: E402
from interview.verdict import code  # noqa: E402
from test_rtcit import simulate  # noqa: E402

ME = {"first_name": "Daniel", "last_name": "Mitchell", "hometown": "Edmonton", "mother_name": "Karen"}


def write_session(d, seed, guilty, rt_ms=35.0, scr_true=1.0, sim=False, hr_responder=False):
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    plan = make_plan(ME, rng)
    cp = case_plan(new_case(rng, "c", "guilty" if guilty else "innocent"))
    # sensor rounds: identity (known) then case, back to back
    items = slow_items(plan, rng, 2, block="identity_slow") + slow_items(cp, rng, 3, block="case_slow")
    t = 1000.0
    for it in items:
        it.onset = t
        knows = it.label == "TRUE" and (it.block == "identity_slow" or guilty)
        it.key, it.rt = "i", max(0.2, nrng.normal(0.75 + (0.08 if knows else 0), 0.12))
        t += it.total_s
    ts = np.arange(995.0, t + 8, 0.05)
    gsr = 5 + nrng.normal(0, 0.05, ts.size)
    hr = 72 + nrng.normal(0, 1.0, ts.size)
    for it in items:
        knows = it.label == "TRUE" and (it.block == "identity_slow" or guilty)
        dd = ts - it.onset
        k = (dd > 1) & (dd < 8)
        amp = (scr_true if knows else 0.4) * nrng.lognormal(0, 0.4)
        if hr_responder:
            amp = 0.4 * nrng.lognormal(0, 0.4)           # sweats the same for everything...
            if knows:
                hr[(dd > 0) & (dd < 5)] -= 4.0           # ...but the heart slows for known items
        gsr[k] += amp * (1 - np.exp(-(dd[k] - 1) / 0.8)) * np.exp(-(dd[k] - 1) / 3.0)
    with open(d / "s.physio.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "gsr", "hr", "resp", "ppg"] + (["sim"] if sim else []))
        for i in range(ts.size):
            w.writerow([f"{ts[i]:.3f}", f"{gsr[i]:.4f}", f"{hr[i]:.2f}", "", ""] + (["1"] if sim else []))
    with open(d / "s.slow.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(items[0].log_row()))
        w.writeheader()
        w.writerows(i.log_row() for i in items)
    trials = []
    for k, b in enumerate(cp.blocks(14, name="case")):
        trials += simulate("TRUE", effect_ms=rt_ms if guilty else 0.0, seed=seed * 7 + k, block=b)
    for k, b in enumerate(plan.blocks(10)):
        trials += simulate("TRUE", effect_ms=rt_ms, seed=seed * 7 + 5 + k, block=b)
    with open(d / "s.trials.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(trials[0].log_row()))
        w.writeheader()
        w.writerows(x.log_row() for x in trials)
    return str(d / "s")


def split(stem, a, b, in_a):
    """Copy a session's rows into two runs by block (physio goes to both, like two recordings)."""
    import shutil
    for kind in ("trials", "slow"):
        with open(f"{stem}.{kind}.csv", newline="") as f:
            rows = list(csv.DictReader(f))
        for dst, keep in ((a, True), (b, False)):
            with open(f"{dst}.{kind}.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(r for r in rows if in_a(r["block"]) == keep)
    for dst in (a, b):
        shutil.copy(f"{stem}.physio.csv", f"{dst}.physio.csv")


class SessionAnalysisTests(unittest.TestCase):
    def run_one(self, seed, guilty, **kw):
        with tempfile.TemporaryDirectory() as d:
            return analyse(write_session(pathlib.Path(d), seed, guilty, **kw), n_perm=300)

    def test_guilty_detected_innocent_quiet(self):
        g = [self.run_one(s, True) for s in range(6)]
        i = [self.run_one(100 + s, False) for s in range(6)]
        self.assertGreaterEqual(sum(code(r["verdict"]) == "recognizes" for r in g), 5)
        self.assertLessEqual(sum(code(r["verdict"]) == "recognizes" for r in i), 1)
        r = g[0]
        self.assertEqual(set(r["variants"]), {"rt_only", "rt_plus_sensors", "plus_gating", "plus_profile", "full"})
        self.assertEqual(r["channels_used"], ["reaction_time", "sensor_round"])
        self.assertTrue(r["has_physiology"])
        self.assertIn("scr_amp", r["profile"]["responders"])

    def test_profile_finds_a_heart_rate_responder(self):
        r = self.run_one(7, True, hr_responder=True)
        self.assertIn("hr_change", r["profile"]["responders"])
        self.assertNotIn("scr_amp", r["profile"]["responders"])
        w = r["profile"]["cue_weights"]
        self.assertGreater(w["hr_change"]["weight"], 0.5)              # up-weighted from its 0.5 prior
        self.assertLess(w["scr_amp"]["weight"], 1.0)                   # down from its 1.0 prior

    def test_simulated_sensors_never_count(self):
        r = self.run_one(3, True, sim=True)
        self.assertTrue(r["simulated_sensors"])
        self.assertFalse(r["has_physiology"])
        self.assertTrue(any("SIMULATED" in n for n in r["notes"]))

    def test_calibration_from_an_earlier_run(self):
        """Pitch mode: the identity rounds live in an earlier run's files. Same data split over two
        runs must learn the same profile and give the same verdict as one combined session."""
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            one = write_session(d, 7, True, hr_responder=True)
            split(one, d / "ident", d / "pitch", lambda b: b.startswith("identity"))
            both = analyse(one, n_perm=300)
            r = analyse(str(d / "pitch"), cal_stem=str(d / "ident"), n_perm=300)
            alone = analyse(str(d / "pitch"), n_perm=300)
            cal_without_sensors = analyse(str(d / "pitch"), cal_stem=str(d / "nothing"), n_perm=300)
        self.assertEqual(r["profile"]["cue_weights"], both["profile"]["cue_weights"])
        self.assertIn("hr_change", r["profile"]["responders"])
        self.assertEqual(code(r["verdict"]), code(both["verdict"]))
        self.assertEqual(r["rt"]["p"], both["rt"]["p"])
        self.assertIsNotNone(r["rt_calibration"])
        self.assertIsNone(alone["profile"])                   # without --calibration: no profile at all
        self.assertIsNone(cal_without_sensors["profile"])
        self.assertTrue(any("no sensor round" in n for n in cal_without_sensors["notes"]))

    def test_missing_files_give_no_result_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            r = analyse(str(pathlib.Path(d) / "nothing"), n_perm=50)
        self.assertTrue(r["verdict"].startswith("NO RESULT"))


class GatingCapTests(unittest.TestCase):
    def test_camera_check_that_flags_everything_is_not_applied(self):
        from interview.analysis import gate_trials
        from interview.rtcit import RtTrial
        from vision.offline import Frames
        t = np.arange(0, 100, 1 / 30)
        frames = Frames(t, {"face_present": np.ones_like(t), "eye_openness": np.full_like(t, 0.2)})  # "closed"
        trials = [RtTrial("case", 0, "TRUE", "x", "candidate", "i", 0, onset=1.0 + k) for k in range(60)]
        g = gate_trials(trials, frames)
        self.assertFalse(g["applied"])
        self.assertIn("NOT applied", g["summary"])
        self.assertTrue(all(not tr.gate for tr in trials))
        frames.cols["eye_openness"][(t > 10.0) & (t < 10.3)] = 0.2
        frames.cols["eye_openness"][~((t > 10.0) & (t < 10.3))] = 0.9
        g = gate_trials(trials, frames)
        self.assertTrue(g["applied"])
        self.assertEqual([tr.gate for tr in trials if tr.gate], ["eyes_closed"])


if __name__ == "__main__":
    unittest.main()
