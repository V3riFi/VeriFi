# VeriFi

**natHacks 2026 · Security & Defense track**

VeriFi is a multimodal **suspicion detector** for interrogations. An interrogator asks questions, and can also show objects or crime-scene images, while the suspect answers normally. VeriFi watches the subject's body the whole time and shows a live stress indicator, plus which signals spiked after each question, compared against that person's own calm baseline.

It is not a truth machine. It shows *when* the subject reacted, *which* signals reacted and by how much, so the interrogator makes the call.

### How a session runs

1. **Fit the sensors and start the session.** The subject sits calmly for about 60 seconds while VeriFi records their **baseline**.
2. **Ask a question.** The interrogator clicks **Ask** in the dashboard (or presses the hardware button) at the moment the question starts. Optionally an object or image is shown with it.
3. **The subject answers.** Short answers work best (see Limitations).
4. **VeriFi scores the response window**, about 10 seconds from the question's start, and adds a marker to the timeline showing how big the spike was and which signals drove it.
5. Repeat for each question. At the end, the dashboard shows every question ranked by response.

## Signals

| Signal | Source | What we look at |
|---|---|---|
| Skin conductance | Grove GSR sensor | Sweat response in the seconds after each question |
| Heart rate | Pulse / SpO2 sensor (finger) | Change from baseline |
| Breathing | Flex sensor on a chest belt | Breathing rate, breath holding |
| Face | Webcam + MediaPipe Face Landmarker | Blink rate (Eye Aspect Ratio), hand over face, lip compression |
| Voice (cut for now, maybe Sunday) | Laptop mic + Parselmouth | Pitch and jitter |

## How it works

```
Sensors -> Arduino UNO --USB serial--> Python backend <-- webcam (OpenCV + MediaPipe)
                                            |
                                            +-- per-person baseline + per-question scoring
                                            |
                                        WebSocket
                                            |
                                         Frontend (interrogator dashboard: live charts,
                                                   stress indicator, question timeline)
```

## Tech stack

Everything runs locally on one laptop. No cloud, no database, no Docker.

