# VeriFi frontend

The interrogator's dashboard (Vite + React, charts with uPlot). It's for the operator only: nothing on it is meant for the subject to see.

```
npm install
npm run dev      # http://localhost:5173
npm run lint
```

Start the backend first (`uvicorn backend.server:app --port 8000` from the repo root). The dashboard reconnects on its own if the backend restarts.

## What's on screen

- **Navbar:** status of each part of the rig (backend, Arduino, pulse, breathing belt, camera, face tracking, voice), from the backend's `status` message.
- **Live video:** webcam frames sent by the backend. The browser never opens the camera itself.
- **Session:** a name (defaults to Session 1, 2, 3 ...) and Start / Stop. The first part of each session records the baseline.
- **Signal tiles:** current value of every signal and how far it is from baseline. Click one to show its chart.
- **Combined stress chart:** the backend's live `stress` value. Every chart shows the baseline and the normal range (baseline ± 2 SD); the line turns red outside it.
- **Sessions:** this tab's sessions. Click one to look at its recording again.

Question markers come from the backend's `question` / `response` messages (the hardware button, or another client sending `question`).

## Settings

Optional, in `frontend/.env.local`:

| Variable | Default | Meaning |
|---|---|---|
| `VITE_WS_URL` | `ws://localhost:8000/ws` | Backend WebSocket |

## Layout

- `src/lib/store.js`: all state, and the handler for every WebSocket message
- `src/lib/signals.js`: the signals shown, and how normal ranges are worked out
- `src/lib/socket.js`: WebSocket connection with reconnect
- `src/components/`: UI
