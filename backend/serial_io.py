"""Reads the Arduino over serial in a background thread.

Parses EVT:HELLO / EVT:DATA / EVT:BUTTON lines (see "Serial protocol" in README)
and timestamps each sample with laptop time on arrival.
Port comes from the VERIFI_PORT env variable, or is auto-detected.
"""
