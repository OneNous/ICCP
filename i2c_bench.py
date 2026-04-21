"""
Pure smbus2 helpers for INA219 and ADS1115 (bench probe + reference path).

No pi-ina219 / Blinka dependency — works under `sudo` and user venv alike.
"""

from __future__ import annotations

import threading
import time
from typing import Any

# One lock per /dev/i2c-N adapter. Multiple SMBus() handles + mux (TCA9548A) on the
# same adapter can produce errno 5 (EIO) on Raspberry Pi kernels; serialize hot-path
# traffic with :func:`i2c_bus_lock`.
_I2C_BUS_LOCKS: dict[int, threading.RLock] = {}
_I2C_BUS_LOCKS_GUARD = threading.Lock()


def i2c_bus_lock(bus: int) -> threading.RLock:
    """Return a process-wide reentrant lock for ``bus`` (adapter number).

    Uses :class:`threading.RLock` so nested acquisition on the same thread is safe
    (e.g. reference + sensor paths). Creation is guarded so two threads never install
    different locks for the same adapter number.
    """
    b = int(bus)
    with _I2C_BUS_LOCKS_GUARD:
        lock = _I2C_BUS_LOCKS.get(b)
        if lock is None:
            lock = threading.RLock()
            _I2C_BUS_LOCKS[b] = lock
        return lock


def word_in(raw: int) -> int:
    """Linux SMBus read_word_data byte order → big-endian register value."""
    return ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)


def word_out(value: int) -> int:
    """Register value → Linux SMBus write_word_data byte order."""
    return ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)


# ---------------------------------------------------------------------------
# INA219 — CONFIG word aligned with pi-ina219 + TI INA219 register map
# ---------------------------------------------------------------------------
# Bitfield (TI INA219 CONFIG register): RST[15], BRNG[13], PG[12:11], BADC[10:7],
# SADC[6:3], MODE[2:0].  Shunt LSB vs PGA: TI Table 7-1 (same as _INA219_PGA_LSB_V).
#
# pi-ina219 INA219._configure (chrisb2/pi_ina219) builds:
#   (voltage_range << 13) | (gain << 11) | (bus_adc << 7) | (shunt_adc << 3) | 7
# with MODE=7 = continuous shunt and bus.  This matches sensors.py / reference.py:
#   RANGE_16V (0), GAIN_1_40MV (0), ADC_128SAMP (15), ADC_128SAMP (15).
INA219_BRNG_16V = 0  # 0–16 V bus full-scale (TI: BRNG=0)
INA219_BRNG_32V = 1
INA219_PGA_DIV1 = 0  # ±40 mV shunt → 10 µV/LSB on register 01h
INA219_ADC_128SAMP = 15  # pi-ina219 ADC_128SAMP
INA219_MODE_CONT_SHUNT_BUS = 7  # continuous shunt + bus

INA219_DEFAULT_CONFIG_WORD: int = (
    (INA219_BRNG_16V << 13)
    | (INA219_PGA_DIV1 << 11)
    | (INA219_ADC_128SAMP << 7)
    | (INA219_ADC_128SAMP << 3)
    | INA219_MODE_CONT_SHUNT_BUS
)  # 0x07FF — same CONFIG as pi-ina219 for the CoilShield configure() tuple


def mux_select_on_bus(bus: Any, mux_addr: int | None, mux_ch: int | None) -> None:
    """TCA9548A: select downstream port (0–7). No-op if mux_addr or mux_ch is None."""
    if mux_addr is None or mux_ch is None:
        return
    if mux_ch < 0 or mux_ch > 7:
        raise ValueError("mux channel must be 0..7")
    bus.write_byte(int(mux_addr), 1 << int(mux_ch))
    mux_post_select_stabilize()


