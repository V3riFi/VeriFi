# Connecting the sensors (for the hardware / backend team)

The interview software is finished and tested on its side. To connect, it needs **one line of text per sensor sample**. The sample can arrive over USB serial or over UDP. Everything else (timestamps, file, scoring) is handled.

## 1. What the Arduino should print

One line per sample, **at least 20 lines per second** (skin conductance needs ≥ 10 Hz). Default baud rate is **115200**.

Any of these formats works:

```
gsr:512 hr:74 resp:301          <- recommended: named values, column order can't get mixed up
512,74,301                      <- plain numbers in the order gsr,hr,resp (or set --sensor-columns)
{"gsr": 512, "hr": 74}          <- JSON
```

- **Accepted names:**
  - `gsr` / `eda` / `scl` → skin conductance
  - `hr` / `bpm` → heart rate in beats per minute
  - `resp` / `breath` / `belt` → breathing
  - `ppg` / `pulse` / `ir` → raw pulse waveform. Heart rate is computed from it when no `hr` is sent.
- **Optional:** any sensor may be missing; scoring skips it.
- **Ignored:**
  - debug prints and header lines (they're counted as "not understood")
  - a device time column (`t:` / `ms:`)
- **Heart rate 0** means "no finger" and is treated as a gap, not as 0 bpm.

A minimal sketch:

```cpp
void setup() { Serial.begin(115200); }
void loop() {
  Serial.print("gsr:");    Serial.print(analogRead(A0));
  Serial.print(" ppg:");   Serial.print(analogRead(A1));   // or " hr:" + your bpm value
  Serial.print(" resp:");  Serial.println(analogRead(A2));
  delay(20);                                               // ~50 lines per second
}
```

## 2. Three ways to connect (pick one)

| | Setup | Run with |
|---|---|---|
| **A. USB straight into the interview laptop** (simplest) | Arduino on the laptop running the interview. Close the Arduino IDE Serial Monitor (it locks the port). | `--sensor COM5` (`COM5@9600` for another baud rate) |
| **B. Backend forwards samples over UDP** | The backend reads the Arduino and sends each line (or JSON) as a UDP packet to the interview laptop, port 5008. | `--sensor udp:0.0.0.0:5008` |
| **C. Backend writes its own log** | CSV `t,gsr,hr,resp` where **`t = time.time()` on the interview laptop** when the sample arrived. | `--physio path\to\log.csv` (or afterwards: `.venv\Scripts\python.exe examples\fuse_identity.py <session>.trials.csv --physio log.csv`) |

**Clock rule (C only):** the samples and the item onsets must use the same clock. A and B timestamp on arrival, so they always do. For C, if the backend runs on another computer, its clock has to be synced with the interview laptop's. Even 1 s of offset smears the 1–5 s response window.

## 3. Before every session: `run_sensor_check.bat`

It takes about 45 s. It lists the serial ports, records, and reports:
- lines parsed vs. not understood, with examples
- rate per sensor
- flat or stuck signals

At 20 s it asks for a deep breath, to find **which way your GSR board goes**:
- conductance rises with sweating: the default
- resistance falls: add `--gsr-inverted` to every run

It finishes with **READY** or a list of what to fix.

## 4. Running the interview with sensors

```
run_identity.bat --sensor COM5                                identity test with the sensor round (~18 min)
.venv\Scripts\python.exe examples\make_case.py p01            mock-crime case + sealed envelope for the subject
run_verifi.bat --case data\cases\p01.json --sensor COM5       full session (~25-30 min, --quick ~15)
```

- **Electrodes / pulse sensor:** put them on the **left** hand.
- **Sensor rounds:** the subject presses I (NO) with the right hand and keeps the left hand still on the table.
- **Keyboard rounds:** use both hands. Those rounds are scored on reaction time only, not on the sensors.
- **Status line:** the window's status line shows `sensors NN Hz` live. It shows `NO DATA` if the stream stops; the cable can be re-plugged and it reconnects.
- **Simulated sensors:** `--sensor sim` tests the software without hardware. That data is marked and **never** counted in a result.

## 5. Live feed for the backend / frontend (optional) 

With `--udp HOST:PORT`, every sensor-round word is sent the moment it's drawn:

```json
{"cmd": "item_onset", "item": "TRUE", "timestamp": 1794076154.53, "block": "case_slow",
 "series": 3, "group": 1, "kind": "test", "duration": 5.0}
```

- `item` is a label (`TRUE`, `F1`–`F4`, `BUFFER`), never the word shown.
- `timestamp` is `time.time()` on the interview laptop.
- `block` is one of:
  - `identity_slow`: own details, known answers
  - `case_slow`: the test

Vision's own live messages are described in [VISION.md](VISION.md).

## 6. What a session saves

`data\sessions\<case>\session.*`. Every file holds labels and numbers only, never names or places.

| File | Contents |
|---|---|
| `.trials.csv` | keyboard trials: label, onset, reaction time, key, correct/wrong/slow |
| `.slow.csv` | sensor-round words: label, onset, key, key-press time |
| `.physio.csv` | `t,gsr,hr,resp,ppg[,sim]` as received |
| `.frames.csv` | camera, one row per frame (eyes, head pose, gaze, mouth, hand-on-face) |
| `.vision.csv` | face cues per sensor-round word (exploratory) |
| `.results.json` | every channel, every variant, the verdict, the case condition |

To re-score after a fix or with a later sensor log: `.venv\Scripts\python.exe examples\fuse_identity.py data\sessions\p01\session.trials.csv`.

To compare across people: `.venv\Scripts\python.exe examples\evaluate.py`.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `could not open port` / `Access is denied` | Close the Arduino Serial Monitor or any other program using the port; re-plug. |
| Lines arrive but "not understood" | Wrong column count, or text mixed into the numbers. Use the `gsr:… hr:…` format. |
| Rate far below what the sketch sends | `delay()` too long, or the baud rate doesn't match (`COM5@9600`). |
| GSR flat | Electrode off or dry, or the input is saturated (raw value pinned at 0 or 1023). |
| Window keys do nothing | Click the window once (Windows sometimes leaves focus on the console). |
