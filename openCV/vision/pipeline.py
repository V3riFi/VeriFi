"""VisionEngine: Camera -> Face Mesh -> features -> smoothing -> windows -> sinks.

Usage from the fusion engine (same process):
    engine = VisionEngine.from_config(VisionConfig(show_window=False), extra_sinks=[QueueSink()])
    engine.start_background()
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

import numpy as np

from . import geometry as G
from .aggregator import WindowAggregator
from .blink import BlinkDetector, BlinkUpdate, EyeState
from .camera import Camera
from .cit import CitSession, ItemResponse
from .config import VisionConfig
from .face_features import FacialFeatureExtractor, FacialFeatures, blendshape_subset
from .headpose import HeadPose, HeadPoseEstimator
from .landmarks import FaceMeshDetector, FaceObservation
from .output import FeaturePublisher, JsonlFileSink, Sink, StdoutSink, UdpSink

log = logging.getLogger(__name__)


@dataclass
class FrameResult:
    t: float
    obs: Optional[FaceObservation]
    pose: Optional[HeadPose]
    blink: Optional[BlinkUpdate]
    facial: Optional[FacialFeatures]
    record: Dict[str, Optional[float]]
    window: Optional[Dict[str, Optional[float]]]
    fps: float
    latency_ms: float
    items: List[ItemResponse] = field(default_factory=list)   # CIT epochs finished this frame


class VisionEngine:
    def __init__(self, cfg: VisionConfig, sinks: Iterable[Sink] = ()):
        self.cfg = cfg
        self.mesh = FaceMeshDetector(cfg.mesh.backend, cfg.mesh.model_path,
                                     cfg.mesh.min_detection_confidence, cfg.mesh.min_tracking_confidence)
        self.head = HeadPoseEstimator(cfg.head)
        self.blink = BlinkDetector(cfg.blink)
        self.face = FacialFeatureExtractor(cfg.gaze)
        self.agg = WindowAggregator(cfg.agg, cfg.head, cfg.anomaly_weights)
        o = cfg.output
        self.pub = FeaturePublisher(sinks, cfg.agg.instant_rate_hz, o.emit_instant, o.emit_window, o.emit_events)
        self.hands = None
        if cfg.enable_hands:
            try:
                from .hands import FaceTouchDetector
                self.hands = FaceTouchDetector(every_n_frames=cfg.hands_every_n_frames)
            except Exception as e:  # noqa: BLE001 - optional tier-2 feature
                log.warning("Face-touch detection disabled: %s", e)
        self.frames_csv = None
        if cfg.output.frames_csv:
            from .output import FrameCsvWriter
            self.frames_csv = FrameCsvWriter(cfg.output.frames_csv)
        self.cit = CitSession(cfg.cit)
        self.control = None
        if cfg.cit.control_udp:
            from .control import ControlListener
            self.control = ControlListener(cfg.cit.control_udp, self.mark_item, self.reset_cit)
        self.display: Optional[tuple] = None
        self.hide_scores = False          # interview mode: the subject must not see live scores
        self.on_key = None                # callback(key_code) for keys the engine doesn't use
        self.last_record: Dict[str, Optional[float]] = {}
        self.fps = 0.0
        self._last_t: Optional[float] = None
        self._prev_touch = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @classmethod
    def from_config(cls, cfg: VisionConfig, extra_sinks: Iterable[Sink] = ()) -> "VisionEngine":
        sinks: List[Sink] = list(extra_sinks)
        if cfg.output.stdout:
            sinks.append(StdoutSink())
        if cfg.output.jsonl_path:
            sinks.append(JsonlFileSink(cfg.output.jsonl_path))
        if cfg.output.udp:
            sinks.append(UdpSink(cfg.output.udp))
        return cls(cfg, sinks)

    # ------------------------------------------------------------------ per frame
    def process(self, frame: np.ndarray, t: float) -> FrameResult:
        t0 = time.perf_counter()
        obs = self.mesh.process(frame, t)
        rec: Dict[str, Optional[float]] = {"t": t, "face_present": 0.0}
        pose = bu = ff = None

        if obs is not None:
            pts = obs.points
            pose = self.head.estimate(pts, obs.width, obs.height, t)
            pitch, yaw = (pose.pitch, pose.yaw) if pose else (0.0, 0.0)
            ear_r = G.eye_aspect_ratio(pts, G.RIGHT_EYE_EAR)
            ear_l = G.eye_aspect_ratio(pts, G.LEFT_EYE_EAR)
            ear = self.blink.combine_ears(ear_r, ear_l, G.eye_width(pts, G.RIGHT_EYE_CORNERS),
                                          G.eye_width(pts, G.LEFT_EYE_CORNERS), yaw)
            bu = self.blink.update(t, ear, pitch, yaw)
            ff = self.face.update(obs, yaw, bu.state == EyeState.OPEN)
            rec.update({
                "face_present": 1.0, "ear": ear, "ear_right": ear_r, "ear_left": ear_l,
                "eye_openness": ear / bu.open_ear if bu.open_ear > 0 else None,   # 1 = this person's open eye
                "eye_closed": None if bu.state == EyeState.UNKNOWN else float(bu.state == EyeState.CLOSED),
                "mouth_open": ff.mouth_open, "lip_thickness": ff.lip_thickness,
                "brow_raise": ff.brow_raise, "brow_inner_gap": ff.brow_inner_gap,
                "facial_motion_energy": ff.facial_motion_energy,
                "gaze_h": ff.gaze_h, "gaze_v": ff.gaze_v,
                "gaze_offaxis": None if ff.gaze_offaxis is None else float(ff.gaze_offaxis),
                "gaze_shift": float(ff.gaze_shift), "yawn": float(ff.yawn),
            })
            if pose is not None:
                tx, ty, tz = pose.translation
                rec.update({"head_pitch": pose.pitch, "head_yaw": pose.yaw, "head_roll": pose.roll,
                            "head_speed": pose.angular_speed, "head_tx": tx, "head_ty": ty, "head_tz": tz})
            rec.update(blendshape_subset(obs.blendshapes))
            if bu.blink_completed is not None:
                self.pub.publish_event(t, "blink_event", bu.blink_completed)
            if bu.long_closure_completed is not None:
                self.pub.publish_event(t, "long_eye_closure_ms", bu.long_closure_completed)
            if ff.yawn:
                self.pub.publish_event(t, "yawn_event", 1.0)
        else:
            self.blink.mark_unknown()
            self.head.reset()
            self.face.reset_motion()

        if self.hands is not None:
            touch = self.hands.update(frame, t, obs.bbox(0.15) if obs else None)
            rec["face_touch"] = float(touch)
            if touch and not self._prev_touch:
                self.pub.publish_event(t, "face_touch_event", 1.0)
            self._prev_touch = touch

        if self._last_t is not None and t > self._last_t:
            inst = 1.0 / (t - self._last_t)
            self.fps = inst if self.fps == 0 else 0.9 * self.fps + 0.1 * inst
        self._last_t = t

        self.agg.add(rec)
        self.cit.add_frame(rec)
        self.last_record = rec
        if self.frames_csv is not None:
            cur = self.cit.current_item(t)
            self.frames_csv.write({**rec, "t": round(t, 4), "item": cur[0] if cur else "",
                                   "blink_event": 1 if bu and bu.blink_completed is not None else 0,
                                   "eye_state": int(bu.state) if bu else -1,
                                   "ear_close_thr": bu.close_threshold if bu else None})
        self.pub.publish_instant(t, self._instant_values(rec, t))
        window = None
        if self.agg.due(t):
            window = self.agg.compute(t, self.blink)
            self.pub.publish_window(t, window)
        items = self.cit.collect_ready(t, self.blink)
        for r in items:
            self.pub.publish_item(r.onset, r.item, r.valid, r.features, r.meta)
        for block in sorted({str(r.meta.get("block")) for r in items if r.meta.get("kind") == "test"}):
            self.pub.publish_cit_scores(t, self.cit.scores(block), block)
        return FrameResult(t, obs, pose, bu, ff, rec, window, self.fps,
                           (time.perf_counter() - t0) * 1000, items)

    # ------------------------------------------------------------------ CIT API (thread-safe)
    def mark_item(self, item: str, t: Optional[float] = None, meta: Optional[Dict[str, object]] = None,
                  duration: Optional[float] = None) -> None:
        """Call at the moment an item / question is shown. `t` = time.time() of display (default: now).
        `meta` = {"block", "series", "kind"} from interview.protocol; `duration` = response window (s)."""
        self.cit.mark_item(item, time.time() if t is None else t, meta, duration)
        log.debug("onset: %s %s", item, meta or "")

    def set_display(self, title: Optional[str], subtitle: str = "") -> None:
        """Text the overlay shows on top of the video (interview prompts). None = clear."""
        self.display = (title, subtitle) if title else None

    def reset_cit(self) -> None:
        self.cit.reset()
        log.info("CIT session reset")

    def _instant_values(self, rec: Dict[str, Optional[float]], t: float) -> Dict[str, Optional[float]]:
        keys = ("face_present", "ear", "eye_closed", "head_pitch", "head_yaw", "head_roll", "head_speed",
                "mouth_open", "lip_thickness", "brow_raise", "brow_inner_gap", "facial_motion_energy",
                "gaze_h", "gaze_offaxis", "face_touch")
        out = {k: rec.get(k) for k in keys if k in rec}
        out["blink_rate"] = self.blink.blink_rate_per_minute(t)
        out["fps"] = self.fps
        return out

    def recalibrate(self) -> None:
        self.agg.baseline.reset()
        log.info("Baseline reset - stay calm for %.0f s", self.cfg.agg.baseline_s)

    # ------------------------------------------------------------------ loops
    def run(self, camera: Optional[Camera] = None) -> None:
        """Blocking capture loop. Shows the overlay window if cfg.show_window."""
        overlay = None
        if self.cfg.show_window:
            import cv2
            from .overlay import Overlay
            overlay = Overlay(self, draw_mesh=self.cfg.draw_mesh)
        cam = camera or Camera(self.cfg.camera)
        cam.start()
        try:
            while not self._stop.is_set():
                item = cam.read()
                if item is None:
                    if not cam.is_live:
                        log.info("End of video")
                        break
                    continue
                frame, t = item
                res = self.process(frame, t)
                if overlay is not None:
                    cv2.imshow("VeriFi Vision", overlay.draw(frame, res))
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        break
                    if key == ord("b"):
                        self.recalibrate()
                    if key == ord("m"):
                        overlay.draw_mesh = not overlay.draw_mesh
                    if key != 255 and self.on_key is not None:
                        self.on_key(key)
        except KeyboardInterrupt:
            pass
        finally:
            cam.stop()
            if overlay is not None:
                cv2.destroyAllWindows()
            self.close()

    def start_background(self) -> threading.Thread:
        self._thread = threading.Thread(target=self.run, name="vision-engine", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def close(self) -> None:
        if self.control is not None:
            self.control.close()
        if self.frames_csv is not None:
            self.frames_csv.close()
        self.mesh.close()
        if self.hands is not None:
            self.hands.close()
        self.pub.close()
