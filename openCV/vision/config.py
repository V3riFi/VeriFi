"""Central configuration for the VeriFi vision subsystem.

Every tunable lives here so nobody has to grep through modules at 3 AM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Union


@dataclass
class CameraConfig:
    source: Union[int, str] = 0          # webcam index or path to a video file (demo fallback!)
    width: int = 640                     # 640x480 is the sweet spot for CPU Face Mesh
    height: int = 480
    fps: int = 30
    mirror: bool = True                  # selfie view; all features are mirror-agnostic
    reconnect_after_failures: int = 30   # consecutive failed reads before reopening the device


@dataclass
class MeshConfig:
    backend: str = "auto"                # "auto" | "tasks" | "legacy"
    model_path: str = "vision/face_landmarker.task"
    min_detection_confidence: float = 0.5
    min_tracking_confidence: float = 0.5


@dataclass
class BlinkConfig:
    # Adaptive thresholds: relative to this person's own open-eye EAR.
    close_ratio: float = 0.72            # CLOSED when EAR < open_ear * close_ratio
    reopen_ratio: float = 0.82           # OPEN again when EAR > open_ear * reopen_ratio (hysteresis)
    min_close_threshold: float = 0.12    # absolute clamps on the closing threshold
    max_close_threshold: float = 0.28
    default_open_ear: float = 0.30       # used until enough open-eye samples exist
    baseline_window_s: float = 10.0      # rolling window for the open-eye EAR estimate
    baseline_percentile: float = 80.0    # "typical open eye" = 80th percentile of recent EAR
    min_blink_ms: float = 50.0           # shorter dips = landmark jitter
    max_blink_ms: float = 500.0          # longer = deliberate/long eye closure, not a blink
    refractory_ms: float = 80.0          # min gap between two blinks
    rate_window_s: float = 30.0          # trailing window for blinks/minute
    max_abs_pitch_deg: float = 25.0      # EAR unreliable when looking far down/up -> gate
    max_abs_yaw_deg: float = 35.0
    near_eye_yaw_deg: float = 15.0       # beyond this yaw use only the eye nearer the camera


@dataclass
class HeadPoseConfig:
    one_euro_min_cutoff: float = 1.0     # lower = smoother at rest
    one_euro_beta: float = 0.05          # higher = less lag during fast motion
    reversal_deadband_dps: float = 8.0   # ignore velocity sign flips below this speed (deg/s)


@dataclass
class GazeConfig:
    center_samples: int = 150            # frames used to learn this person's "looking at camera" iris position
    aversion_threshold: float = 0.10     # |gaze_h - center| above this = eyes off-axis
    aversion_yaw_deg: float = 20.0       # or head turned beyond this
    shift_velocity: float = 1.2          # normalized gaze units / s that counts as a gaze shift
    shift_refractory_ms: float = 150.0


@dataclass
class AggregationConfig:
    window_s: float = 5.0                # emit window_features every N seconds
    instant_rate_hz: float = 10.0        # throttle for per-feature instant messages
    baseline_s: float = 45.0             # seconds of calm data used as personal baseline
    min_presence_for_baseline: float = 0.6
    z_clip: float = 4.0


@dataclass
class OutputConfig:
    stdout: bool = False                 # JSON lines to stdout
    jsonl_path: Optional[str] = None     # JSON lines to file (great for replay / debugging)
    udp: Optional[str] = None            # "127.0.0.1:5005" -> fusion engine
    frames_csv: Optional[str] = None     # per-frame debug CSV (EAR, blink score, pose, current item)
    emit_instant: bool = True
    emit_window: bool = True
    emit_events: bool = True             # blink / yawn / face-touch events as they happen


def _default_cit_weights() -> Dict[str, float]:
    # Multipliers on the prior cue weights defined in vision/cit.py (VISION_CUES). 1.0 = keep the prior,
    # 0 = turn a cue off. Example: {"face_touch_ratio": 0.0} if hand detection is disabled.
    return {}


@dataclass
class CitConfig:
    """Concealed Information Test: per-item response windows locked to item onset."""
    pre_s: float = 1.0                   # reference window before the item appears
    post_s: float = 5.0                  # response window after the item appears
    early_s: float = 2.0                 # blink-suppression window (0..early_s after onset)
    min_presence: float = 0.6            # epoch is invalid if the face is tracked less than this
    z_clip: float = 4.0
    control_udp: Optional[str] = None    # "127.0.0.1:5006": listen for item-onset commands
    weights: Dict[str, float] = field(default_factory=_default_cit_weights)


def _default_weights() -> Dict[str, float]:
    # Sign encodes direction: +z on these features = more stress-like.
    return {
        "blink_rate": 1.0,
        "head_motion_energy": 1.0,
        "facial_motion_energy": 0.75,
        "lip_compression": 0.75,
        "brow_furrow": 0.75,
        "gaze_aversion_ratio": 0.75,
        "head_reversal_rate": 0.5,
        "face_touch_ratio": 0.5,
    }


@dataclass
class VisionConfig:
    camera: CameraConfig = field(default_factory=CameraConfig)
    mesh: MeshConfig = field(default_factory=MeshConfig)
    blink: BlinkConfig = field(default_factory=BlinkConfig)
    head: HeadPoseConfig = field(default_factory=HeadPoseConfig)
    gaze: GazeConfig = field(default_factory=GazeConfig)
    agg: AggregationConfig = field(default_factory=AggregationConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    cit: CitConfig = field(default_factory=CitConfig)
    show_window: bool = True
    draw_mesh: bool = True
    enable_hands: bool = True            # hand-over-face is one of the team's listed face signals
    hands_every_n_frames: int = 2
    anomaly_weights: Dict[str, float] = field(default_factory=_default_weights)
