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

Then two terminals, both from the repo root with the venv active. This needs to be run every startup:

```bash
# terminal 1 — backend (auto-detects the Arduino)
uvicorn backend.server:app --port 8000

# terminal 2 — frontend
cd frontend && npm install && npm run dev     # http://localhost:5173
```

`http://localhost:8000/` shows whether the Arduino is connected. Add `VERIFI_BASELINE_S=10` for a 10 s baseline instead of 60 s while testing. Full details in [Setup](#setup).

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
| Skin conductance | Grove GSR | Sweat response in the seconds after each question |
| Heart rate | Pulse / SpO2 (finger) | Change from baseline |
| Breathing | Flex sensor on chest belt | Rate, breath holding |
| Face | Webcam + MediaPipe Face Landmarker (`openCV/vision`) | Blink rate (Eye Aspect Ratio), hand over face, lip compression. Blinks ignored if head turned >35° or tilted >25° — keep camera at eye level |
| Voice | Laptop mic + Parselmouth (`voice/live.py`) | Live: pitch, HNR (voice clarity), jitter, shimmer, loudness, spectral slope and vocal tension. Per answer: response latency, length, speech rate, pauses, pitch, intonation (pitch SD), HNR, tension. Shown in the dashboard's Voice dropdown, not part of stress. Speech errors, repetitions and stuttering need a transcript, which VeriFi doesn't make |

---

## Architecture

```text
   ┌─────────────────────┐  ┌──────────┐  ┌──────────┐
   │ GSR · Pulse · SpO2  │  │ Webcam   │  │ Mic      │
   │ Flex · Button       │  │          │  │          │
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
                              response        ~10 s after a question
                              summary         on stop, ranked
                              status          ~1/s, per-component health
                              frame           webcam JPEG, ~10/s
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
{"type": "sample",   "t": 1234.5, "gsr": 512, "hr": 74, "flex": 330, "breath_rate": 14, "temp": 31.5, "spo2": 98, "blink_rate": 18, "pitch": 182.4, "jitter": 1.1, "shimmer": 6.3, "voice_db": 61.0, "hnr": 14.2, "spectral_slope": -18.5, "vocal_tension": 0.6, "stress": 41}
{"type": "baseline", "t": 1180.0, "phase": "start", "duration": 60}
{"type": "baseline", "t": 1240.0, "phase": "done", "mean": {"gsr": 451.2, "hr": 72.4}, "std": {"gsr": 3.1, "hr": 1.8}, "alert_sd": 2, "stress_alert": 25}
{"type": "voice_baseline", "t": 1240.0, "phase": "start", "duration": 25, "sentences": ["I am sitting in a chair right now.", "..."]}
{"type": "voice_baseline", "t": 1265.0, "phase": "done", "voiced_samples": 140, "mean": {"gsr": 451.2, "pitch": 182.0}, "std": {"gsr": 3.1, "pitch": 9.0}, "alert_sd": 2, "stress_alert": 25, "reading": {"duration_s": 22.1, "speech_rate": 3.1, "pauses": 4, "pitch": 181.0, "intonation": 24.0, "hnr": 15.2}}
{"type": "question", "t": 1250.0, "label": "Q3", "phase": "start"}
{"type": "question", "t": 1252.4, "label": "Q3", "phase": "end"}
{"type": "response", "t": 1260.0, "label": "Q3", "spike": 2.1, "signals": {"gsr": 2.4, "hr": 1.1, "breath_hold_s": 2.6, "blink": -0.8}, "voice": {"latency_s": 1.4, "latency_marked": true, "duration_s": 2.2, "speech_rate": 3.6, "pauses": 1, "pause_s": 0.5, "pitch": 190.2, "intonation": 18.0, "hnr": 13.1, "tension": 0.9, "cut": false}}
{"type": "summary",  "questions": [{"label": "Q3", "spike": 2.1, "latency_s": 1.4}, {"label": "Q1", "spike": 0.4, "latency_s": 0.8}]}
{"type": "status",   "components": {"arduino": "ok", "gsr": "ok", "pulse": "missing", "breathing": "ok", "camera": "ok", "face": "ok", "voice": "off"}, "session": null}
{"type": "frame",    "t": 1250.1, "jpeg": "<base64 JPEG>"}
```

| Type | Notes |
|---|---|
| `sample` | ~10/sec from Arduino. `t` = laptop Unix time (s). `stress` is 0–100 and appears only after baseline. **Any field may be `null` or missing** — draw a gap, don't crash. `hr`/`spo2`/`temp` are null until the pulse sensor locks (~4 s); `breath_rate` until ~15 s; `blink_rate` for the first 10 s of face tracking and whenever no face is visible. `hand_on_face` is boolean. Voice fields (`pitch`, `jitter`, `shimmer`, `hnr`, `spectral_slope`, `vocal_tension`) are null when nobody talks; `voice_db` (loudness) always has a value; all are missing if the mic is off. `hnr` and `spectral_slope` are in dB and can be negative. `vocal_tension` is SDs above the voice baseline (average of loudness, jitter, shimmer and a steeper spectral slope), after the voice baseline only. `hr` updates in ~4 s steps. `temp` in °C, `spo2` in %. |
| `baseline` | `start` carries the baseline length; `done` carries each signal's `mean`/`std`, plus `alert_sd` and `stress_alert` (the dashboard uses these to draw baseline bands and turn lines red outside them). |
| `voice_baseline` | Subject reads `sentences` aloud for `duration` s (`VERIFI_VOICE_BASELINE_S`, default 25; `0` skips). `done` carries the baseline again, now with the voice fields (from moments the subject is speaking), plus `reading`: the reading's per-answer features, to compare answers with (`null` if it couldn't be measured). Questions are refused until it's done. Skipped entirely if the mic is broken; if it hears too little speech, an `error` follows and voice has no normal range. The silent baseline also measures room loudness — afterward `pitch`/`jitter`/`shimmer` only count when the voice is ≥6 dB above the room. |
| `question` | Timestamped echo: `phase` `start` (the dashboard places a marker) or `end` (Done asking). |
| `response` | Sent ~10 s after a question starts, or 10 s after Done asking if that's later. `voice` holds the answer's voice features (`null` without a mic): `latency_s` (Done asking, or the estimated end of the question when `latency_marked` is false, to the first word), `duration_s` (first to last word), `speech_rate` (syllables/s), `pauses` and `pause_s` (silences of 0.3 s or more), `pitch` (Hz), `intonation` (pitch SD, Hz), `hnr` (dB), `tension` (SD), `cut` (still talking when the window closed). Fields are `null` when no answer was heard. `spike` = overall response in SD above baseline; `signals` breaks it down per signal (SD, except `breath_hold_s` in seconds). |
| `summary` | Sent on `stop`. Every question, highest spike first, with its `latency_s`. |
| `status` | ~1/sec and on connect. Each component is `ok`, `missing`, or `off`. `session` describes the running session (or `null`) so a reloaded dashboard can reattach. |
| `frame` | Webcam, ~10/sec, base64 JPEG ~480 px wide. `VERIFI_CAMERA=off` disables, `VERIFI_CAMERA=1` picks another camera. The Mac terminal needs camera permission. |

