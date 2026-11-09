"""FastAPI app: the single backend process.

Run from the repo root:  uvicorn backend.server:app --port 8000

Owns the WebSocket at /ws (see "WebSocket contract" in README): runs the
interrogation session (baseline -> questions -> responses -> summary),
adds the live stress indicator to samples, and logs every session to data/
(a CSV of every sample and event, plus the session's audio as a WAV with the same name).

Data source:
  - default: the Arduino over USB serial (backend/serial_io.py), auto-detected
  - VERIFI_FAKE=1: fake signals, no hardware needed. Fake HR/breathing
    react to about half the questions so spikes show up.

Env variables:
  VERIFI_FAKE        1 = fake signals instead of the Arduino
  VERIFI_PORT        serial port, if auto-detect picks the wrong one
  VERIFI_BASELINE_S  length of the baseline period in seconds (default 60; use 10 while testing)
  VERIFI_VOICE_BASELINE_S  length of the voice baseline that follows it (default 25; 0 skips it)
  VERIFI_CAMERA      webcam index (default 0), or "off" (see backend/camera.py)
  VERIFI_VOICE       mic device index or name (default: system mic), or "off" (see voice/live.py)
"""

from __future__ import annotations  # allows "str | None" hints on Python 3.9

import asyncio
import csv
import json
import math
import os
import random
import re
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from backend import confidence, scoring, verdict
from backend.camera import Camera
from backend.clients import Client
from backend.face import FaceTracker
from backend.serial_io import SerialReader
from voice.live import VoiceTracker

FAKE = os.environ.get("VERIFI_FAKE", "") not in ("", "0")
SAMPLE_HZ = 20  # fake signals only; the Arduino sends at its own rate (10 Hz)
STATUS_EVERY_S = 1.0
FRAME_POLL_S = 0.005  # how often frame_loop checks for a new webcam frame
SENSOR_STALE_S = 5.0  # a sensor with no value for this long shows as missing
BASELINE_S = float(os.environ.get("VERIFI_BASELINE_S", "60"))
VOICE_BASELINE_S = float(os.environ.get("VERIFI_VOICE_BASELINE_S", "25"))
# Read aloud during the voice baseline. Neutral and true for anyone, so they set the
# subject's normal pitch and jitter while speaking. The counting gives steady voicing.
VOICE_SENTENCES = [
    "I am sitting in a chair right now.",
    "There are seven days in a week.",
    "The sky is blue on a clear day.",
    "Water is wet and fire is hot.",
    "One, two, three, four, five, six, seven, eight, nine, ten.",
]
# The mic also hears the room (other people talking, fans). The silent baseline
# measures the room's loudness; after it, voice only counts as the subject speaking
# when it's this much louder. TUNE: raise it if background chatter still gets through.
SPEECH_MARGIN_DB = 6.0
# Warn when a voice is heard for at least this share of the silent baseline (it should be ~0).
TALKING_IN_BASELINE = 0.3
STRESS_SMOOTHING = 0.1  # 0-1 per sample, lower = smoother live stress
BREATH_WINDOW_S = 30.0  # breathing rate is counted over this many seconds
DATA_DIR = Path("data")
VOICE_KEYS = ["pitch", "jitter", "shimmer", "voice_db", "hnr", "spectral_slope"]
SPEECH_KEYS = ("pitch", "jitter", "shimmer", "hnr", "spectral_slope")  # None unless the subject is speaking
# After "Done asking", the answer gets this long before its response is sent.
ANSWER_S = 10.0
# Per-answer voice features: the "voice" object in "response" (see voice/live.py answer_features).
ANSWER_KEYS = ["latency_s", "latency_marked", "duration_s", "speech_rate", "pauses", "pause_s",
               "pitch", "intonation", "hnr", "cut"]
VOICE_RESULT_TIMEOUT_S = 5.0
# While an answer's window is open, a provisional verdict on the partial window goes out this often.
PROGRESS_EVERY_S = 0.3
FACE_KEYS = ["blink_rate", "hand_on_face", "lip_compression"]
SIGNAL_KEYS = ["gsr", "hr", "flex", "breath_rate", *FACE_KEYS, *VOICE_KEYS]

clients: set[Client] = set()


