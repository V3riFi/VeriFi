"""Live voice features for the backend: pitch, jitter, shimmer, loudness.

Imported by the backend, which runs it in a background thread (like vision/face.py):

    voice = VoiceTracker()
    voice.start()      # opens the mic, analyzes in the background
    voice.latest()     # -> {"pitch": 182.4, "jitter": 1.1, "shimmer": 6.3, "voice_db": 61.0}
                       #    pitch/jitter/shimmer are None while nobody is talking
    voice.status       # "ok" | "missing" | "off"
    voice.stop()

Uses the same Praat measurements as voice/analyze.py (the standalone recorder), but
works on audio in memory: every UPDATE_S it analyzes the last WINDOW_S seconds of
the mic. Jitter and shimmer need several vocal-fold cycles, so a 1 s sliding window
is steadier than separate 0.2 s chunks.

Only the backend opens the mic while it runs. Don't run voice/analyze.py at the same time.

Env variable:
  VERIFI_VOICE  input device index or name (default: system default mic), or "off"
"""

from __future__ import annotations  # allows "X | None" hints on Python 3.9

import math
import os
import threading
import time
from collections import deque

import numpy as np

SAMPLE_RATE = 16000     # plenty for speech pitch, and cheaper to analyze than 44.1 kHz
WINDOW_S = 1.0          # audio analyzed each update
UPDATE_S = 0.2          # how often features are recomputed
PITCH_FLOOR = 75        # Hz, the same 75-500 Hz search range as voice/analyze.py
PITCH_CEILING = 500
MIN_VOICED_S = 0.15     # less voiced audio than this in the window = not speaking
REOPEN_EVERY_S = 5.0
LOUDNESS_S = 0.2        # loudness uses only the newest audio so it reacts quickly


def _finite(x: float) -> float | None:
    return None if x is None or math.isnan(x) or math.isinf(x) else x


def analyze(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> dict:
    """Voice features for a mono float array (-1..1). Never raises on silence or short audio."""
    import parselmouth
    from parselmouth import praat

    out = {"pitch": None, "jitter": None, "shimmer": None, "voice_db": None}
    sound = parselmouth.Sound(samples.astype(np.float64), sampling_frequency=sample_rate)
    if sound.duration < 3 / PITCH_FLOOR:  # Praat needs 3 periods of the lowest pitch
        return out

    recent = samples[-int(LOUDNESS_S * sample_rate):]
    loud = parselmouth.Sound(samples.recent.astype(np.float64), sampling_frequency=sample_rate)
    intensity = loud.to_intensity(minimum_pitch=PITCH_FLOOR)
    out["voice_db"] = _finite(praat.call(intensity, "Get mean", 0, 0, "energy"))

    pitch = sound.to_pitch(pitch_floor=PITCH_FLOOR, pitch_ceiling=PITCH_CEILING)
    voiced = pitch.selected_array["frequency"] > 0
    if voiced.sum() * pitch.time_step < MIN_VOICED_S:
        return out  # silence or noise: leave pitch/jitter/shimmer as None (a gap on the chart)

    out["pitch"] = _finite(praat.call(pitch, "Get mean", 0, 0, "Hertz"))
    points = praat.call(sound, "To PointProcess (periodic, cc)", PITCH_FLOOR, PITCH_CEILING)
    jitter = _finite(praat.call(points, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3))
    shimmer = _finite(praat.call([sound, points], "Get shimmer (local)", 0, 0, 0.0001, 0.02, 1.3, 1.6))
    out["jitter"] = None if jitter is None else jitter * 100     # percent
    out["shimmer"] = None if shimmer is None else shimmer * 100  # percent
    return out


class VoiceTracker:
    def __init__(self) -> None:
        setting = os.environ.get("VERIFI_VOICE", "").strip()
        self.disabled = setting.lower() == "off"
        self.device = int(setting) if setting.isdigit() else (setting or None)
        self.status = "off" if self.disabled else "missing"  # "ok" | "missing" | "off"
        self._audio: deque[np.ndarray] = deque()
        self._audio_len = 0
        self._latest: tuple[float, dict] | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_block = 0.0

    def start(self) -> None:
        if self.disabled:
            return
        try:
            import parselmouth  # noqa: F401
            import sounddevice  # noqa: F401
        except ImportError as e:
            print(f"voice: {e.name} isn't installed, voice is off", flush=True)
            self.status = "off"
            return
        threading.Thread(target=self._run, name="voice", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def latest(self, max_age_s: float = 1.0) -> dict | None:
        """Newest features, or None if there's nothing recent (mic missing or stalled)."""
        with self._lock:
            if self._latest is None or time.time() - self._latest[0] > max_age_s:
                return None
            return self._latest[1]

    # ------------------------------------------------------------ internals

    def _on_audio(self, indata, frames, time_info, status) -> None:
        block = indata[:, 0].copy()
        with self._lock:
            self._audio.append(block)
            self._audio_len += len(block)
            keep = int(WINDOW_S * SAMPLE_RATE)
            while self._audio_len - len(self._audio[0]) >= keep:
                self._audio_len -= len(self._audio.popleft())
            self._last_block = time.time()

    def _open(self):
        import sounddevice as sd

        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                device=self.device, callback=self._on_audio,
            )
            stream.start()
        except Exception as e:
            print(f"voice: couldn't open the microphone ({e})", flush=True)
            return None
        print(f"voice: listening on {sd.query_devices(stream.device)['name']}", flush=True)
        return stream

    def _run(self) -> None:
        stream = None
        next_tick = time.monotonic()
        while not self._stop.is_set():
            if stream is None:
                self.status = "missing"
                stream = self._open()
                if stream is None:
                    self._stop.wait(REOPEN_EVERY_S)
                    continue
                self._last_block = time.time()

            next_tick += UPDATE_S
            self._stop.wait(max(0.0, next_tick - time.monotonic()))

            now = time.time()
            if now - self._last_block > 2.0:  # mic unplugged or stream died
                print("voice: microphone stopped sending audio, reopening", flush=True)
                stream.close()
                stream = None
                continue

            with self._lock:
                if self._audio_len < int(0.5 * WINDOW_S * SAMPLE_RATE):
                    continue
                audio = np.concatenate(self._audio)
            try:
                features = analyze(audio)
            except Exception as e:  # never let one bad window kill the thread
                print(f"voice: analysis error: {e}", flush=True)
                continue
            with self._lock:
                self._latest = (now, features)
            self.status = "ok"
            next_tick = max(next_tick, time.monotonic())  # don't burst if we fell behind

        if stream is not None:
            stream.close()


if __name__ == "__main__":
    # Quick mic check without the backend: prints features 5 times a second.
    tracker = VoiceTracker()
    tracker.start()
    try:
        while True:
            time.sleep(UPDATE_S)
            f = tracker.latest()
            if f:
                print("  ".join(f"{k}={'-' if v is None else round(v, 1)}" for k, v in f.items()), flush=True)
    except KeyboardInterrupt:
        tracker.stop()
