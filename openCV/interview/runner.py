"""Plays an interview script in real time. Front-end agnostic: it only calls your callbacks.

    runner = InterviewRunner(steps, on_display=..., on_onset=..., check=...)
    threading.Thread(target=runner.run, daemon=True).start()
    runner.press_key()      # operator pressed SPACE (instructions / skip a wait)

on_display(title, subtitle)  - show text to the subject ("+" = fixation cross)
on_onset(step, t)            - a scored epoch starts NOW (t = time.time() when the text was shown)
check() -> (ok, message)     - setup check, polled while a setup_check step is active
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List, Optional, Sequence, Tuple

from .protocol import Step

log = logging.getLogger(__name__)


class InterviewRunner:
    SETUP_HOLD_S = 3.0              # setup must be OK continuously this long

    def __init__(self, steps: Sequence[Step], on_display: Callable[[str, str], None],
                 on_onset: Callable[[Step, float], None],
                 check: Optional[Callable[[], Tuple[bool, str]]] = None,
                 on_step: Optional[Callable[[int, Step], None]] = None):
        self.steps = list(steps)
        self.on_display, self.on_onset, self.check, self.on_step = on_display, on_onset, check, on_step
        self._key = threading.Event()
        self.stop_event = threading.Event()
        self.warnings: List[str] = []
        self.finished = threading.Event()
        self.current: Optional[Step] = None

    def press_key(self) -> None:
        self._key.set()

    def stop(self) -> None:
        self.stop_event.set()
        self._key.set()

    def _sleep(self, s: float) -> bool:
        """False if stopped."""
        return not self.stop_event.wait(max(0.0, s))

    def _setup(self, step: Step) -> None:
        if self.check is None:
            return
        deadline, ok_since = time.time() + step.duration, None
        while time.time() < deadline and not self.stop_event.is_set():
            ok, msg = self.check()
            self.on_display(step.title, msg)
            if ok:
                ok_since = ok_since or time.time()
                if time.time() - ok_since >= self.SETUP_HOLD_S:
                    return
            else:
                ok_since = None
            if self._key.is_set():                 # operator override
                self._key.clear()
                self.warnings.append(f"setup check skipped by operator: {msg}")
                return
            time.sleep(0.2)
        self.warnings.append("setup check timed out: " + (self.check()[1] if self.check else ""))

    def run(self) -> None:
        try:
            for i, step in enumerate(self.steps):
                if self.stop_event.is_set():
                    break
                self.current = step
                if self.on_step:
                    self.on_step(i, step)
                if step.kind == "setup_check":
                    self._setup(step)
                    continue
                if step.kind == "results":
                    self.on_display(step.title, step.subtitle)
                    break
                self._key.clear()
                t = time.time()
                self.on_display(step.title, step.subtitle)
                if step.scored_epoch:
                    self.on_onset(step, t)
                if step.wait_for_key:
                    self._key.wait(step.duration)
                    continue
                if step.display_s and step.display_s < step.duration:
                    if not self._sleep(step.display_s):
                        break
                    self.on_display("+", "")
                if not self._sleep(t + step.duration - time.time()):
                    break
        finally:
            self.finished.set()