### Scoring (`backend/scoring.py`)

- **Baseline:** the ~60 s calm period at the start. Its mean and std per signal are the reference for everything after.
- **Question response:** for each signal, peak in the ~10 s window after the question minus the mean of the 2 s before, divided by that signal's baseline std. `spike` combines these, weighted toward GSR.
- **Live `stress`:** smoothed, weighted combination of how far each signal is from baseline, scaled 0–100. Weights favour GSR (0.5) and heart rate (0.3), then blink rate and lip compression. GSR and heart rate count only when above baseline; blink counts either way. **Breathing rate is excluded on purpose** — talking changes it, which would spike stress every answer (breath holds still count in each question's `spike`). Outliers above 1 SD get their weight multiplied by their distance. `stress_alert` (25) corresponds to 2 SD.

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
| Grove GSR | Red → 5V, Black → GND, Yellow → A0, White → not connected |
| Flex sensor (breathing belt) | 5V → flex → **A1** → resistor → GND (resistor ≈ flex's flat resistance) |
| BioAMP EXG Pill (optional) | VCC → 5V, GND → GND, OUT → A2 |
| Pulse / SpO2 (DFRobot MAX30102, I²C 0x57) | SDA → A4, SCL → A5, plus power and GND |
| Question button (interrogator) | D2 → GND (internal pullup, no resistor needed) |

### Serial protocol

115200 baud, one line per sample, 10/sec:

```
STATUS: starting
STATUS: MAX30102 ready
breath: 512, gsr: 480, heartbeat: 72, spo2: 98, temp: 31.5
```

| Field | Meaning |
|---|---|
| `breath` | Flex sensor raw ADC (0–1023). The backend calls it `flex` and derives `breath_rate` |
| `gsr` | Skin conductance raw ADC (0–1023) |
| `heartbeat` | BPM from the pulse module, recalculated ~every 4 s (board polls 1/sec, averages last 2). `0` = no reading yet |
| `spo2` | Blood oxygen (%). Older sketches label it `Blood Pressure:` — still accepted |
| `temp` | °C from the pulse module chip |

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
- **Talking affects the signals.** Speaking changes breathing and nudges heart rate, so breathing data during an answer is unreliable. Short answers (yes/no, a few words) give cleaner results; GSR, which lags a few seconds anyway, is the most trustworthy signal.
- **Compare within one session, not across people.** Everything is relative to that person's own baseline.
- Small test set, collected during a weekend hackathon.
- **Prototype only.** Not for real investigations, hiring, or any decision about a person.