def mux_post_select_stabilize() -> None:
    """Optional short sleep after mux select (``config.settings.I2C_MUX_POST_SELECT_DELAY_S``)."""
    try:
        import config.settings as _cfg

        d = float(getattr(_cfg, "I2C_MUX_POST_SELECT_DELAY_S", 0.0) or 0.0)
    except Exception:
        d = 0.0
    if d > 0:
        time.sleep(d)


def ads1115_behind_i2c_mux(mux_addr: int | None, mux_channel_ads: int | None) -> bool:
    """True when ADS1115 sits behind a TCA9548A port (idle bus scan cannot see 0x48)."""
    return mux_addr is not None and mux_channel_ads is not None


def ina219_write_config(bus: Any, addr: int, config: int) -> None:
    bus.write_word_data(addr, 0, word_out(config & 0xFFFF))


def ina219_read_registers(bus: Any, addr: int) -> tuple[int, int]:
    """Return (shunt_raw, bus_raw) 16-bit register values (already word_in)."""
    raw_s = word_in(bus.read_word_data(addr, 1))
    raw_b = word_in(bus.read_word_data(addr, 2))
    return raw_s, raw_b


def ina219_read_config(bus: Any, addr: int) -> int:
    """CONFIG register (0x00), host-endian value after word_in."""
    return word_in(bus.read_word_data(addr, 0))


# TI INA219 datasheet: shunt voltage LSB vs PGA bits [12:11] of CONFIG.
# 00=±40mV→10µV, 01=±80mV→20µV, 10=±160mV→40µV, 11=±320mV→80µV per LSB.
_INA219_PGA_LSB_V: dict[int, float] = {
    0: 10e-6,
    1: 20e-6,
    2: 40e-6,
    3: 80e-6,
}


def _ina219_pga_bits(config_word: int) -> int:
    return (config_word >> 11) & 3


def ina219_parse(
    shunt_raw: int,
    bus_raw: int,
    shunt_ohm: float,
    *,
    pga_bits: int = 0,
) -> dict[str, Any]:
    """Convert raw INA219 registers; shunt LSB follows PGA [12:11] (TI INA219)."""
    lsb_v = _INA219_PGA_LSB_V.get(pga_bits & 3, 10e-6)
    if shunt_raw & 0x8000:
        shunt_v = (shunt_raw - 65536) * lsb_v
    else:
        shunt_v = shunt_raw * lsb_v
    shunt_mv = shunt_v * 1000.0

    bus_adc = (bus_raw >> 3) & 0x1FFF
    bus_v = bus_adc * 0.004
    cnvr = bool((bus_raw >> 1) & 1)
    ovf = bool(bus_raw & 1)

    current_ma = (shunt_v / shunt_ohm) * 1000.0 if shunt_ohm > 0 else float("nan")
    power_mw = bus_v * current_ma

    return {
        "ok": True,
        "bus_v": bus_v,
        "shunt_mv": shunt_mv,
        "current_ma": current_ma,
        "power_mw": power_mw,
        "cnvr": cnvr,
        "ovf": ovf,
        "pga_bits": int(pga_bits & 3),
    }


def ina219_ensure_converting(bus: Any, addr: int) -> None:
    """If bus voltage looks stale, write CONFIG matching sensors.py (pi-ina219 defaults)."""
    try:
        raw_cfg = word_in(bus.read_word_data(addr, 0))
    except OSError:
        return
    if raw_cfg == 0:
        ina219_write_config(bus, addr, INA219_DEFAULT_CONFIG_WORD)
        time.sleep(0.02)
        return
    _, raw_b = ina219_read_registers(bus, addr)
    bus_adc = (raw_b >> 3) & 0x1FFF
    if bus_adc == 0:
        ina219_write_config(bus, addr, INA219_DEFAULT_CONFIG_WORD)
        time.sleep(0.02)


