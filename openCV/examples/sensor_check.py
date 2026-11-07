"""Check the sensor hardware BEFORE an interview (~45 s).

    run_sensor_check.bat                       lists serial ports, then asks which one
    run_sensor_check.bat COM5                  Arduino on COM5 at 115200 baud
    run_sensor_check.bat COM5@9600 --columns gsr,hr,resp
    run_sensor_check.bat udp:0.0.0.0:5008      samples forwarded by the backend over UDP
    run_sensor_check.bat sim                   simulated sensors (tests the software only)

What it checks:
  1. lines arrive and parse (shows examples of lines it can't read)
  2. sample rate per column (need >= 10 Hz for skin conductance, >= 1 Hz for heart rate)
  3. flat or stuck signals (unplugged electrode, saturated input)
  4. GSR direction: at 20 s you take a deep breath and hold it 3 s. Sweat glands respond within
     1-4 s, so conductance goes UP. If the number goes DOWN, your board reads resistance: run the
     interview with --gsr-inverted.
The recording is saved to data/sensor_check.physio.csv.
"""
from __future__ import annotations

import argparse
import math
import pathlib
import statistics as st
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sensors import list_ports, start_sensors, stop_sensors  # noqa: E402
from interview.fusion import Physio  # noqa: E402


def pick_port() -> str:
    ports = list_ports()
    if not ports:
        print("No serial ports found. Is the Arduino plugged in (and the Arduino IDE's Serial Monitor closed)?")
        return input("Type a source anyway (COM5 / udp:0.0.0.0:5008 / sim): ").strip()
    print("Serial ports:")
    for i, (dev, desc) in enumerate(ports, 1):
        print(f"  {i}  {dev}  {desc}")
    a = input("Pick a number (or type COMx / udp:host:port / sim): ").strip()
    return ports[int(a) - 1][0] if a.isdigit() and 1 <= int(a) <= len(ports) else a


def analyse(path: str, cue_t: float) -> dict:
    ph = Physio.from_csv(path)
    out = {"rate": ph.rate(), "columns": {}}
    if len(ph.t) < 2:
        return out
    dur = ph.t[-1] - ph.t[0]
    for c, vals in ph.cols.items():
        v = [x for x in vals if math.isfinite(x)]
        info = {"n": len(v), "hz": len(v) / dur if dur > 0 else 0}
        if len(v) > 2:
            info.update(min=min(v), max=max(v), sd=st.pstdev(v))
            same = max(v.count(x) for x in set(v[-200:])) / min(len(v), 200) if v else 0
            info["flat"] = info["sd"] == 0 or same > 0.9
        out["columns"][c] = info
    if "gsr" in ph.cols and cue_t:
        def change(at: float):
            before, after = ph.window("gsr", at - 2, at), ph.window("gsr", at + 1, at + 6)
            if not before or not after:
                return None
            m = st.mean(before)
            return max(after) - m, min(after) - m
        obs = change(cue_t)
        # what the same window does at rest (drift and noise), to tell a response from wandering
        rest = [change(x) for x in frange(ph.t[0] + 2, cue_t - 6, 0.5)]
        rest = [r for r in rest if r]
        if obs and len(rest) >= 5:
            out["gsr_change"] = (obs[0], obs[1], max(r[0] for r in rest), min(r[1] for r in rest))
    return out


