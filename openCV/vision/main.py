"""CLI entry point.

    python -m vision.main                          # webcam 0, overlay window
    python -m vision.main --udp 127.0.0.1:5005     # stream to fusion engine
    python -m vision.main --source demo.mp4        # recorded fallback clip
    python -m vision.main --stdout --no-window     # headless, JSON lines
    python -m vision.main --udp 127.0.0.1:5005 --control 127.0.0.1:5006   # backend drives CIT onsets
"""
from __future__ import annotations

import argparse
import logging

from .config import VisionConfig
from .pipeline import VisionEngine


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="VeriFi vision subsystem")
    p.add_argument("--source", default="0", help="webcam index or video file path")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--no-mirror", action="store_true")
    p.add_argument("--backend", default="auto", choices=["auto", "tasks", "legacy"])
    p.add_argument("--model", default="vision/face_landmarker.task")
    p.add_argument("--udp", help="host:port of the fusion engine")
    p.add_argument("--jsonl", help="append all messages to this file")
    p.add_argument("--stdout", action="store_true", help="print JSON lines")
    p.add_argument("--no-window", action="store_true")
    p.add_argument("--no-instant", action="store_true", help="only send window + event messages")
    p.add_argument("--no-hands", action="store_true", help="disable hand-over-face detection (+FPS)")
    p.add_argument("--hands", action="store_true", help=argparse.SUPPRESS)   # old flag; now the default
    p.add_argument("--control", metavar="HOST:PORT",
                   help="listen for CIT item-onset commands, e.g. 127.0.0.1:5006")
    p.add_argument("--window", type=float, default=5.0, help="aggregation window, seconds")
    p.add_argument("--baseline", type=float, default=45.0, help="baseline calibration, seconds")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> None:
    a = parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = VisionConfig()
    cfg.camera.source = int(a.source) if a.source.isdigit() else a.source
    cfg.camera.width, cfg.camera.height = a.width, a.height
    cfg.camera.mirror = not a.no_mirror
    cfg.mesh.backend, cfg.mesh.model_path = a.backend, a.model
    cfg.output.udp, cfg.output.jsonl_path, cfg.output.stdout = a.udp, a.jsonl, a.stdout
    cfg.output.emit_instant = not a.no_instant
    cfg.show_window = not a.no_window
    cfg.enable_hands = not a.no_hands
    cfg.cit.control_udp = a.control
    cfg.agg.window_s, cfg.agg.baseline_s = a.window, a.baseline
    VisionEngine.from_config(cfg).run()


if __name__ == "__main__":
    main()
