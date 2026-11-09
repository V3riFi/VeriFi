# VeriFi

<p align="center"><img src="docs/banner.webp" alt="VeriFi logo: a figure drawn in ASCII characters inside a wide eye shape" width="720"></p>

**natHacks 2026 · Security & Defense track**

A multimodal **suspicion detector** for interrogations. The interrogator asks questions and can show objects or crime-scene images; the suspect answers normally. VeriFi watches the subject's body and shows a live stress indicator plus which signals spiked after each question, compared against that person's own calm baseline.

---

## Quick run

Prereqs: sensors wired per [Hardware](#hardware-arduino-uno), Arduino flashed with `firmware/verifi/sensors/` per [Arduino](#arduino), and the **Serial Monitor/Plotter closed**.

First time only:

```bash
git clone https://github.com/V3riFi/VeriFi.git
cd VeriFi
git config core.hooksPath .githooks                    # once per clone; enables the commit-message hook
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 openCV/download_models.py
```

Then, every time, one command from the repo root starts the backend and frontend and opens the dashboard in your browser:

```bash
python3 run.py     # Windows: python run.py
```

It uses the real Arduino, camera and mic. `python3 run.py --fake-body` replaces only the body sensors (GSR, heart rate, breathing) with fake signals and keeps the camera and mic real. Press **Ctrl+C** to stop everything. To run the backend and frontend separately (for example with `VERIFI_FAKE=1`), see [Run](#6-run-backend--frontend).

`http://localhost:8000/` shows whether the Arduino is connected. For a 10 s baseline instead of 60 s while testing: `VERIFI_BASELINE_S=10 python3 run.py`. Full details in [Setup](#setup).

---

## How a session runs

```text
  start
    │
    ▼
  BASELINE ──────────────  ~60 s, silent and still
    │                      → mean/std per signal = the reference for the whole session
    ▼
  VOICE BASELINE ────────  ~25 s, subject reads 5 neutral sentences aloud
    │                      → adds a normal range for pitch/jitter
    ▼
  QUESTION ──────────────  interrogator hits Ask (+ optional object/image)
    │  t = 0, timeline marker
    ▼
  ANSWER ────────────────  short answers score cleanest
    │  t ≈ 10 s
    ▼
  RESPONSE ──────────────  peak in window − mean of the 2 s before, ÷ baseline SD
    │                      → spike + per-signal breakdown
    ▼
  (repeat per question) ─► STOP ─► summary, questions ranked by spike
```

1. **Baseline.** Subject sits calmly and silently for ~60 s (recorded as the **baseline**). Then the dashboard shows five neutral sentences, which the subject reads aloud for ~25 s: the **voice baseline**.
2. **Ask.** Interrogator clicks **Ask** (or presses the hardware button) when the question starts; optionally an object or image is shown with it. The moment the question is finished, they click **Done asking** (or press Space): the subject's response latency is timed from there.
3. **Answer.** Short answers work best (see Limitations).
4. **Score.** VeriFi scores the ~10 s window from the question's start and adds a timeline marker showing spike size and which signals drove it.
5. **Repeat.** At the end, every question is ranked by response.

---

## Signals

| Signal | Source | What we look at |
|---|---|---|
| Skin conductance | Grove GSR | **Simulated.** The sensor reads too steadily to use, so the dashboard shows a flat value at a real session's average. No weight in any score |
| Heart rate | BioAmp ECG | Change from baseline |
| Breathing | BioAmp EMG on the chest | Breath events, used to estimate the rate and spot holds |
| Face | Webcam + MediaPipe Face Landmarker (`openCV/vision`) | Blink rate (Eye Aspect Ratio), hand over face, lip compression. Blinks ignored if head turned >35° or tilted >25° — keep camera at eye level |
| Voice | Laptop mic + Parselmouth (`voice/live.py`) | Live: pitch, HNR (voice clarity), jitter, shimmer, loudness, and spectral slope. Per answer: response latency, length, speech rate, pauses, pitch, intonation (pitch SD), HNR. Shown in the dashboard's Voice dropdown, not part of stress. Speech errors, repetitions and stuttering need a transcript, which VeriFi doesn't make |

---

## Architecture

```text
   ┌─────────────────────┐  ┌──────────┐  ┌──────────┐
   │ ECG · EMG · GSR     │  │ Webcam   │  │ Mic      │
   │ Button              │  │          │  │          │
   └──────────┬──────────┘  └────┬─────┘  └────┬─────┘
              │                  │             │
   ┌──────────▼──────────┐       │             │
   │ Arduino UNO         │       │             │
   │ 115200 baud · 10 Hz │       │             │
   └──────────┬──────────┘       │             │
              │                  │             │
              ├──────────────────┴─────────────┘
              │
   ┌──────────▼──────────────────────────────────────────────┐
   │  PYTHON BACKEND — one process, one clock                │
   │                                                         │
   │  inputs    serial_io.py · camera.py · voice/live.py     │
   │  vision    face.py  (MediaPipe on camera frames)        │
   │  scoring   scoring.py  (baseline + per-signal z-scores) │
   │  outputs   server.py → WebSocket JSON · data/*.csv      │
   └─────────────────────────────┬───────────────────────────┘
                                 │ WebSocket JSON · ~20/s
   ┌─────────────────────────────▼────────────────────────────┐
   │  FRONTEND — Vite + React                                 │
   │  live charts · stress indicator · question timeline      │
   └──────────────────────────────────────────────────────────┘
```

| Layer | Tech |
|---|---|
| Firmware | Arduino C++ (Arduino IDE) on an Arduino UNO |
| Backend | Python, FastAPI + uvicorn, **one process** |
| Serial | `pyserial`, background thread |
| Vision | `opencv-python` + `mediapipe` (Tasks API), background thread |
| Voice | `sounddevice` + `praat-parselmouth`, background thread |
| Scoring | `numpy` (z-scores against baseline, no training) |
| Storage | CSV files in `data/` |
| Frontend | Vite + React; charts with uPlot or Chart.js |
| Backend → frontend | WebSocket `ws://localhost:8000/ws`, JSON, ~20 updates/sec |

**Two rules:**
- **Only the backend opens the webcam and serial port.** The frontend gets video as small JPEG frames over the WebSocket.
- **Serial and vision share one Python process**, so every signal uses the same clock.

---

## Repo layout

```
firmware/verifi/sensors/   Arduino sketch (sensors.ino), streams sensors over serial (hardware team)
backend/server.py          FastAPI app + /ws WebSocket, session/question timing, CSV logging (entry point)
backend/serial_io.py       serial reader thread
backend/scoring.py         baseline, live stress indicator, per-question scoring
backend/camera.py          webcam capture thread, sends small JPEG frames
backend/face.py            runs the CV group's face engine on camera frames
openCV/                    CV group: face engine (vision/), plus the reaction-time identity test (see openCV/README.md)
voice/live.py              the mic: live voice features, per-answer features, and the session's WAV
                           (python -m voice.live checks the mic; python -m voice.live file.wav analyzes a recording)
frontend/                  Vite + React dashboard
data/                      recorded sessions: <subject>_<date-time>.csv + .wav (NOT committed, see CONTRIBUTING.md)
requirements.txt           Python packages (hand-written)
```

---

## Setup

Setup has seven steps. Steps 2–3 apply to everyone; step 4 is only for face tracking; step 7 is only for hardware.

### 1. Prerequisites
- Python 3.9–3.12 (required by MediaPipe)
- Node.js **20.19+ or 22.12+** (Vite requirement)
- Arduino IDE (for the firmware)

### 2. Clone + Python environment (whole repo, everyone)

```bash
git clone https://github.com/V3riFi/VeriFi.git
cd VeriFi
git config core.hooksPath .githooks     # once per clone; enables the commit-message hook
python3 -m venv .venv
source .venv/bin/activate               # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Python dependencies (whole repo, everyone)

`requirements.txt` is written **by hand** — do not overwrite it with `pip freeze` (the team mixes Mac/Windows; a frozen file from one OS can break installs on the other). Contents:

```
fastapi
uvicorn[standard]
pyserial
opencv-python
mediapipe==1.2.2
numpy
sounddevice
praat-parselmouth
matplotlib
```

`mediapipe` is pinned (our vision code needs the 1.x Tasks API). `uvicorn[standard]` is required for WebSocket support.

**Intel Mac note:** if `uname -m` prints `x86_64` on a Mac, `mediapipe==1.2.2` won't install (Apple Silicon wheels only). Install `mediapipe==0.10.21` by hand instead (needs Python 3.9–3.12) and leave `requirements.txt` unchanged.

### 4. Face models (vision only, once per laptop)

```bash
python3 openCV/download_models.py
```

Without them the backend still runs, with face features off.

### 5. Frontend dependencies

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

### 6. Run (backend + frontend)

From the repo root, with the venv active:

```bash
uvicorn backend.server:app --port 8000
```

The backend auto-detects the Arduino (Arduino, CH340, FTDI, CP210x vendor IDs first, then port name). It prints `serial: connected to ...` on success, or lists visible ports on failure. Override with `VERIFI_PORT=/dev/cu.usbmodem1101 uvicorn backend.server:app --port 8000`.

Add `VERIFI_BASELINE_S=10` for a 10 s baseline instead of 60 s while testing. On Windows PowerShell, set each variable first (`$env:VERIFI_BASELINE_S=10`), then run `uvicorn`.

Start the frontend in a second terminal. `http://localhost:8000/` shows whether the Arduino is connected.

**Hardware check without the backend:** `python3 -m backend.serial_io` opens a live plot of the Arduino's sensors. Don't run it while the backend is running.

### 7. Arduino firmware (hardware only)

1. Install the [Arduino IDE](https://www.arduino.cc/en/software).
2. Install the **DFRobot_BloodOxygen_S** library (for the MAX30102 module).
3. Open `firmware/verifi/sensors/`, select **Arduino Uno** + the right port, upload.
4. **Close the Serial Monitor/Plotter before running the backend** — only one program can use the port at a time.

Port names: Windows `COM3`, `COM4`, … (Device Manager) · Mac `/dev/cu.usbmodem…` · Linux `/dev/ttyACM0` (or `/dev/ttyUSB0` on some clones).

---

## WebSocket contract

`ws://localhost:8000/ws` — one JSON object per message; every message has a `type`.

```text
  Frontend ──► backend        backend ──► frontend
  ─────────────────────       ──────────────────────────────────────────
  start                       sample          ~10/s, live values
  question  (Ask)             baseline        start / done (mean, std)
  question_end (Done asking)  voice_baseline  start / done
  stop
                              question        timestamped echo (marker)
                              verdict_progress ~3/s while an answer's window is open
                              response        ~10 s after a question, with its verdict
                              session_verdict after each response, all answers combined
                              summary         on stop, ranked
                              status          ~1/s, per-component health
                              frame           webcam JPEG, ~30/s
```

### Frontend → backend

```json
{"type": "start",    "subject": "subject04"}
{"type": "question", "label": "Q3", "text": "Where were you on Tuesday night?", "stimulus": "knife.jpg"}
{"type": "question_end"}
{"type": "stop"}
```

- `start` — begins a session; baseline is recorded first.
- `question` — sent on **Ask**. `text` and `stimulus` optional (`stimulus` is the object/image, or `null`). A hardware button press will start a question too (not in the sketch yet).
- `question_end` — sent on **Done asking**, the moment the interrogator finishes the question. Response latency is timed from here, and the answer gets 10 s from here before the `response` is sent. Without it, latency is estimated (the first 0.5 s silence after the question).
- `stop` — ends the session.

### Backend → frontend

```json
{"type": "sample",   "t": 1234.5, "gsr": 17.4, "hr": 74, "flex": 0, "breath_rate": 14, "blink_rate": 18, "pitch": 182.4, "jitter": 1.1, "shimmer": 6.3, "voice_db": 61.0, "hnr": 14.2, "spectral_slope": -18.5, "stress": 41}
{"type": "baseline", "t": 1180.0, "phase": "start", "duration": 60}
{"type": "baseline", "t": 1240.0, "phase": "done", "mean": {"gsr": 17.4, "hr": 72.4}, "std": {"gsr": 1.0, "hr": 1.8}, "alert_sd": 2, "stress_alert": 25}
{"type": "voice_baseline", "t": 1240.0, "phase": "start", "duration": 25, "sentences": ["I am sitting in a chair right now.", "..."]}
{"type": "voice_baseline", "t": 1265.0, "phase": "done", "voiced_samples": 140, "mean": {"gsr": 17.4, "pitch": 182.0}, "std": {"gsr": 1.0, "pitch": 9.0}, "alert_sd": 2, "stress_alert": 25, "reading": {"duration_s": 22.1, "speech_rate": 3.1, "pauses": 4, "pitch": 181.0, "intonation": 24.0, "hnr": 15.2}}
{"type": "question", "t": 1250.0, "label": "Q3", "text": "Where were you on Tuesday night?", "due": 1260.0, "phase": "start"}
{"type": "question", "t": 1252.4, "label": "Q3", "due": 1262.4, "phase": "end"}
{"type": "verdict_progress", "t": 1255.1, "label": "Q3", "elapsed": 5.1, "due": 1262.4, "verdict": {"provisional": true, "band": "elevated", "likelihood": 0.71, "interval": [0.44, 0.9], "z": 1.82, "categories": [{"name": "Cardiac", "weight": 0.15, "z": 2.9, "contribution": 1.1, "suspicion": 0.998}], "gates": [{"key": "coverage", "label": "Sensors kept streaming", "pass": true, "detail": "Longest gap 0.1 s so far"}]}}
{"type": "response", "t": 1260.0, "label": "Q3", "spike": 2.1, "signals": {"hr": 1.1, "breath_hold_s": 2.6, "blink": -0.8}, "voice": {"latency_s": 1.4, "latency_marked": true, "duration_s": 2.2, "speech_rate": 3.6, "pauses": 1, "pause_s": 0.5, "pitch": 190.2, "intonation": 18.0, "hnr": 13.1, "cut": false}, "verdict": {"provisional": false, "band": "elevated", "result": "reaction", "likelihood": 0.78, "interval": [0.52, 0.93], "reason": null, "z": 1.94, "z_eff": 1.94, "log_lr": 1.27, "norm": 0.47, "model": {"lambda": 2.0, "sigma": 1.0, "prior": 0.5, "interval_z": 1.2816}, "categories": ["..."], "gates": ["..."], "null_total": {"n": 48, "mean": 0.1, "sd": 1.0, "edges": ["..."], "counts": ["..."], "percentile": 0.96}, "trace": {"from": -10, "to": 10, "series": {"hr": {"label": "Heart rate", "unit": "bpm", "points": [[-9.9, 72.1], "..."]}}, "windows": ["..."], "end": 2.4}}}
{"type": "session_verdict", "t": 1262.4, "likelihood": 0.64, "interval": [0.35, 0.86], "band": "elevated", "log_lr": 0.58, "z": 1.1, "answers": 3, "counts": {"high": 0, "elevated": 1, "unclear": 1, "low": 1, "insufficient": 0}, "history": [{"label": "Q1", "band": "low", "log_lr": -1.3, "cum_log_lr": -1.3, "likelihood": 0.21, "interval": [0.11, 0.55]}, "..."]}
{"type": "summary",  "questions": [{"label": "Q3", "spike": 2.1, "latency_s": 1.4, "suspicion": 0.97}, {"label": "Q1", "spike": 0.4, "latency_s": 0.8, "suspicion": 0.31}]}
{"type": "status",   "components": {"arduino": "ok", "gsr": "simulated", "pulse": "missing", "breathing": "ok", "camera": "ok", "face": "ok", "voice": "off"}, "session": null}
{"type": "frame",    "t": 1250.1, "jpeg": "<base64 JPEG>"}
```

| Type | Notes |
|---|---|
| `sample` | ~10/sec from Arduino. `t` = laptop Unix time (s). `stress` is 0–100 and appears only after baseline. **Any field may be `null` or missing** — draw a gap, don't crash. `hr` is null until the heartbeat locks; `gsr` is simulated; `flex` is 1 on the sample where a breath seems to start and 0 otherwise (an event, not an exact count); `breath_rate` is an estimate anchored at 15/min that moves part of the way toward how often events arrive; `breath_rate` until ~15 s; `blink_rate` for the first 10 s of face tracking and whenever no face is visible. `hand_on_face` is boolean. Voice fields (`pitch`, `jitter`, `shimmer`, `hnr`, `spectral_slope`) are null when nobody talks; `voice_db` (loudness) always has a value; all are missing if the mic is off. `hnr` and `spectral_slope` are in dB and can be negative. `hr` updates in ~4 s steps. `temp` in °C, `spo2` in %. |
| `baseline` | `start` carries the baseline length; `done` carries each signal's `mean`/`std`, plus `alert_sd` and `stress_alert` (the dashboard uses these to draw baseline bands and turn lines red outside them). |
| `voice_baseline` | Subject reads `sentences` aloud for `duration` s (`VERIFI_VOICE_BASELINE_S`, default 25; `0` skips). `done` carries the baseline again, now with the voice fields (from moments the subject is speaking), plus `reading`: the reading's per-answer features, to compare answers with (`null` if it couldn't be measured). Questions are refused until it's done. Skipped entirely if the mic is broken; if it hears too little speech, an `error` follows and voice has no normal range. The silent baseline also measures room loudness — afterward `pitch`/`jitter`/`shimmer` only count when the voice is ≥6 dB above the room. |
| `question` | Timestamped echo: `phase` `start` (the dashboard places a marker) or `end` (Done asking). `start` also carries the question's `text` (`null` if none was typed). Both carry `due`: when the response will be sent (Done asking moves it to 10 s after). |
| `verdict_progress` | Every ~0.3 s while an answer's window is open: a provisional `verdict` (`provisional: true`) on the window so far, so the dashboard's gauge moves as the reaction builds. Lighter than the final one: no `signals`, `null`, `null_total` or `trace`. `elapsed` = seconds since Ask. |
| `response` | Sent ~10 s after a question starts, or 10 s after Done asking if that's later. `voice` holds the answer's voice features (`null` without a mic): `latency_s` (Done asking, or the estimated end of the question when `latency_marked` is false, to the first word), `duration_s` (first to last word), `speech_rate` (syllables/s), `pauses` and `pause_s` (silences of 0.3 s or more), `pitch` (Hz), `intonation` (pitch SD, Hz), `hnr` (dB), `cut` (still talking when the window closed). Fields are `null` when no answer was heard. `spike` = overall response in SD above baseline; `signals` breaks it down per signal (SD, except `breath_hold_s` in seconds). `confidence` (`backend/confidence.py`, `null` if it couldn't be scored): `suspicion` 0-1 overall, and `categories`, a list of `{name, weight, suspicion, signals, missing}` (each signal: `key`, `name`, `weight`, `direction` +1/-1/0, `phasic`, `base`, `now`, `z`, `suspicion`; `missing` says why a category or signal had no data). Suspicion is how far the answer departs from the baseline (1 - the chance of a departure this big if nothing changed); the dashboard's Question summary shows it. `verdict` (`backend/verdict.py`, `null` if it couldn't be scored) is the answer's deception likelihood: `likelihood` 0-1 (`null` when a quality gate failed), `interval` its 80% range, `band` `low`/`unclear`/`elevated`/`high`/`insufficient`, `reason` why not when insufficient, and `result` (`calm`/`unclear`/`reaction`/`insufficient`, kept for compatibility). For the "Why this reading" breakdown: `z` (combined score), `z_eff`, `log_lr`, `norm` (√Σw²), `model`; `categories`, one per signal group: `name`, `weight`, `z`, `contribution` (w·z/√Σw²; they add up to `z`), `suspicion`, `method`, `missing`, `signals` (raw values per cue: `base`/`base_sd`, `now`/`now_sd`, `z`, `p`, `direction`, `phasic`) and `null` (pseudo-questions across the calm baseline: `n`, `mean`, `sd`, histogram `edges`/`counts`, and this answer's `percentile`); `null_total` the same for `z`; `gates` (`key`, `label`, `pass`, `detail`); `trace` (raw signals from −10 to +10 s around Ask, `points` as [seconds from Ask, value], plus the measurement `windows` and `end` = Done asking). |
| `session_verdict` | After every `response`: all scored answers so far, re-combined. `likelihood`, `interval`, `band` as in a verdict, `log_lr` (sum over answers), `z` (Stouffer over the answers' scores), `answers` (how many added evidence), `counts` per band, and `history`: the session likelihood after each answer. |
| `summary` | Sent on `stop`. Every question, highest spike first, with its `latency_s`, overall `suspicion` (see `confidence` in `response`), `likelihood` and `band`; `session` is the final `session_verdict`. |
| `status` | ~1/sec and on connect. Each component is `ok`, `missing`, or `off`. `session` describes the running session (or `null`) so a reloaded dashboard can reattach: its questions carry `text`, `due` and `verdict`, and `session.verdict` is the latest `session_verdict`. |
| `frame` | Webcam, up to 30/sec (every camera frame), base64 JPEG ~480 px wide. `VERIFI_CAMERA=off` disables, `VERIFI_CAMERA=1` picks another camera. The Mac terminal needs camera permission. |

### Scoring (`backend/scoring.py`)

- **Baseline:** the ~60 s calm period at the start. Its mean and std per signal are the reference for everything after.
- **Question response:** for each signal, peak in the ~10 s window after the question minus the mean of the 2 s before, divided by that signal's baseline std. `spike` combines these, weighted toward heart rate.
- **Live `stress`:** smoothed, weighted combination of how far each signal is from baseline, scaled 0–100. Weights favour heart rate (0.3), then blink rate (0.13) and lip compression (0.07); GSR has no weight. Heart rate counts only when above baseline; blink counts either way. **Breathing rate is excluded on purpose** — talking changes it, which would spike stress every answer (breath holds still count in each question's `spike`). Outliers above 1 SD get their weight multiplied by their distance. `stress_alert` (25) corresponds to 2 SD.

### Per-answer verdict (`backend/verdict.py` on top of `backend/confidence.py`)

Every answer is scored on its own the moment its window closes (`due`), and a live estimate streams while it is open. The dashboard shows a **deception likelihood**, never "truth" or "lie": it measures arousal against this person's own calm baseline, not lying itself.

1. **Score.** `backend/confidence.py` compares the answer with the baseline per signal and combines the signal groups (Breathing, Cardiac, Voice, Blink, Lip / hand) into one score `z` with weighted Stouffer. With no change from baseline, `z` behaves like a standard normal.
2. **Likelihood.** `LR = N(z; λ, 1+σ²) / N(z; 0, 1)` and `P = prior·LR / (prior·LR + 1 − prior)`. Below `z = −λ/σ²` the ratio would rise again, so a very calm answer would read as suspicious; `z` is floored there. The 80% range is `P` at `z ± 1.28`.
3. **Bands.** Low < 30% ≤ Unclear < 60% ≤ Elevated < 85% ≤ High.
4. **Quality gates.** Baseline analyzed; the window ≥ 80% covered with no gap over 2 s; at least 2 weighted signal groups usable. If one fails the answer shows **Not enough signal** with the reason. Gates never change a score.
5. **Session.** After every answer, all scored answers' log LRs are added up (one shared state for the session) into the session likelihood.
6. **Why this reading.** Each group's contribution `w·z/√Σw²` adds up exactly to `z` (the waterfall). Pseudo-questions every second across the silent baseline give each group's calm-moment distribution (the histograms).

Constants to tune on real sessions (all in `backend/verdict.py`): `LAMBDA` 2.0, `SIGMA` 1.0, `PRIOR` 0.5, `BANDS` 0.30/0.60/0.85, `MIN_CATEGORIES` 2, `MIN_COVERAGE` 0.8, `MAX_GAP_S` 2, `PSEUDO_STEP_S` 1; `PROGRESS_EVERY_S` 0.3 in `backend/server.py`. Tests: `python -m unittest discover -s backend/tests -t . -v`.

### Rules

- The backend owns all timing and timestamps; the frontend sends controls and displays what it's told.
- Add fields freely; don't rename or remove existing ones without telling everyone.
- Unknown `type` values must be ignored, not crash the frontend.
- If a control can't run (e.g. `question` during baseline), the backend replies to that client only with `{"type": "error", "message": "..."}` — show it.

### Env vars

| Var | Effect |
|---|---|
| `VERIFI_BASELINE_S=10` | Baseline length in seconds (default 60) |
| `VERIFI_PORT=…` | Force a serial port |
| `VERIFI_VOICE=off` / `=<device>` | Disable the mic / pick a device |
| `VERIFI_VOICE_BASELINE_S=0` | Skip the voice baseline |
| `VERIFI_CAMERA=off` / `=1` | Disable video / pick another camera |

---

## Hardware (Arduino UNO)

| Part | Pins |
|---|---|
| Grove GSR | Red → 5V, Black → GND, Yellow → A1, White → not connected |
| BioAmp ECG (heart rate) | VCC → 5V, GND → GND, OUT → A0 |
| BioAmp EMG (breathing) | VCC → 5V, GND → GND, OUT → A2 |
| Question button (interrogator) | D2 → GND (internal pullup, no resistor needed) |

### Serial protocol

115200 baud, one line per sample, 10/sec:

```
STATUS: starting
breath: 0, gsr: 480, heartbeat: 72, raw_ecg: 515, raw_emg: 508
```

| Field | Meaning |
|---|---|
| `breath` | `1` on the line where a breath seems to start, `0` on every other line. An event, not an exact count: the backend calls it `flex` and uses how often it arrives to guide an estimated `breath_rate` |
| `gsr` | Skin conductance raw ADC (0–1023). Read but not used: the dashboard shows a simulated value (see Scoring) |
| `heartbeat` | BPM from the ECG. `0` = no reading yet |
| `raw_ecg`, `raw_emg` | Raw ADC values for debugging; the backend ignores them |

Lines from older sketches ending in `spo2:` and `temp:` still parse; those two values are ignored.

`STATUS:` lines print in the backend terminal and are otherwise ignored. **If you change the line format, update `LINE_RE` in `backend/serial_io.py` in the same commit.**

**Not in the sketch yet:** the interrogator's question button (planned: print `BUTTON` on its own line).

---

## Team

| Name | Area |
|---|---|
| Aaryan | frontend |
| Matthew | frontend, OpenCV |
| Aagrim | OpenCV |
| Cohen | hardware, OpenCV, backend |
| Zhenjie Wang | Parselmouth |
| Namish | backend |

---

## Limitations

- **Physiological responses show stress and arousal, not lying.** An innocent person can spike on an emotional or accusatory question; a calm liar may not spike at all. VeriFi flags reactions for follow-up — never a verdict.
- **Talking affects the signals.** Speaking changes breathing and nudges heart rate, so breathing data during an answer is unreliable. Short answers (yes/no, a few words) give cleaner results.
- **Compare within one session, not across people.** Everything is relative to that person's own baseline.
- Small test set, collected during a weekend hackathon.
- **Prototype only.** Not for real investigations, hiring, or any decision about a person.
