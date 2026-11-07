"""Sensor bridge: Arduino (serial) or backend (UDP) -> one timestamped CSV that the scorer reads.

    from sensors import open_source, SensorRecorder
    rec = SensorRecorder("data/x.physio.csv")
    src = open_source("serial:COM5@115200", rec)     # or "udp:0.0.0.0:5008", or "sim"
    src.start(); ...; src.stop(); rec.close()

Output contract (what interview/fusion.py reads):  t,gsr,hr,resp,ppg[,sim]
  t = time.time() on THIS laptop when the line arrived (same clock as the item onsets).
"""
from .bridge import (COLUMNS, SensorRecorder, SerialSource, SimSource, UdpSource, list_ports, open_source,
                     parse_line, start_sensors, stop_sensors)

__all__ = ["COLUMNS", "SensorRecorder", "SerialSource", "SimSource", "UdpSource", "list_ports", "open_source",
           "parse_line", "start_sensors", "stop_sensors"]
