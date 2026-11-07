"""
Live voice analysis with Parselmouth (Praat).

Flow:
  1. record() starts a ChunkRecorder that saves the microphone to short WAV
     chunks and calls analyze_chunks() on every finished chunk.
  2. analyze_chunks() extracts voice features from that chunk and saves them
     as a JSON file in outputs/temp/.
  3. stop_recording() stops the recorder, which merges all chunks into one
     file, then analyzes the whole recording and saves one JSON in outputs/final/.

Changes from the previous version:
  - extract_features() now accepts either a file path OR an in-memory numpy
    array, so live.py can call it on the microphone buffer.
  - New optional parameters: include_data_points, include_pauses,
    pitch_floor, pitch_ceiling, min_voiced_s, loudness_window_s.
    Defaults preserve the old standalone behaviour.
  - record_chunks is imported lazily inside record() so the module can be
    imported without the recorder dependency (needed for live.py).
  - Missing/NaN measurements are written to JSON as 0 instead of null,
    so downstream code never has to handle null.
"""

import parselmouth
from parselmouth import praat
import math
import os
import json
import time

from pathlib import Path
import numpy as np                              # for array input

# ---- Paths and settings ------------------------------------------------------
cwd = Path(__file__).resolve().parent
audio_directory = cwd / "audio"
datapoint_interval = 0.2

# Note from author: higher pitch results in more deception.


def load_audio_files(directory):
    """Return the full paths of every .wav file in `directory`. (Currently unused.)"""
    audio_files = []
    for filename in os.listdir(directory):
        if filename.endswith(".wav"):
            audio_files.append(os.path.join(directory, filename))
    return audio_files


def find_pauses(snd, min_pause=0.5, silence_db_below_max=25, min_pitch=100):
    """Find stretches of silence in a Parselmouth Sound."""
    intensity = snd.to_intensity(minimum_pitch=min_pitch)
    times = intensity.xs()
    db = intensity.values[0]
    threshold = db.max() - silence_db_below_max
    pauses, start = [], None

    for t, d in zip(times, db):
        if d < threshold and start is None:
            start = t
        elif d >= threshold and start is not None:
            if t - start >= min_pause:
                pauses.append((start, t))
            start = None

    if start is not None and snd.xmax - start >= min_pause:
        pauses.append((start, snd.xmax))
    return pauses


def extract_features(
    audio,
    sampling_frequency=None,      # required when audio is an array
    include_data_points=True,     # live use: False (expensive per-window loop)
    include_pauses=True,          # live use: False (needs whole file)
    pitch_floor=75.0,
    pitch_ceiling=500.0,
    min_voiced_s=0.0,             # live use: 0.15 (return zeros on silence)
    loudness_window_s=None,       # live use: 0.2 (reactive loudness)
):
    """Analyze one audio file OR an in-memory numpy array.

    Returns a dict of voice features, or None for audio too short to analyze.
    Missing/NaN measurements come back as 0.0, not None.
    Defaults reproduce the previous standalone behaviour; live.py passes
    flags to skip the expensive parts.
    """
    # --- Load audio (path or array) ---
    if isinstance(audio, (str, os.PathLike)):
        sound = parselmouth.Sound(audio)
    else:
        arr = np.asarray(audio, dtype=np.float64).flatten()
        fs = sampling_frequency or 16000
        sound = parselmouth.Sound(arr, sampling_frequency=fs)

    if sound.duration < 0.05:
        return None

    # --- Intensity (loudness) ---
    intensity = sound.to_intensity()
    if loudness_window_s is not None and loudness_window_s < sound.duration:
        t_end = sound.xmax
        t_start = t_end - loudness_window_s
        intensity_mean = praat.call(intensity, "Get mean", t_start, t_end, "dB")
    else:
        intensity_mean = praat.call(intensity, "Get mean", 0, 0, "dB")

    # --- Pitch + voiced-duration check ---
    pitch = sound.to_pitch(pitch_floor=pitch_floor, pitch_ceiling=pitch_ceiling)
    voiced = pitch.selected_array["frequency"] > 0
    voiced_duration = voiced.sum() * pitch.time_step

    # Silence sentinel (only when min_voiced_s > 0 — preserves old behaviour otherwise)
    if min_voiced_s > 0 and voiced_duration < min_voiced_s:
        return {
            "pitch_mean": 0.0, "pitch_min": 0.0, "pitch_max": 0.0,
            "intensity_mean": intensity_mean,   # real dB value, even when silent
            "jitter": 0.0, "shimmer": 0.0, "harmonicity": 0.0,
            "data_points": [], "pauses": [],
        }

    # --- Whole-file summary ---
    pitch_mean = praat.call(pitch, "Get mean", 0, 0, "Hertz")
    pitch_min  = praat.call(pitch, "Get minimum", 0, 0, "Hertz", "Parabolic")
    pitch_max  = praat.call(pitch, "Get maximum", 0, 0, "Hertz", "Parabolic")

    point_process = praat.call(sound, "To PointProcess (periodic, cc)", pitch_floor, pitch_ceiling)
    jitter  = praat.call(point_process, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3)
    shimmer = praat.call([sound, point_process], "Get shimmer (local)", 0, 0, 0.0001, 0.02, 1.3, 1.6)

    harmonicity = praat.call(sound, "To Harmonicity (cc)", 0.01, 75, 0.1, 1.0)
    hnr_mean = praat.call(harmonicity, "Get mean", 0.0, 0.0)

    # --- Optional per-window data ---
    data_points = []
    if include_data_points:
        n_windows = math.floor(sound.duration / datapoint_interval + 1e-6)
        if n_windows > 1:
            for i in range(n_windows):
                lo = i * datapoint_interval
                hi = (i + 1) * datapoint_interval
                data_points.append({
                    "pitch": praat.call(pitch, "Get mean", lo, hi, "Hertz"),
                    "intensity": praat.call(intensity, "Get mean", lo, hi, "dB"),
                    "jitter": praat.call(point_process, "Get jitter (local)", lo, hi, 0.0001, 0.02, 1.3),
                    "shimmer": praat.call([sound, point_process], "Get shimmer (local)", lo, hi, 0.0001, 0.02, 1.3, 1.6),
                    "harmonicity": praat.call(harmonicity, "Get mean", lo, hi),
                })

    # --- Optional pauses ---
    pauses = find_pauses(sound) if include_pauses else []

    return {
        "pitch_mean": pitch_mean,
        "pitch_min": pitch_min,
        "pitch_max": pitch_max,
        "intensity_mean": intensity_mean,
        "jitter": jitter,
        "shimmer": shimmer,
        "harmonicity": hnr_mean,
        "data_points": data_points,
        "pauses": pauses,
    }


