"""
CoilShield — DS18B20 drain-pan temperature (Linux 1-Wire).

Setup (Pi): sudo modprobe w1-gpio && sudo modprobe w1-therm
Or /boot/config.txt: dtoverlay=w1-gpio
Wiring: VCC 3.3V, GND, DATA → GPIO4 with 4.7kΩ pull-up to 3.3V.

SIM_MODE: realistic diurnal curve.
"""

from __future__ import annotations

import math
import os
import random
import time
from pathlib import Path

SIM_MODE = os.environ.get("COILSHIELD_SIM", "0") == "1"

_W1_BASE = Path("/sys/bus/w1/devices")

# DS18B20 known bogus values: 0°C = CRC failure, 85°C = power-on default.
# Readings outside this band are discarded and treated as missing.
_MIN_VALID_C = 4.0   # 40°F
_MAX_VALID_C = 48.9  # 120°F


def _find_device() -> Path | None:
    try:
        for p in _W1_BASE.iterdir():
            if p.name.startswith("28-"):
                return p / "w1_slave"
    except Exception:
        pass
    return None


def read_celsius() -> float | None:
    if SIM_MODE:
        return _sim_celsius()
    dev = _find_device()
    if dev is None:
        return None
    try:
        text = dev.read_text()
        if "YES" not in text:
            return None
        idx = text.find("t=")
        if idx == -1:
            return None
        raw = int(text[idx + 2 :].strip().split()[0])
        c = round(raw / 1000.0, 2)
        if not (_MIN_VALID_C <= c <= _MAX_VALID_C):
            return None
        return c
    except Exception:
        return None


def read_fahrenheit() -> float | None:
    c = read_celsius()
    return None if c is None else round(c * 9 / 5 + 32, 2)


def _sim_celsius() -> float:
    try:
        import sensors

        scale = getattr(sensors, "SIM_REAL_S_PER_SIM_HOUR", 10)
        sim_s = (time.monotonic() * (3600.0 / scale)) % 86400.0
        hour = sim_s / 3600.0
    except Exception:
        hour = (time.time() % 86400) / 3600.0

    base_c = 22.0
    amp_c = 4.0
    temp_c = base_c + amp_c * math.sin((hour - 6.0) * math.pi / 9.0)
    return round(temp_c + random.gauss(0, 0.2), 2)
