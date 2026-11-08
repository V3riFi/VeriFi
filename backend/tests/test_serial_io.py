"""The Arduino's serial lines. Run from the repo root: python -m unittest discover -s backend/tests -t ."""

import unittest

from backend.serial_io import parse_line


class ParseLine(unittest.TestCase):
    def test_bioamp_sketch(self):
        sample = parse_line("breath: 120, heartbeat: 72, raw_ecg: 515, raw_emg: 508")
        self.assertEqual(sample, {"flex": 120, "hr": 72, "spo2": None, "temp": None})

    def test_board_not_yet_reflashed_still_works_and_gsr_is_ignored(self):
        sample = parse_line("breath: 120, gsr: 480, heartbeat: 72, raw_ecg: 515, raw_emg: 508")
        self.assertEqual(sample, {"flex": 120, "hr": 72, "spo2": None, "temp": None})

    def test_pulse_module_sketch(self):
        sample = parse_line("breath: 512, heartbeat: 72, spo2: 98, temp: 31.5")
        self.assertEqual(sample, {"flex": 512, "hr": 72, "spo2": 98, "temp": 31.5})

    def test_no_heartbeat_yet_is_missing(self):
        self.assertIsNone(parse_line("breath: 1, heartbeat: 0, raw_ecg: 3, raw_emg: 4")["hr"])

    def test_cut_off_line_is_rejected(self):
        self.assertIsNone(parse_line("breath: 120, heartbeat: 7"))

    def test_status_line_is_rejected(self):
        self.assertIsNone(parse_line("STATUS: starting"))


if __name__ == "__main__":
    unittest.main()
