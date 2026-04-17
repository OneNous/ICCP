"""
CoilShield ICCP — sensor abstraction layer.

Real path  : reads two INA3221 breakout boards via I2C (5 channels total).
Sim path   : 10 distinct HVAC cooling cycles over a compressed 24-hour window.
             Each of the 5 channels has unique wet/dry timing so the control
             loop is exercised with anodes in different DORMANT / PROBING /
             PROTECTING (and FAULT via inject) combinations.
             Current responds to PWM duty cycle so the loop can converge.

Time scale : SIM_TIME_SCALE env var sets real-seconds per simulated hour
             (read at import). Examples:
               10  → 24 sim-hours in ~4 real minutes  (default)
               60  → 24 sim-hours in ~24 real minutes
               1   → 24 sim-hours in ~24 real hours
"""

from __future__ import annotations

import math
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any

import config.settings as cfg

SIM_MODE: bool = os.environ.get("COILSHIELD_SIM", "0") == "1"

ChannelReading = dict[str, Any]

# ---------------------------------------------------------------------------
# Real hardware path — two INA3221 boards, 5 channels total
# ---------------------------------------------------------------------------

_i2c = None
_chips: list[Any] = []

if not SIM_MODE:
    try:
        import board
        import busio
        from adafruit_ina3221 import INA3221

        _i2c = busio.I2C(board.SCL, board.SDA)
        _chips = [
            INA3221(_i2c, address=cfg.INA3221_ADDRESSES[0], enable=[0, 1, 2]),
            INA3221(_i2c, address=cfg.INA3221_ADDRESSES[1], enable=[0, 1]),
        ]
    except Exception as _hw_err:
        print(f"[sensors] Hardware init failed: {_hw_err}")
        _chips = []


def read_all_real() -> dict[int, ChannelReading]:
    """
    Read all 5 ICCP channels from hardware.

    Channel mapping:
      INA3221 chip 0 (addr 0x40)  local ch 0,1,2  →  ICCP CH0, CH1, CH2
      INA3221 chip 1 (addr 0x41)  local ch 0,1    →  ICCP CH3, CH4
    """
    mapping = [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1)]
    results: dict[int, ChannelReading] = {}
    for iccp_ch, (chip_idx, local_ch) in enumerate(mapping):
        if chip_idx >= len(_chips) or not _chips:
            results[iccp_ch] = {
                "bus_v": 0.0,
                "shunt_mv": 0.0,
                "current": 0.0,
                "power": 0.0,
                "ok": False,
                "error": "no hardware",
            }
            continue
        try:
            ch_obj = _chips[chip_idx][local_ch]
            bus_v = float(ch_obj.bus_voltage)
            shunt_mv = float(ch_obj.shunt_voltage)
            # Adafruit driver returns current in mA.
            current_ma = float(ch_obj.current)
            if current_ma != current_ma:  # NaN
                raise ValueError("NaN current")
            power = current_ma * bus_v / 1000.0 * 1000.0
            results[iccp_ch] = {
                "bus_v": round(bus_v, 4),
                "shunt_mv": round(shunt_mv, 4),
                "current": round(current_ma, 6),
                "power": round(power, 6),
                "ok": True,
            }
        except Exception as e:
            results[iccp_ch] = {
                "bus_v": 0.0,
                "shunt_mv": 0.0,
                "current": 0.0,
                "power": 0.0,
                "ok": False,
                "error": str(e),
            }
    return results


# ---------------------------------------------------------------------------
# Simulator — 24-hour wet/dry cycle model
# ---------------------------------------------------------------------------

COOLING_CYCLES: tuple[tuple[int, int], ...] = (
    (21600, 24300),  # 06:00–06:45
    (27000, 30600),  # 07:30–08:30
    (33300, 36000),  # 09:15–10:00
    (39600, 45000),  # 11:00–12:30
    (46800, 52200),  # 13:00–14:30
    (54000, 60300),  # 15:00–16:45
    (63000, 66600),  # 17:30–18:30
    (68400, 72000),  # 19:00–20:00
    (75600, 78300),  # 21:00–21:45
    (84600, 86400),  # 23:30–24:00
)

ANODE_WET_PARAMS: tuple[tuple[int, int], ...] = (
    (120, 480),
    (180, 720),
    (60, 2400),
    (60, 3000),
    (300, 1200),
)

