# VeriFi

It is a Concealed Information Test (CIT) that combines three channels:

| Channel | What it measures | Role |
|---|---|---|
| **Reaction time** (keyboard) | Saying NO to something you recognize takes a few tens of milliseconds longer | the core signal |
| **Physiology** (Arduino: skin conductance, heart rate, breathing) | the orienting response to a recognized detail | second, independent signal |
| **Camera** (MediaPipe face mesh) | eyes closed, looking away, moving, talking, hand on face | throws out unusable trials; face cues count only if they prove themselves for that person |

Every verdict is personal. A calibration round on the person's own details, where the answers are known, learns which channels respond for them. Every result comes with a p-value, and the only claim is made at p < .05.

natHacks 2026, Security & Defense track.

## Setup (Windows)

You need **Python 3.14** on PATH and a webcam. Everything here lives in the `openCV/` folder of the team repo and has its own environment (`openCV/.venv`), separate from the backend's. Double-click any `run_*.bat` in this folder. The first one sets everything up (about 2 min, needs internet); after that it starts in seconds, offline. Commands below are run from inside `openCV/`.

These tests open the webcam and the Arduino port themselves, so **close the backend first** (only one program can hold the camera or a serial port at a time).

## Running a test

### Identity test: about 10 min, keyboard only

```bash
run_identity.bat
```
```bash
run_identity.bat --control
```

1. The console asks for 4 details: first name, last name, hometown, mother's first name. It picks believable alternatives for each one: same name style and gender, towns of similar size in the same province or state. It may ask one or two questions about this. Your details appear only on screen and are **never written to a file**.
2. A window opens. Words flash one at a time. Press **E = YES** for the 2 words you're told to remember and for FAMILIAR / MINE / RECOGNIZED. Press **I = NO** for everything else, *including your own details*. Be fast: there are 0.8 s per word.
3. The report shows each detail, the camera check, and the verdict: **RECOGNIZES**, **INCONCLUSIVE**, **NO EVIDENCE**, or INVALID / INCOMPLETE when the task wasn't followed or was stopped early.

`--control` runs the same test on a random identity the person never had. It should come out NO EVIDENCE. A test is only convincing if it fires on the real details *and* stays quiet on the control.

| Option | Effect |
|---|---|
| `--quick` | 10 rounds instead of 14 (about 7 min). Fine for practice and collecting data; use the full length for demos |
| `--sensor COM5` | adds the sensor round first (+8 min) and records the Arduino. Also `udp:0.0.0.0:5008` (backend forwards samples) or `sim` (software test, never scored) |
| `--no-camera` | keyboard and sensors only |

### Full session: about 25–30 min, every channel, mock crime

```bash
.venv\Scripts\python.exe examples\make_case.py p01
```
```bash
run_verifi.bat --case data\cases\p01.json --sensor COM5
```

`make_case.py` writes a case (stolen item, code word, hiding place) and a sealed envelope, `data\cases\p01_ENVELOPE.txt`. The envelope says GUILTY (memorize the details) or INNOCENT, chosen at random. Give it to the subject unread, so the operator stays blind.

The session runs in this order:
1. sensor round on the subject's own details (calibration)
2. sensor round on the case
3. keyboard round on the case
4. keyboard round on their own details

Then it prints the per-person profile, every channel, **one pre-registered verdict**, and only then the condition from the envelope. `--quick` takes about 15 min.

**Pitch mode: calibrate earlier, test live (about 9 min on stage).** Run the identity test with sensors on the subject before the pitch, then point the session at that run:

```bash
run_identity.bat --sensor COM5
```
```bash
run_verifi.bat --case data\cases\p01.json --sensor COM5 --quick --calibration data\identity_7
```

Steps 1 and 4 are skipped. The per-person profile is learned from the earlier run, using its own sensor log and camera, and fixed before the case is scored. It must be the **same person, with the same sensors on the same spots, on the same day**. A `--control` run or one stopped early is refused. A run without `--sensor` is accepted, but then there is no profile and default weights are used.

