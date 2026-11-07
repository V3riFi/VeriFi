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

Heart rate is passed through a median-of-3 filter over *distinct* sensor
readings before being reported. This rejects single-window misdetections
(missed beats, double-counted dicrotic notches) that otherwise show up as
40 BPM jumps even when the actual rate is steady. See HRFilter for details.

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
from statistics import median
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


# ----------------------------- Heart-rate filter ----------------------------


class HRFilter:
    """Median-of-3 filter over *distinct* heart-rate readings.

    Why distinct readings and not every 10 Hz sample: the DFRobot module only
    produces a new HR value every ~4 s, so the same number arrives in ~40
    consecutive serial lines. A 3-point median over the 10 Hz stream would
    therefore always see [x, x, x] — useless. Instead we count each *new*
    value from the sensor once, and take the median of the last N distinct
    readings. A single bad window (72, 160, 72) then resolves to 72, while a
    real sustained change (72, 73, 74) still comes through with ~8 s of lag.

    Missing readings (None) are passed through untouched so the plot shows an
    honest gap, and they don't pollute the history. During warm-up (fewer than
    N distinct readings seen), the raw value is passed through.
    """

    def __init__(self, n: int = 3) -> None:
        # Force odd window so median() returns an observed value, not an average.
        self.n = n if n % 2 == 1 else n + 1
        self.history: deque[float] = deque(maxlen=self.n)
        self.last_raw: float | None = None

    def update(self, raw: float | None) -> float | None:
        if raw is None:
            return None                      # real missing data -> gap in plot
        if raw == self.last_raw:
            return self._current()           # no new sensor reading, hold filtered
        self.last_raw = raw
        self.history.append(raw)
        return self._current()

    def _current(self) -> float | None:
        if not self.history:
            return None
        if len(self.history) < self.n:
            return self.last_raw             # warm-up: pass raw through
        return float(median(self.history))


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


# USB vendor IDs of Arduino boards and of the USB-serial chips that clone boards use.
# These are built into the hardware, so they work whatever name the OS gives the port.
ARDUINO_VIDS = {
    0x2341: "Arduino",
    0x2A03: "Arduino",
    0x1A86: "CH340/CH9102 (clone board)",
    0x0403: "FTDI",
    0x10C4: "CP210x",
}
# Fallback: words in the port's name or description. "usb serial device" is how
# Windows 10/11 often lists an Uno.
NAME_HINTS = (
    "arduino", "usbmodem", "usbserial", "usb serial", "usb-serial",
    "ttyacm", "ttyusb", "ch340", "ch910", "wch", "cp210", "ftdi",
)


def describe_port(p) -> str:
    vid = f" vid={p.vid:04x}" if p.vid is not None else ""
    return f"{p.device} ({p.description}{vid})"


def find_port() -> str | None:
    """VERIFI_PORT if set, otherwise the port that looks most like an Arduino.

    In order: a known Arduino/USB-serial vendor ID, then a known name, then the
    only USB serial port if there is exactly one (Bluetooth and built-in ports
    have no USB vendor ID, so they never count).
    """
    if os.environ.get("VERIFI_PORT"):
        return os.environ["VERIFI_PORT"]
    ports = sorted(list_ports.comports(), key=lambda p: p.device)
    for p in ports:
        if p.vid in ARDUINO_VIDS:
            return p.device
    for p in ports:
        text = f"{p.device} {p.description} {p.manufacturer or ''}".lower()
        if any(h in text for h in NAME_HINTS):
            return p.device
    usb = [p for p in ports if p.vid is not None]
    if len(usb) == 1:
        return usb[0].device
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
    Heart rate is median-filtered before being reported.
    """

    def __init__(self, on_sample: Callable[[dict], None], baud: int = DEFAULT_BAUD, opener=open_serial) -> None:
        super().__init__(daemon=True, name="serial-reader")
        self.on_sample = on_sample
        self.baud = baud
        self.opener = opener
        self.stopped = threading.Event()
        self.connected = False
        self.port: str | None = None
        self.hr_filter = HRFilter()

    def stop(self) -> None:
        self.stopped.set()

    def run(self) -> None:
        warned = False
        while not self.stopped.is_set():
            self.port = find_port()
            if not self.port:
                if not warned:
                    seen = ", ".join(describe_port(p) for p in list_ports.comports()) or "none"
                    print(f"serial: no Arduino found (ports seen: {seen}). Plug it in, or set VERIFI_PORT. Retrying...", flush=True)
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
                        sample["hr"] = self.hr_filter.update(sample["hr"])
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
    hr_filter = HRFilter()

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
            s["hr"] = hr_filter.update(s["hr"])
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
