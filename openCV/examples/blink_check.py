"""60-second blink check: is the blink detector catching your natural blinks?

Look at the screen naturally for 60 s. Don't try to blink more or less. No talking needed.
Afterwards it compares three counts:
  * detector  - blinks the EAR state machine accepted (what the CIT uses)
  * model     - blinks seen by MediaPipe's own trained eye-closure score (bs_eye_blink)
  * EAR dips  - every short dip in the raw eye-openness signal
If detector << model, the detector is too strict and the printout says why.

    run_blink_check.bat                          (records + analyzes)
    python examples/blink_check.py --analyze data/blink_check.frames.csv   (re-analyze a file)
"""
from __future__ import annotations

import argparse
import csv
import logging
import pathlib
import sys
import threading

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def load(path: str):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    num = lambda r, k: float(r[k]) if r.get(k) not in (None, "") else np.nan
    cols = ("t", "face_present", "ear", "eye_state", "ear_close_thr", "bs_eye_blink", "head_pitch", "head_yaw",
            "blink_event")
    return {k: np.array([num(r, k) for r in rows]) for k in cols}


def events(t, mask, min_gap=0.15):
    """Start times + lengths (frames) of runs where mask is True."""
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            if not out or t[i] - out[-1][0] > min_gap:
                out.append((t[i], j - i + 1))
            i = j + 1
        else:
            i += 1
    return out


def analyze(path: str) -> None:
    d = load(path)
    t, ear = d["t"], d["ear"]
    face = d["face_present"] == 1
    dur = t[-1] - t[0] if len(t) > 1 else 0
    if dur < 10 or face.sum() < 50:
        print("Too little data (face not visible?).")
        return
    mins = dur / 60.0
    fps = len(t) / dur
    st = d["eye_state"]
    gated = np.mean(st[face] == -1)
    pitch, yaw = d["head_pitch"][face], d["head_yaw"][face]

    det = int(np.nansum(d["blink_event"]))              # blinks the detector accepted
    closures = int(np.sum((st[1:] == 1) & (st[:-1] == 0)))
    bs = np.nan_to_num(d["bs_eye_blink"])
    model = events(t, face & (bs > 0.5))
    open_level = np.nanpercentile(ear[face], 80)
    thr = np.nanmedian(d["ear_close_thr"][face])
    dips = events(t, face & (ear < 0.80 * open_level))
    one_frame = sum(1 for _, n in dips if n == 1)

    print(f"\n=== Blink check: {path} ===")
    print(f"duration {dur:.0f} s, {fps:.1f} FPS, face visible {100 * face.mean():.0f}%")
    print(f"head pitch median {np.nanmedian(pitch):+.0f} deg, yaw median {np.nanmedian(yaw):+.0f} deg; "
          f"blink detection paused (pose gate) {100 * gated:.0f}% of face frames")
    print(f"EAR: open level {open_level:.3f}, closing threshold {thr:.3f} ({thr / open_level:.0%} of open)")
    print(f"\n  detector (CIT uses this):  {det / mins:5.1f} blinks/min   "
          f"({closures - det} closures rejected as too short/long)")
    print(f"  model eye-blink score:     {len(model) / mins:5.1f} blinks/min")
    print(f"  raw EAR dips (<80% open):  {len(dips) / mins:5.1f} /min, {one_frame} of {len(dips)} last only 1 frame")
    if len(model):
        peak = [np.nanmin(ear[(t >= s) & (t <= s + 0.4)]) for s, _ in model]
        print(f"  EAR minimum during model-detected blinks: median {np.median(peak):.3f} "
              f"({np.median(peak) / open_level:.0%} of open) vs threshold {thr:.3f}")

    print("\nReading it:")
    if gated > 0.2:
        print(" - Detection was paused a lot: the camera is probably too low/high. Raise it to eye level.")
    if len(model) / mins < 4:
        print(" - The model also saw few blinks, so you may really have blinked rarely (screens do that).")
    elif det < 0.6 * len(model):
        print(" - Detector is missing blinks the model sees.")
        if one_frame > 0.3 * max(len(dips), 1):
            print("   Many dips last a single frame, so they're rejected as jitter (< 50 ms at this FPS).")
        if len(model) and np.median(peak) > thr:
            print("   Your natural blinks don't dip below the closing threshold (partial blinks).")
    else:
        print(" - Detector and model roughly agree. Blink counting looks OK.")


def record(seconds: float, source: str, hands: bool = False) -> str:
    from rt_cit_test import unique_path
    from vision.config import VisionConfig
    from vision.pipeline import VisionEngine

    path = unique_path(pathlib.Path("data/blink_check.frames.csv"))
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = VisionConfig()
    cfg.camera.source = int(source) if source.isdigit() else source
    cfg.output.frames_csv = str(path)
    cfg.output.emit_instant = False
    cfg.enable_hands = hands            # --hands: same load as a CIT run (hand detection lowers FPS)
    engine = VisionEngine.from_config(cfg)
    print(f"Recording {seconds:.0f} s to {path}. Look at the screen naturally (q to stop early)...", flush=True)
    threading.Timer(seconds, engine.stop).start()
    engine.run()
    return str(path)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=60.0)
    p.add_argument("--source", default="0")
    p.add_argument("--analyze", metavar="CSV", help="only analyze an existing frames CSV")
    p.add_argument("--hands", action="store_true", help="also run hand detection, like a CIT run")
    a = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    analyze(a.analyze or record(a.seconds, a.source, a.hands))


if __name__ == "__main__":
    main()
