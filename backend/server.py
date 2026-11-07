"""FastAPI app: the single backend process.

Run from the repo root:  uvicorn backend.server:app --port 8000

Owns the WebSocket at /ws (see "WebSocket contract" in README): runs the
interrogation session (baseline -> questions -> responses -> summary),
adds the live stress indicator to samples, and logs every session to data/.

Signals are FAKE for now (FakeSignals), so the frontend can be built before the
hardware works. Fake GSR/HR/breathing react to some questions so spikes show up.
Real serial/vision data replaces FakeSignals later.

Env variables:
  VERIFI_BASELINE_S  length of the baseline period in seconds (default 60; use 10 while testing)
  VERIFI_CAMERA      webcam index (default 0), or "off" (see backend/camera.py)
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

from backend import scoring
from backend.camera import SEND_FPS, Camera

SAMPLE_HZ = 20
STATUS_EVERY_S = 1.0
BASELINE_S = float(os.environ.get("VERIFI_BASELINE_S", "60"))
STRESS_SMOOTHING = 0.1  # 0-1, lower = smoother live stress
DATA_DIR = Path("data")
SIGNAL_KEYS = ["gsr", "hr", "flex", "breath_rate", "blink_rate"]

clients: set[WebSocket] = set()


# ----------------------------- Fake signals --------------------------------


class FakeSignals:
    """Plausible-looking sensor values. react() makes them respond to a question."""

    def __init__(self) -> None:
        self.gsr = 450.0
        self.t0 = time.time()
        self.gsr_rises: list[tuple[float, float]] = []  # (start time, total rise)
        self.hr_bump_until = 0.0
        self.hold: tuple[float, float] | None = None  # (start, end) of a breath hold
        self.hold_flex = 330.0

    def react(self, now: float) -> None:
        """Called when a question is asked. About half the questions get a reaction."""
        if random.random() < 0.5:
            return
        size = random.uniform(0.5, 1.5)
        self.gsr_rises.append((now + random.uniform(1.5, 3.0), 40 * size))
        self.hr_bump_until = now + 8
        if random.random() < 0.5:
            start = now + random.uniform(0.5, 2.0)
            self.hold = (start, start + random.uniform(2.5, 4.0))

    def sample(self) -> dict:
        now = time.time()
        elapsed = now - self.t0
        period = 1 / SAMPLE_HZ

        # GSR: slow drift back to rest, noise, and question responses rising over ~3 s.
        self.gsr += random.gauss(0, 0.8)
        self.gsr += (450 - self.gsr) * 0.002
        for start, rise in self.gsr_rises:
            if start <= now < start + 3:
                self.gsr += rise * period / 3
        self.gsr_rises = [r for r in self.gsr_rises if now < r[0] + 3]

        hr = 72 + 4 * math.sin(elapsed / 7) + random.gauss(0, 0.5)
        if now < self.hr_bump_until:
            hr += 8

        flex = 330 + 25 * math.sin(2 * math.pi * 0.25 * elapsed)  # 15 breaths/min
        if self.hold and self.hold[0] <= now < self.hold[1]:
            flex = self.hold_flex  # chest stops moving
        else:
            self.hold_flex = flex
        flex += random.gauss(0, 1)

        return {
            "type": "sample",
            "t": round(now, 3),
            "gsr": round(self.gsr, 1),
            "hr": round(hr, 1),
            "flex": round(flex, 1),
            "breath_rate": 15,
            "blink_rate": 17,
        }


# ----------------------------- Session -------------------------------------


class SessionLog:
    """One CSV per session in data/: every sample plus event rows (baseline, question, response, stop)."""

    COLUMNS = ["t", *SIGNAL_KEYS, "stress", "event", "label", "spike", "text", "stimulus"]

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
        self.samples: list[dict] = []
        self.recent: deque[dict] = deque(maxlen=int(scoring.STRESS_WINDOW_S * SAMPLE_HZ))
        self.stress: float | None = None
        self.questions: list[dict] = []  # {"label", "t", "spike", "signals", "done"}
        self.log = SessionLog(subject, now)
        self.log.event(now, "baseline_start", label=subject)

    def next_label(self) -> str:
        return f"Q{len(self.questions) + 1}"

    def ask(self, now: float, label: str, text: str | None, stimulus: str | None) -> dict:
        self.questions.append({"label": label, "t": now, "spike": None, "signals": {}, "done": False})
        self.log.event(now, "question", label=label, text=text or "", stimulus=stimulus or "")
        return {"type": "question", "t": round(now, 3), "label": label, "phase": "start"}

    def score(self, q: dict) -> dict:
        lo = q["t"] - scoring.PRE_S - scoring.HOLD_RANGE_S
        hi = q["t"] + scoring.WINDOW_S
        window = [s for s in self.samples if lo <= s["t"] <= hi]
        q["spike"], q["signals"] = scoring.response(window, q["t"], self.baseline)
        q["done"] = True
        now = self.samples[-1]["t"] if self.samples else q["t"]
        self.log.event(now, "response", label=q["label"], spike=q["spike"])
        return {"type": "response", "t": round(now, 3), "label": q["label"], "spike": q["spike"], "signals": q["signals"]}

    def on_sample(self, sample: dict) -> list[dict]:
        """Adds stress to the sample, logs it, and returns any session messages it triggers."""
        t = sample["t"]
        self.samples.append(sample)
        self.recent.append(sample)
        events = []

        if self.baseline is None and t >= self.baseline_end:
            self.baseline = scoring.Baseline([s for s in self.samples if s["t"] >= self.started])
            self.log.event(t, "baseline_done")
            events.append({"type": "baseline", "t": round(t, 3), "phase": "done", **self.baseline.as_message()})

        if self.baseline is not None:
            raw = scoring.stress(list(self.recent), self.baseline)
            if raw is not None:
                self.stress = raw if self.stress is None else self.stress + STRESS_SMOOTHING * (raw - self.stress)
                sample["stress"] = round(self.stress)

        for q in self.questions:
            if not q["done"] and t >= q["t"] + scoring.WINDOW_S:
                events.append(self.score(q))

        self.log.sample(sample)
        return events

    def stop(self, now: float) -> list[dict]:
        """Scores unfinished questions with the data so far, then returns them plus the summary."""
        events = [self.score(q) for q in self.questions if not q["done"]] if self.baseline else []
        ranked = sorted(self.questions, key=lambda q: q["spike"] if q["spike"] is not None else -math.inf, reverse=True)
        events.append({"type": "summary", "questions": [{"label": q["label"], "spike": q["spike"]} for q in ranked]})
        self.log.event(now, "stop")
        self.log.close()
        print(f"session saved to {self.log.path}", flush=True)
        return events


session: Session | None = None
fake = FakeSignals()
camera = Camera()


def status_message() -> dict:
    """What each part of the rig is doing, for the dashboard's navbar.

    Values: "ok", "simulated" (FakeSignals stands in for it), "missing", "off".
    Switch the simulated entries to real checks as serial_io and vision get wired in.
    """
    return {
        "type": "status",
        "components": {
            "arduino": "simulated",
            "gsr": "simulated",
            "pulse": "simulated",
            "breathing": "simulated",
            "camera": camera.status,
            "face": "simulated",  # blink_rate comes from FakeSignals until vision/face.py exists
            "voice": "off",  # cut for now (README)
        },
        # Lets a dashboard that reloads mid-session pick the session back up.
        "session": None if session is None else {
            "subject": session.subject,
            "started": round(session.started, 3),
            "baseline_end": round(session.baseline_end, 3),
            "baseline": session.baseline.as_message() if session.baseline else None,
            "questions": [
                {"label": q["label"], "t": round(q["t"], 3), "spike": q["spike"], "signals": q["signals"]}
                for q in session.questions
            ],
        },
    }


# ----------------------------- Loop + WebSocket ----------------------------


async def broadcast(message: dict) -> None:
    text = json.dumps(message)
    for ws in list(clients):
        try:
            await ws.send_text(text)
        except Exception:
            clients.discard(ws)


async def sample_loop() -> None:
    period = 1 / SAMPLE_HZ
    next_tick = time.monotonic()
    while True:
        sample = fake.sample()
        events = session.on_sample(sample) if session else []
        await broadcast(sample)
        for event in events:
            await broadcast(event)
        next_tick += period
        await asyncio.sleep(max(0.0, next_tick - time.monotonic()))


async def status_loop() -> None:
    while True:
        await broadcast(status_message())
        await asyncio.sleep(STATUS_EVERY_S)


async def frame_loop() -> None:
    last_sent = 0.0
    while True:
        await asyncio.sleep(1 / SEND_FPS)
        frame = camera.latest()
        if clients and frame and frame[0] > last_sent:
            last_sent = frame[0]
            await broadcast({"type": "frame", "t": round(frame[0], 3), "jpeg": frame[1]})


@asynccontextmanager
async def lifespan(app: FastAPI):
    camera.start()
    tasks = [asyncio.create_task(loop()) for loop in (sample_loop, status_loop, frame_loop)]
    yield
    for task in tasks:
        task.cancel()
    camera.stop()
    if session:
        session.log.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def health() -> dict:
    return {
        "ok": True,
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
        label = str(message.get("label") or session.next_label())
        await broadcast(session.ask(now, label, message.get("text"), message.get("stimulus")))
        fake.react(now)

    elif kind == "stop":
        if not session:
            return "No session running."
        events = session.stop(now)
        session = None
        for event in events:
            await broadcast(event)

    else:
        return f"Unknown message type: {kind!r}"
    return None


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    clients.add(ws)
    await ws.send_text(json.dumps(status_message()))
    try:
        while True:
            text = await ws.receive_text()
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"type": "error", "message": "Message wasn't valid JSON."}))
                continue
            if not isinstance(message, dict):
                await ws.send_text(json.dumps({"type": "error", "message": "Message must be a JSON object."}))
                continue
            print("from frontend:", message, flush=True)
            error = await handle(message)
            if error:
                await ws.send_text(json.dumps({"type": "error", "message": error}))
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)
