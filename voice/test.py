"""
Live voice analysis with Parselmouth (Praat).

Flow:
  1. record() starts a ChunkRecorder that saves the microphone to short WAV
     chunks and calls analyze_chunks() on every finished chunk.
  2. analyze_chunks() extracts voice features from that chunk and saves them
     as a JSON file in outputs/temp/.
  3. stop_recording() stops the recorder, which merges all chunks into one
     file, then analyzes the whole recording and saves one JSON in outputs/final/.
"""

import parselmouth
from parselmouth import praat          # lets us call Praat commands by name: praat.call(...)
import math
import os
import json
import time

from pathlib import Path
from record_chunks import ChunkRecorder

# ---- Paths and settings ------------------------------------------------------
cwd = Path(__file__).resolve().parent      # folder this script lives in
audio_directory = cwd / "audio"            # audio/temp = live chunks, audio/final = merged recording
datapoint_interval = 0.2                   # seconds: used both as the chunk length and the
                                           # window size for per-window data points

# Note from author: higher pitch results in more deception.

def load_audio_files(directory):
    """Return the full paths of every .wav file in `directory`. (Currently unused.)"""
    audio_files = []
    for filename in os.listdir(directory):
        if filename.endswith(".wav"):
            audio_files.append(os.path.join(directory, filename))
    return audio_files

def find_pauses(snd, min_pause=0.5, silence_db_below_max=25, min_pitch=100):
    """Find stretches of silence in a Parselmouth Sound.

    Returns a list of (start, end) times in seconds for every silence that
    lasts at least `min_pause` seconds.
    A moment counts as silent when its loudness is more than
    `silence_db_below_max` dB below the loudest moment in the sound.
    """
    # Loudness (dB) over time; min_pitch sets how long the analysis window is
    intensity = snd.to_intensity(minimum_pitch=min_pitch)
    times = intensity.xs()          # time of each loudness sample
    db = intensity.values[0]        # loudness at each of those times

    # Anything quieter than this threshold is treated as silence
    threshold = db.max() - silence_db_below_max
    pauses, start = [], None        # `start` is None while we are not inside a silence

    for t, d in zip(times, db):
        if d < threshold and start is None:
            # Quiet and not already in a silence -> a silence begins here
            start = t
        elif d >= threshold and start is not None:
            # Sound resumed -> the silence ends here; keep it only if it was long enough
            if t - start >= min_pause:
                pauses.append((start, t))
            start = None

    # If the sound ends while still silent, close out that last silence
    if start is not None and snd.xmax - start >= min_pause:
        pauses.append((start, snd.xmax))
    return pauses

