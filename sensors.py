"""
CoilShield ICCP — sensor abstraction layer.

Real path : reads four INA219 boards via I2C (4 channels total).
            Addresses: 0x40, 0x41, 0x44, 0x45
            Each board measures one anode channel.

Sim path  : 10 distinct HVAC cooling cycles over a compressed 24-hour window.
            Each of the 4 channels has unique wet/dry timing so the control
            loop is exercised with anodes in different OPEN / REGULATE / PROTECTING
            states. Current responds to PWM duty; SIM_CH_* offsets spread bus/mA
            per channel so verbose output is not four identical columns.

Time scale: SIM_TIME_SCALE env var sets real-seconds per simulated hour
            (read at import). Examples:
              10 → 24 sim-hours in ~4 real minutes (default)
              60 → 24 sim-hours in ~24 real minutes
               1 → 24 sim-hours in ~24 real hours

Install:
    pip install pi-ina219 --break-system-packages
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from typing import Any

import config.settings as cfg

SIM_MODE: bool = os.environ.get("COILSHIELD_SIM", "0") == "1"

ChannelReading = dict[str, Any]

# ---------------------------------------------------------------------------
# Real hardware path — four INA219 boards, one channel each
# ---------------------------------------------------------------------------

_sensors: list[Any] = []

if not SIM_MODE:
    try:
        from ina219 import INA219, DeviceRangeError

        SHUNT_OHMS = 0.1  # R100 shunt resistor on each board

        # CONFIG matches i2c_bench.INA219_DEFAULT_CONFIG_WORD (0x07FF) when
        # GAIN_AUTO resolves to GAIN_1_40MV — see pi-ina219 INA219._configure.
        mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None)
        per_mux = getattr(cfg, "I2C_MUX_CHANNELS_INA219", None)
        leg_mux = getattr(cfg, "I2C_MUX_CHANNEL_INA219", None)
        mux_bus = None
        from i2c_bench import mux_select_on_bus

        if mux_addr is not None and (per_mux is not None or leg_mux is not None):
            import smbus2

            mux_bus = smbus2.SMBus(int(cfg.I2C_BUS))
        try:
            for idx, addr in enumerate(cfg.INA219_ADDRESSES):
                try:
                    if mux_bus is not None and mux_addr is not None:
                        if per_mux is not None and idx < len(per_mux):
                            mux_select_on_bus(
                                mux_bus, int(mux_addr), int(per_mux[idx])
                            )
                            port_desc = f"TCA9548A ch{per_mux[idx]}"
                        elif leg_mux is not None:
                            mux_select_on_bus(
                                mux_bus, int(mux_addr), int(leg_mux)
                            )
                            port_desc = f"TCA9548A ch{leg_mux}"
                        else:
                            port_desc = "mux configured but no INA219 port selected"
                    else:
                        port_desc = "no mux"
                    sensor = INA219(SHUNT_OHMS, address=addr, busnum=cfg.I2C_BUS)
                    sensor.configure(
                        voltage_range=INA219.RANGE_16V,
                        gain=INA219.GAIN_AUTO,
                        bus_adc=INA219.ADC_128SAMP,
                        shunt_adc=INA219.ADC_128SAMP,
                    )
                    _sensors.append(sensor)
                except Exception as e:
                    print(
                        f"[sensors] CH{idx} INA219 @ {hex(addr)} init failed "
                        f"({port_desc}, i2c-{cfg.I2C_BUS}): {e}"
                    )
                    raise
        finally:
            if mux_bus is not None:
                mux_bus.close()

        print(f"[sensors] INA219 initialized on {len(_sensors)} channels "
              f"at addresses {[hex(a) for a in cfg.INA219_ADDRESSES]}")

    except Exception as _hw_err:
        print(f"[sensors] Hardware init failed: {_hw_err}")
        _sensors = []


def _mux_select_ina219_bus() -> None:
    """TCA9548A: one shared downstream port for all anode INA219s (legacy; no-op otherwise)."""
    mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None)
    mux_ch = getattr(cfg, "I2C_MUX_CHANNEL_INA219", None)
    if getattr(cfg, "I2C_MUX_CHANNELS_INA219", None) is not None:
        return
    if mux_addr is None or mux_ch is None:
        return
    try:
        import smbus2

        from i2c_bench import mux_select_on_bus

        b = smbus2.SMBus(int(cfg.I2C_BUS))
        try:
            mux_select_on_bus(b, int(mux_addr), int(mux_ch))
        finally:
            b.close()
    except OSError:
        pass


def _ina219_one_off_diag(iccp_ch: int, addr: int) -> dict[str, Any] | None:
    """Best-effort INA219 register snapshot after a read/init failure."""
    if SIM_MODE:
        return None
    from i2c_bench import ina219_diag_snapshot, mux_select_on_bus

    shunt = 0.1
    try:
        import smbus2

        mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None)
        per_mux = getattr(cfg, "I2C_MUX_CHANNELS_INA219", None)
        leg_mux = getattr(cfg, "I2C_MUX_CHANNEL_INA219", None)
        sm = smbus2.SMBus(int(cfg.I2C_BUS))
        try:
            if mux_addr is not None:
                if per_mux is not None and iccp_ch < len(per_mux):
                    mux_select_on_bus(sm, int(mux_addr), int(per_mux[iccp_ch]))
                elif leg_mux is not None:
                    mux_select_on_bus(sm, int(mux_addr), int(leg_mux))
            return ina219_diag_snapshot(sm, int(addr), shunt_ohm=shunt)
        finally:
            sm.close()
    except Exception as e:
        return {"ok": False, "error": str(e), "errno": getattr(e, "errno", None)}


def read_all_real() -> dict[int, ChannelReading]:
    """
    Read all 4 ICCP channels from INA219 hardware.

    Channel mapping:
        CH0 → INA219 at 0x40
        CH1 → INA219 at 0x41
        CH2 → INA219 at 0x44
        CH3 → INA219 at 0x45

    If ``I2C_MUX_CHANNELS_INA219`` is set, selects that TCA9548A port (0..7) before
    each channel read (one downstream branch at a time). Legacy single-port layout
    uses ``I2C_MUX_CHANNEL_INA219`` only (see ``_mux_select_ina219_bus``).
    """
    from ina219 import DeviceRangeError

    from i2c_bench import mux_select_on_bus

    mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None)
    per_mux = getattr(cfg, "I2C_MUX_CHANNELS_INA219", None)
    leg_mux = getattr(cfg, "I2C_MUX_CHANNEL_INA219", None)
    mux_bus = None
    if mux_addr is not None and (per_mux is not None or leg_mux is not None):
        import smbus2

        mux_bus = smbus2.SMBus(int(cfg.I2C_BUS))
    results: dict[int, ChannelReading] = {}
    try:
        if mux_bus is not None and mux_addr is not None and per_mux is None and leg_mux is not None:
            mux_select_on_bus(mux_bus, int(mux_addr), int(leg_mux))
        elif mux_addr is not None and per_mux is None:
            _mux_select_ina219_bus()

        for iccp_ch, sensor in enumerate(_sensors):
            if iccp_ch >= cfg.NUM_CHANNELS:
                break
            if (
                mux_bus is not None
                and mux_addr is not None
                and per_mux is not None
                and iccp_ch < len(per_mux)
            ):
                mux_select_on_bus(mux_bus, int(mux_addr), int(per_mux[iccp_ch]))
            try:
                bus_v = sensor.voltage()  # V
                current_ma = sensor.current()  # mA
                shunt_mv = sensor.shunt_voltage()  # mV
                power_mw = sensor.power()  # mW

                if current_ma != current_ma:  # NaN check
                    raise ValueError("NaN current")

                results[iccp_ch] = {
                    "bus_v": round(bus_v, 4),
                    "shunt_mv": round(shunt_mv, 4),
                    "current": round(current_ma, 6),
                    "power": round(power_mw, 6),
                    "ok": True,
                }

            except DeviceRangeError as e:
                diag = _ina219_one_off_diag(iccp_ch, int(cfg.INA219_ADDRESSES[iccp_ch]))
                results[iccp_ch] = {
                    "bus_v": 0.0,
                    "shunt_mv": 0.0,
                    "current": 0.0,
                    "power": 0.0,
                    "ok": False,
                    "error": f"DeviceRangeError: {e}",
                    "diag": diag,
                }

            except Exception as e:
                en = getattr(e, "errno", None)
                extra = f" [errno {en}]" if en is not None else ""
                diag = _ina219_one_off_diag(iccp_ch, int(cfg.INA219_ADDRESSES[iccp_ch]))
                results[iccp_ch] = {
                    "bus_v": 0.0,
                    "shunt_mv": 0.0,
                    "current": 0.0,
                    "power": 0.0,
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}{extra}",
                    "diag": diag,
                }

    finally:
        if mux_bus is not None:
            mux_bus.close()

    # Fill missing channels
    for ch in range(cfg.NUM_CHANNELS):
        if ch not in results:
            results[ch] = {
                "bus_v": 0.0,
                "shunt_mv": 0.0,
                "current": 0.0,
                "power": 0.0,
                "ok": False,
                "error": "no hardware",
            }

    return results


# ---------------------------------------------------------------------------
# Simulator — 24-hour wet/dry cycle model
# ---------------------------------------------------------------------------

COOLING_CYCLES: tuple[tuple[int, int], ...] = (
    (21600, 24300),   # 06:00–06:45
    (27000, 30600),   # 07:30–08:30
    (33300, 36000),   # 09:15–10:00
    (39600, 45000),   # 11:00–12:30
    (46800, 52200),   # 13:00–14:30
    (54000, 60300),   # 15:00–16:45
    (63000, 66600),   # 17:30–18:30
    (68400, 72000),   # 19:00–20:00
    (75600, 78300),   # 21:00–21:45
    (84600, 86400),   # 23:30–24:00
)

# (wet_delay_s, dry_delay_s) per channel after cycle start/end
ANODE_WET_PARAMS: tuple[tuple[int, int], ...] = (
    (120,  480),   # CH0: bottom left  — wets 2min after, dries 8min after
    (180,  720),   # CH1: bottom right — wets 3min after, dries 12min after
    (60,  2400),   # CH2: top left     — wets 1min after, dries 40min after
    (300, 1200),   # CH3: center       — wets 5min after, dries 20min after
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

    def sim_seconds(self) -> float:
        elapsed_real = time.monotonic() - self.start_real
        return (elapsed_real * _SIM_S_PER_REAL_S) % 86400.0

    def sim_hhmm(self) -> str:
        total = int(self.sim_seconds())
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}"

    def active_cycle(self, sim_s: float) -> int | None:
        for i, (start, end) in enumerate(COOLING_CYCLES):
            if start <= sim_s <= end:
                return i + 1
        return None

    def channel_is_wet(self, ch: int, sim_s: float) -> bool:
        if ch >= len(ANODE_WET_PARAMS):
            return False
        wet_delay, dry_delay = ANODE_WET_PARAMS[ch]
        for cycle_start, cycle_end in COOLING_CYCLES:
            if (cycle_start + wet_delay) <= sim_s <= (cycle_end + dry_delay):
                return True
        return False

    def wet_map(self, sim_s: float) -> str:
        return "".join(
            "W" if self.channel_is_wet(i, sim_s) else "."
            for i in range(cfg.NUM_CHANNELS)
        )


def _sim_ch_nudge(name: str, ch: int, default: float = 0.0) -> float:
    tup = getattr(cfg, name, ())
    try:
        return float(tup[ch]) if ch < len(tup) else default
    except (TypeError, ValueError):
        return default


def read_all_sim(state: SimSensorState) -> dict[int, ChannelReading]:
    """
    Generate one tick of simulated sensor readings.
    Dry → noise below CHANNEL_WET_THRESHOLD_MA (per-channel bias/scale).
    Wet → current tracks previous-tick duty (duty feedback loop).
    """
    sim_s = state.sim_seconds()
    results: dict[int, ChannelReading] = {}

    for ch in range(cfg.NUM_CHANNELS):
        wet = state.channel_is_wet(ch, sim_s)
        duty = state.duties.get(ch, 0.0)
        # cfg.SIM_NOMINAL_BUS_V is a bench nominal; field supply is often ~4.85 V.
        bus_v = (
            cfg.SIM_NOMINAL_BUS_V
            + _sim_ch_nudge("SIM_CH_BUS_OFFSET_V", ch)
            + random.gauss(0, 0.022 + 0.006 * ch)
        )

        if wet:
            duty_factor = 0.3 + (duty / max(cfg.PWM_MAX_DUTY, 1)) * 1.4
            nscale = _sim_ch_nudge("SIM_CH_WET_NOISE_SCALE", ch, 1.0)
            current = (
                cfg.TARGET_MA * duty_factor
                + random.gauss(0, cfg.SIM_NOISE_MA * nscale)
                + _sim_ch_nudge("SIM_CH_MA_BIAS_WET", ch)
            )
            current = max(current, cfg.CHANNEL_WET_THRESHOLD_MA * 1.5)
        else:
            ceiling = cfg.CHANNEL_WET_THRESHOLD_MA * 0.4
            dscale = _sim_ch_nudge("SIM_CH_DRY_NOISE_SCALE", ch, 1.0)
            current = abs(random.gauss(0, ceiling * 0.5 * dscale))
            current = min(current, ceiling)
            current += _sim_ch_nudge("SIM_CH_MA_BIAS_DRY", ch)
            current = max(current, 0.0)

        if cfg.SIM_INJECT_FAULT_CH is not None and ch == cfg.SIM_INJECT_FAULT_CH:
            current = cfg.SIM_INJECT_OVERCURRENT_MA

        results[ch] = {
            "bus_v":    round(bus_v, 4),
            "shunt_mv": round(current * 0.1, 4),
            "current":  round(current, 6),
            "power":    round(current * bus_v, 6),
            "sim_wet":  wet,
            "ok":       True,
        }

    return results