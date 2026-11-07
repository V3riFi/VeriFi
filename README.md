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

## Planned repo layout

> The repo currently has empty placeholder files in the root. This is the layout we're moving to.

```
firmware/verifi/verifi.ino   Arduino sketch, streams all sensors over serial
backend/                     serial reader, buffers, CSV logging, scoring, WebSocket server
vision/                      webcam + MediaPipe face features
frontend/                    dashboard UI
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