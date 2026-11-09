"""Voice for the backend: live features, per-answer features, and the session's audio file.

One microphone stream (sounddevice) in a background thread, analyzed with Praat
(Parselmouth). The backend uses it like this:

    voice = VoiceTracker()
    voice.start()                       # opens the mic
    voice.latest()                      # live features, recomputed every UPDATE_S (see LIVE_KEYS)
    voice.analyze_answer(...)           # -> Future with one answer's features (see answer_features)
    voice.start_recording(path)         # the session's audio, as a WAV next to its CSV
    voice.stop_recording()
    voice.status                        # "ok" | "missing" | "off"

Live features (every UPDATE_S, over the last WINDOW_S of audio). All but voice_db are
None while nobody is talking:
    pitch           mean pitch, Hz
    jitter          cycle-to-cycle pitch variation, %
    shimmer         cycle-to-cycle loudness variation, %
    voice_db        loudness over the newest LOUDNESS_S, dB
    hnr             harmonics-to-noise ratio, dB: how clear (periodic) the voice is
    spectral_slope  energy 1-4 kHz minus energy 0-1 kHz, dB

Per answer (answer_features): response latency, answer length, speech rate, pauses,
mean pitch, intonation (pitch SD) and HNR. Speech errors, repetitions and stuttering
need a transcript, which VeriFi doesn't make, so they aren't measured.

Only this module opens the mic. Quick checks without the backend:
    python -m voice.live              live features from the mic, 5 times a second
    python -m voice.live talk.wav     features of a recording (e.g. a saved session)

Env variable:
  VERIFI_VOICE  input device index or name (default: system default mic), or "off"
"""

from __future__ import annotations  # allows "X | None" hints on Python 3.9

import concurrent.futures
import math
import os
import queue
import sys
import threading
import time
import wave
from collections import deque
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000     # plenty for speech pitch, and cheaper to analyze than 44.1 kHz
WINDOW_S = 1.0          # audio analyzed for each live update
UPDATE_S = 0.2          # how often live features are recomputed
LOUDNESS_S = 0.2        # loudness uses only the newest audio so it reacts quickly
PITCH_FLOOR = 75        # Hz, the pitch search range
PITCH_CEILING = 500
MIN_VOICED_S = 0.15     # less voiced audio than this in the window = not speaking
REOPEN_EVERY_S = 5.0
STALL_S = 2.0           # no audio for this long means the mic has stopped
CLEANUP_WAIT_S = 2.0    # longest to wait for a dead device to close
HISTORY_S = 45.0        # audio kept in memory for answer analysis (longest answer window + margin)

LIVE_KEYS = ("pitch", "jitter", "shimmer", "voice_db", "hnr", "spectral_slope")

# Splitting audio into speech and silence (answers)
MIN_SPEECH_S = 0.1      # shorter bursts of sound are clicks or bumps, not speech
JOIN_GAP_S = 0.15       # shorter silences are inside a word (stop consonants), not gaps
PAUSE_MIN_S = 0.3       # a silence inside an answer at least this long counts as a pause
GUESS_GAP_S = 0.5       # no "Done asking": the first silence this long after the question ends it
FLOOR_BELOW_MAX_DB = 25 # without a room level, speech = within this many dB of the loudest moment
SYLLABLE_DIP_DB = 1.5   # a syllable is a loudness peak with a dip this deep before it (after de Jong & Wempe 2009)


def _finite(x) -> float | None:
    return None if x is None or math.isnan(x) or math.isinf(x) else float(x)


def _round(x, digits: int) -> float | None:
    return None if x is None else round(x, digits)


def _sound(samples: np.ndarray, sample_rate: int):
    import parselmouth

    return parselmouth.Sound(np.asarray(samples, dtype=np.float64), sampling_frequency=sample_rate)


