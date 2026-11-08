"""Webcam capture for the dashboard's live video feed.

The backend is the only program that opens the webcam (see README), and the
dashboard can't, so this thread keeps the newest frame as a small JPEG and the
server sends it over the WebSocket as a "frame" message.

backend/face.py runs the CV group's engine (openCV/vision) on these frames, so it
never opens the webcam itself (only one program can hold it). It reads:

    t, frame = camera.latest_frame()   # full-size BGR numpy array, as cv2 gives it
    # skip it if t is the same as last time (no new frame yet)

Env variable:
  VERIFI_CAMERA  camera index (default 0), or "off" to disable video
"""

from __future__ import annotations  # allows "X | None" hints on Python 3.9

import base64
import os
import threading
import time

FRAME_WIDTH = 480
JPEG_QUALITY = 70
SEND_FPS = 30  # cap on dashboard video; most webcams give 30, so every frame goes out
REOPEN_EVERY_S = 5.0


class Camera:
    def __init__(self) -> None:
        setting = os.environ.get("VERIFI_CAMERA", "0").strip().lower()
        self.disabled = setting == "off"
        self.index = 0 if self.disabled else int(setting)
        self.status = "off" if self.disabled else "missing"  # "ok" | "missing" | "off"
        self._latest: tuple[float, str] | None = None
        self._raw = None  # (capture time, full-size BGR frame) for vision
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._capture = None

    def start(self) -> None:
        if self.disabled:
            return
        # Open once here, on the main thread: on macOS the camera permission
        # prompt can't appear from a background thread.
        self._capture = self._open()
        threading.Thread(target=self._run, name="camera", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def latest(self) -> tuple[float, str] | None:
        """(capture time, base64 JPEG) of the newest frame, or None."""
        with self._lock:
            return self._latest

    def latest_frame(self):
        """(capture time, full-size BGR numpy frame) of the newest frame, or None. For vision.

        Every camera frame, even above SEND_FPS. Don't modify the
        array in place; copy it first if you need to draw on it.
        """
        with self._lock:
            return self._raw

    def _open(self):
        try:
            import cv2
        except ImportError:
            print("camera: opencv-python isn't installed, video is off", flush=True)
            return None
        capture = cv2.VideoCapture(self.index)
        if not capture.isOpened():
            capture.release()
            print(f"camera: couldn't open camera {self.index}", flush=True)
            return None
        return capture

    def _run(self) -> None:
        import cv2

        capture = self._capture
        next_encode = 0.0
        while not self._stop.is_set():
            if capture is None:
                self.status = "missing"
                time.sleep(REOPEN_EVERY_S)
                capture = self._open()
                continue

            ok, frame = capture.read()  # blocks at the camera's own frame rate
            if not ok:
                capture.release()
                capture = None
                continue
            self.status = "ok"

            now = time.time()
            with self._lock:
                self._raw = (now, frame)  # read() gives a new array each time, so sharing it is safe
            # A schedule, not "time since the last one": frames arrive unevenly (one late,
            # the next right behind it), and a spacing check would drop the second.
            if now < next_encode:
                continue
            next_encode = max(next_encode + 1 / SEND_FPS, now - 1 / SEND_FPS)
            height, width = frame.shape[:2]
            if width > FRAME_WIDTH:
                frame = cv2.resize(frame, (FRAME_WIDTH, round(height * FRAME_WIDTH / width)))
            ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if ok:
                with self._lock:
                    self._latest = (now, base64.b64encode(jpeg.tobytes()).decode("ascii"))

        if capture is not None:
            capture.release()
