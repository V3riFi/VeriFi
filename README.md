# VeriFi

<p align="center"><img src="docs/banner.webp" alt="VeriFi logo: a figure drawn in ASCII characters inside a wide eye shape" width="720"></p>

**natHacks 2026 · Security & Defense track**

VeriFi is a multimodal **stress-reaction detector for interviews and interrogations**. It records how a person's body behaves while they sit calmly, then shows, question by question, when their heart rate, breathing, face and voice move away from that calm baseline.

It doesn't claim to detect lies. It flags the questions where someone reacted, shows which signals moved and by how much, and leaves the follow-up to the interviewer.

---

## The problem

Interviewers judge stress by watching the person in front of them. That is subjective, they miss small changes while they're thinking about the next question, and nothing is recorded to check afterwards. A classic polygraph does measure the body, but it needs a trained examiner and special equipment, and its single number is hard to question.

VeriFi uses low-cost sensors, a webcam and a laptop mic. It compares each person only with their own baseline and shows how it reached every reading.

## What it does

1. **Calm baseline (~60 s).** The subject sits still and silent. VeriFi learns the normal range of every signal *for this person*.
2. **Voice baseline (~25 s).** The subject reads five neutral sentences aloud, so their normal pitch and voice quality are known too.
3. **Questions.** The interviewer clicks **Ask**, can show an object or image, and clicks **Done asking** when the question ends. Response latency is timed from that moment.
4. **Per-answer reading.** About 10 s later, each answer gets a **reaction likelihood** with an 80% range (Low / Unclear / Elevated / High) and a "Why this reading" breakdown: how much each signal group contributed, the raw traces around the question, and how this answer compares with calm moments from the baseline.
5. **Session summary.** When the session stops, every question is ranked by reaction strength, along with an overall session likelihood.

A live **combined stress** indicator and the webcam feed run on the dashboard the whole time.

## Signals