def ina219_read(bus: Any, addr: int, shunt_ohm: float) -> dict[str, Any]:
    try:
        ina219_ensure_converting(bus, addr)
        cfg = ina219_read_config(bus, addr)
        pga = _ina219_pga_bits(cfg)
        raw_s, raw_b = ina219_read_registers(bus, addr)
        return ina219_parse(raw_s, raw_b, shunt_ohm, pga_bits=pga)
    except OSError as e:
        return {"ok": False, "error": str(e)}


def ina219_diag_snapshot(bus: Any, addr: int, *, shunt_ohm: float = 0.1) -> dict[str, Any]:
    """INA219 register dump for support logs (smbus2 only; no pi-ina219)."""
    out: dict[str, Any] = {"address": int(addr), "ok": False}
    try:
        ina219_ensure_converting(bus, addr)
        cfg = ina219_read_config(bus, addr)
        pga = _ina219_pga_bits(cfg)
        raw_s, raw_b = ina219_read_registers(bus, addr)
        parsed = ina219_parse(raw_s, raw_b, shunt_ohm, pga_bits=pga)
        out.update(
            {
                "ok": True,
                "config_hex": f"0x{cfg & 0xFFFF:04X}",
                "brng_32v": bool((cfg >> 13) & 1),
                "pga_bits": int(pga),
                "mode_bits": int(cfg & 7),
                "shunt_raw": int(raw_s) & 0xFFFF,
                "bus_raw": int(raw_b) & 0xFFFF,
                "cnvr": parsed.get("cnvr"),
                "ovf": parsed.get("ovf"),
                "bus_v": round(float(parsed.get("bus_v", 0.0)), 6),
                "current_ma": round(float(parsed.get("current_ma", 0.0)), 6),
            }
        )
    except OSError as e:
        out["error"] = str(e)
        out["errno"] = getattr(e, "errno", None)
    except Exception as e:
        out["error"] = str(e)
    return out


def ads1115_read_config_word(bus: Any, addr: int) -> int:
    """ADS1115 Pointer Register 0x01 — config/status 16-bit (big-endian on wire)."""
    hi, lo = bus.read_i2c_block_data(addr, 0x01, 2)
    return ((hi << 8) | lo) & 0xFFFF


def _ads1115_dr_conversion_s(dr: int) -> float:
    """TI ADS1115: single-shot conversion time ≈ 1 / DR (Table 6-5, nominal)."""
    sps = (8.0, 16.0, 32.0, 64.0, 128.0, 250.0, 475.0, 860.0)[dr & 7]
    return 1.0 / sps


def _ads1115_config_word(channel: int, fsr_v: float, dr: int = 5) -> int:
    """Single-ended AINn vs GND, single-shot; DR[2:0] in bits [7:5] (default 101 = 250 SPS)."""
    if channel not in (0, 1, 2, 3):
        raise ValueError("ADS1115 channel must be 0..3")
    if (dr & 7) != dr:
        raise ValueError("ADS1115 DR must be 0..7")
    mux = (4 + channel) << 12
    # PGA bits 11-9
    pga_map = {6.144: 0x0000, 4.096: 0x0200, 2.048: 0x0400, 1.024: 0x0600, 0.512: 0x0800, 0.256: 0x0A00}
    pga = pga_map.get(round(fsr_v, 3), 0x0200)
    # OS=1 start, MUX, PGA, MODE=1 single, DR; COMP_QUE=00 (not 11) so ALERT/RDY can act as conversion-ready
    # when Lo/Hi_thresh are programmed (see reference._init_ref_ads1115).
    return 0x8000 | mux | pga | 0x0100 | ((dr & 7) << 5) | 0


def _ads1115_volts_per_lsb(fsr_v: float) -> float:
    return float(fsr_v) / 32768.0


