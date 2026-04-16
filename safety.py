"""Fault detection: overcurrent, bus voltage, read errors, cathode bonding."""

from __future__ import annotations

import config.settings as cfg


def evaluate(readings: dict[int, dict], wet: bool) -> list[str]:
    """
    Return human-readable fault strings (empty if none).
    Bonding: wet + every *ok* channel reports current below MIN_EXPECTED_MA_WHEN_WET.
    """
    faults: list[str] = []

    for ch, r in readings.items():
        label = f"CH{ch + 1}"
        if not r.get("ok"):
            faults.append(f"{label} READ ERROR: {r.get('error', 'unknown')}")
            continue
        cur = float(r["current"])
        bus_v = float(r["bus_v"])
        if cur > cfg.MAX_MA:
            faults.append(
                f"{label} OVERCURRENT: {cur:.4f} mA (max {cfg.MAX_MA} mA)"
            )
        if bus_v < cfg.MIN_BUS_V:
            faults.append(
                f"{label} UNDERVOLTAGE: {bus_v:.2f} V (min {cfg.MIN_BUS_V} V)"
            )
        if bus_v > cfg.MAX_BUS_V:
            faults.append(
                f"{label} OVERVOLTAGE: {bus_v:.2f} V (max {cfg.MAX_BUS_V} V)"
            )

    if wet:
        ok_readings = [r for r in readings.values() if r.get("ok")]
        if len(ok_readings) >= 1 and all(
            float(r["current"]) < cfg.MIN_EXPECTED_MA_WHEN_WET for r in ok_readings
        ):
            faults.append(
                "BONDING FAULT: wet but all channels below "
                f"{cfg.MIN_EXPECTED_MA_WHEN_WET} mA (open cathode / bonding)"
            )

    return faults


def should_latch(faults: list[str]) -> bool:
    """Any safety fault trips the latch until operator clears it."""
    return len(faults) > 0
