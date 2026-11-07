"""UDP control channel: lets the backend drive CIT item onsets when vision runs as its own process.

Send one JSON datagram per command to the control port (default 127.0.0.1:5006):

    {"cmd": "item_onset", "item": "7 of Hearts", "timestamp": 1731000000.12,   # timestamp optional
     "block": "cards", "series": 2, "kind": "test", "duration": 5.0}          # all optional (see VISION.md)
    {"cmd": "reset"}                                                           # new subject / session

Use the backend's own time.time() when the item is drawn on screen. Vision and backend
run on the same laptop, so the clocks match. Leave "timestamp" out and vision uses its
receive time instead, which is fine for local UDP (< 1 ms).
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)


class ControlListener:
    def __init__(self, address: str, on_item: Callable[..., None], on_reset: Callable[[], None]):
        host, port = address.rsplit(":", 1)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((host, int(port)))
        self._sock.settimeout(0.5)
        self._on_item, self._on_reset = on_item, on_reset
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="vision-control", daemon=True)
        self._thread.start()
        log.info("CIT control listening on udp://%s", address)

    def _loop(self) -> None:
        while self._running:
            try:
                data, _ = self._sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            received = time.time()
            try:
                msg = json.loads(data)
                cmd = msg.get("cmd")
                if cmd == "item_onset":
                    meta = {k: msg[k] for k in ("block", "series", "kind") if k in msg}
                    self._on_item(str(msg["item"]), float(msg.get("timestamp") or received),
                                  meta or None, msg.get("duration"))
                elif cmd == "reset":
                    self._on_reset()
                else:
                    log.warning("unknown control command: %r", msg)
            except (ValueError, KeyError, TypeError) as e:
                log.warning("bad control message %r: %s", data[:200], e)

    def close(self) -> None:
        self._running = False
        self._sock.close()


def send_item_onset(item: str, address: str = "127.0.0.1:5006", timestamp: Optional[float] = None) -> None:
    """Helper for the backend: call this the moment an item is shown."""
    host, port = address.rsplit(":", 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(json.dumps({"cmd": "item_onset", "item": item,
                             "timestamp": timestamp if timestamp is not None else time.time()}).encode(),
                 (host, int(port)))
