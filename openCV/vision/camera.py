"""Camera capture.

Live webcams are read on a background thread that keeps ONLY the newest frame,
so processing hiccups never build up latency. Video files are read
synchronously (every frame, paced to file FPS) - use a recorded clip as the
demo fallback if the venue webcam misbehaves.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Optional, Tuple

import cv2
import numpy as np

from .config import CameraConfig

log = logging.getLogger(__name__)
Frame = Tuple[np.ndarray, float]   # (BGR image, capture timestamp - epoch seconds)


class Camera:
    def __init__(self, cfg: CameraConfig):
        self.cfg = cfg
        self.is_live = isinstance(cfg.source, int) or (isinstance(cfg.source, str) and cfg.source.isdigit())
        self._cap: Optional[cv2.VideoCapture] = None
        self._lock = threading.Condition()
        self._latest: Optional[Frame] = None
        self._seq = 0
        self._read_seq = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._file_period = 0.0
        self._next_file_t = 0.0

    # ------------------------------------------------------------------ lifecycle
    def _open(self) -> cv2.VideoCapture:
        if self.is_live:
            idx = int(self.cfg.source)
            api = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
            cap = cv2.VideoCapture(idx, api)
            if not cap.isOpened():
                cap = cv2.VideoCapture(idx)
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.height)
            cap.set(cv2.CAP_PROP_FPS, self.cfg.fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        else:
            cap = cv2.VideoCapture(str(self.cfg.source))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open camera/video source {self.cfg.source!r}")
        return cap

    def start(self) -> "Camera":
        self._cap = self._open()
        self._running = True
        if self.is_live:
            self._thread = threading.Thread(target=self._reader, name="camera-reader", daemon=True)
            self._thread.start()
        else:
            fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
            self._file_period = 1.0 / fps
            self._next_file_t = time.time()
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        log.info("Camera %r opened at %dx%d", self.cfg.source, w, h)
        return self

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._cap:
            self._cap.release()

    def __enter__(self) -> "Camera":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------------------------------------------------------------------ reading
    def _reader(self) -> None:
        failures = 0
        while self._running:
            ok, frame = self._cap.read()
            t = time.time()
            if not ok or frame is None:
                failures += 1
                if failures >= self.cfg.reconnect_after_failures:
                    log.warning("Camera stalled - reopening")
                    self._cap.release()
                    time.sleep(0.5)
                    try:
                        self._cap = self._open()
                    except RuntimeError as e:
                        log.error("%s", e)
                    failures = 0
                time.sleep(0.01)
                continue
            failures = 0
            if self.cfg.mirror:
                frame = cv2.flip(frame, 1)
            with self._lock:
                self._latest = (frame, t)
                self._seq += 1
                self._lock.notify_all()

    def read(self, timeout: float = 1.0) -> Optional[Frame]:
        """Next unseen frame, or None (timeout / end of file)."""
        if not self.is_live:
            ok, frame = self._cap.read()
            if not ok:
                return None
            # pace playback to real time so temporal features (blinks/min) stay correct
            now = time.time()
            if self._next_file_t > now:
                time.sleep(self._next_file_t - now)
            self._next_file_t = max(self._next_file_t + self._file_period, time.time() - 0.5)
            if self.cfg.mirror:
                frame = cv2.flip(frame, 1)
            return frame, time.time()
        with self._lock:
            if not self._lock.wait_for(lambda: self._seq != self._read_seq or not self._running, timeout):
                return None
            self._read_seq = self._seq
            return self._latest
