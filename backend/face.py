"""Live face features for the dashboard, from the CV group's engine in openCV/vision.

Runs their VisionEngine on the backend camera's frames (backend/camera.py holds the
webcam; only one program can), in a background thread:

    face = FaceTracker(camera)
    face.start()
    face.latest()   # -> {"blink_rate": 16.0, "hand_on_face": False, "lip_compression": 0.12}
    face.status     # "ok" | "missing" | "off"

Fields (same names the dashboard and scoring already use):
  blink_rate       blinks per minute over the last 30 s. None for the first 10 s, and
                   blinks aren't counted while the head is turned > 35 deg or tilted
                   > 25 deg, so put the camera at eye level, facing the subject.
  hand_on_face     True while a hand overlaps the face
  lip_compression  MediaPipe's "mouth press" score, 0-1, averaged over LIP_SMOOTH_S

openCV/ also holds the reaction-time identity test (run before the interview, on its
own). That isn't used here. Models: openCV/vision/*.task, fetched by openCV/download_models.py.
"""

from __future__ import annotations  # allows "X | None" hints on Python 3.9

import sys
import threading
import time
from collections import deque
from pathlib import Path

OPENCV_DIR = Path(__file__).resolve().parent.parent / "openCV"
FACE_MODEL = OPENCV_DIR / "vision" / "face_landmarker.task"
HAND_MODEL = OPENCV_DIR / "vision" / "hand_landmarker.task"
PROCESS_WIDTH = 640    # what the engine is tuned for; full HD frames would only slow it down
LIP_SMOOTH_S = 0.5
STALE_S = 1.0          # no processed frame for this long -> latest() returns None


class FaceTracker:
    def __init__(self, camera) -> None:
        self.camera = camera
        self.status = "off" if camera.disabled else "missing"  # "ok" | "missing" | "off"
        self._latest: tuple[float, dict] | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self) -> None:
        if self.camera.disabled:
            return
        missing = [p.name for p in (FACE_MODEL, HAND_MODEL) if not p.exists()]
        if missing:
            print(f"face: {', '.join(missing)} not found in openCV/vision/. "
                  "Run: python openCV/download_models.py  (face features are off)", flush=True)
            self.status = "off"
            return
        threading.Thread(target=self._run, name="face", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def latest(self) -> dict | None:
        with self._lock:
            if self._latest is None or time.time() - self._latest[0] > STALE_S:
                return None
            return self._latest[1]

    def _engine(self):
        # openCV/vision imports its sibling packages (interview/) by their top-level names.
        if str(OPENCV_DIR) not in sys.path:
            sys.path.insert(0, str(OPENCV_DIR))
        from vision import VisionConfig, VisionEngine

        cfg = VisionConfig(show_window=False)
        cfg.mesh.model_path = str(FACE_MODEL)
        cfg.output.emit_instant = cfg.output.emit_window = cfg.output.emit_events = False
        # No sinks: we read each frame's result directly. The hand model's default path
        # ("vision/hand_landmarker.task") resolves to openCV/vision/ (vision/landmarks.py).
        return VisionEngine(cfg)

    def _run(self) -> None:
        import cv2

        try:
            engine = self._engine()
        except Exception as e:  # missing mediapipe, bad model file, ...
            print(f"face: couldn't start the vision engine ({e}). Face features are off.", flush=True)
            self.status = "off"
            return
        print("face: vision engine running", flush=True)

        lips: deque[tuple[float, float]] = deque()
        last_t = 0.0
        while not self._stop.is_set():
            item = self.camera.latest_frame()
            if item is None or item[0] <= last_t:
                if self.camera.status != "ok":
                    self.status = "missing"
                time.sleep(0.005)
                continue
            last_t, frame = item

            height, width = frame.shape[:2]
            if width > PROCESS_WIDTH:
                frame = cv2.resize(frame, (PROCESS_WIDTH, round(height * PROCESS_WIDTH / width)))
            frame = cv2.flip(frame, 1)  # the engine's own camera mirrors; keep its tested setup
            try:
                rec = engine.process(frame, last_t).record
            except Exception as e:  # never let one bad frame kill the thread
                print(f"face: frame error: {e}", flush=True)
                continue

            if rec.get("face_present"):
                press = rec.get("bs_mouth_press")
                if press is not None:
                    lips.append((last_t, press))
            while lips and lips[0][0] < last_t - LIP_SMOOTH_S:
                lips.popleft()

            features = {
                # Only while a face is in view; otherwise the dashboard shows a gap.
                "blink_rate": engine.blink.blink_rate_per_minute(last_t) if rec.get("face_present") else None,
                "hand_on_face": bool(rec["face_touch"]) if "face_touch" in rec else None,
                "lip_compression": sum(v for _, v in lips) / len(lips) if lips else None,
            }
            with self._lock:
                self._latest = (time.time(), features)
            self.status = "ok"
