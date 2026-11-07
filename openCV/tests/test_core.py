"""Offline tests: no webcam, no MediaPipe needed (except the last, which is skipped without it).

    python -m unittest discover -s tests -v
"""
import math
import pathlib
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from vision import geometry as G  # noqa: E402
from vision.aggregator import WindowAggregator  # noqa: E402
from vision.blink import BlinkDetector, EyeState  # noqa: E402
from vision.config import AggregationConfig, BlinkConfig, HeadPoseConfig, VisionConfig  # noqa: E402
from vision.headpose import MODEL_POINTS, HeadPoseEstimator  # noqa: E402
from vision.output import window_message  # noqa: E402

FPS = 30.0


class BlinkTests(unittest.TestCase):
    def run_signal(self, ears, pitch=None):
        det = BlinkDetector(BlinkConfig())
        events = []
        for i, e in enumerate(ears):
            u = det.update(i / FPS, e, pitch[i] if pitch is not None else 0.0, 0.0)
            if u.blink_completed:
                events.append(("blink", u.blink_completed))
            if u.long_closure_completed:
                events.append(("long", u.long_closure_completed))
        return det, events

    def signal(self, seconds, dips, seed=0):
        """dips: list of (start_s, duration_s, depth_ear)."""
        rng = np.random.default_rng(seed)
        n = int(seconds * FPS)
        ears = 0.30 + rng.normal(0, 0.008, n)
        for start, dur, depth in dips:
            a, b = int(start * FPS), int((start + dur) * FPS)
            ears[a:b] = depth
        return ears

    def test_counts_real_blinks(self):
        dips = [(3 + 3 * k, 0.15, 0.08) for k in range(15)]   # 15 blinks, 150 ms each
        det, ev = self.run_signal(self.signal(50, dips))
        self.assertEqual(sum(1 for e in ev if e[0] == "blink"), 15)
        for _, d in ev:
            self.assertTrue(100 <= d <= 200, d)

    def test_rejects_single_frame_jitter(self):
        det, ev = self.run_signal(self.signal(20, [(5, 1 / FPS, 0.05), (10, 1 / FPS, 0.05)]))
        self.assertEqual(ev, [])

    def test_long_closure_not_a_blink(self):
        det, ev = self.run_signal(self.signal(20, [(8, 1.2, 0.05)]))
        self.assertEqual([e[0] for e in ev], ["long"])
        self.assertAlmostEqual(ev[0][1], 1200, delta=60)

    def test_lowered_eyelids_do_not_stick_closed(self):
        """Real-run bug: the eye opening drops to ~75% of open (looking down at the keys) and sits
        between the close (72%) and reopen (82%) thresholds -> stuck 'closed' for 20 s. Must recover
        within ~3 s, count no long closure, and keep counting blinks at the new level."""
        ears = self.signal(40, [])
        ears[int(10 * FPS):] = 0.30 * 0.70 + np.random.default_rng(1).normal(0, 0.004, len(ears) - int(10 * FPS))
        for k in range(5):                                   # blinks at the lowered level
            a = int((20 + 3 * k) * FPS)
            ears[a:a + int(0.15 * FPS)] = 0.06
        det, ev = self.run_signal(ears)
        self.assertNotIn("long", [e[0] for e in ev])
        self.assertEqual(sum(1 for e in ev if e[0] == "blink"), 5)
        self.assertEqual(det.state, EyeState.OPEN)

    def test_pose_gate_blocks_looking_down(self):
        ears = self.signal(20, [(8, 2.0, 0.12)])            # looking down: EAR drops for 2 s
        pitch = np.zeros(len(ears))
        pitch[int(7.9 * FPS):int(10.1 * FPS)] = -35.0
        det, ev = self.run_signal(ears, pitch)
        self.assertEqual(ev, [])

    def test_adaptive_threshold_small_eyes(self):
        # someone whose open EAR is 0.22: fixed 0.2 threshold would miss/flicker; adaptive must not
        dips = [(3 + 2 * k, 0.2, 0.09) for k in range(8)]
        ears = self.signal(22, dips) - 0.08
        det, ev = self.run_signal(ears)
        self.assertEqual(sum(1 for e in ev if e[0] == "blink"), 8)
        self.assertLess(det.close_threshold, 0.2)

    def test_blink_rate(self):
        dips = [(1 + 4 * k, 0.15, 0.08) for k in range(15)]  # one every 4 s = 15/min
        det, _ = self.run_signal(self.signal(61, dips))
        self.assertAlmostEqual(det.blink_rate_per_minute(60.9), 15.0, delta=2.5)
        self.assertEqual(det.current_eye_state, "open")


def euler_R(axis, deg):
    v = np.zeros(3)
    v["xyz".index(axis)] = math.radians(deg)
    return cv2.Rodrigues(v)[0]


