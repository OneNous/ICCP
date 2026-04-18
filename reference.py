"""
CoilShield — zinc reference electrode.

Reads zinc-rod-to-GND voltage via ESP32 ADC on IO12.
Tracks native potential baseline and computes protection shift.

SIM_MODE: COILSHIELD_SIM=1 uses simulated zinc readings (no hardware).
"""

from __future__ import annotations

import json
import os
import random
import time

import config.settings as cfg

SIM_MODE = os.environ.get("COILSHIELD_SIM", "0") == "1"

_COMM_FILE = cfg.PROJECT_ROOT / "commissioning.json"

if not SIM_MODE:
    try:
        from machine import ADC, Pin
        _adc = ADC(Pin(12))
        _adc.atten(ADC.ATTN_11DB)    # 0–3.3V range
        _adc.width(ADC.WIDTH_12BIT)  # 12-bit resolution: 0–4095
    except Exception as _hw_err:
        print(f"[reference] ADC init failed: {_hw_err}")
        _adc = None
else:
    _adc = None


def _read_raw_mv_hw() -> float:
    if _adc is None:
        return 0.0
    raw = _adc.read()
    return (raw / 4095.0) * 3300.0


def _read_raw_mv_sim(duties: dict[int, float], statuses: dict[int, str]) -> float:
    native = getattr(cfg, "SIM_NATIVE_ZINC_MV", 200.0)
    shift = 0.0
    for i in range(cfg.NUM_CHANNELS):
        if statuses.get(i) == "PROTECTING":
            d = duties.get(i, 0.0)
            shift += 25.0 * (d / max(cfg.PWM_MAX_DUTY, 1))
    return round(native + shift + random.gauss(0, 1.5), 2)


class ReferenceElectrode:
    """Zinc reference reader and shift tracker."""

    def __init__(self) -> None:
        self.native_mv: float | None = None
        self._last_raw_mv: float = 0.0

    def load_native(self) -> bool:
        if not _COMM_FILE.exists():
            return False
        try:
            data = json.loads(_COMM_FILE.read_text())
            self.native_mv = float(data["native_mv"])
            return True
        except Exception:
            return False

    def save_native(self, mv: float) -> None:
        self.native_mv = mv
        _update_comm_file(
            {
                "native_mv": mv,
                "native_measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )

    def read(
        self,
        duties: dict[int, float] | None = None,
        statuses: dict[int, str] | None = None,
    ) -> float:
        if SIM_MODE:
            mv = _read_raw_mv_sim(duties or {}, statuses or {})
        else:
            mv = _read_raw_mv_hw()
        self._last_raw_mv = mv
        return mv

    def shift_mv(
        self,
        duties: dict[int, float] | None = None,
        statuses: dict[int, str] | None = None,
    ) -> float | None:
        if self.native_mv is None:
            return None
        raw = self.read(duties, statuses)
        return round(raw - self.native_mv, 2)

    def protection_status(self, shift_mv: float | None = None) -> str:
        if shift_mv is None:
            return "UNKNOWN"
        lo = getattr(cfg, "TARGET_SHIFT_MV", 100)
        hi = getattr(cfg, "MAX_SHIFT_MV", 200)
        if shift_mv < lo * 0.8:
            return "UNDER"
        if shift_mv > hi:
            return "OVER"
        return "OK"

    @property
    def last_raw_mv(self) -> float:
        return self._last_raw_mv


def _update_comm_file(data: dict) -> None:
    existing: dict = {}
    if _COMM_FILE.exists():
        try:
            existing = json.loads(_COMM_FILE.read_text())
        except Exception:
            pass
    existing.update(data)
    _COMM_FILE.write_text(json.dumps(existing, indent=2))