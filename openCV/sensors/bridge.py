"""Read the physiological sensors and write the CSV the scorer uses.

Accepted line formats (one sample per line, from the Arduino's Serial.println or a UDP packet):
    512,74,301                  plain numbers, in the order given by `columns` (default gsr,hr,resp)
    gsr:512 hr:74 resp:301      key:value or key=value, separated by spaces, commas, tabs or semicolons
    {"gsr": 512, "hr": 74}      JSON
Header lines, blank lines and debug prints are skipped (counted as unparsed, see sensor_check.py).
Key names are forgiving: eda/scl/sc/skin -> gsr; bpm/heartrate/heart_rate/heart -> hr;
breath/breathing/rsp/belt/respiration -> resp; pulse/ppg/ir/raw_pulse -> ppg (raw pulse waveform:
heart rate is derived from it when there's no hr column). A device time column (t/time/ms/millis) is
ignored: the laptop clock is used, so onsets and samples share one clock.
"""
from __future__ import annotations

import csv
import json
import math
import random
import re
import socket
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Sequence, Tuple

COLUMNS = ("gsr", "hr", "resp", "ppg")
ALIASES = {
    "gsr": "gsr", "eda": "gsr", "scl": "gsr", "sc": "gsr", "skin": "gsr", "gsr_raw": "gsr", "conductance": "gsr",
    "hr": "hr", "bpm": "hr", "heartrate": "hr", "heart_rate": "hr", "heart": "hr", "pulse_bpm": "hr",
    "resp": "resp", "breath": "resp", "breathing": "resp", "rsp": "resp", "belt": "resp", "respiration": "resp",
    "ppg": "ppg", "pulse": "ppg", "ir": "ppg", "raw_pulse": "ppg", "pulse_raw": "ppg",
}
DEVICE_TIME = {"t", "time", "ms", "millis", "timestamp", "ts"}
_KV = re.compile(r"([A-Za-z_]+)\s*[:=]\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")
_SPLIT = re.compile(r"[,\s;\t]+")


def _num(s: str) -> Optional[float]:
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def parse_line(line: str, columns: Sequence[str] = ("gsr", "hr", "resp")) -> Optional[Dict[str, float]]:
    """One line -> {column: value}, or None if it holds no sample."""
    line = line.strip().strip("\x00")
    if not line:
        return None
    if line.startswith("{"):
        try:
            obj = json.loads(line)
        except ValueError:
            return None
        out = {}
        for k, v in obj.items():
            col = ALIASES.get(str(k).lower())
            val = _num(v) if not isinstance(v, bool) else None
            if col and val is not None:
                out[col] = val
        return out or None
    kv = _KV.findall(line)
    if kv:
        out = {}
        for k, v in kv:
            col = ALIASES.get(k.lower())
            if col:
                out[col] = float(v)
        return out or None
    parts = [p for p in _SPLIT.split(line) if p]
    vals = [_num(p) for p in parts]
    if not parts or any(v is None for v in vals):
        return None                       # header or debug text
    if len(vals) != len(columns):
        return None                       # half a line (serial buffer split) or a different sketch
    return {c: v for c, v in zip(columns, vals) if c in COLUMNS}


class SensorRecorder:
    """Thread-safe CSV writer + a short in-memory buffer for live checks."""

    def __init__(self, path: Optional[str], simulated: bool = False, keep_s: float = 30.0):
        self.path = path
        self.simulated = simulated
        self.lock = threading.Lock()
        self.recent: Deque[Tuple[float, Dict[str, float]]] = deque()
        self.keep_s = keep_s
        self.n_ok = 0
        self.n_bad = 0
        self.bad_examples: List[str] = []
        self._f = None
        self._w = None
        if path:
            self._f = open(path, "w", newline="", encoding="utf-8")
            self._w = csv.writer(self._f)
            self._w.writerow(["t", *COLUMNS] + (["sim"] if simulated else []))
        self._last_flush = time.time()

    def add_line(self, line: str, columns: Sequence[str], t: Optional[float] = None) -> bool:
        t = time.time() if t is None else t
        s = parse_line(line, columns)
        if s is None:
            with self.lock:
                self.n_bad += 1
                if len(self.bad_examples) < 5 and line.strip():
                    self.bad_examples.append(line.strip()[:80])
            return False
        self.add(t, s)
        return True

    def add(self, t: float, sample: Dict[str, float]) -> None:
        with self.lock:
            self.n_ok += 1
            self.recent.append((t, sample))
            while self.recent and self.recent[0][0] < t - self.keep_s:
                self.recent.popleft()
            if self._w:
                self._w.writerow([f"{t:.4f}"] + ["" if sample.get(c) is None else f"{sample[c]:g}" for c in COLUMNS]
                                 + (["1"] if self.simulated else []))
                if t - self._last_flush > 1.0:     # a crash loses at most ~1 s
                    self._f.flush()
                    self._last_flush = t

    def snapshot(self, seconds: float = 5.0) -> Dict[str, object]:
        """Live status: sample rate and latest values over the last `seconds`."""
        with self.lock:
            now = time.time()
            rec = [(t, s) for t, s in self.recent if t >= now - seconds]
        out: Dict[str, object] = {"rate": len(rec) / seconds, "n_ok": self.n_ok, "n_bad": self.n_bad}
        for c in COLUMNS:
            vals = [s[c] for _, s in rec if c in s]
            if vals:
                out[c] = vals[-1]
        return out

    def close(self) -> None:
        with self.lock:
            if self._f:
                self._f.flush()
                self._f.close()
                self._f = self._w = None