### Before every session with hardware: about 45 s

```bash
run_sensor_check.bat COM5
```

It checks that samples arrive fast enough, that no signal is flat, and which way the GSR board reads. It finishes with **READY** or a list of what to fix. Wiring, line formats and the UDP option are in **[HARDWARE.md](HARDWARE.md)**.

## Results

Every run saves itself to `data\`: `<name>.trials.csv`, `.slow.csv`, `.physio.csv`, `.frames.csv` and `.results.json`. These hold labels and numbers only. You don't need to copy anything from the console.

```bash
.venv\Scripts\python.exe examples\evaluate.py
```
Lists every run, then the detection rate, false alarms and AUC per variant. Runs stopped early or INVALID are listed but not counted.

```bash
.venv\Scripts\python.exe examples\fuse_identity.py data\identity.trials.csv
```
Prints any run's report again with the current code, for example after a sensor log arrives (`--physio log.csv`).

**So far (identity test, real details):** 3 of 4 complete runs detected, at p = .001, < .001 and < .001. One of those three used alternatives that didn't match the person's background, a problem since fixed, so it is weaker evidence. One run was not detected (p = .36). The one control run was not flagged. This is a pilot, not an accuracy figure. Claiming a detection rate needs at least 10 people, each run with real details and with `--control`. Details are in [METHOD.md](METHOD.md#8-results-so-far).

## How it works, briefly

- **Test design:** 4 details, each shown with 4 believable alternatives. The keyboard round runs in 2 parts of 2 details, with 14 presentations of every word, in random order with filler words. Each presentation round is scored on its own (within-round z-scores of log reaction time), then a permutation test gives one p-value for the true details.
- **Combining channels:** the sensor round and the keyboard round are independent, so their p-values combine with Stouffer's method, weighted by the person's calibration.
- **Camera:** it only throws out unusable trials, and it can never create a detection. Face cues get a weight only if they separate the person's own known details.

The full method, the design choices and why the face-only approach was dropped are in **[METHOD.md](METHOD.md)**.

## Docs

| File | For |
|---|---|
| [METHOD.md](METHOD.md) | the science: test design, scoring, fusion, validation plan, results, limits |
| [HARDWARE.md](HARDWARE.md) | the hardware / backend team: Arduino output format, connecting over USB or UDP, the sensor check |
| [VISION.md](VISION.md) | the camera pipeline and the live vision stream for the backend / frontend (`run.bat`) |

## Repository

```
run_identity.bat  run_verifi.bat  run_sensor_check.bat   the three things you run
run.bat                   live vision stream + HUD (for the backend / frontend)
run_rtcit.bat             card version: guess which playing card someone picked (quick demo)
run_blink_check.bat       60 s camera / blink check
run_tests.bat             offline tests (no camera or hardware needed)
setup.bat                 called by every run_*.bat
interview/                test design and scoring: identity, regions (alternatives), rtcit, fusion,
                          profile, analysis (whole session), verdict, case (mock crime), scoring
sensors/                  Arduino / UDP input, recorded with the interview laptop's clock
vision/                   MediaPipe face pipeline, camera quality checks (offline.py), live stream
examples/                 the programs the .bat files run, plus make_case, evaluate, fuse_identity
tests/                    offline tests
```

## Privacy and limits

- **Privacy:** personal details are typed by the operator, shown on screen and kept in memory only. Every file holds labels (`TRUE`, `F1`–`F4`, `YES`), never the words. `data/` is excluded from git.
- **What a reaction means:** recognition, not lying. A familiar alternative, such as a friend's name or a town you once visited, can trigger one too. The console asks about personally meaningful alternatives before the test, so they can be replaced.
- **Countermeasures:** a person can deliberately slow down on chosen words to fool the reaction-time channel. The physiology channel and the camera's countermeasure check make that harder, but not impossible.
- **Not for real use:** this is a weekend prototype tested on a handful of people. It is not for real investigations, hiring, or any decision about a person.