def frange(a: float, b: float, step: float):
    while a < b:
        yield a
        a += step


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", nargs="?", help="COM5, COM5@9600, serial:COM5, udp:0.0.0.0:5008, or sim")
    p.add_argument("--columns", default="gsr,hr,resp",
                   help="order of plain comma-separated numbers (default gsr,hr,resp; use t for a device "
                        "time column, e.g. t,gsr,hr)")
    p.add_argument("--seconds", type=float, default=40.0)
    p.add_argument("--out", default="data/sensor_check.physio.csv")
    a = p.parse_args()
    print(__doc__)
    src_spec = a.source or pick_port()
    cols = [c.strip() for c in a.columns.split(",") if c.strip()]
    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    rec, src = start_sensors(src_spec, a.out, cols)
    print(f"\nRecording from {src_spec} for {a.seconds:.0f} s. Sit still, sensor hand relaxed.")
    t0 = time.time()
    cue_t = 0.0
    try:
        while time.time() - t0 < a.seconds:
            time.sleep(1.0)
            el = time.time() - t0
            snap = rec.snapshot(2.0)
            vals = "  ".join(f"{c}={snap[c]:.4g}" for c in ("gsr", "hr", "resp", "ppg") if c in snap)
            err = f"   ! {src.error}" if src.error else ""
            print(f"  {el:4.0f} s  {snap['rate']:5.1f} lines/s  {vals or 'no samples yet'}{err}")
            if not cue_t and el >= 20:
                cue_t = time.time()
                print("\n  >>> NOW: take a DEEP breath and hold it for 3 seconds, then breathe normally <<<\n")
    except KeyboardInterrupt:
        pass
    finally:
        stop_sensors(rec, src)

    print("\n" + "=" * 70 + "\nSensor check\n" + "=" * 70)
    print(f"lines parsed {rec.n_ok}, not understood {rec.n_bad}")
    for ex in rec.bad_examples:
        print(f"   e.g. {ex!r}")
    if rec.n_ok == 0:
        print("\nNO SAMPLES. Check: right port? baud rate (--> COMx@9600)? Serial Monitor closed? "
              "Does the sketch print one line per sample?")
        if rec.bad_examples:
            print("Lines arrive but don't parse: send numbers like '512,74,301' or 'gsr:512 hr:74' "
                  "(see sensors/bridge.py), or set --columns to match.")
        return
    res = analyse(a.out, cue_t)
    ok = True
    need = {"gsr": 10.0, "hr": 1.0, "resp": 10.0, "ppg": 25.0}
    for c, info in res["columns"].items():
        flags = []
        if info["hz"] < need.get(c, 1.0):
            flags.append(f"rate too low (need >= {need.get(c, 1.0):.0f} Hz)")
        if info.get("flat"):
            flags.append("FLAT / stuck: electrode off or input saturated")
        ok &= not flags
        rng = f"range {info.get('min', 0):.4g} .. {info.get('max', 0):.4g}" if "min" in info else ""
        print(f"  {c:<5} {info['hz']:6.1f} Hz  {rng}  " + ("OK" if not flags else "; ".join(flags)))
    missing = [c for c in ("gsr", "hr", "resp") if c not in res["columns"]]
    if missing:
        print(f"  missing columns: {', '.join(missing)} (fine if that sensor isn't used; scoring skips it)")
    if "gsr_change" in res:
        up, down, rest_up, rest_down = res["gsr_change"]
        print(f"\n  GSR 1-6 s after the deep breath: up to {up:+.4g}, down to {down:+.4g}\n"
              f"  (the same window at rest moved at most {rest_up:+.4g} / {rest_down:+.4g})")
        rises = up > 2 * max(rest_up, 0) and up > 2 * abs(down)
        falls = down < 2 * min(rest_down, 0) and abs(down) > 2 * up
        if rises and not falls:
            print("  -> rises with arousal: conductance. Use the default (no --gsr-inverted).")
        elif falls and not rises:
            print("  -> FALLS with arousal: the board reads resistance. Use --gsr-inverted in every run.")
        else:
            print("  -> no clear response. Repeat with a bigger breath, or check electrode contact. "
                  "Some people barely respond (about 1 in 10); try another person.")
    if src_spec.lower() == "sim":
        print("\n  (simulated sensors: this only tested the software)")
    print(f"\nSaved {a.out}.  Overall: {'READY' if ok else 'FIX THE ISSUES ABOVE'}")


if __name__ == "__main__":
    main()