# ----------------------------- Fake signals --------------------------------


class SimulatedGSR:
    """The GSR sensor reads too steadily to use, so the dashboard shows this instead.

    Stays close to the average of a real session, so it always matches its own baseline.
    It has no weight in stress, spike or the verdict, and status reports it as "simulated".
    """

    AVERAGE = 17.4  # mean of a real session's GSR readings
    MAX_OFFSET = 1.8  # stays inside the alert line (2 * scoring.MIN_STD["gsr"])

    def __init__(self) -> None:
        self.offset = 0.0

    def next(self) -> float:
        self.offset += random.gauss(0, 0.26) - 0.1 * self.offset
        self.offset = max(-self.MAX_OFFSET, min(self.MAX_OFFSET, self.offset))
        return round(self.AVERAGE + self.offset, 1)


class FakeSignals:
    """Plausible-looking sensor values. react() makes them respond to a question."""

    def __init__(self) -> None:
        self.t0 = time.time()
        self.hr_bump_until = 0.0
        self.hold: tuple[float, float] | None = None  # (start, end) of a breath hold
        self.next_breath = self.t0 + 4.0

    def react(self, now: float) -> None:
        """Called when a question is asked. About half the questions get a reaction."""
        if random.random() < 0.5:
            return
        self.hr_bump_until = now + 8
        if random.random() < 0.5:
            start = now + random.uniform(0.5, 2.0)
            self.hold = (start, start + random.uniform(7.0, 10.0))

    def sample(self) -> dict:
        now = time.time()
        elapsed = now - self.t0

        hr = 72 + 4 * math.sin(elapsed / 7) + random.gauss(0, 0.5)
        if now < self.hr_bump_until:
            hr += 8

        # A single event per breath, as the Arduino sends it; none during a hold.
        flex = 0
        if self.hold and self.hold[0] <= now < self.hold[1]:
            self.next_breath = self.hold[1]
        elif now >= self.next_breath:
            flex = 1
            self.next_breath = now + random.uniform(3.6, 4.4)  # about 15 breaths/min

        return {
            "type": "sample",
            "t": round(now, 3),
            "hr": round(hr, 1),
            "flex": flex,
            # Varies a little like a real blink rate: a perfectly flat signal would read as
            # "exactly like baseline" and pull every verdict toward calm.
            "blink_rate": round(17 + 2 * math.sin(elapsed / 11) + random.gauss(0, 0.8), 1),
        }


# ----------------------------- Session -------------------------------------


class SessionLog:
    """One CSV per session in data/: every sample plus event rows (baseline, question, response, answer, stop).

    The session's audio is saved next to it with the same name (.wav); the "audio_start"
    row gives the laptop time of the WAV's first sample, so CSV times map onto the audio.
    """

    COLUMNS = ["t", *SIGNAL_KEYS, "stress", "event", "label", "spike", "text", "stimulus",
               *(f"answer_{k}" for k in ANSWER_KEYS)]

    def __init__(self, subject: str, started: float) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", subject) or "subject"
        self.path = DATA_DIR / f"{safe}_{datetime.fromtimestamp(started):%Y%m%d-%H%M%S}.csv"
        self.file = open(self.path, "w", newline="")
        self.writer = csv.DictWriter(self.file, fieldnames=self.COLUMNS, extrasaction="ignore")
        self.writer.writeheader()

    def sample(self, sample: dict) -> None:
        self.writer.writerow(sample)

    def event(self, t: float, event: str, **fields) -> None:
        if self.file.closed:  # a late answer after stop
            return
        self.writer.writerow({"t": round(t, 3), "event": event, **fields})
        self.file.flush()

    def close(self) -> None:
        self.file.close()


