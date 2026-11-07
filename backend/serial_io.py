"""
READ ME:

THIS CODE MAKES A DIRECTORY CALLED LOGS. INSIDE OF THE LOGS ARE THE DATA


Reads the Arduino over serial in a background thread.

Parses EVT:HELLO / EVT:DATA / EVT:BUTTON lines (see "Serial protocol" in README)
and timestamps each sample with laptop time on arrival.
Port comes from the VERIFI_PORT env variable, or is auto-detected.
"""
#!/usr/bin/env python3
"""
serial_io.py — GSR sensor serial reader, live plot, and CSV logger.

Matches Arduino sketch:
    Serial.begin(9600);
    Serial.println(gsrAverage);   # one integer per line

Usage:
    python serial_io.py
    python serial_io.py --port /dev/ttyACM0 --baud 9600
"""

import argparse
import csv
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import serial
import matplotlib.pyplot as plt


# ----------------------------- Configuration -----------------------------

DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_BAUD = 9600
MAX_POINTS   = 300          # how many recent samples to show on the plot
LOG_DIR      = Path("logs") # CSV logs go here


# ----------------------------- Serial helpers ----------------------------

def open_serial(port: str, baud: int, timeout: float = 1.0) -> serial.Serial:
    """Open the serial port with a timeout so readline() never blocks forever."""
    ser = serial.Serial(port, baud, timeout=timeout)
    ser.reset_input_buffer()
    return ser


def read_value(ser: serial.Serial):
    """Read one line, return a float or None if the line is empty/bad."""
    raw = ser.readline().decode("utf-8", errors="ignore").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


# ----------------------------- Main loop ---------------------------------

def run(port: str, baud: int, log_path: Path) -> None:
    ser = open_serial(port, baud)
    print(f"Connected to {port} @ {baud} baud. Logging to {log_path}")
    print("Press Ctrl+C to stop.\n")

    buffer = deque(maxlen=MAX_POINTS)

    plt.ion()
    fig, ax = plt.subplots()
    (line,) = ax.plot([], [], lw=1, color="tab:blue")
    ax.set_ylim(0, 1023)
    ax.set_xlim(0, MAX_POINTS)
    ax.set_xlabel("Sample")
    ax.set_ylabel("GSR (raw ADC)")
    ax.set_title("Live GSR Signal")
    fig.tight_layout()

    new_file = not log_path.exists()
    csv_file = open(log_path, "a", newline="")
    writer = csv.writer(csv_file)
    if new_file:
        writer.writerow(["iso_time", "unix_time", "gsr_raw"])

    try:
        while True:
            value = read_value(ser)
            if value is None:
                continue

            now = time.time()
            writer.writerow([datetime.fromtimestamp(now).isoformat(), now, value])
            csv_file.flush()

            buffer.append(value)
            line.set_data(range(len(buffer)), list(buffer))
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
    parser = argparse.ArgumentParser(description="Read GSR data from Arduino over serial.")
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"Serial port (default {DEFAULT_PORT})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Baud rate (default {DEFAULT_BAUD})")
    parser.add_argument("--log", default=None, help="CSV log path (default logs/gsr_<HHMMSS>.csv)")
    args = parser.parse_args()

    LOG_DIR.mkdir(exist_ok=True)
    log_path = Path(args.log) if args.log else LOG_DIR / f"gsr_{datetime.now():%H%M%S}.csv"

    run(args.port, args.baud, log_path)


if __name__ == "__main__":
    main()
