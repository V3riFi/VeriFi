# VeriFi vision

The `vision/` package turns a webcam into numbers: eye openness and blinks, head pose and motion, gaze, mouth and brow movement, and hand-over-face. It uses MediaPipe Face Landmarker (478 points + 52 blendshapes) and Hand Landmarker, on the CPU, with no training data and no cloud.

It is used in two ways.

| Use | Where | What it does |
|---|---|---|
| **Quality control in every test** | `run_identity.bat`, `run_verifi.bat` | records one row per frame (`<session>.frames.csv`). After the test, `vision/offline.py` drops trials where the eyes were closed, the person looked away, moved, talked or touched their face. Face cues get a weight only if they prove themselves on the person's own known details. See [METHOD.md](METHOD.md#6-the-camera-quality-control) |
| **Live stream for the backend / frontend** | `run.bat` | streams features and events over UDP with a live HUD, and accepts item onsets |

## Live stream (`run.bat`)

```bash
run.bat
```
```bash
run.bat --udp 127.0.0.1:5005 --control 127.0.0.1:5006
```

- **Options:**
  - `--udp HOST:PORT` sends every message as JSON.
  - `--control HOST:PORT` receives item onsets.
  - `--jsonl file` / `--stdout` log the messages.
  - `--source 1` picks another webcam (or `--source video.mp4` reads a file).
  - `--no-hands` gives more FPS.
  - `--no-window` runs headless.
- **Keys in the video window:** **q** quit, **b** recalibrate the baseline, **m** toggle the mesh.
- **Speed:** about 25–30 FPS on a laptop CPU, about 20 with hand detection.

**Telling vision when an item appears.** Pick one option.

A. Same Python process:
```python
from vision import VisionConfig, VisionEngine, CallbackSink

engine = VisionEngine.from_config(VisionConfig(show_window=False), extra_sinks=[CallbackSink(on_vision_msg)])
engine.start_background()
engine.mark_item("TRUE")        # at the moment the item is drawn (time.time())
engine.reset_cit()              # new subject
engine.stop()
```

B. Separate process: run `run.bat --udp 127.0.0.1:5005 --control 127.0.0.1:5006`, then
```python
from vision.control import send_item_onset
send_item_onset("TRUE", "127.0.0.1:5006")      # or send {"cmd": "item_onset", "item": "TRUE"} yourself
```

Send the onset when the item is actually **drawn**, and keep at least 6.6 s between items: a 5 s response window plus blink grace.

**Messages** (all have `"source": "vision"` and `timestamp` = `time.time()` on the same laptop):

| Message | When |
|---|---|
| `{"feature": "blink_rate", "value": 14.2}` and other instant features | up to 10 Hz each |
| `{"feature": "blink_event", "value": 142}` (ms), `face_touch_event`, `long_eye_closure_ms`, `yawn_event` | as they happen |
| `{"window_features": {...}}` | every 5 s: rates, means, and z-scores against the first 45 s baseline |
| `{"item": "TRUE", "valid": true, "item_features": {...}}` | about 5.6 s after each item onset |
| `{"cit_scores": {"TRUE": {"vision_cit_score": 1.4, ...}}}` | after each item |

`item_features` holds these keys:
- `blinks_early` (0–2 s), `blinks_late` (2–5 s), `first_blink_latency_s`
- `lip_compression`, `brow_furrow`
- `head_motion_pre`, `head_motion_post`, `facial_motion_post`
- `gaze_aversion_post`, `face_touch_ratio`, `mouth_open_max`, `face_presence_ratio`

When the face is visible less than 60% of the window, `valid` is false and every cue is `null`, never a fake zero.

Treat these as exploratory inputs, not a verdict. On their own, face cues were at chance in our card tests (METHOD.md, section 2).

## How it works

| Stage | Detail |
|---|---|
| Camera (`camera.py`) | background thread keeps only the newest frame (no lag build-up); DirectShow + MJPG on Windows; reconnects after failed reads |
| Landmarks (`landmarks.py`) | Face Landmarker in video mode (detect once, then track). Current mediapipe ships only the Tasks API, so the `.task` models come from `setup.bat` |
| Alignment (`geometry.py`) | eye midpoint at the origin, eye line horizontal, inter-eye distance = 1, so features don't depend on distance or head tilt |
| Blinks (`blink.py`) | eye aspect ratio with per-person thresholds (see below) |
| Head pose (`headpose.py`) | `solvePnP` on 6 landmarks, a model already in camera axes (no 180° pitch flip), mirrored-frame handling, One-Euro smoothing. Pitch + = looking up, yaw + = toward image right |
| Face features (`face_features.py`) | lip compression, brow furrow/raise, mouth opening, facial motion energy (head motion removed), iris-based gaze with a learned per-person centre, yawns |
| Hands (`hands.py`) | hand-over-face, every 2nd frame |
| Output (`aggregator.py`, `output.py`, `cit.py`) | instant values, events, 5 s windows with baseline z-scores, per-item epochs, frame CSV |
| After a test (`offline.py`) | the quality checks and countermeasure check described in METHOD.md |

**Blink detector:**
- **Openness thresholds:** open-eye openness is learned per person (80th percentile of the first 30 frames, then updated from the last 10 s). Closing is at 72% of it and reopening at 82% (hysteresis). A fixed threshold misses blinks for people with narrow eyes.
- **Durations:** 50–500 ms is a blink, > 500 ms a long closure, < 50 ms jitter.
- **Unusable poses:** pitch > 25° or yaw > 35° makes the eye state "unknown". Looking down at notes fakes blinks.
- **Stuck-closed guard:** if a "closure" lasts > 2 s while the eye is clearly open (openness > 45% of open), the state resets and the thresholds re-learn. This happens when someone looks down at the keys.

**Data flow rules:** nothing after `landmarks.py` imports MediaPipe. All durations come from timestamps, not frame counts, so FPS drops don't distort them. `process(frame, t)` is testable without a camera (`tests/test_core.py`).