| Signal | Hardware | What VeriFi measures |
|---|---|---|
| Heart rate | BioAmp ECG → Arduino UNO | Rise above baseline. Beats are detected on the Arduino with a band-pass filter and an adaptive threshold |
| Breathing | BioAmp EMG on the chest → Arduino | Breath events, an estimated breathing rate and **breath holds** (a gap between breaths that runs past normal) |
| Face | Webcam + MediaPipe Face Landmarker | Blink rate, lip compression, hand over face |
| Voice | Laptop mic + Praat (Parselmouth) | Pitch, jitter, shimmer, voice clarity (HNR), loudness; per answer: response latency, speech rate, pauses, intonation |
| Skin conductance | Grove GSR | Shown, but **simulated** and not scored. Our sensor read too steadily to use (see [Limitations](#limitations)) |

## How the scoring works

No machine-learning training and no population averages: every number is relative to the person's own baseline.

- **Per signal:** for each answer, VeriFi takes the peak in the ~10 s window after the question, subtracts the 2 s before it, and divides by the spread of that signal during the calm baseline (a z-score).
- **Combining:** signals are grouped (Cardiac, Breathing, Voice, Blink, Lip / hand) and merged into one score with weighted Stouffer's method. With no real change, that score behaves like a standard normal.
- **Likelihood:** a likelihood ratio between "no reaction" and "reaction" models turns the score into a reaction likelihood with an 80% interval. Very calm answers are capped so they can't flip back to suspicious.
- **Checking itself:** pseudo-questions taken every second during the calm baseline show what an ordinary moment scores for this person. Each answer is shown against that distribution.
- **Quality gates:** if sensors dropped out, the window has gaps, or fewer than two signal groups are usable, the answer reads **Not enough signal** with the reason, instead of a guess.
- **Session:** the evidence from all answers is added up into one running session likelihood.

The live combined stress indicator weights heart rate most (46%), then breath holds (23%), blink rate (20%) and lip compression (11%). Breathing *rate* is left out on purpose because talking changes it.

## Architecture

```text
   ┌─────────────────────┐  ┌──────────┐  ┌──────────┐
   │ ECG · EMG · GSR     │  │ Webcam   │  │ Mic      │
   └──────────┬──────────┘  └────┬─────┘  └────┬─────┘
   ┌──────────▼──────────┐       │             │
   │ Arduino UNO (C++)   │       │             │
   │ beat + breath detect│       │             │
   └──────────┬──────────┘       │             │
              ├──────────────────┴─────────────┘
   ┌──────────▼──────────────────────────────────────────────┐
   │  PYTHON BACKEND (FastAPI): one process, one clock       │
   │  serial reader · MediaPipe face · Parselmouth voice     │
   │  baseline · per-answer scoring · verdict · CSV + WAV    │
   └─────────────────────────────┬───────────────────────────┘
                                 │ WebSocket JSON, ~20 updates/s
   ┌─────────────────────────────▼────────────────────────────┐
   │  DASHBOARD (Vite + React + uPlot)                        │
   │  live charts · stress gauge · per-question verdicts      │
   └──────────────────────────────────────────────────────────┘
```

All sensors are read by one Python process, so every signal shares the same clock and lines up with the question markers. Each session is saved as a CSV of all signals plus a WAV of the audio.

**Stack:** Arduino C++ · Python (FastAPI, numpy, OpenCV, MediaPipe, Parselmouth, sounddevice) · React 19 + Vite + uPlot.

---

## Try it

### Option A: dashboard demo in the browser (no hardware, no Python)

The dashboard can run against a simulated backend in the browser. It runs a full session by itself, including the baseline, questions and verdicts.

Needs Node.js 20.19+ or 22.12+.

```bash
git clone https://github.com/V3riFi/VeriFi.git
cd VeriFi/frontend
npm install
npx vite --mode demo        # then open http://localhost:5173
```

### Option B: the real system

Needs Python 3.9–3.12 and Node.js 20.19+ or 22.12+.

```bash
git clone https://github.com/V3riFi/VeriFi.git
cd VeriFi
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 openCV/download_models.py                      # face-tracking models, once

python3 run.py              # backend + dashboard, opens the browser
python3 run.py --fake-body  # no Arduino: simulated heart rate and breathing, real webcam and mic
```

With the Arduino: flash `firmware/verifi/sensors/` from the Arduino IDE and close the Serial Monitor before starting. Wiring, troubleshooting and every option are in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

**Tests:** `python -m unittest discover -s backend/tests -t . -v`

---

## What we're proud of

- **Every reading is explained.** Each verdict breaks down into per-signal contributions that add up exactly to the score, alongside the raw traces and the person's own calm-moment distribution.
- **It says when it doesn't know.** Quality gates and an 80% interval keep it from showing a confident number on bad data.
- **On-device heartbeat detection** that holds up to 200 BPM. An earlier version silently halved any rate above 85 BPM, and we found and fixed it.
- **Robust in a live demo.** The mic and camera recover on their own after dropouts, and a frozen browser tab can't stall the session.

## Limitations

- **Stress is not lying.** An innocent person can react to an emotional or accusatory question; a calm liar may not react at all. VeriFi flags reactions for follow-up. It is never a verdict on guilt.
- **Few signals carry the score.** Our GSR sensor read too steadily to be useful, so skin conductance, normally a polygraph's strongest channel, is simulated and left out. One signal reacting alone usually reads as Low; it takes two or more moving together.
- **Talking changes the body.** Speaking affects breathing and heart rate, so short answers (yes/no, a few words) read most cleanly.
- **One person at a time.** Readings compare a person only with their own baseline, not with other people.
- **Not validated.** The model constants were set during a weekend hackathon on a small number of test sessions.
- **Prototype only.** Not for real investigations, hiring, or any decision about a person.

## Team

| Name | Area |
|---|---|
| Aaryan | Frontend |
| Matthew | Frontend, computer vision |
| Aagrim | Computer vision |
| Cohen | Hardware, computer vision, backend |
| Zhenjie Wang | Voice analysis (Parselmouth) |
| Namish | Backend |
