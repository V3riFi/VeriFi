"""Webcam capture for the dashboard's live video feed.

The backend is the only program that opens the webcam (see README), and the
dashboard can't, so this thread keeps the newest frame as a small JPEG and the
server sends it over the WebSocket as a "frame" message.

When vision/face.py's FaceTracker lands, it should share this capture rather
than open the camera a second time: only one program can hold the webcam.

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
SEND_FPS = 10
REOPEN_EVERY_S = 5.0


class Camera:
    def __init__(self) -> None:
        setting = os.environ.get("VERIFI_CAMERA", "0").strip().lower()
        self.disabled = setting == "off"
        self.index = 0 if self.disabled else int(setting)
        self.status = "off" if self.disabled else "missing"  # "ok" | "missing" | "off"
        self._latest: tuple[float, str] | None = None
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
        last_encode = 0.0
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
            if now - last_encode < 1 / SEND_FPS:
                continue
            last_encode = now
            height, width = frame.shape[:2]
            if width > FRAME_WIDTH:
                frame = cv2.resize(frame, (FRAME_WIDTH, round(height * FRAME_WIDTH / width)))
            ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if ok:
                with self._lock:
                    self._latest = (now, base64.b64encode(jpeg.tobytes()).decode("ascii"))

        if capture is not None:
            capture.release()
