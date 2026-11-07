"""What the fusion team runs to consume vision output over UDP.

    python -m vision.main --udp 127.0.0.1:5005      (terminal 1)
    python examples/fusion_receiver.py               (terminal 2)
"""
import json
import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("127.0.0.1", 5005))
print("listening on udp://127.0.0.1:5005")

latest = {}   # feature -> (timestamp, value): the fusion engine's "current vision state"
while True:
    msg = json.loads(sock.recvfrom(65536)[0])
    if "window_features" in msg:
        wf = msg["window_features"]
        print(f"[window {msg['timestamp']:.1f}] blink_rate={wf.get('blink_rate')} "
              f"head_motion={wf.get('head_motion_energy')} anomaly={wf.get('vision_anomaly_score')}")
    else:
        latest[msg["feature"]] = (msg["timestamp"], msg["value"])
        if msg["feature"].endswith("_event"):
            print(f"  event {msg['feature']} = {msg['value']:.0f}")