def process_audio_files(directory):
    """Analyze every .wav file in `directory`. (Currently unused.)"""
    features_list = []
    for filename in os.listdir(directory):
        if filename.endswith(".wav"):
            audio_file = os.path.join(directory, filename)
            features = extract_features(audio_file)
            features["file"] = filename
            features_list.append(features)
    return features_list


def _nan_to_zero(value):
    """Praat gives NaN for silence and we may emit None for missing data;
    JSON has neither, so write 0 instead."""
    if value is None:
        return 0
    if isinstance(value, float) and math.isnan(value):
        return 0
    if isinstance(value, dict):
        return {k: _nan_to_zero(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_nan_to_zero(v) for v in value]
    return value


def analyze_chunks(path):
    """Callback for ChunkRecorder: writes features to outputs/temp/<chunk>.json."""
    data = extract_features(path)
    if data is None:
        return
    data.pop("data_points", None)
    with open(cwd / "outputs" / "temp" / f"{Path(path).stem}.json", "w") as json_file:
        json.dump(_nan_to_zero(data), json_file, indent=4)


def record(filename):
    """Start recording in the background and return the recorder."""
    from record_chunks import ChunkRecorder   # LAZY IMPORT (moved from module level)

    (cwd / "outputs" / "temp").mkdir(parents=True, exist_ok=True)
    (cwd / "outputs" / "final").mkdir(parents=True, exist_ok=True)

    for file_path in (cwd / "outputs" / "temp").iterdir():
        if file_path.is_file():
            file_path.unlink()

    rec = ChunkRecorder(
        output=audio_directory / "final" / f"{filename}.wav",
        chunk_dir=audio_directory / "temp",
        chunk_seconds=datapoint_interval,
        on_chunk=analyze_chunks,
    )
    rec.start()
    return rec


def stop_recording(rec):
    """Stop recording, analyze the merged file, save the results, return the JSON path."""
    path = rec.stop()
    if path is None:
        print("Nothing was recorded.")
        return None

    features_list = extract_features(path)
    filepath = cwd / "outputs" / "final" / f"{time.time()}.json"
    with open(filepath, "w") as json_file:
        json.dump(_nan_to_zero(features_list), json_file, indent=4)
    return filepath


def main():
    rec = record("filename")
    input("Enter to stop recording ")
    stop_recording(rec)


if __name__ == "__main__":
    main()
