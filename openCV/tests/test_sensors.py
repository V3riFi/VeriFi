"""Sensor bridge: line parsing, the recorder's CSV (the contract fusion.py reads), UDP input,
heart rate from a raw pulse wave, and that simulated data is marked."""
import json
import math
import pathlib
import socket
import sys
import tempfile
import time
import unittest

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from interview.fusion import Physio, hr_from_ppg  # noqa: E402
from sensors import SensorRecorder, UdpSource, parse_line, start_sensors, stop_sensors  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_line("512,74,301"), {"gsr": 512, "hr": 74, "resp": 301})
        self.assertEqual(parse_line("512 74 301\r\n"), {"gsr": 512, "hr": 74, "resp": 301})
        self.assertEqual(parse_line("GSR=512, HR: 74.5 breath:3"), {"gsr": 512, "hr": 74.5, "resp": 3})
        self.assertEqual(parse_line('{"eda": 1.5, "bpm": 70, "ms": 12345}'), {"gsr": 1.5, "hr": 70})
        self.assertEqual(parse_line("1234,512,74", columns=["t", "gsr", "hr"]), {"gsr": 512, "hr": 74})
        self.assertEqual(parse_line("pulse:530"), {"ppg": 530})

    def test_junk_is_skipped(self):
        for line in ("", "GSR,HR,RESP", "Sensor ready", "512,74", "init: 1", "{bad json", "nan,1,2"):
            self.assertIsNone(parse_line(line), line)


class RecorderTests(unittest.TestCase):
    def test_csv_roundtrip_with_gaps_and_finger_off(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(pathlib.Path(d) / "x.physio.csv")
            rec = SensorRecorder(path)
            for i in range(200):
                hr = "0" if 50 <= i < 60 else "72"            # 0 = no finger on the sensor
                rec.add_line(f"{500 + i},{hr},{i % 7}", ["gsr", "hr", "resp"], t=1000 + i * 0.05)
            rec.add_line("garbage", ["gsr", "hr", "resp"])
            rec.close()
            self.assertEqual((rec.n_ok, rec.n_bad), (200, 1))
            ph = Physio.from_csv(path)
            self.assertFalse(ph.simulated)
            self.assertEqual(set(ph.cols), {"gsr", "hr", "resp"})   # empty ppg column dropped
            self.assertTrue(math.isnan(ph.cols["hr"][55]))
            self.assertAlmostEqual(ph.rate(), 20.0, places=3)

    def test_sim_source_is_marked(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(pathlib.Path(d) / "s.physio.csv")
            rec, src = start_sensors("sim", path)
            time.sleep(0.4)
            stop_sensors(rec, src)
            ph = Physio.from_csv(path)
            self.assertTrue(ph.simulated)
            self.assertGreater(len(ph.t), 5)

    def test_udp_source(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(pathlib.Path(d) / "u.physio.csv")
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.close()
            rec = SensorRecorder(path)
            src = UdpSource(rec, f"127.0.0.1:{port}").start()
            tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for i in range(5):
                tx.sendto(json.dumps({"gsr": 1 + i, "hr": 70}).encode(), ("127.0.0.1", port))
            tx.sendto(b"7,71,0\n8,72,0", ("127.0.0.1", port))          # two lines in one packet
            time.sleep(0.5)
            tx.close()
            src.stop()
            rec.close()
            self.assertEqual(rec.n_ok, 7)


class PpgTests(unittest.TestCase):
    def test_heart_rate_from_pulse_wave(self):
        t = np.arange(0, 30, 0.01)
        bpm = 75.0
        ppg = 500 + 40 * np.sin(2 * np.pi * bpm / 60 * t) ** 21 + np.random.default_rng(0).normal(0, 1, t.size)
        hr = np.array(hr_from_ppg(list(t), list(ppg)))
        self.assertAlmostEqual(float(np.nanmedian(hr[t > 5])), bpm, delta=3)


if __name__ == "__main__":
    unittest.main()
