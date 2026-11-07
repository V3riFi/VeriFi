# VeriFi frontend

Vite + React dashboard: live signal charts, the CIT subject screen, and results. uPlot draws the charts.

```
npm install
npm run dev      # http://localhost:5173
npm run lint
```

Start the backend first (`uvicorn backend.server:app --port 8000` from the repo root). The dashboard reconnects on its own if the backend restarts.

## Settings

Optional, in `frontend/.env.local`:

| Variable | Default | Meaning |
|---|---|---|
| `VITE_WS_URL` | `ws://localhost:8000/ws` | Backend WebSocket |
| `VITE_SESSION_SOURCE` | `mock` | Which session source the setup form starts on: `mock` or `backend` |

## Simulated sessions

The backend streams `sample` messages but doesn't run sessions yet, so `src/lib/mockSession.js` emits `trial` and `result` messages locally, in the shape the README contract defines. Its scores are random numbers. Anything it produces is labelled "Simulated" on screen.

When the backend starts sending sessions, set `VITE_SESSION_SOURCE=backend`. A `trial` from the backend also takes over the screen automatically unless a simulated session is running.

## Layout

- `src/lib/store.js`: all state, and the handler for WebSocket messages
- `src/lib/socket.js`: WebSocket connection with reconnect
- `src/lib/mockSession.js`: simulated sessions
- `src/components/`: UI
