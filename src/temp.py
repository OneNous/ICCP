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

import config.settings as cfg

SIM_MODE = os.environ.get("COILSHIELD_SIM", "0") == "1"

_W1_BASE = Path("/sys/bus/w1/devices")

# DS18B20 hardware error values — always discard regardless of operating range.
_DS18B20_ERROR_C = {0.0, 85.0}  # CRC failure, power-on default

# Operating window: outside this range the controller shuts all channels off.
# Below 35°F = potential freeze / no condensate cycle.
# Above 80°F = heat mode running, not a cooling cycle.
#
# Logger `cooling_cycles` uses the same band: each row is one continuous segment
# while temp stayed in-band (ICC active), with duration_s = ended_at − started_at
# and chN_protect_s = time each channel spent in PROTECTING (at-target) during that segment.
# Impedance vs temp_f in `readings` supports condensate / drain diagnostics offline.
TEMP_MIN_F: float = 35.0
TEMP_MAX_F: float = 80.0


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
        if c in _DS18B20_ERROR_C:
            return None
        return c
    except Exception:
        return None


def read_fahrenheit() -> float | None:
    c = read_celsius()
    return None if c is None else round(c * 9 / 5 + 32, 2)


def in_operating_range(temp_f: float | None) -> bool:
    """False when a reading is outside [TEMP_MIN_F, TEMP_MAX_F] or when fail-safe applies.

    If ``temp_f`` is None (sensor absent / unreadable): returns False when
    ``cfg.THERMAL_PAUSE_WHEN_SENSOR_MISSING`` is True (thermal pause / outputs off);
    otherwise True (legacy: do not block when sensor is missing).
    """
    if temp_f is None:
        return not bool(getattr(cfg, "THERMAL_PAUSE_WHEN_SENSOR_MISSING", False))
    return TEMP_MIN_F <= temp_f <= TEMP_MAX_F


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