SIM_REAL_S_PER_SIM_HOUR: float = float(os.environ.get("SIM_TIME_SCALE", "10"))
_SIM_S_PER_REAL_S: float = 3600.0 / SIM_REAL_S_PER_SIM_HOUR


@dataclass
class SimSensorState:
    """Mutable simulator state carried between ticks."""

    start_real: float = field(default_factory=time.monotonic)
    duties: dict[int, float] = field(
        default_factory=lambda: {i: 0.0 for i in range(cfg.NUM_CHANNELS)}
    )
    # Phase 7 — optional sim diagnostics (same physics as reference.py / temp.py sim)
    sim_ref_mv: float | None = None
    sim_temp_f: float | None = None

    def sim_seconds(self) -> float:
        """Elapsed simulated seconds since midnight, wrapping at 86400."""
        elapsed_real = time.monotonic() - self.start_real
        return (elapsed_real * _SIM_S_PER_REAL_S) % 86400.0

    def sim_hhmm(self) -> str:
        total = int(self.sim_seconds())
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}"

    def active_cycle(self, sim_s: float) -> int | None:
        """Return 1-based cycle index if a cycle is active, else None."""
        for i, (start, end) in enumerate(COOLING_CYCLES):
            if start <= sim_s <= end:
                return i + 1
        return None

    def channel_is_wet(self, ch: int, sim_s: float) -> bool:
        wet_delay, dry_delay = ANODE_WET_PARAMS[ch]
        for cycle_start, cycle_end in COOLING_CYCLES:
            if (cycle_start + wet_delay) <= sim_s <= (cycle_end + dry_delay):
                return True
        return False

    def wet_map(self, sim_s: float) -> str:
        return "".join(
            "W" if self.channel_is_wet(i, sim_s) else "." for i in range(cfg.NUM_CHANNELS)
        )


def read_all_sim(state: SimSensorState) -> dict[int, ChannelReading]:
    """
    Generate one tick of simulated sensor readings.

    Dry  → noise below CHANNEL_WET_THRESHOLD_MA.
    Wet  → current tracks previous-tick duty (see main loop feedback).
    """
    sim_s = state.sim_seconds()
    results: dict[int, ChannelReading] = {}

    for ch in range(cfg.NUM_CHANNELS):
        wet = state.channel_is_wet(ch, sim_s)
        duty = state.duties.get(ch, 0.0)
        bus_v = cfg.SIM_NOMINAL_BUS_V + random.gauss(0, 0.03)

        if wet:
            duty_factor = 0.3 + (duty / max(cfg.PWM_MAX_DUTY, 1)) * 1.4
            current = cfg.TARGET_MA * duty_factor + random.gauss(0, cfg.SIM_NOISE_MA)
            current = max(current, cfg.CHANNEL_WET_THRESHOLD_MA * 1.5)
        else:
            ceiling = cfg.CHANNEL_WET_THRESHOLD_MA * 0.4
            current = abs(random.gauss(0, ceiling * 0.5))
            current = min(current, ceiling)

        if cfg.SIM_INJECT_FAULT_CH is not None and ch == cfg.SIM_INJECT_FAULT_CH:
            current = cfg.SIM_INJECT_OVERCURRENT_MA

        shunt_mv = current * 0.1
        power = current * bus_v / 1000.0 * 1000.0
        results[ch] = {
            "bus_v": round(bus_v, 4),
            "shunt_mv": round(shunt_mv, 4),
            "current": round(current, 6),
            "power": round(power, 6),
            "sim_wet": wet,
            "ok": True,
        }

    native = getattr(cfg, "SIM_NATIVE_ZINC_MV", 200.0)
    raw_mv = native
    for ch in range(cfg.NUM_CHANNELS):
        if state.channel_is_wet(ch, sim_s):
            d = state.duties.get(ch, 0.0)
            raw_mv += 25.0 * (d / max(cfg.PWM_MAX_DUTY, 1))
    state.sim_ref_mv = round(raw_mv + random.gauss(0, 1.5), 2)
    hour = sim_s / 3600.0
    base_c = 22.0
    amp_c = 4.0
    temp_c = base_c + amp_c * math.sin((hour - 6.0) * math.pi / 9.0)
    state.sim_temp_f = round((temp_c + random.gauss(0, 0.2)) * 9 / 5 + 32, 2)

    return results
