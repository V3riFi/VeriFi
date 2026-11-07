"""FastAPI app: the single backend process.

Run from the repo root:  uvicorn backend.server:app --port 8000

Owns the WebSocket at /ws (message contract in README), starts the serial
and vision threads, runs the CIT session timing, and logs CSVs to data/.

Right now it streams FAKE "sample" messages so the frontend can be built
before the hardware works. Real serial/vision data replaces fake_sample() later.
"""

import asyncio
import json
import math
import random
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

SAMPLE_HZ = 20

clients: set[WebSocket] = set()


class FakeSignals:
    """Plausible-looking sensor values so charts have something to draw."""

    def __init__(self) -> None:
        self.gsr = 450.0
        self.t0 = time.time()

    def sample(self) -> dict:
        now = time.time()
        elapsed = now - self.t0
        # GSR: slow random drift, plus an occasional spike like a real skin response.
        self.gsr += random.gauss(0, 0.8)
        if random.random() < 0.004:
            self.gsr += random.uniform(15, 40)
        self.gsr += (450 - self.gsr) * 0.002  # drift back toward resting level
        breath_hz = 0.25  # 15 breaths/min
        return {
            "type": "sample",
            "t": round(now, 3),
            "gsr": round(self.gsr, 1),
            "hr": round(72 + 4 * math.sin(elapsed / 7) + random.gauss(0, 0.5), 1),
            "flex": round(330 + 25 * math.sin(2 * math.pi * breath_hz * elapsed) + random.gauss(0, 2), 1),
            "breath_rate": 15,
            "blink_rate": 17,
        }


async def broadcast(message: dict) -> None:
    text = json.dumps(message)
    for ws in list(clients):
        try:
            await ws.send_text(text)
        except Exception:
            clients.discard(ws)


async def sample_loop() -> None:
    signals = FakeSignals()
    period = 1 / SAMPLE_HZ
    next_tick = time.monotonic()
    while True:
        await broadcast(signals.sample())
        next_tick += period
        await asyncio.sleep(max(0.0, next_tick - time.monotonic()))


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(sample_loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def health() -> dict:
    return {"ok": True, "clients": len(clients)}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            text = await ws.receive_text()
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                print("ignored non-JSON message from frontend:", text[:100], flush=True)
                continue
            # TODO: handle {"type": "start", ...} to run a CIT session.
            print("from frontend:", message, flush=True)
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)
