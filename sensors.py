"""
INA3221 readings — real hardware or simulator.

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
    from adafruit_ina3221 import INA3221

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
            if cfg.SIM_INJECT_FAULT_CH == i:
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
    """
    Read all INA3221 channels: first chip CH1–CH3, second chip CH4–CH5.
    Missing chip or channel yields ok=False for that slot.
    """
    i2c = busio.I2C(board.SCL, board.SDA)
    readings: dict[int, ChannelReading] = {}
    logical = 0

    for _chip_idx, addr in enumerate(cfg.INA3221_ADDRESSES):
        remaining = cfg.NUM_CHANNELS - logical
        if remaining <= 0:
            break
        n_here = min(3, remaining)
        enable = list(range(n_here))
        try:
            ina = INA3221(i2c, address=addr, enable=enable)
        except Exception as e:
            for j in range(n_here):
                readings[logical + j] = {
                    "bus_v": 0.0,
                    "shunt_mv": 0.0,
                    "current": 0.0,
                    "power": 0.0,
                    "ok": False,
                    "error": f"no chip at 0x{addr:02x}: {e}",
                }
            logical += n_here
            continue

        for local in range(n_here):
            g = logical + local
            try:
                ch = ina[local]
                bus_v = float(ch.bus_voltage)
                shunt_mv = float(ch.shunt_voltage)
                current = float(ch.current)
                if current != current:  # NaN
                    raise ValueError("NaN current")
                power = current * bus_v / 1000.0 * 1000.0
                readings[g] = {
                    "bus_v": round(bus_v, 4),
                    "shunt_mv": round(shunt_mv, 4),
                    "current": round(current, 6),
                    "power": round(power, 6),
                    "ok": True,
                }
            except Exception as e:
                readings[g] = {
                    "bus_v": 0.0,
                    "shunt_mv": 0.0,
                    "current": 0.0,
                    "power": 0.0,
                    "ok": False,
                    "error": str(e),
                }
        logical += n_here

    for i in range(cfg.NUM_CHANNELS):
        if i not in readings:
            readings[i] = {
                "bus_v": 0.0,
                "shunt_mv": 0.0,
                "current": 0.0,
                "power": 0.0,
                "ok": False,
                "error": "no mapping",
            }

    try:
        i2c.deinit()
    except Exception:
        pass
    return readings
