"""
INA219 readings — real hardware or simulator.

Pi-specific imports load only when COILSHIELD_SIM is not set to 1, so macOS
can run with COILSHIELD_SIM=1 without board/RPi.GPIO.
"""

from __future__ import annotations

import os
import random
from typing import Any

SIM_MODE = os.environ.get("COILSHIELD_SIM", "0") == "1"

if not SIM_MODE:
    import board
    import busio
    from adafruit_ina219 import INA219

import config.settings as cfg

ChannelReading = dict[str, Any]


class SimSensorState:
    """In-process fake currents with drift and noise (no hardware imports)."""

    def __init__(self) -> None:
        self._currents: list[float] = [
            cfg.TARGET_MA + random.uniform(-0.02, 0.02) for _ in range(cfg.NUM_CHANNELS)
        ]

    def read_all(self) -> dict[int, ChannelReading]:
        out: dict[int, ChannelReading] = {}
        for i in range(cfg.NUM_CHANNELS):
            ch_1 = i + 1
            if cfg.SIM_INJECT_FAULT_CH == ch_1:
                current = cfg.SIM_INJECT_OVERCURRENT_MA
            else:
                self._currents[i] += cfg.SIM_DRIFT_MA + random.uniform(
                    -cfg.SIM_NOISE_MA, cfg.SIM_NOISE_MA
                )
                self._currents[i] = max(0.0, self._currents[i])
                current = self._currents[i]
            bus_v = cfg.SIM_NOMINAL_BUS_V + random.uniform(-0.02, 0.02)
            shunt_mv = current * 0.1
            power = current * bus_v / 1000.0 * 1000.0
            out[i] = {
                "bus_v": round(bus_v, 4),
                "shunt_mv": round(shunt_mv, 4),
                "current": round(current, 6),
                "power": round(power, 6),
                "ok": True,
            }
        return out


def read_all_sim(state: SimSensorState) -> dict[int, ChannelReading]:
    return state.read_all()


def read_all_real() -> dict[int, ChannelReading]:
    """Read all INA219 boards; missing addresses are skipped."""
    i2c = busio.I2C(board.SCL, board.SDA)
    sensors: dict[int, Any] = {}
    for i, addr in enumerate(cfg.CHANNEL_ADDRESSES):
        try:
            sensors[i] = INA219(i2c, addr=addr)
        except Exception:
            pass

    readings: dict[int, ChannelReading] = {}
    for i in range(cfg.NUM_CHANNELS):
        if i not in sensors:
            readings[i] = {
                "bus_v": 0.0,
                "shunt_mv": 0.0,
                "current": 0.0,
                "power": 0.0,
                "ok": False,
                "error": "no sensor",
            }
            continue
        try:
            s = sensors[i]
            bus_v = float(s.bus_voltage)
            shunt_v = float(s.shunt_voltage)
            current = float(s.current)
            power = float(s.power)
            readings[i] = {
                "bus_v": round(bus_v, 4),
                "shunt_mv": round(shunt_v * 1000.0, 4),
                "current": round(current, 6),
                "power": round(power, 6),
                "ok": True,
            }
        except Exception as e:
            readings[i] = {
                "bus_v": 0.0,
                "shunt_mv": 0.0,
                "current": 0.0,
                "power": 0.0,
                "ok": False,
                "error": str(e),
            }
    try:
        i2c.deinit()
    except Exception:
        pass
    return readings
