"""Webcam + MediaPipe Face Landmarker features (owned by the CV group).

Imported by the backend, which runs it in a background thread.

Don't open the webcam here: backend/camera.py already holds it (for the dashboard's
video), and only one program can. Take frames from it instead:

    tracker = FaceTracker(camera)   # camera = backend.camera.Camera, already started
    tracker.start()          # starts your processing thread; it calls camera.latest_frame()
                             # -> (capture time, BGR numpy frame) or None
    tracker.latest()         # -> {"blink_rate": 18.0, "hand_on_face": False, "lip_compression": 0.3, ...}
    tracker.stop()

Key names the dashboard already charts: blink_rate (per min), hand_on_face (bool),
lip_compression (0-1). For testing on your own, use cv2.VideoCapture directly, but
never while the backend is running.

Add new keys to the dict freely; don't rename existing ones without telling the backend.
Model file: vision/face_landmarker.task (download it, it's gitignored; see README).
"""
