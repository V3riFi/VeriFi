# VeriFi

**natHacks 2026 · Security & Defense track**

VeriFi is a multimodal recognition detector. It runs a **Concealed Information Test (CIT)**: the subject is shown several items (for example 5 cards) and answers "no" to every one. VeriFi measures the body's response to each item and flags the one that stands out against that person's own baseline.

It is not a truth machine. It shows *which* signals reacted and by how much, so a human makes the call.

## Signals

| Signal | Source | What we look at |
|---|---|---|
| Skin conductance | Grove GSR sensor | Sweat response in the seconds after each item |
| Heart rate | Pulse / SpO2 sensor (finger) | Change from baseline |
| Breathing | Flex sensor on a chest belt | Breathing rate, breath holding |
| Face | Webcam + MediaPipe Face Landmarker | Blink rate (Eye Aspect Ratio), hand over face, lip compression |
| Voice (stretch goal) | Laptop mic + Parselmouth | Pitch and jitter |

## How it works

```
Sensors -> Arduino UNO --USB serial--> Python backend <-- webcam (OpenCV + MediaPipe)
                                            |
                                            +-- per-person baseline + per-item scoring
                                            |
                                        WebSocket
                                            |
                                         Frontend (live charts, CIT screen, results)
```

## Tech stack

Everything runs locally on one laptop. No cloud, no database, no Docker.

| Layer | Tech |
|---|---|
| Firmware | Arduino C++ (Arduino IDE) on an Arduino UNO |
| Backend | Python, FastAPI + uvicorn, **one process** |
| Serial | `pyserial`, background thread |
| Vision | `opencv-python` + `mediapipe` (Tasks API), background thread in the backend process |
| Voice (stretch goal) | `sounddevice` + `praat-parselmouth` |
| Scoring | `numpy` (z-scores against each person's baseline, no model training) |
| Storage | CSV files in `data/` |
| Frontend | Vite + React, charts with uPlot or Chart.js |
| Backend -> frontend | WebSocket at `ws://localhost:8000/ws`, JSON, about 20 updates/sec |

Two rules:

- **Only the backend opens the webcam and the serial port.** Both can only be used by one program at a time. If the frontend needs video, the backend sends small JPEG frames over the WebSocket.
- **Serial and vision run in the same Python process**, so every signal shares one clock and lines up in time.

## Planned repo layout

> The repo currently has empty placeholder files in the root. This is the layout we're moving to.

```
firmware/verifi/verifi.ino   Arduino sketch, streams all sensors over serial
backend/                     serial reader, buffers, CSV logging, scoring, WebSocket server
vision/                      webcam + MediaPipe face features (imported by the backend, not run on its own)
frontend/                    Vite + React dashboard
data/                        recorded sessions (NOT committed, see CONTRIBUTING.md)
requirements.txt             pinned Python packages
```

## Setup

### Python (backend + vision)

```
git clone https://github.com/V3riFi/VeriFi.git
cd VeriFi
python -m venv .venv
```

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

### Run (once the backend exists)

From the repo root, with the venv active:

```
uvicorn backend.server:app --port 8000
```

Then start the frontend in a second terminal. `backend/server.py` with a FastAPI `app` is the planned entry point; update this line if the file ends up named differently.

### MediaPipe note

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
2. Library Manager: install **SparkFun MAX3010x Pulse and Proximity Sensor Library** (only needed if the pulse sensor is a MAX30102/MAX30105, check the chip).
3. Open `firmware/verifi/verifi.ino`, select **Arduino Uno** and the right port, upload.
4. **Close the Serial Monitor/Plotter before running the Python backend.** Only one program can use the port at a time.

Serial port names:

- Windows: `COM3`, `COM4`, ... (check Device Manager)
- Mac: `/dev/cu.usbmodem...`
- Linux: `/dev/ttyACM0` (or `/dev/ttyUSB0` on some clone boards)

## Wiring (Arduino UNO)

| Part | Arduino pin |
|---|---|
| Grove GSR | Red -> 5V, Black -> GND, Yellow -> A0, White -> not connected |
| Flex sensor (breathing belt) | 5V -> flex -> **A1** -> resistor -> GND (resistor about equal to the flex sensor's flat resistance) |
| BioAMP EXG Pill (optional) | VCC -> 5V, GND -> GND, OUT -> A2 |
| Pulse / SpO2 (MAX3010x) | SDA -> A4, SCL -> A5, plus power and GND (check the board's voltage marking) |
| Answer button | D2 and GND (internal pullup, no resistor needed) |

## Serial protocol

115200 baud, one message per line.

| Direction | Message | Meaning |
|---|---|---|
| Board -> laptop | `EVT:HELLO` | Board booted |
| Board -> laptop | `EVT:DATA:<ms>,<gsr>,<flex>,<exg>,<ir>,<red>` | One sample of every sensor (~50 Hz). `ir`/`red` are `-1` if the pulse sensor isn't found |
| Board -> laptop | `EVT:BUTTON` | Button pressed |

## Team

| Name | Area |
|---|---|
| Aaryan | Frontend |
| Matthew | Frontend / backend |
| Aagrim | Computer vision |
| Cohen | Computer vision |
| _____ | Hardware |
| _____ | Hardware |

## Limitations

- Physiological responses show **arousal and recognition, not lying**. Stress, surprise or a personal connection to an item can all cause a spike.
- Small number of test subjects, collected during a weekend hackathon.
- Prototype only. Not for real investigations, hiring, or any decision about a person.