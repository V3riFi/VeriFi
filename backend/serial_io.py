#!/usr/bin/env python3
"""Reads the Arduino over USB serial.

Two ways to use it:

1. Inside the backend (normal use): server.py starts a SerialReader thread,
   which turns every Arduino line into a sample dict with laptop time.

2. On its own, as a quick hardware check with a live matplotlib plot and CSV log
   (don't run this while the backend is running, only one program can use the port):
       python -m backend.serial_io
       python -m backend.serial_io --port /dev/cu.usbmodem1101

Matches firmware/verifi/sensors (10 Hz, one labeled line per sample):
    breath: 512, gsr: 480, heartbeat: 72, spo2: 98, temp: 31.5
The sketch currently prints "Blood Pressure:" instead of "spo2:" for the
oxygen value; both are accepted. Lines starting with "STATUS:" are printed, not parsed.

Port: VERIFI_PORT env variable, or --port, or auto-detected.
"""

from __future__ import annotations  # allows "dict | None" hints on Python 3.9

import argparse
import csv
import os
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable

import serial
from serial.tools import list_ports

DEFAULT_BAUD = 115200  # must match Serial.begin() in the sketch
MAX_POINTS = 600       # plot history, ~60 s at 10 Hz
LOG_DIR = Path("logs")
RECONNECT_S = 2.0

NUM = r"(-?[\d.]+)"
LINE_RE = re.compile(
    rf"breath:\s*{NUM},\s*gsr:\s*{NUM},\s*heartbeat:\s*{NUM},"
    rf"\s*(?:spo2|blood pressure):\s*{NUM},\s*temp:\s*{NUM}",
    re.IGNORECASE,
)


# ----------------------------- Parsing / ports -----------------------------


def parse_line(raw: str) -> dict | None:
    """One Arduino line -> {"flex", "gsr", "hr", "spo2", "temp"}, or None for STATUS lines / garbage.

    heartbeat, spo2 and temp read 0 (or -1) until the pulse sensor has a reading;
    those become None so they count as missing, not as real zeros.
    """
    m = LINE_RE.search(raw or "")
    if not m:
        return None
    try:
        breath, gsr, hr, spo2, temp = (float(g) for g in m.groups())
    except ValueError:
        return None
    return {
        "flex": breath,
        "gsr": gsr,
        "hr": hr if hr > 0 else None,
        "spo2": spo2 if spo2 > 0 else None,
        "temp": temp if temp > 0 else None,
    }


def find_port() -> str | None:
    """VERIFI_PORT if set, otherwise the first port that looks like an Arduino."""
    if os.environ.get("VERIFI_PORT"):
        return os.environ["VERIFI_PORT"]
    hints = ("arduino", "usbmodem", "usbserial", "ttyacm", "ttyusb", "ch340", "wch")
    for p in list_ports.comports():
        text = f"{p.device} {p.description} {p.manufacturer or ''}".lower()
        if any(h in text for h in hints):
            return p.device
    return None


def open_serial(port: str, baud: int = DEFAULT_BAUD, timeout: float = 1.0):
    """Open the port with a timeout so readline() never blocks forever."""
    ser = serial.serial_for_url(port, baud, timeout=timeout)
    ser.reset_input_buffer()
    return ser


# ----------------------------- Backend reader ------------------------------


class SerialReader(threading.Thread):
    """Background thread: reads lines, calls on_sample(dict) for each parsed sample.

    Each sample gets "t" = laptop time when the line arrived, so it lines up
    with questions and vision. Reconnects automatically if the cable is pulled.
    """

    def __init__(self, on_sample: Callable[[dict], None], baud: int = DEFAULT_BAUD, opener=open_serial) -> None:
        super().__init__(daemon=True, name="serial-reader")
        self.on_sample = on_sample
        self.baud = baud
        self.opener = opener
        self.stopped = threading.Event()
        self.connected = False
        self.port: str | None = None

    def stop(self) -> None:
        self.stopped.set()

    def run(self) -> None:
        warned = False
        while not self.stopped.is_set():
            self.port = find_port()
            if not self.port:
                if not warned:
                    print("serial: no Arduino found. Plug it in, or set VERIFI_PORT. Retrying...", flush=True)
                    warned = True
                self.stopped.wait(RECONNECT_S)
                continue
            try:
                ser = self.opener(self.port, self.baud)
            except (serial.SerialException, OSError) as e:
                if not warned:
                    print(f"serial: can't open {self.port} ({e}). Is the Serial Monitor open? Retrying...", flush=True)
                    warned = True
                self.stopped.wait(RECONNECT_S)
                continue

            print(f"serial: connected to {self.port} @ {self.baud}", flush=True)
            self.connected, warned = True, False
            try:
                while not self.stopped.is_set():
                    raw = ser.readline().decode("utf-8", errors="ignore").strip()
                    if not raw:
                        continue
                    if raw.startswith("STATUS:"):
                        print(f"serial: {raw}", flush=True)
                        continue
                    sample = parse_line(raw)
                    if sample is not None:
                        self.on_sample({"t": round(time.time(), 3), **sample})
            except (serial.SerialException, OSError) as e:
                print(f"serial: lost connection ({e}). Reconnecting...", flush=True)
            finally:
                self.connected = False
                try:
                    ser.close()
                except Exception:
                    pass
            self.stopped.wait(RECONNECT_S)


