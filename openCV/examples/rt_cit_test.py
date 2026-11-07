"""VeriFi reaction-time CIT (the new primary test). Keyboard + optional webcam recording.

    run_rtcit.bat                      (~6 min incl. practice)
    run_rtcit.bat --no-camera          (cleanest timing; no face recording)

Before starting: secretly pick a CARD (Ace of Spades, 7 of Hearts, Queen of Clubs, 3 of Diamonds,
King of Hearts) and write it on paper. Don't type it. You'll type your date of birth: it is used
for a check round whose answer the system knows, and is never saved.

Task: words appear one at a time. Press  E = YES  or  I = NO  as FAST as you can.
  YES for the one item you're told to look for, and for FAMILIAR / MINE / RECOGNIZED.
  NO for everything else, INCLUDING your own birthday and your own card.
Esc = quit.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import pathlib
import random
import sys
import time
import tkinter as tk
from typing import Callable, Iterator, List, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from interview.facts import _date_foils, fmt_day, parse_birthday  # noqa: E402
from interview.protocol import CARDS  # noqa: E402
from interview.rtcit import (FAMILIAR_FILLERS, NO, YES, RtBlock, RtResult, RtTrial, build_trials,  # noqa: E402
                             practice_fillers, practice_targets, practice_trials, score_rt)

BG, FG, DIM = "#101014", "#f2f2f2", "#9a9aa5"


def unique_path(p: pathlib.Path) -> pathlib.Path:
    """data/identity.trials.csv -> data/identity_2.trials.csv if it already exists, and so on.
    Multi-part extensions stay together, so a session's .frames.csv, .slow.csv and .results.json
    get matching names."""
    if not p.exists():
        return p
    base, dot, ext = p.name.partition(".")
    n = 2
    while p.with_name(f"{base}_{n}{dot}{ext}").exists():
        n += 1
    return p.with_name(f"{base}_{n}{dot}{ext}")


# ---------------------------------------------------------------------- blocks
def birthday_block(birthday: dt.date, rng: random.Random, cycles: int = 10) -> RtBlock:
    foils = _date_foils(birthday, 5, rng, same_month=False)
    cands = [("TRUE", fmt_day(birthday))] + [(f"F{i + 1}", d) for i, d in enumerate(foils[:4])]
    return RtBlock.single("birthday_check", cands, ("YESITEM", foils[4]), cycles=cycles, known_label="TRUE",
                          category="date")


def card_block(cycles: int = 18) -> RtBlock:
    return RtBlock.single("cards", [(c, c) for c in CARDS], ("Jack of Diamonds", "Jack of Diamonds"),
                          cycles=cycles, category="card")


# ---------------------------------------------------------------------- window
class TaskWindow:
    """Tk window driven by a generator script. Timing uses time.perf_counter (sub-ms); onsets are
    also logged in time.time() so they line up with the vision frame CSV and the other sensors."""

    def __init__(self, script: Callable[["TaskWindow"], Iterator], status: Optional[Callable[[], str]] = None,
                 on_onset: Optional[Callable[[object], None]] = None):
        self.on_onset = on_onset          # called with each slow-round item the moment it's drawn
        self.flash = None
        self.root = tk.Tk()
        self.root.title("VeriFi RT test")
        self.root.configure(bg=BG)
        self.root.geometry("1000x600")
        self.big = tk.Label(self.root, text="", fg=FG, bg=BG, font=("Segoe UI", 44, "bold"), wraplength=900)
        self.big.place(relx=0.5, rely=0.42, anchor="center")
        self.small = tk.Label(self.root, text="", fg=DIM, bg=BG, font=("Segoe UI", 15), wraplength=900,
                              justify="center")
        self.small.place(relx=0.5, rely=0.75, anchor="center")
        self.status_lbl = tk.Label(self.root, text="", fg=DIM, bg=BG, font=("Segoe UI", 11))
        self.status_lbl.place(relx=0.5, rely=0.96, anchor="center")
        self.status = status
        self.gen = script(self)
        self.wait_keys: set = set()
        self.trial: Optional[RtTrial] = None
        self.t0 = 0.0
        self.timer = None
        self.aborted = False
        self.root.bind("<KeyPress>", self._on_key)
        self.root.protocol("WM_DELETE_WINDOW", self.abort)
        self.root.bind("<Button-1>", lambda e: self.root.focus_force())
        self.root.after(200, self._step)
        self.root.after(500, self._tick)
        for ms in (100, 700):                      # the console keeps focus on Windows otherwise
            self.root.after(ms, self._grab_focus)

    def _grab_focus(self) -> None:
        """Bring the window to the front with keyboard focus. Windows refuses SetForegroundWindow
        to a background process unless a key event just happened, hence the ALT tap."""
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
            if sys.platform == "win32":
                import ctypes
                u = ctypes.windll.user32
                hwnd = int(self.root.wm_frame(), 16)
                u.keybd_event(0x12, 0, 0, 0)       # ALT down
                u.SetForegroundWindow(hwnd)
                u.keybd_event(0x12, 0, 2, 0)       # ALT up
            self.root.focus_force()
        except Exception:                          # focus is a convenience; clicking the window also works
            pass

    def run(self) -> None:
        self.root.mainloop()

    def abort(self) -> None:
        self.aborted = True
        self.root.destroy()

    def _tick(self) -> None:
        if self.status:
            try:
                self.status_lbl.config(text=self.status())
            except Exception:              # status is cosmetic; never let it break the task
                pass
        self.root.after(500, self._tick)

    def _show(self, big: str, small: str = "", color: str = FG) -> None:
        self.big.config(text=big, fg=color)
        self.small.config(text=small)

    def _step(self, value=None) -> None:
        try:
            cmd = self.gen.send(value)
        except StopIteration:
            return
        except Exception:
            # Never freeze silently on a "press SPACE" screen: show the error and print it.
            import traceback
            traceback.print_exc()
            self._show("Internal error", "The interview script crashed (details in the console).\n"
                       "Press Esc to close; the trials so far are saved.", "#ff5a5a")
            self.wait_keys = set()
            return
        kind = cmd[0]
        if kind == "screen":                       # ("screen", big, small, keys)
            self._show(cmd[1], cmd[2])
            self.wait_keys = set(cmd[3])
        elif kind == "pause":                      # ("pause", seconds, big, color)
            self._show(cmd[2] if len(cmd) > 2 else "", "", cmd[3] if len(cmd) > 3 else FG)
            self.root.after(int(cmd[1] * 1000), self._step)
        elif kind == "trial":                      # ("trial", RtTrial, deadline_s)
            self.trial = cmd[1]
            self._show(self.trial.text)
            self.root.update_idletasks()
            self.t0 = time.perf_counter()
            self.trial.onset = time.time()
            self.timer = self.root.after(int(cmd[2] * 1000), self._too_slow)
        elif kind == "flash":                      # ("flash", SlowItem): sensor round, fixed timing
            it = self.flash = cmd[1]
            self._show(it.text)
            self.root.update_idletasks()
            self.t0 = time.perf_counter()
            it.onset = time.time()
            if self.on_onset:
                try:
                    self.on_onset(it)
                except Exception as e:             # a sensor hiccup must not stop the interview
                    print("onset hook error:", e)
            self.root.after(int(it.display_s * 1000), lambda: self._show("+"))
            self.root.after(int(it.total_s * 1000), self._end_flash)

    def _end_flash(self) -> None:
        self.flash = None
        self._step()

    def _too_slow(self) -> None:
        if self.trial is None:
            return
        self.trial.outcome = "slow"
        self.trial = None
        self._step("slow")

    def _on_key(self, ev) -> None:
        now = time.perf_counter()
        k = ev.keysym.lower()
        if k == "escape":
            self.abort()
            return
        if self.flash is not None:
            if k in (YES, NO) and self.flash.key is None:
                self.flash.key, self.flash.rt = k, now - self.t0
            return
        if self.trial is not None:
            if k not in (YES, NO):
                return
            t, self.trial = self.trial, None
            self.root.after_cancel(self.timer)
            t.rt, t.key = now - self.t0, k
            t.outcome = "correct" if k == t.correct_key else "wrong"
            self._step(t.outcome)
        elif k in self.wait_keys:
            self.wait_keys = set()
            self._step(k)


# ---------------------------------------------------------------------- script
def run_trials(trials: List[RtTrial], deadline: float, rng: random.Random) -> Iterator:
    for t in trials:
        yield ("pause", rng.uniform(0.25, 0.75))
        out = yield ("trial", t, deadline)
        if out == "wrong":
            yield ("pause", 0.5, "WRONG", "#ff5a5a")
        elif out == "slow":
            yield ("pause", 0.5, "TOO SLOW", "#ffb020")


def instructions(b: RtBlock) -> str:
    own = b.hide or (f"your own {b.category}" if len(b.groups) == 1 else "your own real details")
    return (f"Press  E = YES  for:   {'   '.join(b.targets)}   and for the words  {', '.join(FAMILIAR_FILLERS)}\n"
            f"Press  I = NO  for everything else, INCLUDING {own}.\n"
            "Be as fast as you can. Mistakes happen; just keep going.")


def remember_title(b: RtBlock) -> str:
    if len(b.groups) == 1:
        return f"remember this YES {b.category}\n{b.targets[0]}"
    return "remember these YES words\n" + "\n".join(
        f"{g.name.replace('_', ' ')}:  {g.target[1]}" for g in b.groups)


def _acc(trials: List[RtTrial], roles=None) -> float:
    ts = [t for t in trials if roles is None or t.role in roles]
    return sum(t.outcome == "correct" for t in ts) / len(ts) if ts else 1.0


def practice_stage(title: str, rule: str, make, deadline: float, passed, rng: random.Random,
                   max_auto: int = 3) -> Iterator:
    """Run a practice set; repeat it automatically until `passed`, then let the subject move on."""
    yield ("screen", title, rule + "\n\nPress SPACE to start.", {"space"})
    for attempt in range(1, 100):
        pr = make()
        yield from run_trials(pr, deadline, rng)
        ok = passed(pr)
        if ok:
            yield ("screen", f"{title}: {_acc(pr):.0%} correct, well done", "Press SPACE to continue.", {"space"})
            return
        if attempt < max_auto:
            yield ("screen", f"{title}: {_acc(pr):.0%} correct, let's do it once more", rule +
                   "\n\nPress SPACE to try again.", {"space"})
            continue
        k = yield ("screen", f"{title}: {_acc(pr):.0%} correct", rule +
                   "\n\nR = practise again (recommended)      SPACE = move on anyway", {"space", "r"})
        if k == "space":
            return


def slow_round(items: list, settle_s: float = 20.0, title: str = "Sensor round (slow)",
               own: str = "your own details", nxt: str = "the fast keyboard round") -> Iterator:
    """Sensor round: fixed slow timing so skin conductance / breathing / heart rate can respond."""
    yield ("screen", title,
           "Words appear every ~7 seconds, each for 3 seconds.\n"
           f"Read each one and press I (NO) once with your free hand, also for {own}.\n"
           "Keep the sensor hand still, breathe normally, don't talk.\n\n"
           f"First {settle_s:.0f} s of rest so the sensors settle. Press SPACE.\n"
           "(If keys do nothing, click this window once.)", {"space"})
    yield ("pause", settle_s, "+")
    for it in items:
        yield ("flash", it)
    yield ("screen", "Sensor round done", f"Next: {nxt}. Press SPACE.", {"space"})


def slow_rounds(slow) -> List[dict]:
    """`slow` = a list of SlowItems (one round) or a list of dicts {items, title, own, settle_s, next}."""
    if not slow:
        return []
    if isinstance(slow[0], dict):
        return list(slow)
    return [{"items": slow}]


def make_script(blocks: List[RtBlock], rng: random.Random, done: List[RtTrial], slow: Optional[list] = None):
    def script(win: TaskWindow) -> Iterator:
        for k, r in enumerate(slow_rounds(slow)):
            yield from slow_round(r["items"], r.get("settle_s", 20.0 if k == 0 else 10.0),
                                  r.get("title", "Sensor round (slow)"), r.get("own", "your own details"),
                                  r.get("next", "the fast keyboard round"))
        yield ("screen", "VeriFi reaction-time test",
               "Words appear one at a time. Answer each with one key, as FAST as you can:\n"
               "E = YES (left hand)        I = NO (right hand)\n\n"
               "Rest two fingers on E and I. Look at the centre of the screen.\nPress SPACE to start.\n"
               "(If keys do nothing, click this window once.)", {"space"})
        for n, b in enumerate(blocks):
            if n == 0:
                yield ("screen", "How it works",
                       "Every word asks one question:  is this FAMILIAR to you?\n\n"
                       "Words that MEAN familiar (FAMILIAR, MINE, RECOGNIZED)  ->  E = YES\n"
                       "Words that MEAN unfamiliar (UNFAMILIAR, OTHER, FOREIGN, THEIRS, RANDOM, UNKNOWN)  ->  I = NO\n\n" +
                       (f"{b.hide[0].upper() + b.hide[1:]}: you HIDE it  ->  I = NO\n" if b.hide else
                        f"Your own real {'details' if len(b.groups) > 1 else b.category}: you HIDE them  ->  I = NO\n") +
                       "A few words you'll be told to remember  ->  E = YES\n\n"
                       "We'll learn this in 3 short practice steps. Press SPACE.", {"space"})
                # the filler rule is the same in every part: learn it once
                yield from practice_stage(
                    "Practice 1 of 3: just the meaning words", "FAMILIAR, MINE, RECOGNIZED -> E (YES)\n"
                    "UNFAMILIAR, OTHER, FOREIGN, THEIRS, RANDOM, UNKNOWN -> I (NO)\nNo rush yet.",
                    lambda: practice_fillers(b, rng), 2.0, lambda pr: _acc(pr) >= 0.8, rng)
            else:
                yield ("screen", f"Part {n + 1} of {len(blocks)}: new YES words",
                       "Same rules as before. Other details now, so there are new words to remember.\n"
                       "The earlier ones won't appear any more.\nPress SPACE.", {"space"})
            yield ("screen", f"Part {n + 1} of {len(blocks)}: {remember_title(b)}",
                   "Say them out loud once. Press E (YES) whenever one of these appears.\n"
                   "Press SPACE when you've memorized them.", {"space"})
            yield from practice_stage(
                "Practice 2 of 3: your YES words", remember_title(b).split("\n", 1)[-1] +
                "\n\nThese -> E (YES).  Other options -> I (NO).", lambda: practice_targets(b, rng), 2.0,
                lambda pr: _acc(pr, ("target",)) >= 0.8 and _acc(pr, ("candidate",)) >= 0.8, rng)
            yield from practice_stage(
                "Practice 3 of 3: everything, faster", instructions(b), lambda: practice_trials(b, rng, 30), 1.0,
                lambda pr: _acc(pr) >= 0.75 and _acc(pr, ("target", "filler_yes")) >= 0.7, rng)
            yield ("screen", "Real round", instructions(b) + "\n\nNow 0.8 s per word. Press SPACE to start.",
                   {"space"})
            trials = build_trials(b, rng)
            half = len(trials) // 2 if len(trials) > 240 else len(trials)   # long block: pause midway
            yield from run_trials(trials[:half], b.deadline_s, rng)
            done.extend(trials[:half])
            if half < len(trials):
                yield ("screen", "Halfway: short break", "Same rules:\n" + instructions(b) +
                       "\n\nPress SPACE to continue.", {"space"})
                yield from run_trials(trials[half:], b.deadline_s, rng)
                done.extend(trials[half:])
            if n + 1 < len(blocks):
                yield ("screen", "Short break", "Press SPACE when ready.", {"space"})
        yield ("screen", "Done", "Results are in the console. Press SPACE to close.", {"space"})
        win.root.destroy()
    return script


# ---------------------------------------------------------------------- report
def _f(v, spec: str) -> str:
    return "--" if v is None else format(v, spec)


def describe(r: RtResult, names: dict) -> List[str]:
    lines = []
    for lab, sc in r.score.ranking():
        ms = r.mean_rt_ms.get(lab)
        lines.append(f"  {names.get(lab, lab):<18} {'--' if ms is None else f'{ms:5.0f} ms'}   "
                     f"errors {r.error_rate.get(lab, 0):4.0%}   score {sc:+.2f}")
    lines.append(f"  task checks: target hit {r.target_hit_rate:.0%}, candidates {r.candidate_accuracy:.0%} "
                 f"correct, fillers {r.filler_accuracy:.0%} correct")
    lines += ["  ! " + n for n in r.notes + r.score.notes]
    return lines


def report(trials: List[RtTrial], quality: dict) -> dict:
    out: dict = {"quality": quality}
    print("\n" + "=" * 78 + "\nVeriFi reaction-time CIT report\n" + "=" * 78)
    if quality:
        print(f"camera: face visible {quality.get('face', 0):.0%} of the time, head yaw median "
              f"{quality.get('yaw', 0):+.0f} deg (context only; RT doesn't depend on it)")
    check_ok = None
    bd = [t for t in trials if t.block == "birthday_check"]
    if bd:
        r = score_rt(bd, known_label="TRUE")
        names = {"TRUE": "your birthday", **{f"F{i}": f"other date {i}" for i in range(1, 5)}}
        print("\nCheck round (your birthday is known): slower = recognized")
        print("\n".join(describe(r, names)))
        if r.score.known_rank:
            check_ok = r.valid and (r.score.known_rank == 1 or (r.score.p_known or 1) < 0.2)
            print(f"  -> your birthday ranked {r.score.known_rank} of 5, d = {_f(r.d_cit, '+.2f')}, "
                  f"p = {_f(r.score.p_known, '.3f')}  => method {'WORKS' if check_ok else 'did NOT clearly work'} for you")
        out["birthday_check"] = {"rank": r.score.known_rank, "p": r.score.p_known, "d_cit": r.d_cit,
                                 "valid": r.valid, "ok": check_ok}
    cd = [t for t in trials if t.block == "cards"]
    if cd:
        r = score_rt(cd)
        print("\nCard test: the card you were slowest to deny")
        print("\n".join(describe(r, {})))
        label = r.score.label if r.valid else "invalid"
        if label in ("strong", "suggestive") and check_ok is False:
            label += " (but the check round didn't work, so be careful)"
        if r.score.top_item:
            print(f"\n  Slowest card: {r.score.top_item}   d = {_f(r.d_cit, '+.2f')}   p = {_f(r.score.p_top, '.3f')} "
                  "(chance one card is this much slower if none were special)")
        print(f"  Verdict: {label.upper()}")
        out["cards"] = {"ranking": r.score.ranking(), "mean_rt_ms": r.mean_rt_ms, "error_rate": r.error_rate,
                        "top": r.score.top_item, "p_top": r.score.p_top, "d_cit": r.d_cit, "label": label,
                        "valid": r.valid, "notes": r.notes}
    print("\nNow turn over your paper. A reaction means recognition, not lying.")
    return out


# ---------------------------------------------------------------------- main
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="data/rtcit.trials.csv", help="trial log (labels only)")
    p.add_argument("--no-camera", action="store_true", help="don't record the face (cleanest RT timing)")
    p.add_argument("--no-check", action="store_true", help="skip the birthday check round")
    p.add_argument("--cycles", type=int, default=18, help="presentations of each card (default 18)")
    p.add_argument("--source", default="0")
    a = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    print(__doc__)
    rng = random.Random()
    blocks: List[RtBlock] = []
    if not a.no_check:
        while True:
            try:
                bday = parse_birthday(input("Your date of birth (e.g. March 14), kept in memory only: "))
                break
            except ValueError as e:
                print("  ", e)
        blocks.append(birthday_block(bday, rng))
    blocks.append(card_block(a.cycles))

    path = unique_path(pathlib.Path(a.out))
    trials, quality, _ = run_session(blocks, rng, path, a.no_camera, a.source)
    if not trials:
        print("No trials completed.")
        return
    res = report(trials, quality)
    save_results(path, res)


# ---------------------------------------------------------------------- shared session helpers
def stem(path: pathlib.Path) -> pathlib.Path:
    return path.with_name(path.name.replace(".trials.csv", ""))


def _udp_sender(address: Optional[str]):
    if not address:
        return None
    import socket
    host, port = address.rsplit(":", 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return lambda msg: sock.sendto(json.dumps(msg).encode(), (host, int(port)))


def run_session(blocks: List[RtBlock], rng: random.Random, path: pathlib.Path, no_camera: bool,
                source: str = "0", slow: Optional[list] = None, udp: Optional[str] = None,
                sensor: Optional[str] = None, sensor_columns=("gsr", "hr", "resp")):
    """Task window (+ webcam and sensor recording in the background). Saves the trial log (labels
    only) and, with a slow sensor round, its onset log <name>.slow.csv. Each slow onset is also sent
    to vision and, with `udp`, to the backend: {"cmd":"item_onset","item","timestamp","block",
    "series","group","kind","duration"}. With `sensor` (COM5 / udp:0.0.0.0:5008 / sim) the sensors
    are recorded to <name>.physio.csv (extras["physio"]).
    Returns (completed trials, camera quality dict, extras)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    engine, samples = None, {"face": [], "yaw": []}
    rounds = slow_rounds(slow)
    all_slow = [it for r in rounds for it in r["items"]]
    extras: dict = {"slow": all_slow, "vision_slow": None, "vision_by_block": {}, "vision_epochs": {},
                    "physio": None}
    send = _udp_sender(udp)
    rec = src = None
    if sensor:
        from sensors import start_sensors
        extras["physio"] = str(stem(path)) + ".physio.csv"
        rec, src = start_sensors(sensor, extras["physio"], sensor_columns)

    def sensor_status() -> str:
        if rec is None:
            return ""
        s = rec.snapshot(2.0)
        if src.error and not s["rate"]:
            return f"   |   sensors: {src.error[:40]}"
        return f"   |   sensors {s['rate']:.0f} Hz" + ("" if s["rate"] else " (NO DATA)")
    if not no_camera:
        from vision.config import VisionConfig
        from vision.pipeline import VisionEngine
        cfg = VisionConfig(show_window=False)
        cfg.camera.source = int(source) if source.isdigit() else source
        cfg.output.frames_csv = str(stem(path)) + ".frames.csv"
        cfg.output.emit_instant = False
        engine = VisionEngine.from_config(cfg)
        engine.start_background()

        def status() -> str:
            r = engine.last_record or {}
            face = bool(r.get("face_present"))
            samples["face"].append(face)
            if face and r.get("head_yaw") is not None:
                samples["yaw"].append(r["head_yaw"])
                return f"camera: face OK, yaw {r['head_yaw']:+.0f} deg" + sensor_status()
            return "camera: face not found" + sensor_status()
    elif rec is not None:
        def status() -> str:
            return sensor_status().strip(" |")
    else:
        status = None

    def on_onset(it) -> None:
        meta = {"block": it.block, "series": it.series, "kind": it.kind}
        if engine:
            engine.mark_item(it.label, it.onset, meta, 5.0)
        if send:
            send({"cmd": "item_onset", "item": it.label, "timestamp": it.onset, "group": it.group,
                  "duration": 5.0, **meta})

    trials: List[RtTrial] = []
    win = TaskWindow(make_script(blocks, rng, trials, slow), status, on_onset)
    try:
        win.run()
    finally:
        if rec is not None:
            from sensors import stop_sensors
            stop_sensors(rec, src)
            print(f"sensors: {rec.n_ok} samples recorded, {rec.n_bad} lines not understood -> {extras['physio']}")
        if engine:
            if all_slow:
                try:
                    from vision.cit import VISION_CUES
                    names = {c.name for c in VISION_CUES}
                    for b in dict.fromkeys(it.block for it in all_slow):
                        extras["vision_by_block"][b] = engine.cit.block_result(b, known_item="TRUE")
                        for e in engine.cit.epochs(b):
                            if e.valid:
                                extras["vision_epochs"][(e.block, e.series, e.item)] = {
                                    k: v for k, v in e.features.items() if k in names}
                    extras["vision_slow"] = next(iter(extras["vision_by_block"].values()), None)
                except Exception as e:
                    print("vision slow-round scoring failed:", e)
            engine.stop()
    shown = [it for it in all_slow if it.onset]
    if shown:
        with open(str(stem(path)) + ".slow.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(shown[0].log_row()))
            w.writeheader()
            w.writerows(it.log_row() for it in shown)
    trials = [t for t in trials if t.outcome]
    if trials:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(trials[0].log_row()))
            w.writeheader()
            w.writerows(t.log_row() for t in trials)
    quality = {}
    if samples["face"]:
        ys = sorted(samples["yaw"])
        quality = {"face": sum(samples["face"]) / len(samples["face"]), "yaw": ys[len(ys) // 2] if ys else 0.0}
    return trials, quality, extras


def progress(blocks: List[RtBlock], trials: List[RtTrial], slow=None) -> dict:
    """Words shown vs. planned, per round type (practice not counted). Used to mark a session that
    was stopped early (interview/analysis.py mark_progress)."""
    planned = sum(len(build_trials(b, random.Random(0))) for b in blocks)
    items = [it for r in slow_rounds(slow) for it in r["items"]]
    return {"keyboard": [len(trials), planned], "sensor": [sum(1 for it in items if it.onset), len(items)]}


def save_results(path: pathlib.Path, res: dict) -> None:
    res_path = pathlib.Path(str(stem(path)) + ".results.json")
    res_path.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved {path} and {res_path}")


if __name__ == "__main__":
    main()
