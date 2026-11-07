"""Message schema + output sinks. The ONLY contract the fusion engine depends on.

Instant message (throttled, ~10 Hz per feature; events sent immediately):
    {"timestamp": 1731000000.12, "source": "vision", "feature": "blink_rate", "value": 14.2}

Window message (every window_s seconds):
    {"timestamp": 1731000005.00, "source": "vision", "window_features": {"blink_rate": 14.2, ...}}

CIT item message (once per item presentation, ~5.6 s after its onset):
    {"timestamp": <onset>, "source": "vision", "item": "7 of Hearts", "valid": true,
     "item_features": {"blinks_early": 0.0, "first_blink_latency_s": 3.1, ...}}
CIT scores message (after each item message):
    {"timestamp": ..., "source": "vision", "cit_scores": {"7 of Hearts": {"vision_cit_score": 1.4, ...}, ...}}

Rules: timestamps are epoch seconds (time.time()) so they align with other
sensors on the same machine. Instant values are always finite floats (invalid
features are simply not sent). In window_features a feature that could not be
computed this window is null.
"""
from __future__ import annotations

import json
import logging
import math
import queue
import socket
import sys
from typing import Callable, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)
SOURCE = "vision"
Message = Dict[str, object]


def instant_message(t: float, feature: str, value: float) -> Message:
    return {"timestamp": round(t, 3), "source": SOURCE, "feature": feature, "value": float(value)}


def _clean(d: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    return {k: (None if v is None or not math.isfinite(v) else round(float(v), 5)) for k, v in d.items()}


def window_message(t: float, features: Dict[str, Optional[float]]) -> Message:
    return {"timestamp": round(t, 3), "source": SOURCE, "window_features": _clean(features)}


def item_message(onset: float, item: str, valid: bool, features: Dict[str, Optional[float]],
                 meta: Optional[Dict[str, object]] = None) -> Message:
    """One presentation (item / question / narrative answer). timestamp = its onset time."""
    meta = meta or {}
    return {"timestamp": round(onset, 3), "source": SOURCE, "item": item, "valid": bool(valid),
            "block": meta.get("block"), "series": meta.get("series"), "kind": meta.get("kind"),
            "item_features": _clean(features)}


def cit_scores_message(t: float, scores: Dict[str, Dict[str, Optional[float]]], block: str = "cit") -> Message:
    """Running per-item scores for one block (higher = stood out from the other items)."""
    return {"timestamp": round(t, 3), "source": SOURCE, "block": block,
            "cit_scores": {i: _clean(v) for i, v in scores.items()}}


# ---------------------------------------------------------------------- sinks
class Sink:
    def emit(self, msg: Message) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class StdoutSink(Sink):
    def emit(self, msg: Message) -> None:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


class JsonlFileSink(Sink):
    def __init__(self, path: str):
        self._f = open(path, "a", encoding="utf-8", buffering=1)

    def emit(self, msg: Message) -> None:
        self._f.write(json.dumps(msg) + "\n")

    def close(self) -> None:
        self._f.close()


class UdpSink(Sink):
    """Fire-and-forget JSON datagrams. Fusion engine down? Vision keeps running."""

    def __init__(self, address: str):
        host, port = address.rsplit(":", 1)
        self._addr = (host, int(port))
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def emit(self, msg: Message) -> None:
        try:
            self._sock.sendto(json.dumps(msg).encode("utf-8"), self._addr)
        except OSError as e:
            log.debug("udp send failed: %s", e)

    def close(self) -> None:
        self._sock.close()


class CallbackSink(Sink):
    def __init__(self, fn: Callable[[Message], None]):
        self._fn = fn

    def emit(self, msg: Message) -> None:
        self._fn(msg)


class QueueSink(Sink):
    """In-process integration: fusion engine reads from `sink.queue`. Drops oldest when full."""

    def __init__(self, maxsize: int = 1000):
        self.queue: "queue.Queue[Message]" = queue.Queue(maxsize=maxsize)

    def emit(self, msg: Message) -> None:
        try:
            self.queue.put_nowait(msg)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.queue.put_nowait(msg)


class FrameCsvWriter:
    """Every processed frame as one CSV row. For debugging thresholds offline, not for fusion."""

    COLUMNS = ("t", "face_present", "ear", "ear_left", "ear_right", "eye_openness", "eye_state", "ear_close_thr",
               "blink_event", "bs_eye_blink", "bs_mouth_press",
               "head_pitch", "head_yaw", "head_roll", "head_speed", "mouth_open", "lip_thickness",
               "brow_inner_gap", "face_touch", "item",
               # appended (older CSVs lack them; readers use column names): per-trial quality checks
               "gaze_h", "gaze_v", "gaze_offaxis", "head_tx", "head_ty", "head_tz", "facial_motion_energy", "yawn")

    def __init__(self, path: str):
        import csv
        self._f = open(path, "w", newline="", encoding="utf-8")
        self._w = csv.writer(self._f)
        self._w.writerow(self.COLUMNS)

    def write(self, rec: Dict[str, object]) -> None:
        row = []
        for c in self.COLUMNS:
            v = rec.get(c)
            row.append("" if v is None else (f"{v:.4f}" if isinstance(v, float) and c != "t" else v))
        self._w.writerow(row)

    def close(self) -> None:
        self._f.close()


# ---------------------------------------------------------------------- publisher
class FeaturePublisher:
    def __init__(self, sinks: Iterable[Sink], instant_rate_hz: float = 10.0,
                 emit_instant: bool = True, emit_window: bool = True, emit_events: bool = True):
        self.sinks: List[Sink] = list(sinks)
        self._period = 1.0 / instant_rate_hz if instant_rate_hz > 0 else 0.0
        self._next_instant = 0.0
        self.emit_instant, self.emit_window, self.emit_events = emit_instant, emit_window, emit_events

    def _send(self, msg: Message) -> None:
        for s in self.sinks:
            try:
                s.emit(msg)
            except Exception as e:  # noqa: BLE001 - one bad sink must not kill the camera loop
                log.warning("sink %s failed: %s", type(s).__name__, e)

    def publish_instant(self, t: float, values: Dict[str, Optional[float]]) -> None:
        if not self.emit_instant or t < self._next_instant:
            return
        self._next_instant = t + self._period
        for k, v in values.items():
            if v is not None and math.isfinite(v):
                self._send(instant_message(t, k, v))

    def publish_event(self, t: float, feature: str, value: float) -> None:
        if self.emit_events:
            self._send(instant_message(t, feature, value))

    def publish_window(self, t: float, features: Dict[str, Optional[float]]) -> None:
        if self.emit_window:
            self._send(window_message(t, features))

    def publish(self, msg: Message) -> None:
        """Any other message (e.g. the final interview result)."""
        self._send(msg)

    def publish_item(self, onset: float, item: str, valid: bool, features: Dict[str, Optional[float]],
                     meta: Optional[Dict[str, object]] = None) -> None:
        self._send(item_message(onset, item, valid, features, meta))

    def publish_cit_scores(self, t: float, scores: Dict[str, Dict[str, Optional[float]]], block: str = "cit") -> None:
        self._send(cit_scores_message(t, scores, block))

    def close(self) -> None:
        for s in self.sinks:
            s.close()
