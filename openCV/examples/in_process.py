"""Embedding the vision engine inside the fusion process (no network)."""
import queue
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from vision import QueueSink, VisionConfig, VisionEngine  # noqa: E402

cfg = VisionConfig(show_window=False)
sink = QueueSink()
engine = VisionEngine.from_config(cfg, extra_sinks=[sink])
engine.start_background()
try:
    while True:
        try:
            msg = sink.queue.get(timeout=1.0)
        except queue.Empty:
            continue
        if "window_features" in msg:
            print("window:", {k: msg["window_features"][k] for k in ("blink_rate", "head_motion_energy",
                                                                     "vision_anomaly_score")})
except KeyboardInterrupt:
    engine.stop()
