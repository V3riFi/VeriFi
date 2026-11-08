#!/usr/bin/env python3
"""Starts VeriFi with one command: backend, frontend, and the dashboard in your browser.

    python3 run.py        (Mac/Linux)
    python run.py         (Windows)

Uses the real Arduino, camera and mic. Add --fake-body to replace only the Arduino's
body signals (heart rate, breathing) with fake ones; the camera and mic stay real:

    python3 run.py --fake-body

Press Ctrl+C to stop everything. Setup (the venv and pip install) is still needed
once; see README.
"""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
BACKEND_PORT = 8000
FRONTEND_PORT = 5173
DASHBOARD_URL = f"http://localhost:{FRONTEND_PORT}"
HEALTH_URL = f"http://127.0.0.1:{BACKEND_PORT}/"
START_TIMEOUT_S = 120  # the backend loads the face and voice models on startup
WINDOWS = os.name == "nt"


def fail(message):
    sys.exit(f"\nrun.py: {message}")


def python_for_backend():
    """The repo's .venv if it exists, otherwise whichever Python is running this script."""
    venv = ROOT / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
    return str(venv) if venv.exists() else sys.executable


def port_in_use(port):
    try:
        socket.create_connection(("localhost", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def install_frontend_packages(npm):
    """Runs npm install on first use, and again after a pull changes the packages."""
    installed = FRONTEND / "node_modules" / ".package-lock.json"
    lock = FRONTEND / "package-lock.json"
    if installed.exists() and installed.stat().st_mtime >= lock.stat().st_mtime:
        return
    print("run.py: installing frontend packages...", flush=True)
    if subprocess.run([npm, "install"], cwd=FRONTEND).returncode != 0:
        fail("npm install failed. See the error above.")


def start(command, cwd, env=None):
    # Each server gets its own process group so stop() can end it and its children.
    if WINDOWS:
        return subprocess.Popen(command, cwd=cwd, env=env, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    return subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)


def stop(process):
    if process.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)


def backend_source():
    """"serial" or "fake" once the backend answers, otherwise None."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1) as response:
            return json.load(response).get("source")
    except (OSError, ValueError):
        return None


def wait_until_ready(backend, frontend):
    deadline = time.monotonic() + START_TIMEOUT_S
    source = None
    while time.monotonic() < deadline:
        if backend.poll() is not None:
            fail("the backend stopped while starting. See the error above. "
                 "If a package is missing, run: pip install -r requirements.txt (inside .venv).")
        if frontend.poll() is not None:
            fail("the frontend stopped while starting. See the error above.")
        source = source or backend_source()
        if source and port_in_use(FRONTEND_PORT):
            return source
        time.sleep(0.5)
    fail(f"the servers weren't ready after {START_TIMEOUT_S} seconds.")


def main():
    parser = argparse.ArgumentParser(description="Start VeriFi's backend and dashboard.")
    parser.add_argument("--fake-body", action="store_true", help="fake body sensors; camera and mic stay real")
    fake_body = parser.parse_args().fake_body
    expected_source = "fake" if fake_body else "serial"

    npm = shutil.which("npm")
    if not npm:
        fail("npm wasn't found. Install Node.js (20.19+ or 22.12+) first.")
    for port, name in ((BACKEND_PORT, "backend"), (FRONTEND_PORT, "frontend")):
        if port_in_use(port):
            fail(f"port {port} ({name}) is already in use, probably by an old copy of VeriFi. "
                 "Stop it first (Ctrl+C in its terminal), then run this again.")
    install_frontend_packages(npm)

    env = os.environ.copy()
    env.pop("VERIFI_FAKE", None)  # real signals unless --fake-body asks otherwise
    if fake_body:
        env["VERIFI_FAKE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    processes = []
    try:
        print("run.py: starting the backend (the first start can take a minute)...", flush=True)
        backend = start([python_for_backend(), "-m", "uvicorn", "backend.server:app", "--port", str(BACKEND_PORT)], ROOT, env)
        processes.append(backend)
        frontend = start([npm, "run", "dev", "--", "--port", str(FRONTEND_PORT), "--strictPort"], FRONTEND)
        processes.append(frontend)

        source = wait_until_ready(backend, frontend)
        if source != expected_source:
            fail(f"the backend reports its data source as {source!r}, expected {expected_source!r}.")

        webbrowser.open(DASHBOARD_URL)
        data = "FAKE body sensors, real camera and mic" if fake_body else "real sensor data"
        print(f"\nrun.py: VeriFi is running at {DASHBOARD_URL} on {data}. Press Ctrl+C to stop.\n", flush=True)

        while backend.poll() is None and frontend.poll() is None:
            time.sleep(0.5)
        fail("one of the servers stopped, so VeriFi is shutting down. See the error above.")
    except KeyboardInterrupt:
        print("\nrun.py: stopping...", flush=True)
    finally:
        for process in processes:
            stop(process)


if __name__ == "__main__":
    main()
