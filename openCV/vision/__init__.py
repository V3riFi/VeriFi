"""VeriFi vision subsystem: webcam -> numerical stress-related facial features."""
from .config import VisionConfig
from .output import CallbackSink, QueueSink, Sink, UdpSink

__all__ = ["VisionConfig", "VisionEngine", "Sink", "QueueSink", "CallbackSink", "UdpSink"]


def __getattr__(name):  # lazy: importing the package shouldn't load MediaPipe
    if name == "VisionEngine":
        from .pipeline import VisionEngine
        return VisionEngine
    raise AttributeError(name)