def _voice_quality(sound) -> dict:
    """Jitter, shimmer (%), HNR and spectral slope (dB) of voiced audio."""
    from parselmouth import praat

    points = praat.call(sound, "To PointProcess (periodic, cc)", PITCH_FLOOR, PITCH_CEILING)
    jitter = _finite(praat.call(points, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3))
    shimmer = _finite(praat.call([sound, points], "Get shimmer (local)", 0, 0, 0.0001, 0.02, 1.3, 1.6))
    harmonicity = praat.call(sound, "To Harmonicity (cc)", 0.01, PITCH_FLOOR, 0.1, 1.0)
    ltas = praat.call(sound, "To Ltas", 100)
    return {
        "jitter": None if jitter is None else jitter * 100,
        "shimmer": None if shimmer is None else shimmer * 100,
        "hnr": _finite(praat.call(harmonicity, "Get mean", 0, 0)),
        # Negative: speech has less energy up high. More negative = steeper slope.
        "spectral_slope": _finite(praat.call(ltas, "Get slope", 0, 1000, 1000, 4000, "energy")),
    }


def live_features(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> dict:
    """LIVE_KEYS for a short mono float array (-1..1). Never raises on silence or short audio."""
    from parselmouth import praat

    out = dict.fromkeys(LIVE_KEYS)
    sound = _sound(samples, sample_rate)
    if sound.duration < 3 / PITCH_FLOOR:  # Praat needs 3 periods of the lowest pitch
        return out

    recent = _sound(samples[-int(LOUDNESS_S * sample_rate):], sample_rate)
    out["voice_db"] = _finite(praat.call(recent.to_intensity(minimum_pitch=PITCH_FLOOR), "Get mean", 0, 0, "energy"))

    pitch = sound.to_pitch(pitch_floor=PITCH_FLOOR, pitch_ceiling=PITCH_CEILING)
    voiced = pitch.selected_array["frequency"] > 0
    if voiced.sum() * pitch.time_step < MIN_VOICED_S:
        return out  # silence or noise: the rest stay None (a gap on the chart)

    out["pitch"] = _finite(praat.call(pitch, "Get mean", 0, 0, "Hertz"))
    out.update(_voice_quality(sound))
    return out


# ----------------------------- Answers --------------------------------------


def _speech_segments(times: np.ndarray, loud: np.ndarray, step: float) -> list[list[float]]:
    """[start, end] times of speech, from per-frame loud/quiet flags."""
    segments: list[list[float]] = []
    start = None
    for t, is_loud in zip(times, loud):
        if is_loud and start is None:
            start = t
        elif not is_loud and start is not None:
            segments.append([start, t])
            start = None
    if start is not None:
        segments.append([start, times[-1] + step])

    joined: list[list[float]] = []
    for seg in segments:
        if joined and seg[0] - joined[-1][1] < JOIN_GAP_S:
            joined[-1][1] = seg[1]
        else:
            joined.append(seg)
    return [s for s in joined if s[1] - s[0] >= MIN_SPEECH_S]


def _syllables(sound, pitch, floor_db: float) -> int:
    """Syllable count: voiced loudness peaks, each with a dip of SYLLABLE_DIP_DB before it.

    Finds ~85% of syllables on test speech; it's only compared within one subject, so a
    steady undercount doesn't matter.
    """
    intensity = sound.to_intensity(minimum_pitch=100)
    times, db = intensity.xs(), intensity.values[0]
    threshold = max(floor_db, float(np.median(db)))
    peaks = [i for i in range(1, len(db) - 1) if db[i] > db[i - 1] and db[i] >= db[i + 1] and db[i] > threshold]

    kept: list[int] = []
    for i in peaks:
        if not kept:
            kept.append(i)
        elif min(db[kept[-1]], db[i]) - db[kept[-1]:i].min() >= SYLLABLE_DIP_DB:
            kept.append(i)
        elif db[i] > db[kept[-1]]:
            kept[-1] = i  # same syllable, louder peak
    return sum(1 for i in kept if not math.isnan(pitch.get_value_at_time(times[i])))


def answer_features(samples: np.ndarray, t0: float, answer_from: float, marked: bool,
                    floor_db: float | None, sample_rate: int = SAMPLE_RATE) -> dict | None:
    """Features of one answer, from audio that starts at laptop time t0.

    marked: the interrogator clicked "Done asking" at answer_from, so the answer is the
    first speech that starts after it (speech already going at the click is the end of
    the question). Not marked: answer_from is when the question started; the question is
    the first speech, and the answer starts after the first silence of GUESS_GAP_S.

    Returns None if there's no audio. Times in the result are laptop times.
    """
    if len(samples) < int(0.1 * sample_rate):
        return None
    sound = _sound(samples, sample_rate)
    intensity = sound.to_intensity(minimum_pitch=PITCH_FLOOR)
    times, db = [float(x) for x in intensity.xs() + t0], intensity.values[0]
    if len(db) == 0:
        return None
    floor = floor_db if floor_db is not None else float(db.max()) - FLOOR_BELOW_MAX_DB
    segments = _speech_segments(times, db > floor, intensity.time_step)

    result = {"latency_s": None, "latency_marked": marked, "duration_s": None, "speech_rate": None,
              "pauses": None, "pause_s": None, "pitch": None, "intonation": None, "hnr": None,
              "cut": False, "onset": None, "offset": None}
    if marked:
        question_end = answer_from
        answer = [s for s in segments if s[0] >= answer_from]
    else:
        after = [s for s in segments if s[1] > answer_from]
        question_end, answer = None, []
        for prev, seg in zip(after, after[1:]):
            if seg[0] - prev[1] >= GUESS_GAP_S:
                question_end = prev[1]
                answer = [s for s in after if s[0] >= seg[0]]
                break
    if not answer:
        return result  # no answer heard in the window

    onset, offset = answer[0][0], answer[-1][1]
    gaps = [b[0] - a[1] for a, b in zip(answer, answer[1:])]
    pauses = [g for g in gaps if g >= PAUSE_MIN_S]
    result.update({
        "latency_s": round(onset - question_end, 2) if question_end is not None else None,
        "duration_s": round(offset - onset, 2),
        "pauses": len(pauses),
        "pause_s": round(sum(pauses), 2),
        "cut": bool(offset >= times[-1]),  # still talking when the window closed
        "onset": round(onset, 3),
        "offset": round(offset, 3),
    })

    part = sound.extract_part(from_time=onset - t0, to_time=min(offset - t0, sound.xmax), preserve_times=False)
    if part.duration < 3 / PITCH_FLOOR:
        return result
    pitch = part.to_pitch(pitch_floor=PITCH_FLOOR, pitch_ceiling=PITCH_CEILING)
    f0 = pitch.selected_array["frequency"]
    f0 = f0[f0 > 0]
    if len(f0) * pitch.time_step >= MIN_VOICED_S:
        result["pitch"] = round(float(f0.mean()), 1)
        result["intonation"] = round(float(f0.std()), 1)  # pitch SD in Hz: lower = flatter
        result["hnr"] = _round(_voice_quality(part)["hnr"], 1)
    if result["duration_s"] > 0:
        result["speech_rate"] = round(_syllables(part, pitch, floor) / result["duration_s"], 2)  # syllables/s
    return result


# ----------------------------- Session audio file ---------------------------


class _WavWriter(threading.Thread):
    """Writes audio blocks to a 16-bit WAV in its own thread, so disk never slows the mic."""

    def __init__(self, path: Path) -> None:
        super().__init__(daemon=True, name="voice-wav")
        self.path = path
        self.blocks: queue.Queue = queue.Queue()
        self.t0: float | None = None  # laptop time of the file's first sample
        self.frames = 0

    def run(self) -> None:
        with wave.open(str(self.path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            while (item := self.blocks.get()) is not None:
                t, block = item
                if self.t0 is None:
                    self.t0 = t
                wav.writeframes((np.clip(block, -1, 1) * 32767).astype("<i2").tobytes())
                self.frames += len(block)


# ----------------------------- Tracker --------------------------------------


class VoiceTracker:
    def __init__(self) -> None:
        setting = os.environ.get("VERIFI_VOICE", "").strip()
        self.disabled = setting.lower() == "off"
        self.device = int(setting) if setting.isdigit() else (setting or None)
        self.status = "off" if self.disabled else "missing"  # "ok" | "missing" | "off"
        self._history: deque[tuple[float, np.ndarray]] = deque()  # (laptop time of first sample, block)
        self._latest: tuple[float, dict] | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_block = 0.0
        self._requests: queue.Queue = queue.Queue()
        self._writer: _WavWriter | None = None
        self._running = False
        self._opened_before = False

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
        self._running = True
        threading.Thread(target=self._run, name="voice", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.stop_recording()

    def latest(self, max_age_s: float = 1.0) -> dict | None:
        """Newest live features, or None if there's nothing recent (mic missing or stalled)."""
        with self._lock:
            if self._latest is None or time.time() - self._latest[0] > max_age_s:
                return None
            return self._latest[1]

    def analyze_answer(self, answer_from: float, until: float, marked: bool,
                       floor_db: float | None) -> concurrent.futures.Future | None:
        """Queues one answer's analysis on the voice thread (Praat stays on one thread).

        The Future resolves to answer_features() of the audio from answer_from (minus a
        little, so speech already going at that moment is seen) to until. None if the
        mic isn't running.
        """
        if not self._running:
            return None
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._requests.put((future, answer_from, until, marked, floor_db))
        return future

    def start_recording(self, path: Path) -> bool:
        """Saves the mic to a WAV from now until stop_recording(). False if the mic isn't running."""
        self.stop_recording()
        if not self._running:
            return False
        writer = _WavWriter(path)
        writer.start()
        with self._lock:
            self._writer = writer
        return True

    def stop_recording(self) -> tuple[Path, float | None, float] | None:
        """Finishes the WAV. Returns (path, laptop time of its first sample, seconds), or None."""
        with self._lock:
            writer, self._writer = self._writer, None
        if writer is None:
            return None
        writer.blocks.put(None)
        writer.join(timeout=5)
        return writer.path, writer.t0, writer.frames / SAMPLE_RATE

    # ------------------------------------------------------------ internals

    def _on_audio(self, indata, frames, time_info, status) -> None:
        now = time.time()
        block = indata[:, 0].copy()
        t = now - len(block) / SAMPLE_RATE
        with self._lock:
            self._history.append((t, block))
            while self._history and self._history[0][0] < t - HISTORY_S:
                self._history.popleft()
            self._last_block = now
            if self._writer is not None:
                self._writer.blocks.put((t, block))

    def _audio(self, start: float, end: float) -> tuple[np.ndarray, float]:
        """Audio between two laptop times, and the time of its first sample."""
        with self._lock:
            blocks = [(t, b) for t, b in self._history if t + len(b) / SAMPLE_RATE > start and t < end]
        if not blocks:
            return np.zeros(0, dtype=np.float32), start
        t0 = blocks[0][0]
        audio = np.concatenate([b for _, b in blocks])
        lo = max(0, int((start - t0) * SAMPLE_RATE))
        hi = max(lo, int((end - t0) * SAMPLE_RATE))
        return audio[lo:hi], t0 + lo / SAMPLE_RATE

    def _recent(self, seconds: float) -> np.ndarray | None:
        """The newest `seconds` of audio, or None if there isn't half of that yet."""
        need = int(seconds * SAMPLE_RATE)
        with self._lock:
            blocks, have = [], 0
            for _, block in reversed(self._history):
                blocks.append(block)
                have += len(block)
                if have >= need:
                    break
        if have < need // 2:
            return None
        return np.concatenate(blocks[::-1])[-need:]

    def _answer_requests(self) -> None:
        while True:
            try:
                future, answer_from, until, marked, floor_db = self._requests.get_nowait()
            except queue.Empty:
                return
            if not future.set_running_or_notify_cancel():
                continue
            try:
                audio, t0 = self._audio(answer_from - 1.0, until)
                future.set_result(answer_features(audio, t0, answer_from, marked, floor_db))
            except Exception as e:  # noqa: BLE001 - hand any failure to the waiting backend
                future.set_exception(e)

    def _open(self):
        import sounddevice as sd

        if self._opened_before:
            # PortAudio keeps the device list from when it started. Without a refresh, a mic that
            # dropped out (laptop slept, headset connected, default input changed) never comes back.
            self._bounded(sd._terminate)
            self._bounded(sd._initialize)
        self._opened_before = True
        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                device=self.device, callback=self._on_audio,
            )
            stream.start()
        except Exception as e:
            print(f"voice: couldn't open the microphone ({e})", flush=True)
            return None
        try:
            name = sd.query_devices(stream.device)["name"]
        except Exception:  # noqa: BLE001 - only used for this message
            name = "the microphone"
        print(f"voice: listening on {name}", flush=True)
        return stream

    def _bounded(self, action) -> None:
        """Runs an audio-system call that can raise or hang on a dead device, without risking this thread."""
        def attempt() -> None:
            try:
                action()
            except Exception as e:  # noqa: BLE001 - the next reopen tries again
                print(f"voice: audio system error ({e!r})", flush=True)
        worker = threading.Thread(target=attempt, name="voice-cleanup", daemon=True)
        worker.start()
        worker.join(CLEANUP_WAIT_S)

    def _drop(self, stream) -> None:
        """Lets go of a dead stream and forgets its audio, so old audio can't pass as live."""
        if stream is not None:
            self._bounded(stream.close)
        with self._lock:
            self._history.clear()
            self._latest = None
        self.status = "missing"

    def _run(self) -> None:
        stream = None
        next_tick = time.monotonic()
        while not self._stop.is_set():
            try:
                self._answer_requests()
                if stream is None:
                    self.status = "missing"
                    stream = self._open()
                    if stream is None:
                        self._stop.wait(REOPEN_EVERY_S)
                        continue
                    self._last_block = time.time()
                    next_tick = time.monotonic()

                next_tick += UPDATE_S
                self._stop.wait(max(0.0, next_tick - time.monotonic()))

                now = time.time()
                if now - self._last_block > STALL_S:  # mic unplugged or stream died
                    print("voice: microphone stopped sending audio, reopening", flush=True)
                    self._drop(stream)
                    stream = None
                    continue

                audio = self._recent(WINDOW_S)
                if audio is None:
                    continue
                try:
                    features = live_features(audio)
                except Exception as e:  # never let one bad window kill the thread
                    print(f"voice: analysis error: {e}", flush=True)
                    continue
                with self._lock:
                    self._latest = (now, features)
                self.status = "ok"
                next_tick = max(next_tick, time.monotonic())  # don't burst if we fell behind
            except Exception as e:  # noqa: BLE001 - this thread must outlive any one failure
                print(f"voice: unexpected error, restarting the microphone ({e!r})", flush=True)
                self._drop(stream)
                stream = None
                self._stop.wait(REOPEN_EVERY_S)

        if stream is not None:
            self._bounded(stream.close)


# ----------------------------- Command line ---------------------------------


def _analyze_file(path: str) -> None:
    """Prints the live features and the answer features of a whole recording."""
    import parselmouth

    sound = parselmouth.Sound(path).convert_to_mono().resample(SAMPLE_RATE)
    samples = sound.values[0]
    print(f"{path}: {sound.duration:.1f} s")
    print("  voice quality:", {k: _round(v, 2) for k, v in live_features(samples).items()})
    answer = answer_features(samples, 0.0, 0.0, True, None)
    if answer:
        skip = ("latency_s", "latency_marked", "onset", "offset", "cut")
        print("  speech:", {k: v for k, v in answer.items() if k not in skip})


if __name__ == "__main__":
    if len(sys.argv) > 1:
        _analyze_file(sys.argv[1])
        sys.exit()
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
