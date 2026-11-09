"""The microphone thread has to survive the mic dropping out between sessions.

Uses a stand-in for sounddevice, so no microphone is needed. Run from the repo root:
python -m unittest discover -s backend/tests -t .
"""

import contextlib
import io
import sys
import threading
import time
import types
import unittest
from unittest import mock

import numpy as np

from voice import live

SHORT = {"REOPEN_EVERY_S": 0.05, "STALL_S": 0.2, "UPDATE_S": 0.02, "CLEANUP_WAIT_S": 0.3}


class FakeAudio:
    """Like PortAudio: once a device has gone, it can't be opened until the device list is refreshed."""

    def __init__(self, close_raises=False, comes_back=True):
        self.device_ok = True
        self.comes_back = comes_back
        self.refreshes = 0
        self.streams = []
        self.close_raises = close_raises
        audio = self

        class InputStream:
            def __init__(self, samplerate, channels, dtype, device, callback):
                if not audio.device_ok:
                    raise RuntimeError("device unavailable")
                self.device, self.callback, self.alive = 0, callback, True
                audio.streams.append(self)

            def start(self):
                threading.Thread(target=self.feed, daemon=True).start()

            def feed(self):
                while self.alive and audio.device_ok:
                    self.callback(np.zeros((1600, 1), dtype="float32"), 1600, None, None)
                    time.sleep(0.02)

            def close(self):
                self.alive = False
                if audio.close_raises:
                    raise RuntimeError("close failed")

        self.module = types.ModuleType("sounddevice")
        self.module.InputStream = InputStream
        self.module.query_devices = lambda device: {"name": "Fake mic"}
        self.module._terminate = lambda: None
        self.module._initialize = self.refresh

    def refresh(self):
        self.refreshes += 1
        self.device_ok = self.comes_back

    def lose_device(self):
        self.device_ok = False


def wait_for(condition, timeout=12.0):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


class MicRecovery(unittest.TestCase):
    def run_tracker(self, audio):
        patches = [mock.patch.object(live, k, v, create=True) for k, v in SHORT.items()]
        patches.append(mock.patch.dict(sys.modules, {"sounddevice": audio.module}))
        patches.append(mock.patch.object(live, "live_features", return_value={"voice_db": 50.0}))
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        tracker = live.VoiceTracker()
        tracker._running = True
        self.addCleanup(tracker._stop.set)
        threading.Thread(target=tracker._run, daemon=True).start()
        self.assertTrue(wait_for(lambda: tracker.status == "ok"), "the mic should start")
        return tracker

    def test_comes_back_after_the_device_drops_out(self):
        audio = FakeAudio()
        tracker = self.run_tracker(audio)
        audio.lose_device()
        self.assertTrue(wait_for(lambda: tracker.status == "missing"), "the outage should be reported")
        self.assertTrue(wait_for(lambda: tracker.status == "ok"), "the mic should come back")
        self.assertGreaterEqual(audio.refreshes, 1)
        self.assertEqual(len(audio.streams), 2)

    def test_a_close_that_fails_does_not_end_the_thread(self):
        audio = FakeAudio(close_raises=True)
        tracker = self.run_tracker(audio)
        audio.lose_device()
        self.assertTrue(wait_for(lambda: tracker.status == "missing"))
        self.assertTrue(wait_for(lambda: tracker.status == "ok"), "the mic should come back")

    def test_old_audio_cannot_pass_as_live(self):
        audio = FakeAudio(comes_back=False)
        tracker = self.run_tracker(audio)
        audio.lose_device()
        self.assertTrue(wait_for(lambda: tracker.status == "missing"))
        with tracker._lock:
            history = len(tracker._history)
        self.assertEqual(history, 0)
        self.assertIsNone(tracker.latest())


if __name__ == "__main__":
    unittest.main()
