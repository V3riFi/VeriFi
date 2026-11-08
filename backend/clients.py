"""One open dashboard tab: its own outgoing queue, so a stuck tab can't freeze the session.

The sensor, video and status loops only ever add to a queue here, which never waits.
Each tab has a writer task that does the waiting. A slow tab skips old video frames;
a tab that stops reading is disconnected, and its dashboard reconnects by itself.
"""

from __future__ import annotations  # allows "str | None" hints on Python 3.9

import asyncio
from collections import deque
from typing import Callable

SEND_STALL_S = 5.0  # a tab that can't take one message for this long is disconnected
MAX_QUEUED = 1200   # non-video messages waiting for one tab (about 2 minutes of samples)


class Client:
    def __init__(self, ws, on_drop: Callable[["Client"], None]) -> None:
        self.ws = ws
        self.on_drop = on_drop
        self.dropped = False
        self._queue: deque[str] = deque()
        self._frame: str | None = None  # newest video frame only; older ones are skipped
        self._wake = asyncio.Event()
        self._closer: asyncio.Task | None = None
        self._writer = asyncio.create_task(self._write())

    def send(self, text: str, frame: bool = False) -> None:
        """Queues a message and returns at once. Never waits for the browser."""
        if self.dropped:
            return
        if frame:
            self._frame = text
        elif len(self._queue) >= MAX_QUEUED:
            self._disconnect("it fell too far behind")
            return
        else:
            self._queue.append(text)
        self._wake.set()

    def stop(self) -> None:
        """The tab has gone. Stops its writer."""
        self.dropped = True
        self._writer.cancel()

    async def _write(self) -> None:
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                while self._queue or self._frame is not None:
                    await asyncio.wait_for(self.ws.send_text(self._next()), SEND_STALL_S)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._disconnect(f"it took nothing for {SEND_STALL_S:g} s")
        except Exception:  # noqa: BLE001 - the tab closed mid-send; nothing to report
            self._disconnect(None)

    def _next(self) -> str:
        if self._queue:
            return self._queue.popleft()
        text, self._frame = self._frame, None
        return text

    def _disconnect(self, why: str | None) -> None:
        if self.dropped:
            return
        self.dropped = True
        if why:
            print(f"dashboard: disconnected a browser tab because {why}", flush=True)
        self._writer.cancel()
        self.on_drop(self)
        self._closer = asyncio.create_task(self._close())  # kept so it isn't garbage collected

    async def _close(self) -> None:
        try:
            await self.ws.close(code=1013)  # "try again later": the dashboard reconnects
        except Exception:  # noqa: BLE001 - already gone
            pass