| Layer | Tech |
|---|---|
| Firmware | Arduino C++ (Arduino IDE) on an Arduino UNO |
| Backend | Python, FastAPI + uvicorn, **one process** |
| Serial | `pyserial`, background thread |
| Vision | `opencv-python` + `mediapipe` (Tasks API), background thread in the backend process |
| Voice (cut for now, maybe Sunday) | `sounddevice` + `praat-parselmouth` |
| Scoring | `numpy` (z-scores against each person's baseline period, no model training) |
| Storage | CSV files in `data/` |
| Frontend | Vite + React, charts with uPlot or Chart.js |
| Backend -> frontend | WebSocket at `ws://localhost:8000/ws`, JSON, about 20 updates/sec |

Two rules:

- **Only the backend opens the webcam and the serial port.** Both can only be used by one program at a time. If the frontend needs video, the backend sends small JPEG frames over the WebSocket.
- **Serial and vision run in the same Python process**, so every signal shares one clock and lines up in time.

## Repo layout

```
firmware/verifi/verifi.ino   Arduino sketch, streams all sensors over serial (hardware team)
backend/server.py            FastAPI app + /ws WebSocket, session + question timing, CSV logging (entry point)
backend/serial_io.py         serial reader thread
backend/scoring.py           baseline, live stress indicator, per-question response scoring
vision/face.py               webcam + MediaPipe face features, imported by the backend (CV group)
vision/face_landmarker.task  model file, downloaded separately (gitignored)
frontend/                    Vite + React dashboard (created with npm create vite, see below)
data/                        recorded sessions (NOT committed, see CONTRIBUTING.md)
requirements.txt             Python packages (hand-written)
```

## Setup

### Python (backend + vision)

```
git clone https://github.com/V3riFi/VeriFi.git
cd VeriFi
git config core.hooksPath .githooks
python -m venv .venv
```

`git config core.hooksPath .githooks` turns on the repo's commit-message hook. Run it once per clone.

Activate it:

- Mac/Linux: `source .venv/bin/activate`
- Windows: `.venv\Scripts\activate`

Then:

```
pip install -r requirements.txt
```

`requirements.txt` is written **by hand** with only the packages we use directly. Don't overwrite it with `pip freeze`: our team mixes Mac and Windows, and a frozen file from one OS can fail to install on the other. If it isn't in the repo yet, create it with:

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

`mediapipe` is pinned because our vision code depends on the 1.x Tasks API. `uvicorn[standard]` (not plain `uvicorn`) is needed for WebSocket support.

### Frontend

Needs **Node.js 20.19+ or 22.12+** (Vite's requirement). Check with `node -v`.

First time only (whoever sets up the frontend):

```
npm create vite@latest frontend -- --template react
```

Everyone else:

```
cd frontend
npm install
npm run dev
```

Opens at http://localhost:5173.

### Run

From the repo root (not from inside `backend/`), with the venv active.

**With the Arduino plugged in** (the port is auto-detected):

```
uvicorn backend.server:app --port 8000
```

The terminal prints `serial: connected to ...` when it finds the board. If it picks the wrong port, set it yourself: `VERIFI_PORT=/dev/cu.usbmodem1101 uvicorn backend.server:app --port 8000`.

**Without hardware** (fake signals, for frontend work):

```
VERIFI_FAKE=1 uvicorn backend.server:app --port 8000
```

Add `VERIFI_BASELINE_S=10` to either command for a 10 s baseline instead of 60 s while testing. On Windows PowerShell, set each variable first (`$env:VERIFI_FAKE=1`), then run the `uvicorn` command.

`http://localhost:8000/` shows whether the backend is on fake or serial data and whether the Arduino is connected.

Then start the frontend in a second terminal.

**Hardware check without the backend:** `python -m backend.serial_io` opens a live plot of the Arduino's sensors. Don't run it while the backend is running.

### MediaPipe note

**Intel Mac?** Run `uname -m`. If it prints `x86_64` on a Mac, `mediapipe==1.2.2` won't install (it only ships Apple Silicon wheels for Mac). The newest version with an Intel Mac wheel is `mediapipe==0.10.21`, which needs Python 3.9-3.12. Install that one by hand instead, and don't change `requirements.txt`.

We use **MediaPipe 1.x**, which does **not** have `mp.solutions` (so `mp.solutions.face_mesh` from older tutorials will fail). Use the Tasks API instead:

```python
import mediapipe as mp

BaseOptions = mp.tasks.BaseOptions
FaceLandmarker = mp.tasks.vision.FaceLandmarker
FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode

options = FaceLandmarkerOptions(
    base_options=BaseOptions(model_asset_path="vision/face_landmarker.task"),
    running_mode=VisionRunningMode.VIDEO,
)
```

The model file `face_landmarker.task` is downloaded separately: see the Models section linked from the [Face Landmarker Python guide](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker/python). Put it in `vision/`.

### Arduino

1. Install the [Arduino IDE](https://www.arduino.cc/en/software).
2. Install the **DFRobot_BloodOxygen_S** library (for the DFRobot MAX30102 heart rate / oxygen module).
3. Open the sketch in `firmware/verifi/sensors/`, select **Arduino Uno** and the right port, upload.
4. **Close the Serial Monitor/Plotter before running the Python backend.** Only one program can use the port at a time.

Serial port names:

- Windows: `COM3`, `COM4`, ... (check Device Manager)
- Mac: `/dev/cu.usbmodem...`
- Linux: `/dev/ttyACM0` (or `/dev/ttyUSB0` on some clone boards)

## WebSocket contract (backend <-> frontend)

`ws://localhost:8000/ws`, one JSON object per message. Every message has a `type`.

**Frontend -> backend** (the interrogator's controls):

```
{"type": "start",    "subject": "subject04"}
{"type": "question", "label": "Q3", "text": "Where were you on Tuesday night?", "stimulus": "knife.jpg"}
{"type": "stop"}
```

- `start`: begins a session. The backend records the baseline first.
- `question`: sent when the interrogator clicks **Ask**. `text` and `stimulus` are optional, and `stimulus` is the object or image shown (or `null`). A hardware button press will start a question too, labelled automatically (not in the sketch yet).
- `stop`: ends the session.

**Backend -> frontend:**

```
{"type": "sample",   "t": 1234.5, "gsr": 512, "hr": 74, "flex": 330, "breath_rate": 14, "temp": 31.5, "spo2": 98, "blink_rate": 18, "stress": 41}
{"type": "baseline", "t": 1180.0, "phase": "start"}
{"type": "baseline", "t": 1240.0, "phase": "done"}
{"type": "question", "t": 1250.0, "label": "Q3", "phase": "start"}
{"type": "response", "t": 1260.0, "label": "Q3", "spike": 2.1, "signals": {"gsr": 2.4, "hr": 1.1, "breath_hold_s": 2.6, "blink": -0.8}}
{"type": "summary",  "questions": [{"label": "Q3", "spike": 2.1}, {"label": "Q1", "spike": 0.4}]}
```

- `sample`: about 10 per second from the Arduino (20 with fake signals). `t` is laptop time in seconds (Unix time). `stress` is the live indicator, 0 to 100 (0 = at baseline). It only appears once the baseline is done.
  - **Any signal can be `null` or missing**, so draw a gap instead of crashing. `hr`, `spo2` and `temp` are `null` for the first seconds until the pulse sensor locks on, `breath_rate` is `null` for the first ~15 s, and `blink_rate` is missing until vision is connected.
  - `hr` from the pulse sensor only updates about every 4 s, so it looks like steps, not a smooth line.
  - `temp` is in °C (read by the pulse sensor chip). `spo2` is blood oxygen in %.
- `baseline`: when baseline recording starts and finishes. The dashboard shows a "stay still, recording baseline" state in between.
- `question`: the backend's timestamped echo of a question starting. The dashboard puts a marker on the timeline here.
- `response`: sent about 10 s after a question starts. `spike` is the overall response in standard deviations above baseline. `signals` breaks it down per signal (in standard deviations, except `breath_hold_s` in seconds).
- `summary`: sent on `stop`. Every question, highest spike first.

**Scoring definitions** (`backend/scoring.py`):

- **Baseline:** the ~60 s calm period at the start. Its mean and standard deviation per signal are the reference for everything after.
- **Question response:** for each signal, the peak in the ~10 s window after the question starts minus the mean of the 2 s before it, divided by that signal's baseline standard deviation. `spike` combines these, weighted toward GSR.
- **Live `stress`:** a smoothed combination of how far GSR and heart rate currently are above baseline, scaled to 0 to 100.

**Rules:**

- The backend owns all timing and timestamps. The frontend sends controls and displays what it's told.
- Add fields freely. Don't rename or remove existing ones without telling everyone.
- Unknown `type` values should be ignored, not crash the frontend.

**Errors:** if a control message can't be run (for example `question` while the baseline is still recording), the backend replies to that client only with `{"type": "error", "message": "..."}`. Show it to the interrogator.

**What the backend sends right now:** the full session flow above, on real Arduino data (GSR, heart rate, breathing, temperature, oxygen) or fake signals (`VERIFI_FAKE=1`, see Run). Fake signals react to about half the questions, so spikes show up on the dashboard. Every session is saved to `data/<subject>_<date-time>.csv`. Not connected yet: vision (`blink_rate`) and the hardware question button.

## Wiring (Arduino UNO)

| Part | Arduino pin |
|---|---|
| Grove GSR | Red -> 5V, Black -> GND, Yellow -> A0, White -> not connected |
| Flex sensor (breathing belt) | 5V -> flex -> **A1** -> resistor -> GND (resistor about equal to the flex sensor's flat resistance) |
| BioAMP EXG Pill (optional) | VCC -> 5V, GND -> GND, OUT -> A2 |
| Pulse / SpO2 (DFRobot MAX30102, I2C address 0x57) | SDA -> A4, SCL -> A5, plus power and GND (check the board's voltage marking) |
| Question button (interrogator) | D2 and GND (internal pullup, no resistor needed) |

## Serial protocol

115200 baud, one line per sample, 10 per second. This is what the current sketch sends (parsed by `backend/serial_io.py`):

```
STATUS: starting
STATUS: MAX30102 ready
breath: 512, gsr: 480, heartbeat: 72, Blood Pressure: 98, temp: 31.5
```

| Field | Meaning |
|---|---|
| `breath` | Flex sensor on the chest belt, raw ADC (0-1023). The backend calls it `flex` and works out `breath_rate` from it |
| `gsr` | Skin conductance, raw ADC (0-1023) |
| `heartbeat` | BPM from the pulse module, refreshed every 4 s and averaged on the board. `0` = no reading yet |
| `Blood Pressure` | Actually **blood oxygen (SpO2 %)**, mislabelled in the sketch. The backend also accepts `spo2:` here |
| `temp` | °C from the pulse module chip |

`STATUS:` lines are printed in the backend terminal and otherwise ignored. If you change the line format, update `LINE_RE` in `backend/serial_io.py` in the same commit.

**Not in the sketch yet:** the interrogator's question button (planned: print `BUTTON` on its own line when pressed).

## Team

| Name | Area |
|---|---|
| Aaryan | Software: frontend, backend |
| Matthew | Software: backend, frontend |
| Aagrim | Computer vision |
| Cohen | Computer vision |
| _____ | Hardware |
| _____ | Hardware |

## Limitations

- Physiological responses show **stress and arousal, not lying**. An innocent person can spike on an emotional or accusatory question, and a calm liar may not spike at all. VeriFi flags reactions for the interrogator to follow up on, never a verdict.
- **Talking affects the signals.** Speaking changes breathing and nudges heart rate, so breathing data during an answer is unreliable. Short answers (yes/no or a few words) give cleaner results, and GSR, which lags a few seconds anyway, is the most trustworthy signal.
- **Compare questions within one session, not across people.** Everything is relative to that person's own baseline.
- Small number of test subjects, collected during a weekend hackathon.
- Prototype only. Not for real investigations, hiring, or any decision about a person.