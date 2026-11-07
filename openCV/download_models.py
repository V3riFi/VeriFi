"""Fetch the two MediaPipe face/hand models into ./vision (~12 MB, once). setup.bat runs this.

Current mediapipe releases ship only the Tasks API (no mp.solutions), which needs these .task files.
"""
import pathlib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

MODELS = {
    "face_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    "hand_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
}


def main() -> None:
    quiet = "--quiet" in sys.argv
    out = pathlib.Path(__file__).resolve().parent / "vision"
    out.mkdir(exist_ok=True)
    for name, url in MODELS.items():
        dst = out / name
        if dst.exists() and dst.stat().st_size > 0:
            if not quiet:
                print(f"[ok] {dst} already present")
            continue
        print(f"[..] downloading {name}")
        tmp = dst.with_suffix(".part")
        try:
            urllib.request.urlretrieve(url, tmp)
        except urllib.error.URLError:
            # python.org Python on a Mac often has no CA certificates installed
            # (CERTIFICATE_VERIFY_FAILED); curl uses the system's.
            if not shutil.which("curl"):
                raise
            subprocess.run(["curl", "-sSfL", "-o", str(tmp), url], check=True)
        tmp.replace(dst)                      # no half-written model if the download breaks
        print(f"[ok] {dst} ({dst.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
