"""VeriFi interview protocol + modality-independent CIT scoring (no vision / sensor dependencies)."""
from .scoring import BlockResult, Cue, CueParams, Epoch, calibrate, default_params, score_block

__all__ = ["BlockResult", "Cue", "CueParams", "Epoch", "calibrate", "default_params", "score_block"]
