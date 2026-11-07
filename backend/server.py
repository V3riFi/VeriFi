"""FastAPI app: the single backend process.

Run from the repo root:  uvicorn backend.server:app --port 8000

Owns the WebSocket at /ws (message contract in README), starts the serial
and vision threads, runs the CIT session timing, and logs CSVs to data/.
"""