def ads1115_read_single_ended(
    bus: Any,
    addr: int,
    channel: int,
    fsr_v: float = 4.096,
    *,
    dr: int = 5,
    conversion_delay_s: float | None = None,
    poll_interval_s: float = 0.002,
    poll_max: int | None = None,
) -> float:
    """Return voltage in volts (single-ended vs GND).

    Starts a single-shot conversion then polls TI ADS1115 Config register bit 15
    (OS) until the device reports not busy (Table 8-3), or falls back to a delay
    derived from the selected data rate (TI Table 6-5, ~1/DR) when
    ``conversion_delay_s`` is omitted.
    """
    cfg = _ads1115_config_word(channel, fsr_v, dr=dr)
    dr_bits = (cfg >> 5) & 7
    t_conv = _ads1115_dr_conversion_s(dr_bits)
    if conversion_delay_s is None:
        conversion_delay_s = t_conv * 1.25 + 5e-4
    if poll_max is None:
        poll_max = max(50, int(t_conv / max(poll_interval_s, 1e-6)) + 25)

    bus.write_i2c_block_data(addr, 0x01, [(cfg >> 8) & 0xFF, cfg & 0xFF])
    # Poll Config[15] (OS): reads 1 when not converting (conversion complete).
    for _ in range(poll_max):
        time.sleep(poll_interval_s)
        hi, lo = bus.read_i2c_block_data(addr, 0x01, 2)
        status = ((hi << 8) | lo) & 0xFFFF
        if status & 0x8000:
            break
    else:
        time.sleep(max(0.0, float(conversion_delay_s)))

    raw = bus.read_i2c_block_data(addr, 0x00, 2)
    val = (raw[0] << 8) | raw[1]
    if val & 0x8000:
        val -= 65536
    return val * _ads1115_volts_per_lsb(fsr_v)


def ads1115_start_single_shot(
    bus: Any, addr: int, channel: int, fsr_v: float, dr: int = 5
) -> int:
    """Write config register to start a single-shot conversion; returns config word."""
    cfg = _ads1115_config_word(channel, fsr_v, dr=dr)
    bus.write_i2c_block_data(addr, 0x01, [(cfg >> 8) & 0xFF, cfg & 0xFF])
    return int(cfg)


def ads1115_config_os_ready(bus: Any, addr: int) -> bool:
    """True if ADS1115 config bit 15 (OS) indicates conversion complete / not busy."""
    hi, lo = bus.read_i2c_block_data(addr, 0x01, 2)
    status = ((hi << 8) | lo) & 0xFFFF
    return bool(status & 0x8000)


def ads1115_wait_os_ready(
    bus: Any,
    addr: int,
    *,
    deadline_s: float,
    poll_interval_s: float,
) -> bool:
    """Poll the config register until OS (conversion complete) or time runs out.

    Uses the same OS semantics as :func:`ads1115_config_os_ready`. This is the
    reliable completion path for single-shot conversions; ALERT/RDY edges are
    optional and can be too short for userspace GPIO on some platforms.

    ``deadline_s`` is a duration (seconds) from *now* on the monotonic clock.
    ``poll_interval_s`` is clamped to at least 1 µs when positive to avoid a
    busy spin; if zero or negative, each loop sleeps 1 µs.

    Returns True if OS became ready in time (including a final check at the
    deadline). Returns False if still not ready after the window — caller
    should apply a fixed delay fallback (see :func:`ads1115_read_single_ended`).
    """
    end = time.monotonic() + max(0.0, float(deadline_s))
    interval = float(poll_interval_s)
    sleep_s = interval if interval > 0 else 1e-6
    sleep_s = max(sleep_s, 1e-6)

    while time.monotonic() < end:
        if ads1115_config_os_ready(bus, addr):
            return True
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(sleep_s, remaining))

    return ads1115_config_os_ready(bus, addr)


def ads1115_read_conversion_volts(bus: Any, addr: int, fsr_v: float) -> float:
    """Read conversion register (0x00) as signed voltage vs full-scale."""
    raw = bus.read_i2c_block_data(addr, 0x00, 2)
    val = (raw[0] << 8) | raw[1]
    if val & 0x8000:
        val -= 65536
    return val * _ads1115_volts_per_lsb(fsr_v)
