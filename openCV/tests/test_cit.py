"""Vision CIT epoching / scoring on synthetic data (no camera, no MediaPipe)."""
import json
import pathlib
import socket
import sys
import time
import unittest

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from vision.blink import BlinkDetector  # noqa: E402
from vision.cit import CitSession  # noqa: E402
from vision.config import BlinkConfig, CitConfig  # noqa: E402
from vision.output import item_message  # noqa: E402

FPS = 30.0
ITEMS = ["Ace of Spades", "7 of Hearts", "Queen of Clubs", "3 of Diamonds", "King of Hearts"]
TARGET = "Queen of Clubs"


def simulate(seed=0, target=TARGET, rounds=4, isi=7.0, warmup=5.0, blink_rate_scale=1.0):
    """Every item suppresses blinking a little (as seen in real runs); the recognized item more,
    plus pressed lips. `blink_rate_scale` changes the overall blink rate (between-run drift)."""
    rng = np.random.default_rng(seed)
    cit, blink = CitSession(CitConfig()), BlinkDetector(BlinkConfig())
    schedule, t = [], warmup
    for _ in range(rounds):
        for item in rng.permutation(ITEMS):
            schedule.append((t, str(item)))
            t += isi
    end = t + 7.0
    blink_starts = list(np.arange(1.0, warmup, 2.5))
    for onset, item in schedule:
        first = rng.uniform(2.4, 3.4) if item == target else rng.uniform(0.9, 2.2)
        blink_starts.append(onset + first)
        extra = rng.poisson(1.0 * blink_rate_scale)
        blink_starts += list(onset + first + rng.uniform(0.5, 5.5, extra))
    next_onset = 0
    for i in range(int(end * FPS)):
        tt = i / FPS
        while next_onset < len(schedule) and schedule[next_onset][0] <= tt:
            cit.mark_item(schedule[next_onset][1], schedule[next_onset][0])
            next_onset += 1
        active = cit.current_item(tt)
        is_target = active is not None and active[0] == target
        ear = 0.30 + rng.normal(0, 0.006)
        if any(s <= tt < s + 0.15 for s in blink_starts):
            ear = 0.08
        blink.update(tt, ear)
        cit.add_frame({
            "t": tt, "face_present": 1.0, "eye_openness": ear / 0.30,
            "lip_thickness": (0.285 if is_target else 0.30) + rng.normal(0, 0.006),
            "brow_inner_gap": 0.45 + rng.normal(0, 0.004),
            "head_speed": abs(rng.normal(5, 2)), "mouth_open": 0.1,
        })
        cit.collect_ready(tt, blink)
    return cit


class CitTests(unittest.TestCase):
    def test_epochs_and_series_from_occurrence(self):
        cit = simulate()
        self.assertEqual(len(cit.responses), 20)
        self.assertTrue(all(r.valid for r in cit.responses))
        series = sorted({e.series for e in cit.epochs("cit")})
        self.assertEqual(series, [0, 1, 2, 3])
        tgt = [e for e in cit.epochs("cit") if e.item == TARGET]
        self.assertTrue(all(e.features["first_blink_latency_s"] > 2.3 for e in tgt))

    def test_recognized_item_found_with_low_p(self):
        hits = 0
        for seed in range(5):
            target = ITEMS[seed]
            res = simulate(seed=seed, target=target).block_result(n_perm=1000)
            hits += res.top_item == target
            if res.top_item == target:
                self.assertLess(res.p_top, 0.1, f"seed {seed}")
        self.assertGreaterEqual(hits, 4)

    def test_robust_to_blink_rate_drift(self):
        # a subject who blinks 3x more overall must still be scored on the RELATIVE pattern
        res = simulate(seed=7, target="7 of Hearts", blink_rate_scale=3.0).block_result(n_perm=500)
        self.assertEqual(res.top_item, "7 of Hearts")

    def test_no_target_is_inconclusive(self):
        res = simulate(seed=3, target="not shown").block_result(n_perm=1000)
        self.assertGreater(res.p_top, 0.05)

    def test_invalid_epoch_when_face_missing(self):
        cit, blink = CitSession(CitConfig()), BlinkDetector(BlinkConfig())
        cit.mark_item("X", 1.0)
        for i in range(int(8 * FPS)):
            t = i / FPS
            cit.add_frame({"t": t, "face_present": 0.0})
            cit.collect_ready(t, blink)
        self.assertEqual(len(cit.responses), 1)
        self.assertFalse(cit.responses[0].valid)
        self.assertIsNone(cit.responses[0].features["first_blink_latency_s"])

    def test_custom_duration_and_meta(self):
        cit, blink = CitSession(CitConfig()), BlinkDetector(BlinkConfig())
        cit.mark_item("N1", 1.0, {"block": "narrative", "series": 0, "kind": "narrative"}, duration=20.0)
        for i in range(int(21 * FPS)):                  # window ends at 21.0 s (+0.6 s grace)
            t = i / FPS
            cit.add_frame({"t": t, "face_present": 1.0, "eye_openness": 1.0})
            cit.collect_ready(t, blink)
        self.assertEqual(len(cit.responses), 0)
        for i in range(int(21 * FPS), int(23 * FPS)):
            t = i / FPS
            cit.add_frame({"t": t, "face_present": 1.0, "eye_openness": 1.0})
            cit.collect_ready(t, blink)
        self.assertEqual(len(cit.responses), 1)
        r = cit.responses[0]
        self.assertEqual(r.meta["kind"], "narrative")
        self.assertEqual(r.features["blink_rate_post"], 0.0)
        self.assertEqual(r.features["first_blink_latency_s"], 20.0)

    def test_message_is_json_clean(self):
        r = simulate().responses[0]
        msg = json.loads(json.dumps(item_message(r.onset, r.item, r.valid, r.features, r.meta)))
        self.assertEqual(msg["source"], "vision")
        self.assertEqual(msg["block"], "cit")
        self.assertIn("eye_closure_early", msg["item_features"])

    def test_udp_control_channel(self):
        from vision.control import ControlListener, send_item_onset
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        got, resets = [], []
        lst = ControlListener(f"127.0.0.1:{port}", lambda i, t, *rest: got.append((i, t, rest)),
                              lambda: resets.append(1))
        try:
            send_item_onset("7 of Hearts", f"127.0.0.1:{port}", timestamp=123.5)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.sendto(b'{"cmd": "item_onset", "item": "N1", "block": "narrative", "kind": "narrative",'
                         b' "duration": 20}', ("127.0.0.1", port))
                s.sendto(b'{"cmd": "reset"}', ("127.0.0.1", port))
            deadline = time.time() + 2
            while (len(got) < 2 or not resets) and time.time() < deadline:
                time.sleep(0.02)
        finally:
            lst.close()
        self.assertEqual(got[0][:2], ("7 of Hearts", 123.5))
        self.assertEqual(got[1][2], ({"block": "narrative", "kind": "narrative"}, 20))
        self.assertEqual(resets, [1])


if __name__ == "__main__":
    unittest.main()