class HeadPoseTests(unittest.TestCase):
    W, H = 640, 480

    def synth(self, R, mirror=False):
        cam = np.array([[self.W, 0, self.W / 2], [0, self.W, self.H / 2], [0, 0, 1]], float)
        img, _ = cv2.projectPoints(MODEL_POINTS @ R.T, np.zeros(3), np.array([0, 0, 2500.0]), cam, None)
        img = img.reshape(-1, 2)
        pts = np.zeros((478, 3), np.float32)
        for k, idx in enumerate(G.POSE_LANDMARKS):
            pts[idx, :2] = img[k]
        if mirror:
            pts[:, 0] = self.W - pts[:, 0]
        return pts

    def estimate(self, pts):
        est = HeadPoseEstimator(HeadPoseConfig())
        return est.estimate(pts, self.W, self.H, 0.0)

    def test_frontal_is_zero(self):
        for mirror in (False, True):
            p = self.estimate(self.synth(np.eye(3), mirror))
            for v in (p.pitch, p.yaw, p.roll):
                self.assertAlmostEqual(v, 0.0, delta=1.0)
            tx, ty, tz = p.translation          # regression: crashed on NumPy 2.5 with live faces
            self.assertAlmostEqual(tz, 2500.0, delta=50.0)

    def test_sign_conventions_match_physical_motion(self):
        for mirror in (False, True):
            for s in (+1, -1):
                # YAW: positive = nose moves to image right relative to the eye midpoint
                pts = self.synth(euler_R("y", 20 * s), mirror)
                eye_mid = (pts[33, 0] + pts[263, 0]) / 2
                cue = np.sign(pts[1, 0] - eye_mid)
                p = self.estimate(pts)
                self.assertEqual(np.sign(p.yaw), cue, f"yaw mirror={mirror}")
                self.assertAlmostEqual(abs(p.yaw), 20, delta=2)
                # PITCH: positive = looking up = nose tip moves up relative to eye/mouth centroid
                pts = self.synth(euler_R("x", 20 * s), mirror)
                mid_y = pts[[33, 263, 61, 291], 1].mean()
                base = self.synth(np.eye(3), mirror)
                base_mid = base[[33, 263, 61, 291], 1].mean()
                cue = -np.sign((pts[1, 1] - mid_y) - (base[1, 1] - base_mid))
                p = self.estimate(pts)
                self.assertEqual(np.sign(p.pitch), cue, f"pitch mirror={mirror}")
                self.assertAlmostEqual(abs(p.pitch), 20, delta=2)
                # ROLL: positive = clockwise in the image (image-right eye lower than image-left eye)
                pts = self.synth(euler_R("z", 20 * s), mirror)
                left, right = sorted([pts[33], pts[263]], key=lambda q: q[0])
                cue = np.sign(right[1] - left[1])
                p = self.estimate(pts)
                self.assertEqual(np.sign(p.roll), cue, f"roll mirror={mirror}")
                self.assertAlmostEqual(abs(p.roll), 20, delta=2)


class AggregatorTests(unittest.TestCase):
    def test_baseline_then_stress_raises_score(self):
        cfg = AggregationConfig(window_s=5.0, baseline_s=30.0)
        agg = WindowAggregator(cfg, HeadPoseConfig(), VisionConfig().anomaly_weights)
        blink = BlinkDetector(BlinkConfig())
        rng = np.random.default_rng(1)
        windows = []
        for i in range(int(90 * FPS)):
            t = i / FPS
            stressed = t > 50
            ear = 0.3 + rng.normal(0, 0.005)
            if (t % (2.0 if stressed else 4.0)) < 0.15:     # blink rate doubles under stress
                ear = 0.08
            blink.update(t, ear)
            speed = abs(rng.normal(30 if stressed else 5, 3))
            agg.add({"t": t, "face_present": 1.0, "ear": ear, "eye_closed": float(ear < 0.2),
                     "head_pitch": rng.normal(0, 2), "head_yaw": rng.normal(0, 2), "head_roll": 0.0,
                     "head_speed": speed, "head_tx": 0.0, "head_ty": 0.0, "head_tz": 2500.0,
                     "mouth_open": 0.05, "lip_thickness": 0.30 - (0.06 if stressed else 0) + rng.normal(0, 0.005),
                     "brow_raise": 0.4, "brow_inner_gap": 0.45 + rng.normal(0, 0.005),
                     "facial_motion_energy": abs(rng.normal(0.2, 0.05)), "gaze_offaxis": 0.0,
                     "gaze_shift": 0.0, "yawn": 0.0})
            if agg.due(t):
                windows.append(agg.compute(t, blink))
        calm = [w for w in windows if w["baseline_ready"] and w["vision_anomaly_score"] is not None][:2]
        late = windows[-1]
        self.assertTrue(calm, "baseline never became ready")
        self.assertLess(abs(calm[0]["vision_anomaly_score"]), 1.0)
        # only 3 of the 7 cues changed, so the weighted mean is diluted by design (fewer false alarms)
        self.assertGreater(late["vision_anomaly_score"], 1.0)
        for k in ("blink_rate_z", "head_motion_energy_z", "lip_compression_z"):
            self.assertGreater(late[k], 1.5, k)
        self.assertLess(abs(late["brow_furrow_z"]), 1.0)
        self.assertGreater(late["lip_compression"], 0.1)
        self.assertGreater(late["blink_rate"], 25)
        msg = window_message(late["window_seconds"], late)
        self.assertEqual(msg["source"], "vision")


@unittest.skipUnless((pathlib.Path(__file__).resolve().parent.parent / "vision/face_landmarker.task").exists(),
                     "model not downloaded")
class EngineSmokeTest(unittest.TestCase):
    def test_blank_frames_run_and_emit_windows(self):
        from vision.output import CallbackSink
        from vision.pipeline import VisionEngine
        msgs = []
        cfg = VisionConfig(show_window=False)
        eng = VisionEngine(cfg, [CallbackSink(msgs.append)])
        frame = np.zeros((480, 640, 3), np.uint8)
        for i in range(int(12 * FPS)):
            r = eng.process(frame, 1000.0 + i / FPS)
            self.assertEqual(r.record["face_present"], 0.0)
        eng.close()
        wins = [m for m in msgs if "window_features" in m]
        self.assertEqual(len(wins), 2)
        self.assertEqual(wins[0]["window_features"]["face_presence_ratio"], 0.0)


if __name__ == "__main__":
    unittest.main()
