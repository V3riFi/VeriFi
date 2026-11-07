"""Demo HUD: mesh, eye/lip contours, iris, pose axes, live EAR trace, feature panel.

The EAR trace with the threshold line is the single best demo visual: judges
literally watch each blink dip below the line and the counter tick up.
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Optional

import cv2
import numpy as np

from . import geometry as G
from .blink import EyeState
from .landmarks import tesselation_edges

if TYPE_CHECKING:
    from .pipeline import FrameResult, VisionEngine

CYAN, GREEN, RED, YELLOW, WHITE, GREY = (255, 220, 0), (80, 220, 80), (60, 60, 240), (0, 220, 255), (240, 240, 240), (120, 120, 120)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def _fmt(v: Optional[float], spec: str = ".2f") -> str:
    return "--" if v is None else format(v, spec)


class Overlay:
    PANEL_W = 270

    def __init__(self, engine: "VisionEngine", draw_mesh: bool = True):
        self.engine = engine
        self.draw_mesh = draw_mesh
        self.edges = np.array(tesselation_edges(), dtype=np.int32)
        self.ear_hist: deque = deque(maxlen=150)     # ~5 s at 30 fps
        self.thr_hist: deque = deque(maxlen=150)

    def draw(self, frame: np.ndarray, res: "FrameResult") -> np.ndarray:
        img = frame.copy()
        if res.obs is not None:
            self._face(img, res)
        if res.blink is not None:
            self.ear_hist.append(res.blink.ear)
            self.thr_hist.append(res.blink.close_threshold)
        else:
            self.ear_hist.append(np.nan)
            self.thr_hist.append(np.nan)
        self._cit(img, res)
        return self._panel(img, res)

    # ------------------------------------------------------------------ CIT
    @staticmethod
    def _wrap(text: str, scale: float, thick: int, max_w: int):
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if cv2.getTextSize(trial, FONT, scale, thick)[0][0] <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        return lines + ([cur] if cur else [])

    def _banner(self, img: np.ndarray, title: str, subtitle: str = "", frac: Optional[float] = None) -> None:
        w = img.shape[1]
        big = 1.6 if title == "+" else 0.9
        t_lines = self._wrap(title, big, 2, w - 40)
        s_lines = self._wrap(subtitle, 0.5, 1, w - 40) if subtitle else []
        h = 20 + 34 * len(t_lines) + 20 * len(s_lines) + (8 if frac is not None else 0)
        region = img[8:8 + h, 8:w - 8]
        region[:] = (region * 0.25).astype(np.uint8)
        y = 8 + 34
        for ln in t_lines:
            tw = cv2.getTextSize(ln, FONT, big, 2)[0][0]
            cv2.putText(img, ln, ((w - tw) // 2, y), FONT, big, WHITE, 2, cv2.LINE_AA)
            y += 34
        for ln in s_lines:
            tw = cv2.getTextSize(ln, FONT, 0.5, 1)[0][0]
            cv2.putText(img, ln, ((w - tw) // 2, y - 8), FONT, 0.5, YELLOW, 1, cv2.LINE_AA)
            y += 20
        if frac is not None:
            cv2.rectangle(img, (8, 8 + h - 6), (8 + int((w - 16) * min(1.0, frac)), 8 + h - 2), CYAN, -1)

    def _cit(self, img: np.ndarray, res: "FrameResult") -> None:
        cit = self.engine.cit
        display = getattr(self.engine, "display", None)
        cur = cit.current_item(res.t)
        if display:
            frac = (cur[1] / cit.last_onset[2]) if cur and cit.last_onset else None
            self._banner(img, display[0], display[1], frac)
        elif cur is not None:
            self._banner(img, cur[0], "", cur[1] / cit.last_onset[2])
        if getattr(self.engine, "hide_scores", False):
            return                       # never show live scores to the subject during an interview
        n = len(cit.responses)
        if n != getattr(self, "_rank_n", -1):          # rescoring every frame is wasteful
            self._rank_n, self._ranking = n, cit.ranking()
        ranking = self._ranking
        if not ranking:
            return
        rows = ranking[:5]
        h = img.shape[0]
        top = h - 26 - 18 * len(rows)
        box = img[top - 8:h - 8, 8:250]
        box[:] = (box * 0.35).astype(np.uint8)
        cv2.putText(img, "CIT (vision) - stands out most:", (14, top + 8), FONT, 0.42, CYAN, 1, cv2.LINE_AA)
        for k, (item, score) in enumerate(rows):
            col = RED if k == 0 and score > 1.0 else WHITE
            cv2.putText(img, f"{item[:18]:<18} {score:+.2f}", (14, top + 28 + 18 * k), FONT, 0.45, col, 1,
                        cv2.LINE_AA)

    # ------------------------------------------------------------------ face
    def _face(self, img: np.ndarray, res: "FrameResult") -> None:
        pts = res.obs.points[:, :2].astype(np.int32)
        if self.draw_mesh and len(self.edges):
            lines = pts[self.edges].reshape(-1, 2, 2)
            cv2.polylines(img, list(lines), False, (90, 90, 90), 1, cv2.LINE_AA)
        eye_col = RED if res.blink and res.blink.state == EyeState.CLOSED else GREEN
        for contour in (G.RIGHT_EYE_CONTOUR, G.LEFT_EYE_CONTOUR):
            cv2.polylines(img, [pts[list(contour)]], True, eye_col, 1, cv2.LINE_AA)
        for contour in (G.LIPS_OUTER_CONTOUR, G.LIPS_INNER_CONTOUR):
            cv2.polylines(img, [pts[list(contour)]], True, CYAN, 1, cv2.LINE_AA)
        for brow in (G.RIGHT_BROW, G.LEFT_BROW):
            cv2.polylines(img, [pts[list(brow)]], False, YELLOW, 2, cv2.LINE_AA)
        if res.obs.has_iris:
            for c in (G.RIGHT_IRIS_CENTER, G.LEFT_IRIS_CENTER):
                cv2.circle(img, tuple(pts[c]), 2, WHITE, -1, cv2.LINE_AA)
        if res.pose is not None:
            ax = self.engine.head.project_axes(res.pose, res.obs.width, res.obs.height).astype(np.int32)
            o = tuple(pts[G.NOSE_TIP])
            shift = np.array(o) - ax[0]
            for p, col in zip(ax[1:], (RED, GREEN, (255, 80, 80))):
                cv2.line(img, o, tuple(p + shift), col, 2, cv2.LINE_AA)
        hands = getattr(self.engine.hands, "hand_points", None)
        if hands:
            col = RED if self.engine.hands.touching else WHITE
            for hand in hands:
                for x, y in hand:
                    cv2.circle(img, (int(x), int(y)), 2, col, -1)

    # ------------------------------------------------------------------ side panel
    def _panel(self, img: np.ndarray, res: "FrameResult") -> np.ndarray:
        h = img.shape[0]
        panel = np.full((h, self.PANEL_W, 3), 24, np.uint8)
        e = self.engine
        y = 24

        def line(label: str, value: str, col=WHITE) -> None:
            nonlocal y
            cv2.putText(panel, label, (10, y), FONT, 0.45, GREY, 1, cv2.LINE_AA)
            cv2.putText(panel, value, (150, y), FONT, 0.5, col, 1, cv2.LINE_AA)
            y += 20

        cv2.putText(panel, "VeriFi  VISION", (10, y), FONT, 0.6, CYAN, 2, cv2.LINE_AA)
        y += 26
        line("FPS / latency", f"{res.fps:4.1f} / {res.latency_ms:4.1f}ms")
        line("Face", "TRACKED" if res.obs is not None else "LOST", GREEN if res.obs is not None else RED)
        if res.blink:
            st = res.blink.state
            line("Eye state", st.name, {EyeState.OPEN: GREEN, EyeState.CLOSED: RED}.get(st, YELLOW))
            line("EAR / thr", f"{res.blink.ear:.3f} / {res.blink.close_threshold:.3f}")
        line("Blink rate /min", _fmt(e.blink.blink_rate_per_minute(res.t), ".1f"))
        line("Blinks (total)", str(len(e.blink.blinks)))
        if res.pose:
            line("Pitch/Yaw/Roll", f"{res.pose.pitch:+.0f} {res.pose.yaw:+.0f} {res.pose.roll:+.0f}")
            line("Head speed", f"{res.pose.angular_speed:5.1f} deg/s")
        if res.facial:
            f = res.facial
            line("Mouth open", f"{f.mouth_open:.3f}")
            line("Lip thickness", f"{f.lip_thickness:.3f}")
            line("Brow raise/gap", f"{f.brow_raise:.3f} / {f.brow_inner_gap:.3f}")
            line("Face motion", f"{f.facial_motion_energy:.3f}")
            line("Gaze h", _fmt(f.gaze_h), RED if f.gaze_offaxis else WHITE)
        if e.hands is not None:
            line("Face touch", "YES" if e.hands.touching else "no", RED if e.hands.touching else WHITE)

        # EAR trace
        y += 6
        gh, gw, gx = 70, self.PANEL_W - 20, 10
        cv2.rectangle(panel, (gx, y), (gx + gw, y + gh), (50, 50, 50), 1)
        cv2.putText(panel, "EAR (5 s)", (gx + 4, y + 12), FONT, 0.35, GREY, 1)
        to_y = lambda v: int(y + gh - np.clip(v / 0.4, 0, 1) * gh)
        ear = np.array(self.ear_hist, dtype=float)
        thr = np.array(self.thr_hist, dtype=float)
        n = len(ear)
        for i in range(1, n):
            if np.isfinite(ear[i - 1]) and np.isfinite(ear[i]):
                x0, x1 = gx + int((i - 1) * gw / 150), gx + int(i * gw / 150)
                cv2.line(panel, (x0, to_y(ear[i - 1])), (x1, to_y(ear[i])), GREEN, 1, cv2.LINE_AA)
                cv2.line(panel, (x0, to_y(thr[i - 1])), (x1, to_y(thr[i])), RED, 1)
        y += gh + 22

        # baseline + anomaly
        bl = e.agg.baseline
        if not bl.ready:
            cv2.putText(panel, "Calibrating baseline...", (10, y), FONT, 0.45, YELLOW, 1, cv2.LINE_AA)
            y += 8
            cv2.rectangle(panel, (10, y), (10 + int((self.PANEL_W - 20) * bl.progress), y + 10), YELLOW, -1)
        else:
            score = e.agg.last.get("vision_anomaly_score")
            col = GREEN if score is None or score < 1 else (YELLOW if score < 2 else RED)
            cv2.putText(panel, f"Anomaly score (z): {_fmt(score, '+.2f')}", (10, y), FONT, 0.5, col, 1, cv2.LINE_AA)
            y += 8
            if score is not None:
                mid = 10 + (self.PANEL_W - 20) // 2
                end = int(mid + np.clip(score / 4, -1, 1) * (self.PANEL_W - 20) / 2)
                cv2.rectangle(panel, (min(mid, end), y), (max(mid, end), y + 10), col, -1)
                cv2.line(panel, (mid, y - 2), (mid, y + 12), WHITE, 1)
        cv2.putText(panel, "[q] quit  [b] recalibrate  [m] mesh", (10, h - 10), FONT, 0.38, GREY, 1)
        return np.hstack([img, panel])