# ----------------------------- Standalone check tool -----------------------


def run_plot(port: str, baud: int, log_path: Path) -> None:
    import matplotlib.pyplot as plt  # only needed for this tool, not the backend

    ser = open_serial(port, baud)
    print(f"Connected to {port} @ {baud} baud. Logging to {log_path}")
    print("Press Ctrl+C to stop.\n")

    bufs = {k: deque(maxlen=MAX_POINTS) for k in ("gsr", "flex", "hr")}

    plt.ion()
    fig, (ax_gsr, ax_breath, ax_hr) = plt.subplots(3, 1, figsize=(10, 7), sharex=True)
    fig.tight_layout(pad=2.5)
    (line_gsr,) = ax_gsr.plot([], [], lw=1, color="tab:blue")
    (line_breath,) = ax_breath.plot([], [], lw=1, color="tab:green")
    (line_hr,) = ax_hr.plot([], [], lw=1, color="tab:red")
    ax_gsr.set_ylabel("GSR (raw ADC)")
    ax_gsr.set_title("Live Sensor Stream")
    ax_breath.set_ylabel("Breath (flex ADC)")
    ax_hr.set_ylabel("Heart rate (BPM)")
    ax_hr.set_ylim(0, 200)  # fixed so a missing reading (no finger) is easy to spot
    ax_hr.set_xlabel("Sample")

    new_file = not log_path.exists()
    csv_file = open(log_path, "a", newline="")
    writer = csv.writer(csv_file)
    if new_file:
        writer.writerow(["iso_time", "unix_time", "breath", "gsr", "hr", "spo2", "temp"])

    try:
        while True:
            raw = ser.readline().decode("utf-8", errors="ignore").strip()
            if raw.startswith("STATUS:"):
                print(raw)
                continue
            s = parse_line(raw)
            if s is None:
                continue
            now = time.time()
            writer.writerow([datetime.fromtimestamp(now).isoformat(), now, s["flex"], s["gsr"], s["hr"], s["spo2"], s["temp"]])
            csv_file.flush()

            for k in bufs:
                bufs[k].append(s[k] if s[k] is not None else float("nan"))
            line_gsr.set_data(range(len(bufs["gsr"])), list(bufs["gsr"]))
            line_breath.set_data(range(len(bufs["flex"])), list(bufs["flex"]))
            line_hr.set_data(range(len(bufs["hr"])), list(bufs["hr"]))
            for ax in (ax_gsr, ax_breath):
                ax.relim()
                ax.autoscale_view(False, True, True)
            ax_hr.set_xlim(0, MAX_POINTS)
            fig.canvas.draw()
            fig.canvas.flush_events()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        ser.close()
        csv_file.close()
        plt.ioff()
        plt.close(fig)
        print(f"Serial closed. Data saved to {log_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Hardware check: live plot + CSV of the Arduino's sensors.")
    parser.add_argument("--port", default=None, help="Serial port (default: VERIFI_PORT or auto-detect)")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Baud rate (default {DEFAULT_BAUD})")
    parser.add_argument("--log", default=None, help="CSV log path (default logs/session_<HHMMSS>.csv)")
    args = parser.parse_args()

    port = args.port or find_port()
    if not port:
        raise SystemExit("No Arduino found. Plug it in, or pass --port.")
    LOG_DIR.mkdir(exist_ok=True)
    log_path = Path(args.log) if args.log else LOG_DIR / f"session_{datetime.now():%H%M%S}.csv"
    run_plot(port, args.baud, log_path)


if __name__ == "__main__":
    main()