def extract_features(audio_file):
    """Analyze one audio file and return a dict of voice features.

    Whole-file features: pitch mean/min/max, mean intensity, jitter, shimmer, pauses.
    Also includes `data_points`: the same measures (except min/max) for each
    consecutive `datapoint_interval`-second window.
    """
    sound = parselmouth.Sound(audio_file)
    pitch = sound.to_pitch()            # fundamental frequency (F0) over time
    intensity = sound.to_intensity()    # loudness (dB) over time

    # --- Whole-file summary values (0, 0 means "from start to end") ---
    pitch_mean = praat.call(pitch, "Get mean", 0, 0, "Hertz")
    pitch_min = praat.call(pitch, "Get minimum", 0, 0, "Hertz", "Parabolic")   # parabolic = interpolated, more precise
    pitch_max = praat.call(pitch, "Get maximum", 0, 0, "Hertz", "Parabolic")
    intensity_mean = praat.call(intensity, "Get mean", 0, 0, "energy")         # "energy" = average on the power scale

    # Mark each glottal pulse (one vocal-fold cycle). 75-500 Hz is the pitch search range.
    # Jitter and shimmer are computed from these pulses.
    point_process = praat.call(sound, "To PointProcess (periodic, cc)", 75, 500)

    # Jitter: cycle-to-cycle variation in pitch period.
    # Args: from, to, shortest period (s), longest period (s), max period factor
    jitter = praat.call(point_process, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3)
    # Shimmer: cycle-to-cycle variation in amplitude (needs the sound too).
    # Same args as jitter plus max amplitude factor.
    shimmer = praat.call([sound, point_process], "Get shimmer (local)", 0, 0, 0.0001, 0.02, 1.3, 1.6)

    # --- Per-window values ---
    data_points = []

    # Only do this when the file holds at least two full windows
    # NOTE: for a single 0.2 s chunk, floor(0.2 / 0.2) = 1, so no windows are produced
    if math.floor(sound.duration / datapoint_interval) > 1:
        for i in range(0, math.floor(sound.duration / datapoint_interval)):
            # Window i covers [i * interval, (i + 1) * interval] seconds
            pitch_point = praat.call(pitch, "Get mean", i * datapoint_interval, (i + 1) * datapoint_interval, "Hertz")
            intensity_point = praat.call(intensity, "Get mean", i * datapoint_interval, (i + 1) * datapoint_interval, "energy")
            jitter_point = praat.call(point_process, "Get jitter (local)", i * datapoint_interval, (i + 1) * datapoint_interval, 0.0001, 0.02, 1.3)
            shimmer_point = praat.call([sound, point_process], "Get shimmer (local)", i * datapoint_interval, (i + 1) * datapoint_interval, 0.0001, 0.02, 1.3, 1.6)

            data_points.append(
                {
                    "pitch": pitch_point,
                    "intensity": intensity_point,
                    "jitter": jitter_point,
                    "shimmer": shimmer_point,
                }
            )

    # Long silences (default: 0.5 s or more) as (start, end) pairs
    pauses = find_pauses(sound)

    return {
        "pitch_mean": pitch_mean,
        "pitch_min": pitch_min,
        "pitch_max": pitch_max,
        "intensity_mean": intensity_mean,
        "jitter": jitter,
        "shimmer": shimmer,
        "data_points": data_points,
        "pauses": pauses,
    }

def process_audio_files(directory):
    """Analyze every .wav file in `directory`; return a list of feature dicts,
    each tagged with its file name. (Currently unused.)"""
    features_list = []
    for filename in os.listdir(directory):
        if filename.endswith(".wav"):
            audio_file = os.path.join(directory, filename)
            features = extract_features(audio_file)
            features["file"] = filename
            features_list.append(features)

    return features_list

def analyze_chunks(path):
    """Callback for ChunkRecorder: runs once for each finished chunk.

    Analyzes the chunk at `path` and writes the features to
    outputs/temp/<chunk name>.json (the outputs/temp folder must already exist).
    """
    data = extract_features(path)

    with open(cwd / "outputs" / "temp" / f"{Path(path).stem}.json", "w") as json_file:
        json.dump(data, json_file, indent=4)

def record(filename):
    """Start recording in the background and return the recorder.

    `filename` is the name for the final merged recording (without extension).
    """
    # Clear leftover per-chunk JSON files from the previous session
    # (fails if outputs/temp doesn't exist)
    for file_path in (cwd / "outputs" / "temp").iterdir():
        if file_path.is_file():
            file_path.unlink()

    rec = ChunkRecorder(
        # Where the merged recording goes.
        output=audio_directory / "final" / f"{filename}.wav",
        chunk_dir=audio_directory / "temp",   # where the live WAV chunks appear
        chunk_seconds=datapoint_interval,     # each chunk is 0.2 s long
        on_chunk=analyze_chunks,              # called for every finished chunk
    )

    rec.start()     # returns immediately; recording continues in the background

    return rec

def stop_recording(rec):
    """Stop recording, analyze the merged file, save the results, return the JSON path."""
    path = rec.stop()   # stops the mic, merges the chunks, returns the merged file's path

    features_list = extract_features(path)   # analyze the complete recording

    # Name the results file with the current Unix timestamp so each run is unique
    filepath = cwd / "outputs" / "final" / f"{time.time()}.json"

    with open(filepath, "w") as json_file:
        json.dump(features_list, json_file, indent=4)

    return filepath

def main():
    rec = record("filename")

    input("Enter to stop recording ")   # blocks until you press Enter

    stop_recording(rec)

if __name__ == "__main__":
    main()