class Session:
    def __init__(self, subject: str, now: float) -> None:
        self.subject = subject
        self.started = now
        self.baseline_end = now + BASELINE_S
        self.baseline: scoring.Baseline | None = None
        # Voice baseline (after the silent one): start/end time, None if skipped or not reached yet.
        self.voice_start: float | None = None
        self.voice_end: float | None = None
        self.voice_done = False
        self.reading: dict | None = None  # the voice baseline's speech features, to compare answers with
        self.room_db: float | None = None  # median mic loudness during the silent baseline
        self.samples: list[dict] = []
        self.recent: deque[dict] = deque()  # last STRESS_WINDOW_S seconds
        self.stress: float | None = None
        # {"label", "t", "end", "due", "spike", "signals", "voice", "done"}. "end" is the
        # "Done asking" click (None if not clicked); "due" is when the response is scored.
        self.questions: list[dict] = []
        self.tasks: set[asyncio.Task] = set()  # answers being analyzed
        # Per-answer verdicts (backend/verdict.py): built once both baselines are done.
        self.engine: verdict.Engine | None = None
        self.engine_tried = False
        self.session_verdict: dict | None = None  # every scored answer so far, combined
        self.log = SessionLog(subject, now)
        self.log.event(now, "baseline_start", label=subject)
        self.recording = voice.start_recording(self.log.path.with_suffix(".wav"))

    @property
    def ready(self) -> bool:
        """Both baselines are done, so questions can be asked."""
        return self.baseline is not None and (self.voice_end is None or self.voice_done)

    def voice_message(self) -> dict | None:
        if self.voice_start is None:
            return None
        return {"start": round(self.voice_start, 3), "end": round(self.voice_end, 3),
                "sentences": VOICE_SENTENCES, "done": self.voice_done, "reading": self.reading}

    def next_label(self) -> str:
        return f"Q{len(self.questions) + 1}"

    def speech_floor(self) -> float | None:
        """Loudness (dB) above which the mic counts as someone speaking, once the room is measured."""
        return None if self.room_db is None else self.room_db + SPEECH_MARGIN_DB

    def ask(self, now: float, label: str, text: str | None, stimulus: str | None) -> dict:
        due = now + scoring.WINDOW_S
        self.questions.append({"label": label, "text": text or None, "t": now, "end": None,
                               "due": due, "spike": None, "signals": {}, "voice": None,
                               "confidence": None, "verdict": None, "progress_t": 0.0, "done": False})
        self.log.event(now, "question", label=label, text=text or "", stimulus=stimulus or "")
        return {"type": "question", "t": round(now, 3), "label": label, "text": text or None,
                "due": round(due, 3), "phase": "start"}

    def end_question(self, now: float) -> dict | None:
        """The interrogator finished asking. None if no question is waiting for it."""
        open_questions = [q for q in self.questions if not q["done"] and q["end"] is None]
        if not open_questions:
            return None
        q = open_questions[-1]
        q["end"] = now
        q["due"] = max(q["due"], now + ANSWER_S)  # give the answer its full window
        self.log.event(now, "question_end", label=q["label"])
        return {"type": "question", "t": round(now, 3), "label": q["label"], "due": round(q["due"], 3),
                "phase": "end"}

    def score(self, q: dict, now: float) -> dict:
        """Physiology part of the response. The voice part is added by finish_answer()."""
        lo = q["t"] - scoring.PRE_S
        hi = q["t"] + scoring.WINDOW_S
        window = [s for s in self.samples if lo <= s["t"] <= hi]
        q["spike"], q["signals"] = scoring.response(window, q["t"], self.baseline)
        q["verdict"], raw = self.answer_verdict(q, now)
        q["confidence"] = confidence.to_message(raw) if raw else None
        q["done"] = True
        self.log.event(now, "response", label=q["label"], spike=q["spike"])
        return {"type": "response", "t": round(now, 3), "label": q["label"], "spike": q["spike"],
                "signals": q["signals"], "voice": None, "confidence": q["confidence"],
                "verdict": q["verdict"], "_question": q}

    def verdict_engine(self) -> verdict.Engine | None:
        """The baseline side of the verdicts, built once when both baselines are done."""
        if not self.engine_tried and self.ready:
            self.engine_tried = True
            voice_range = (self.voice_start, self.voice_end) if self.voice_done else None
            try:
                self.engine = verdict.Engine(self.samples, (self.started, self.baseline_end), voice_range)
            except Exception as e:  # noqa: BLE001 - verdicts then say "not enough signal"
                print(f"verdict: couldn't analyze the baseline ({e!r})", flush=True)
        return self.engine

    def answer_verdict(self, q: dict, until: float, provisional: bool = False) -> tuple[dict | None, dict | None]:
        """(verdict, confidence.py's raw result) for one answer, up to `until`. (None, None) if it
        fails: the verdict is an extra, so it must never hold up the response."""
        try:
            return verdict.score(self.verdict_engine(), self.samples, q["t"], until, q["end"], provisional)
        except Exception as e:  # noqa: BLE001
            print(f"verdict: couldn't score {q['label']} ({e!r})", flush=True)
            return None, None

    def session_message(self, now: float) -> dict:
        """Every scored answer so far combined, re-run after each answer."""
        answers = [(q["label"], q["verdict"]) for q in self.questions if q["done"] and q["verdict"]]
        self.session_verdict = verdict.session_summary(answers)
        return {"type": "session_verdict", "t": round(now, 3), **self.session_verdict}

    def answer_request(self, q: dict, until: float):
        """Queues the voice analysis of this question's answer (a Future, or None without a mic)."""
        marked = q["end"] is not None
        return voice.analyze_answer(q["end"] if marked else q["t"], until, marked, self.speech_floor())

    def finish_answer(self, q: dict, result: dict | None, t: float) -> dict | None:
        """Stores the answer's voice features and logs them."""
        if not result:
            return None
        answer = {k: v for k, v in result.items() if k not in ("onset", "offset")}
        q["voice"] = answer
        self.log.event(t, "answer", label=q["label"], **{f"answer_{k}": answer.get(k) for k in ANSWER_KEYS})
        return answer

    def on_sample(self, sample: dict) -> list[dict]:
        """Adds stress to the sample, logs it, and returns any session messages it triggers."""
        t = sample["t"]
        if self.room_db is not None and sample.get("voice_db") is not None \
                and sample["voice_db"] < self.room_db + SPEECH_MARGIN_DB:
            for key in SPEECH_KEYS:
                sample[key] = None  # room noise, not the subject
        self.samples.append(sample)
        self.recent.append(sample)
        while self.recent[0]["t"] < t - scoring.STRESS_WINDOW_S:
            self.recent.popleft()
        events = []

        if self.baseline is None and t >= self.baseline_end:
            silent = [s for s in self.samples if s["t"] >= self.started]
            self.baseline = scoring.Baseline(silent)
            room = sorted(s["voice_db"] for s in silent if s.get("voice_db") is not None)
            if room:
                self.room_db = room[len(room) // 2]
                print(f"voice: room level {self.room_db:.1f} dB, speech counts above "
                      f"{self.room_db + SPEECH_MARGIN_DB:.1f} dB", flush=True)
                # Talking during the silent baseline makes the "room level" a voice level, and then
                # the subject's own answers are too quiet to count as speech. Say so on screen.
                heard = [s for s in silent if s.get("voice_db") is not None]
                talking = sum(s.get("pitch") is not None for s in heard) / len(heard)
                if talking >= TALKING_IN_BASELINE:
                    print(f"voice: someone was talking for {talking:.0%} of the silent baseline", flush=True)
                    events.append({"type": "error", "message": (
                        f"The microphone heard talking during {talking:.0%} of the silent baseline, so it "
                        f"set the room level to {self.room_db:.0f} dB and may not pick up the subject's "
                        "answers. For voice to work, stop and start again, and keep the room quiet "
                        "until the baseline is done.")})
            self.log.event(t, "baseline_done")
            events.append({"type": "baseline", "t": round(t, 3), "phase": "done", **self.baseline.as_message()})
            if VOICE_BASELINE_S > 0 and voice.status == "ok":
                self.voice_start, self.voice_end = t, t + VOICE_BASELINE_S
                self.log.event(t, "voice_baseline_start")
                events.append({"type": "voice_baseline", "t": round(t, 3), "phase": "start",
                               "duration": VOICE_BASELINE_S, "sentences": VOICE_SENTENCES})
            elif VOICE_BASELINE_S > 0:
                print(f"voice baseline skipped: microphone is {voice.status}", flush=True)

        if self.voice_end is not None and not self.voice_done and t >= self.voice_end:
            self.voice_done = True
            voiced = self.baseline.add_voice([s for s in self.samples if self.voice_start <= s["t"] <= t])
            self.log.event(t, "voice_baseline_done")
            event = {"type": "voice_baseline", "t": round(t, 3), "phase": "done",
                     "voiced_samples": voiced, **self.baseline.as_message(), "reading": None,
                     "_reading": voice.analyze_answer(self.voice_start, t, True, self.speech_floor())}
            if voiced < scoring.MIN_VOICE_SAMPLES:
                event["_error"] = ("Didn't hear enough speech for the voice baseline, "
                                   "so voice has no normal range this session. Check the microphone.")
            events.append(event)

        if self.baseline is not None:
            raw = scoring.stress(list(self.recent), self.baseline)
            if raw is not None:
                self.stress = raw if self.stress is None else self.stress + STRESS_SMOOTHING * (raw - self.stress)
                sample["stress"] = round(self.stress)

        self.verdict_engine()  # builds it the moment both baselines are done
        scored = False
        for q in self.questions:
            if q["done"]:
                continue
            if t >= q["due"]:
                events.append(self.score(q, t))
                scored = True
            elif t - q["progress_t"] >= PROGRESS_EVERY_S:
                # The live estimate on the partial window, so the dashboard's gauge moves.
                q["progress_t"] = t
                provisional, _ = self.answer_verdict(q, t, provisional=True)
                if provisional:
                    events.append({"type": "verdict_progress", "t": round(t, 3), "label": q["label"],
                                   "elapsed": round(t - q["t"], 2), "due": round(q["due"], 3),
                                   "verdict": provisional})
        if scored:
            events.append(self.session_message(t))

        self.log.sample(sample)
        return events

    def finish(self, now: float) -> list[dict]:
        """Scores unfinished questions with the data so far (stop)."""
        if not self.baseline:
            return []
        events = [self.score(q, now) for q in self.questions if not q["done"]]
        return events + [self.session_message(now)] if events else events

    def close(self, now: float) -> dict:
        """Ends the session: the ranked summary, the audio file closed, the CSV closed."""
        ranked = sorted(self.questions, key=lambda q: q["spike"] if q["spike"] is not None else -math.inf, reverse=True)
        summary = {"type": "summary", "questions": [
            {"label": q["label"], "spike": q["spike"], "latency_s": (q["voice"] or {}).get("latency_s"),
             "suspicion": (q["confidence"] or {}).get("suspicion"),
             "likelihood": (q["verdict"] or {}).get("likelihood"), "band": (q["verdict"] or {}).get("band")}
            for q in ranked],
            "session": self.session_verdict}
        audio = voice.stop_recording() if self.recording else None
        if audio and audio[1] is not None:
            self.log.event(audio[1], "audio_start", label=audio[0].name, text=f"{audio[2]:.1f} s")
        self.log.event(now, "stop")
        self.log.close()
        print(f"session saved to {self.log.path}" + (f" and {audio[0]}" if audio else ""), flush=True)
        return summary


session: Session | None = None
fake = FakeSignals()
camera = Camera()
face = FaceTracker(camera)
voice = VoiceTracker()


def sensor_status(*keys: str) -> str:
    """"ok" if any of these sample fields had a value in the last SENSOR_STALE_S seconds."""
    if FAKE:
        return "simulated"
    now = time.time()
    return "ok" if any(now - last_seen.get(k, 0.0) < SENSOR_STALE_S for k in keys) else "missing"


def status_message() -> dict:
    """What each part of the rig is doing, for the dashboard's navbar.

    Values: "ok", "simulated" (VERIFI_FAKE=1), "missing", "off".
    """
    if FAKE:
        arduino = "simulated"
    else:
        arduino = "ok" if serial_reader and serial_reader.connected else "missing"
    return {
        "type": "status",
        "components": {
            "arduino": arduino,
            "gsr": "simulated",  # see SimulatedGSR
            "pulse": sensor_status("hr"),
            "breathing": sensor_status("flex"),
            "camera": camera.status,
            # Fake mode sends a fake blink_rate when the real face tracker isn't running.
            "face": "simulated" if FAKE and face.status != "ok" else face.status,
            "voice": voice.status,
        },
        # Lets a dashboard that reloads mid-session pick the session back up.
        "session": None if session is None else {
            "subject": session.subject,
            "started": round(session.started, 3),
            "baseline_end": round(session.baseline_end, 3),
            "baseline": session.baseline.as_message() if session.baseline else None,
            "voice_baseline": session.voice_message(),
            "questions": [
                {"label": q["label"], "text": q["text"], "t": round(q["t"], 3),
                 "end": None if q["end"] is None else round(q["end"], 3), "due": round(q["due"], 3),
                 "spike": q["spike"], "signals": q["signals"], "voice": q["voice"],
                 "confidence": q["confidence"], "verdict": q["verdict"], "done": q["done"]}
                for q in session.questions
            ],
            "verdict": session.session_verdict,
        },
    }


# ----------------------------- Loop + WebSocket ----------------------------


async def broadcast(message: dict) -> None:
    """Queues the message for every open tab. Never waits for a browser (see backend/clients.py)."""
    text = json.dumps(message)
    for client in list(clients):
        client.send(text, frame=message.get("type") == "frame")


breath_history: deque[tuple[float, float]] = deque()  # (t, flex) for breathing rate
simulated_gsr = SimulatedGSR()
serial_reader: SerialReader | None = None
last_seen: dict[str, float] = {}  # sample field -> laptop time it last had a value


async def handle_sample(sample: dict) -> None:
    """Every sample, fake or real, goes through here: breathing rate, session, broadcast."""
    sample["gsr"] = simulated_gsr.next()
    if sample.get("breath_rate") is None and sample.get("flex") is not None:
        breath_history.append((sample["t"], sample["flex"]))
        while breath_history[0][0] < sample["t"] - BREATH_WINDOW_S:
            breath_history.popleft()
        sample["breath_rate"] = scoring.breath_rate(list(breath_history), BREATH_WINDOW_S)
    sample["type"] = "sample"
    looks = face.latest()  # real camera, even with VERIFI_FAKE=1; replaces the fake blink_rate
    if looks:
        sample.update({k: v if v is None or isinstance(v, bool) else round(v, 3) for k, v in looks.items()})
    features = voice.latest()  # real mic, even with VERIFI_FAKE=1 (like the camera)
    if features:
        sample.update({k: None if v is None else round(v, 2) for k, v in features.items()})
    now = time.time()
    for key in ("hr", "flex", *FACE_KEYS):
        if sample.get(key) is not None:
            last_seen[key] = now
    current = session
    events = current.on_sample(sample) if current else []
    await broadcast(sample)
    for event in events:
        if "_question" in event or "_reading" in event:
            # Waits for the voice analysis (a fraction of a second) without holding up samples.
            task = asyncio.create_task(deliver(current, event))
            current.tasks.add(task)
            task.add_done_callback(current.tasks.discard)
        else:
            await broadcast(event)


async def voice_result(future, label: str):
    """The voice thread's answer analysis, or None if there's no mic or it failed."""
    if future is None:
        return None
    try:
        return await asyncio.wait_for(asyncio.wrap_future(future), VOICE_RESULT_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - voice is optional; the response still goes out
        print(f"voice: couldn't analyze {label} ({e!r})", flush=True)
        return None


async def deliver(current: Session, event: dict) -> None:
    """Adds the voice part to a response or voice-baseline message, then sends it."""
    try:
        if "_question" in event:
            q = event.pop("_question")
            result = await voice_result(current.answer_request(q, event["t"]), q["label"])
            event["voice"] = current.finish_answer(q, result, event["t"])
        else:
            reading = await voice_result(event.pop("_reading"), "the voice baseline")
            if reading:
                current.reading = {k: reading[k] for k in ("duration_s", "speech_rate", "pauses", "pitch", "intonation", "hnr")}
            event["reading"] = current.reading
        error = event.pop("_error", None)
        await broadcast(event)
        if error:
            await broadcast({"type": "error", "message": error})
    except Exception as e:  # noqa: BLE001 - never lose a response silently
        print(f"deliver: {e!r}", flush=True)


async def fake_loop() -> None:
    period = 1 / SAMPLE_HZ
    next_tick = time.monotonic()
    while True:
        await handle_sample(fake.sample())
        next_tick += period
        await asyncio.sleep(max(0.0, next_tick - time.monotonic()))


async def status_loop() -> None:
    while True:
        await broadcast(status_message())
        await asyncio.sleep(STATUS_EVERY_S)


async def frame_loop() -> None:
    last_sent = 0.0
    while True:
        # Check far more often than frames arrive, so each one goes out as soon as it's
        # encoded. Polling at SEND_FPS drifts against the camera's clock and drops frames.
        await asyncio.sleep(FRAME_POLL_S)
        frame = camera.latest()
        if clients and frame and frame[0] > last_sent:
            last_sent = frame[0]
            await broadcast({"type": "frame", "t": round(frame[0], 3), "jpeg": frame[1]})


async def serial_loop(queue: asyncio.Queue) -> None:
    while True:
        await handle_sample(await queue.get())


@asynccontextmanager
async def lifespan(app: FastAPI):
    global serial_reader
    camera.start()
    face.start()
    voice.start()
    if FAKE:
        print("source: FAKE signals (VERIFI_FAKE=1)", flush=True)
        source = asyncio.create_task(fake_loop())
    else:
        print("source: Arduino over serial (set VERIFI_FAKE=1 for fake signals)", flush=True)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        # The reader runs in its own thread; hand each sample to the event loop safely.
        serial_reader = SerialReader(lambda s: loop.call_soon_threadsafe(queue.put_nowait, s))
        serial_reader.start()
        source = asyncio.create_task(serial_loop(queue))
    tasks = [source, asyncio.create_task(status_loop()), asyncio.create_task(frame_loop())]
    yield
    for task in tasks:
        task.cancel()
    if session:
        session.close(time.time())  # before voice.stop(), so the audio file is logged
    face.stop()
    camera.stop()
    voice.stop()
    if serial_reader:
        serial_reader.stop()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def health() -> dict:
    return {
        "ok": True,
        "source": "fake" if FAKE else "serial",
        "serial": None if FAKE or not serial_reader else {"port": serial_reader.port, "connected": serial_reader.connected},
        "clients": len(clients),
        "session": None if session is None else {
            "subject": session.subject,
            "baseline_done": session.baseline is not None,
            "questions": len(session.questions),
        },
    }


async def handle(message: dict) -> str | None:
    """Runs one frontend control message. Returns an error string, or None if it worked."""
    global session
    kind = message.get("type")
    now = time.time()

    if kind == "start":
        if session:
            return "A session is already running. Send stop first."
        session = Session(str(message.get("subject") or "subject"), now)
        await broadcast({"type": "baseline", "t": round(now, 3), "phase": "start", "duration": BASELINE_S})

    elif kind == "question":
        if not session:
            return "No session running. Send start first."
        if session.baseline is None:
            return "Still recording the baseline. Wait for it to finish."
        if not session.ready:
            return "Still recording the voice baseline. Wait for the subject to finish reading."
        label = str(message.get("label") or session.next_label())
        await broadcast(session.ask(now, label, message.get("text"), message.get("stimulus")))
        if FAKE:
            fake.react(now)

    elif kind == "question_end":
        if not session:
            return "No session running."
        event = session.end_question(now)
        if event is None:
            return "No question is waiting for Done asking."
        await broadcast(event)

    elif kind == "stop":
        if not session:
            return "No session running."
        ending, session = session, None
        for event in ending.finish(now):
            if "_question" in event or "_reading" in event:
                await deliver(ending, event)
            else:
                await broadcast(event)
        if ending.tasks:  # answers still being analyzed
            await asyncio.wait(ending.tasks, timeout=VOICE_RESULT_TIMEOUT_S)
        await broadcast(ending.close(now))

    else:
        return f"Unknown message type: {kind!r}"
    return None


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    client = Client(ws, clients.discard)
    clients.add(client)
    client.send(json.dumps(status_message()))
    try:
        while True:
            text = await ws.receive_text()
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                client.send(json.dumps({"type": "error", "message": "Message wasn't valid JSON."}))
                continue
            if not isinstance(message, dict):
                client.send(json.dumps({"type": "error", "message": "Message must be a JSON object."}))
                continue
            print("from frontend:", message, flush=True)
            error = await handle(message)
            if error:
                client.send(json.dumps({"type": "error", "message": error}))
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(client)
        client.stop()
