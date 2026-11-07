"""Run a session from the terminal, no frontend needed. For testing the hardware and scoring.

Start the backend first (in another terminal), then:
    python -m backend.console subject01

Prints live signals every 2 s. Press Enter to ask a question, type s + Enter to stop
(prints the summary), q + Enter to quit.
"""

import asyncio
import json
import sys

import websockets

URL = "ws://localhost:8000/ws"
FIELDS = ["gsr", "hr", "flex", "breath_rate", "temp", "spo2", "blink_rate", "stress"]


def fmt(v) -> str:
    return "  -  " if v is None else f"{v:>5}"


async def main(subject: str) -> None:
    try:
        ws = await websockets.connect(URL)
    except OSError:
        sys.exit(f"Can't reach {URL}. Is the backend running?")

    latest: dict = {}
    print("Live signals every 2 s:  " + " ".join(f"{f:>5}"[:5] for f in FIELDS))

    async def reader() -> None:
        async for raw in ws:
            m = json.loads(raw)
            kind = m.get("type")
            if kind == "sample":
                latest.update(m)
            elif kind == "baseline":
                print("\n>>> BASELINE " + ("started: sit still..." if m["phase"] == "start" else "done. Press Enter to ask a question."))
            elif kind == "question":
                print(f"\n>>> {m['label']} asked. Response in ~10 s.")
            elif kind == "response":
                parts = ", ".join(f"{k} {v}" for k, v in m["signals"].items())
                print(f"\n>>> {m['label']} spike {m['spike']}  ({parts})")
            elif kind == "summary":
                print("\n>>> SUMMARY (biggest reaction first)")
                for q in m["questions"]:
                    print(f"    {q['label']:>6}  {q['spike']}")
            elif kind == "error":
                print(f"\n!!! {m['message']}")

    async def ticker() -> None:
        while True:
            await asyncio.sleep(2)
            if latest:
                print("                         " + " ".join(fmt(latest.get(f)) for f in FIELDS))

    tasks = [asyncio.create_task(reader()), asyncio.create_task(ticker())]
    await ws.send(json.dumps({"type": "start", "subject": subject}))
    loop = asyncio.get_running_loop()
    try:
        while True:
            line = (await loop.run_in_executor(None, sys.stdin.readline)).strip().lower()
            if line == "q":
                break
            if line == "s":
                await ws.send(json.dumps({"type": "stop"}))
                await asyncio.sleep(1)
                break
            await ws.send(json.dumps({"type": "question"}))
    finally:
        for t in tasks:
            t.cancel()
        await ws.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "test"))
