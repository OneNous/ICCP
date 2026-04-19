"""
Pure smbus2 helpers for INA219 and ADS1115 (bench probe + reference path).

No pi-ina219 / Blinka dependency — works under `sudo` and user venv alike.
"""

from __future__ import annotations

import time
from typing import Any


def word_in(raw: int) -> int:
    """Linux SMBus read_word_data byte order → big-endian register value."""
    return ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)


def word_out(value: int) -> int:
    """Register value → Linux SMBus write_word_data byte order."""
    return ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)


def mux_select_on_bus(bus: Any, mux_addr: int | None, mux_ch: int | None) -> None:
    """TCA9548A: select downstream port (0–7). No-op if mux_addr or mux_ch is None."""
    if mux_addr is None or mux_ch is None:
        return
    if mux_ch < 0 or mux_ch > 7:
        raise ValueError("mux channel must be 0..7")
    bus.write_byte(int(mux_addr), 1 << int(mux_ch))


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
    """If bus voltage looks stale, write a sane CONFIG (16 V range, /1 PGA, continuous)."""
    try:
        raw_cfg = word_in(bus.read_word_data(addr, 0))
    except OSError:
        return
    if raw_cfg == 0:
        # 0x2197-style: 16 V bus, PGA x1, 12-bit, shunt+bus continuous (matches common breakouts).
        ina219_write_config(bus, addr, 0x2197)
        time.sleep(0.02)
        return
    _, raw_b = ina219_read_registers(bus, addr)
    bus_adc = (raw_b >> 3) & 0x1FFF
    if bus_adc == 0:
        # Use same CONFIG as cold-init so PGA stays /1 (10µV LSB); 0x399F uses PGA/8
        # in many strap defaults and would mismatch a fixed 10µV parse.
        ina219_write_config(bus, addr, 0x2197)
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


def _ads1115_config_word(channel: int, fsr_v: float) -> int:
    """Single-ended AINn vs GND, single-shot, data rate 250 SPS."""
    if channel not in (0, 1, 2, 3):
        raise ValueError("ADS1115 channel must be 0..3")
    mux = (4 + channel) << 12
    # PGA bits 11-9
    pga_map = {6.144: 0x0000, 4.096: 0x0200, 2.048: 0x0400, 1.024: 0x0600, 0.512: 0x0800, 0.256: 0x0A00}
    pga = pga_map.get(round(fsr_v, 3), 0x0200)
    # OS=1 start, MUX, PGA, MODE=1 single, DR=101 (250 SPS), comparator bits = 011
    return 0x8000 | mux | pga | 0x0100 | (5 << 5) | 3


def _ads1115_volts_per_lsb(fsr_v: float) -> float:
    return float(fsr_v) / 32768.0


def ads1115_read_single_ended(
    bus: Any,
    addr: int,
    channel: int,
    fsr_v: float = 4.096,
    *,
    conversion_delay_s: float = 0.004,
    poll_interval_s: float = 0.002,
    poll_max: int = 50,
) -> float:
    """Return voltage in volts (single-ended vs GND).

    Starts a single-shot conversion then polls TI ADS1115 Config register bit 15
    (OS) until the device reports not busy, or falls back to a fixed delay.
    """
    cfg = _ads1115_config_word(channel, fsr_v)
    bus.write_i2c_block_data(addr, 0x01, [(cfg >> 8) & 0xFF, cfg & 0xFF])
    # Poll Config[15] (OS): reads 1 when not converting (conversion complete).
    for _ in range(poll_max):
        time.sleep(poll_interval_s)
        hi, lo = bus.read_i2c_block_data(addr, 0x01, 2)
        status = ((hi << 8) | lo) & 0xFFFF
        if status & 0x8000:
            break
    else:
        time.sleep(max(0.0, conversion_delay_s))

    raw = bus.read_i2c_block_data(addr, 0x00, 2)
    val = (raw[0] << 8) | raw[1]
    if val & 0x8000:
        val -= 65536
    return val * _ads1115_volts_per_lsb(fsr_v)
