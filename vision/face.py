"""Webcam + MediaPipe Face Landmarker features (owned by the CV group).

Imported by the backend, which runs it in a background thread. Don't open the
webcam anywhere else. Interface the backend expects:

    tracker = FaceTracker(camera_index=0)
    tracker.start()          # starts the capture thread
    tracker.latest()         # -> {"blink_rate": 18.0, "hand_on_face": False, ...}
    tracker.stop()

Add new keys to the dict freely; don't rename existing ones without telling the backend.
Model file: vision/face_landmarker.task (download it, it's gitignored; see README).
"""