class _Source:
    def __init__(self, rec: SensorRecorder, columns: Sequence[str]):
        self.rec, self.columns = rec, list(columns)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.error: Optional[str] = None

    def start(self) -> "_Source":
        self._thread = threading.Thread(target=self._run_safe, daemon=True)
        self._thread.start()
        return self

    def _run_safe(self) -> None:
        try:
            self._run()
        except Exception as e:          # report, never kill the interview
            self.error = f"{type(e).__name__}: {e}"

    def _run(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)


class SerialSource(_Source):
    """Arduino over USB serial. Reconnects if the cable is pulled (the gap simply has no samples)."""

    def __init__(self, rec: SensorRecorder, port: str, baud: int = 115200, columns: Sequence[str] = ("gsr", "hr", "resp")):
        super().__init__(rec, columns)
        self.port, self.baud = port, baud
        self.connected = False

    def _run(self) -> None:
        import serial                    # pyserial; imported here so the rest works without it
        while not self._stop.is_set():
            try:
                with serial.Serial(self.port, self.baud, timeout=0.5) as ser:
                    self.connected, self.error = True, None
                    ser.reset_input_buffer()
                    ser.readline()       # drop a possibly partial first line
                    while not self._stop.is_set():
                        raw = ser.readline()
                        if raw:
                            self.rec.add_line(raw.decode("utf-8", "replace"), self.columns)
            except Exception as e:
                self.connected = False
                self.error = f"{type(e).__name__}: {e}"
                self._stop.wait(1.0)


class UdpSource(_Source):
    """Lines or JSON packets from the backend / another logger (one sample per packet or per line)."""

    def __init__(self, rec: SensorRecorder, address: str = "0.0.0.0:5008", columns: Sequence[str] = ("gsr", "hr", "resp")):
        super().__init__(rec, columns)
        host, port = address.rsplit(":", 1)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, int(port)))
        self.sock.settimeout(0.5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self.sock.recvfrom(65536)
            except socket.timeout:
                continue
            t = time.time()
            for line in data.decode("utf-8", "replace").splitlines():
                self.rec.add_line(line, self.columns, t)

    def stop(self) -> None:
        super().stop()
        self.sock.close()


class SimSource(_Source):
    """SIMULATED sensors (noise + drift, no responses to anything). For testing the plumbing only:
    the CSV gets a `sim` column so nobody mistakes it for data."""

    def __init__(self, rec: SensorRecorder, hz: float = 50.0, seed: Optional[int] = None):
        super().__init__(rec, ("gsr", "hr", "resp"))
        self.hz = hz
        self.rng = random.Random(seed)

    def _run(self) -> None:
        t0 = time.time()
        gsr = 5.0
        while not self._stop.is_set():
            t = time.time()
            gsr += self.rng.gauss(0, 0.003)
            self.rec.add(t, {"gsr": gsr + self.rng.gauss(0, 0.01),
                             "hr": 72 + 3 * math.sin((t - t0) * 0.1) + self.rng.gauss(0, 0.8),
                             "resp": math.sin(2 * math.pi * 0.25 * (t - t0)) + self.rng.gauss(0, 0.05)})
            self._stop.wait(1.0 / self.hz)


def list_ports() -> List[Tuple[str, str]]:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    return [(p.device, p.description or "") for p in lp.comports()]


def start_sensors(spec: str, path: str, columns: Sequence[str] = ("gsr", "hr", "resp")) -> Tuple[SensorRecorder, _Source]:
    """Recorder + started source in one call (what the interview scripts use)."""
    rec = SensorRecorder(path, simulated=spec.strip().lower() == "sim")
    src = open_source(spec, rec, columns).start()
    return rec, src


def stop_sensors(rec: SensorRecorder, src: _Source) -> None:
    src.stop()
    rec.close()


def open_source(spec: str, rec: SensorRecorder, columns: Sequence[str] = ("gsr", "hr", "resp")) -> _Source:
    """'serial:COM5', 'serial:COM5@9600', 'COM5', 'udp:0.0.0.0:5008', 'sim'."""
    s = spec.strip()
    if s.lower() == "sim":
        return SimSource(rec)
    if s.lower().startswith("udp:"):
        return UdpSource(rec, s[4:], columns)
    if s.lower().startswith("serial:"):
        s = s[7:]
    port, _, baud = s.partition("@")
    return SerialSource(rec, port, int(baud) if baud else 115200, columns)
