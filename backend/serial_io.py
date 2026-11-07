#!/usr/bin/env python3

# INSTALL: pip install pyserial matplotlib (into a venv)

"""
serial_io.py — Combined sensor reader, live plot, and CSV logger.

Matches the combined Arduino sketch:
    breath: 512, gsr: 480, heartbeat: 72, spo2: 98, temp: 31.5

Usage:
    python serial_io.py
    python serial_io.py --port /dev/ttyACM0 --baud 115200
"""

import argparse
import csv
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import serial
import matplotlib.pyplot as plt


# CONFIG

DEFAULT_PORT = "/dev/ttyACM0"   # port the Arduino connects to via USB
DEFAULT_BAUD = 115200           # must match Serial.begin() in the sketch
MAX_POINTS   = 600              # ~60 s of history at 10 Hz (good for breathing)
LOG_DIR      = Path("logs")

# the labeled line the Arduino prints
LINE_RE = re.compile(
    r"breath:\s*(\d+),\s*gsr:\s*(\d+),\s*heartbeat:\s*(\d+),"
    r"\s*spo2:\s*(\d+),\s*temp:\s*([\d.]+)"
)


# SERIAL HELPERS

def open_serial(port: str, baud: int, timeout: float = 1.0) -> serial.Serial:
    """Open the serial port with a timeout so readline() never blocks forever."""
    ser = serial.Serial(port, baud, timeout=timeout)
    ser.reset_input_buffer()
    return ser


def parse_line(raw: str):
    """Parse one labeled line. Returns dict or None if it's a STATUS line / garbage."""
    if not raw or raw.startswith("STATUS:"):
        return None
    m = LINE_RE.search(raw)
    if not m:
        return None
    try:
        return {
            "breath": float(m.group(1)),
            "gsr":    float(m.group(2)),
            "hr":     float(m.group(3)),
            "spo2":   float(m.group(4)),
            "temp":   float(m.group(5)),
        }
    except ValueError:
        return None


# main loop

def run(port: str, baud: int, log_path: Path) -> None:
    ser = open_serial(port, baud)
    print(f"Connected to {port} @ {baud} baud. Logging to {log_path}")
    print("Press Ctrl+C to stop.\n")

    # one buffer per channel — deque keeps only the last MAX_POINTS
    breath_buf = deque(maxlen=MAX_POINTS)
    gsr_buf    = deque(maxlen=MAX_POINTS)
    hr_buf     = deque(maxlen=MAX_POINTS)

    # three stacked panels sharing the x-axis
    plt.ion()
    fig, (ax_gsr, ax_breath, ax_hr) = plt.subplots(3, 1, figsize=(10, 7), sharex=True)
    fig.tight_layout(pad=2.5)

    (line_gsr,)    = ax_gsr.plot([], [], lw=1, color="tab:blue")
    (line_breath,) = ax_breath.plot([], [], lw=1, color="tab:green")
    (line_hr,)     = ax_hr.plot([], [], lw=1, color="tab:red")

    ax_gsr.set_ylabel("GSR (raw ADC)")
    ax_gsr.set_ylim(0, 1023)
    ax_gsr.set_title("Live Sensor Stream")

    ax_breath.set_ylabel("Breath (flex ADC)")
    ax_breath.set_ylim(0, 1023)

    ax_hr.set_ylabel("Heart rate (BPM)")
    ax_hr.set_ylim(0, 200)      # keep a fixed window so spikes are obvious
    ax_hr.set_xlabel("Sample")

    # CSV setup — append so re-runs add to the same file
    new_file = not log_path.exists()
    csv_file = open(log_path, "a", newline="")
    writer = csv.writer(csv_file)
    if new_file:
        writer.writerow(["iso_time", "unix_time", "breath", "gsr", "hr", "spo2", "temp"])

    try:
        while True:
            raw = ser.readline().decode("utf-8", errors="ignore").strip()
            parsed = parse_line(raw)
            if parsed is None:
                continue

            now = time.time()
            writer.writerow([
                datetime.fromtimestamp(now).isoformat(),
                now,
                parsed["breath"],
                parsed["gsr"],
                parsed["hr"],
                parsed["spo2"],
                parsed["temp"],
            ])
            csv_file.flush()

            breath_buf.append(parsed["breath"])
            gsr_buf.append(parsed["gsr"])
            hr_buf.append(parsed["hr"])

            line_gsr.set_data(range(len(gsr_buf)), list(gsr_buf))
            line_breath.set_data(range(len(breath_buf)), list(breath_buf))
            line_hr.set_data(range(len(hr_buf)), list(hr_buf))
            ax_gsr.relim(); ax_gsr.autoscale_view(False, True, False)
            ax_breath.relim(); ax_breath.autoscale_view(False, True, False)
            # HR stays on its fixed 0–200 scale so a 0 (no finger) is easy to spot

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


# ----------------------------- CLI entry point ---------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Read combined sensor data from Arduino over serial.")
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"Serial port (default {DEFAULT_PORT})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Baud rate (default {DEFAULT_BAUD})")
    parser.add_argument("--log", default=None, help="CSV log path (default logs/session_<HHMMSS>.csv)")
    args = parser.parse_args()

    LOG_DIR.mkdir(exist_ok=True)
    log_path = Path(args.log) if args.log else LOG_DIR / f"session_{datetime.now():%H%M%S}.csv"

    run(args.port, args.baud, log_path)


if __name__ == "__main__":
    main()
