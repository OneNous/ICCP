"""
CoilShield — optional deep I2C / runtime diagnostics for support snapshots.

Used by the main loop (touch-file trigger) and optional `latest.json` diag block.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import config.settings as cfg


def ref_diagnostic_flags() -> dict[str, Any]:
    """Lightweight reference / ALRT state without importing heavy subsystems twice."""
    import reference as ref

    return {
        "ref_adc_backend": str(getattr(ref, "_REF_BACKEND", "")),
        "ads_alrt_edge_wait_broken": bool(ref.ads_alrt_edge_wait_broken()),
        "ref_hw_ok": bool(ref.ref_hw_ok()),
        "ref_init_error": getattr(ref, "_REF_INIT_ERROR", None),
    }


def build_runtime_diag() -> dict[str, Any]:
    """Small dict for periodic inclusion in latest.json (when enabled)."""
    out: dict[str, Any] = {
        "ts_unix": time.time(),
        "ref": ref_diagnostic_flags(),
        "i2c_mux": {
            "address": getattr(cfg, "I2C_MUX_ADDRESS", None),
            "ina219_ports": getattr(cfg, "I2C_MUX_CHANNELS_INA219", None),
            "ina219_legacy_port": getattr(cfg, "I2C_MUX_CHANNEL_INA219", None),
            "ads1115_port": getattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None),
        },
        "ina219_addresses": [hex(a) for a in getattr(cfg, "INA219_ADDRESSES", ())],
    }
    return out


def build_deep_snapshot() -> dict[str, Any]:
    """Full smbus2 register dump (best effort; skip in sim or if buses unavailable)."""
    import os

    if os.environ.get("COILSHIELD_SIM", "0") == "1":
        return {"ok": False, "error": "sim mode — no I2C snapshot"}

    from i2c_bench import ads1115_read_config_word, ina219_diag_snapshot, mux_select_on_bus

    snap: dict[str, Any] = {
        "ok": True,
        "ts_unix": time.time(),
        "ref": ref_diagnostic_flags(),
        "ina219_anodes": [],
    }
    busnum = int(getattr(cfg, "I2C_BUS", 1))
    try:
        import smbus2

        sm = smbus2.SMBus(busnum)
    except Exception as e:
        return {"ok": False, "error": f"SMBus({busnum}): {e}"}

    try:
        mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None)
        per_mux = getattr(cfg, "I2C_MUX_CHANNELS_INA219", None)
        leg_mux = getattr(cfg, "I2C_MUX_CHANNEL_INA219", None)
        for idx, addr in enumerate(getattr(cfg, "INA219_ADDRESSES", ())):
            try:
                if mux_addr is not None:
                    if per_mux is not None and idx < len(per_mux):
                        mux_select_on_bus(sm, int(mux_addr), int(per_mux[idx]))
                    elif leg_mux is not None:
                        mux_select_on_bus(sm, int(mux_addr), int(leg_mux))
                snap["ina219_anodes"].append(
                    {"channel": idx, **ina219_diag_snapshot(sm, int(addr))}
                )
            except Exception as e:
                snap["ina219_anodes"].append(
                    {"channel": idx, "address": int(addr), "ok": False, "error": str(e)}
                )

        backend = str(getattr(cfg, "REF_ADC_BACKEND", "ads1115")).lower()
        if backend == "ads1115":
            ads_bus = int(getattr(cfg, "ADS1115_BUS", busnum))
            if ads_bus != busnum:
                sm.close()
                sm = smbus2.SMBus(ads_bus)
                busnum = ads_bus
            ads_addr = int(getattr(cfg, "ADS1115_ADDRESS", 0x48))
            mux_ch_ads = getattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None)
            mux_select_on_bus(sm, mux_addr, mux_ch_ads)
            try:
                w = ads1115_read_config_word(sm, ads_addr)
                snap["ads1115"] = {
                    "address": hex(ads_addr),
                    "config_hex": f"0x{w & 0xFFFF:04X}",
                    "comp_que": int(w & 3),
                }
            except Exception as e:
                snap["ads1115"] = {"error": str(e)}
        else:
            ref_bus = int(getattr(cfg, "REF_I2C_BUS", busnum))
            if ref_bus != busnum:
                sm.close()
                sm = smbus2.SMBus(ref_bus)
            ref_addr = int(getattr(cfg, "REF_INA219_ADDRESS", 0x40))
            sh = float(getattr(cfg, "REF_INA219_SHUNT_OHMS", 1.0))
            snap["ina219_ref"] = ina219_diag_snapshot(sm, ref_addr, shunt_ohm=sh)
    finally:
        try:
            sm.close()
        except Exception:
            pass

    return snap


def write_diagnostic_snapshot_atomic(path: Path) -> None:
    data = build_deep_snapshot()
    raw = json.dumps(data, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(path)
