"""
CoilShield — reference electrode (polarization shift input).

Hardware backends (see config.settings):
  • **ads1115** (default): ADS1115 @ `ADS1115_ADDRESS` on `ADS1115_BUS`, single-ended
    channel `ADS1115_CHANNEL`; raw scalar = volts × 1000 × `REF_ADS_SCALE` (mV-like).
  • **ina219**: legacy dedicated INA219 on `REF_I2C_BUS` / `REF_INA219_ADDRESS`.

SIM_MODE: COILSHIELD_SIM=1 uses simulated readings (no hardware).
"""

from __future__ import annotations

import json
import os
import random
import statistics
import time
from typing import Any

import config.settings as cfg

SIM_MODE = os.environ.get("COILSHIELD_SIM", "0") == "1"

_COMM_FILE = cfg.PROJECT_ROOT / "commissioning.json"

_REF_ENABLED: bool = bool(getattr(cfg, "REF_ENABLED", True))
_REF_BACKEND: str = str(getattr(cfg, "REF_ADC_BACKEND", "ads1115")).lower().strip()

_ref_ina: object | None = None
_ref_smbus: Any | None = None
_REF_INIT_ERROR: str | None = None
_REF_I2C_BUS: int = int(getattr(cfg, "REF_I2C_BUS", cfg.I2C_BUS))


def _ina219_scalar_mv(sensor: object, source: str) -> float:
    """Map INA219 readings to the mV-like scalar used for native_mv / shift_mv."""
    if source == "shunt_mv":
        return float(sensor.shunt_voltage())
    return float(sensor.voltage()) * 1000.0


def _init_ref_ina219() -> None:
    global _ref_ina, _REF_INIT_ERROR
    try:
        from ina219 import INA219  # type: ignore[import-untyped]

        _ref_ina = INA219(
            cfg.REF_INA219_SHUNT_OHMS,
            address=cfg.REF_INA219_ADDRESS,
            busnum=_REF_I2C_BUS,
        )
        # Same CONFIG tuple as sensors.py; matches i2c_bench.INA219_DEFAULT_CONFIG_WORD.
        _ref_ina.configure(
            voltage_range=INA219.RANGE_16V,
            gain=INA219.GAIN_AUTO,
            bus_adc=INA219.ADC_128SAMP,
            shunt_adc=INA219.ADC_128SAMP,
        )
        print(
            f"[reference] INA219 ref OK at {hex(cfg.REF_INA219_ADDRESS)} "
            f"on i2c-{_REF_I2C_BUS} (source={cfg.REF_INA219_SOURCE!r})"
        )
    except Exception as _hw_err:
        _REF_INIT_ERROR = str(_hw_err)
        print(f"[reference] INA219 ref init failed: {_hw_err}")
        _ref_ina = None


def _init_ref_ads1115() -> None:
    global _ref_smbus, _REF_INIT_ERROR
    sm = None
    try:
        import smbus2

        from i2c_bench import ads1115_read_single_ended, mux_select_on_bus

        busnum = int(getattr(cfg, "ADS1115_BUS", cfg.I2C_BUS))
        addr = int(getattr(cfg, "ADS1115_ADDRESS", 0x48))
        sm = smbus2.SMBus(busnum)
        mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None)
        mux_ch = getattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None)
        mux_select_on_bus(sm, mux_addr, mux_ch)
        ch = int(getattr(cfg, "ADS1115_CHANNEL", 0))
        fsr = float(getattr(cfg, "ADS1115_FSR_V", 4.096))
        ads1115_read_single_ended(sm, addr, ch, fsr)
        _ref_smbus = sm
        sm = None
        kind = getattr(cfg, "REF_ELECTRODE_KIND", "unknown")
        print(
            f"[reference] ADS1115 OK ch AIN{ch} @ {hex(addr)} i2c-{busnum} "
            f"(±{fsr} V, electrode={kind!r})"
        )
    except Exception as _hw_err:
        _REF_INIT_ERROR = str(_hw_err)
        print(f"[reference] ADS1115 init failed: {_hw_err}")
        _ref_smbus = None
    finally:
        if sm is not None:
            try:
                sm.close()
            except Exception:
                pass


if not SIM_MODE and _REF_ENABLED:
    if _REF_BACKEND == "ina219":
        _init_ref_ina219()
    else:
        _init_ref_ads1115()


def ref_hw_ok() -> bool:
    """True if simulator, disabled, or reference hardware initialized."""
    if SIM_MODE or not _REF_ENABLED:
        return True
    if _REF_BACKEND == "ina219":
        return _ref_ina is not None
    return _ref_smbus is not None


def ref_hw_message() -> str:
    """One-line status for console / dashboard."""
    if not _REF_ENABLED:
        return "disabled"
    if SIM_MODE:
        return "sim (no ref ADC)"
    if _REF_BACKEND == "ina219":
        if _ref_ina is not None:
            return f"INA219 OK {hex(cfg.REF_INA219_ADDRESS)} i2c-{_REF_I2C_BUS}"
    elif _ref_smbus is not None:
        ch = int(getattr(cfg, "ADS1115_CHANNEL", 0))
        return (
            f"ADS1115 OK {hex(cfg.ADS1115_ADDRESS)} "
            f"AIN{ch} i2c-{getattr(cfg, 'ADS1115_BUS', cfg.I2C_BUS)}"
        )
    err = (_REF_INIT_ERROR or "unknown").replace("\n", " ")
    if len(err) > 72:
        err = err[:69] + "..."
    return f"ref ADC fault: {err}"


def ref_ux_hint(*, baseline_set: bool, hw_ok: bool, skip_commission: bool) -> str:
    """Short banner text for dashboard / one-shot console tip."""
    if not _REF_ENABLED:
        return ""
    if not hw_ok and not SIM_MODE:
        return "Reference ADC not reachable — check I2C / mux / wiring."
    if baseline_set:
        return ""
    if skip_commission:
        return "Run without --skip-commission to record native_mv and enable polarization shift."
    return ""


def _read_raw_mv_hw() -> float:
    if _REF_BACKEND == "ina219":
        if _ref_ina is None:
            raise RuntimeError("[reference] INA219 unavailable — check I2C wiring and address")
        try:
            src = getattr(cfg, "REF_INA219_SOURCE", "bus_v")
            n = max(1, int(getattr(cfg, "REF_INA219_MEDIAN_SAMPLES", 1)))
            if n == 1:
                return _ina219_scalar_mv(_ref_ina, src)
            samples = [_ina219_scalar_mv(_ref_ina, src) for _ in range(n)]
            return float(statistics.median(samples))
        except Exception as e:
            print(f"[reference] INA219 read failed: {e}")
            return 0.0

    if _ref_smbus is None:
        raise RuntimeError("[reference] ADS1115 unavailable — check I2C wiring and address")
    try:
        from i2c_bench import ads1115_read_single_ended, mux_select_on_bus

        mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None)
        mux_ch = getattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None)
        mux_select_on_bus(_ref_smbus, mux_addr, mux_ch)
        addr = int(getattr(cfg, "ADS1115_ADDRESS", 0x48))
        ch = int(getattr(cfg, "ADS1115_CHANNEL", 0))
        fsr = float(getattr(cfg, "ADS1115_FSR_V", 4.096))
        scale = float(getattr(cfg, "REF_ADS_SCALE", 1.0))
        n = max(1, int(getattr(cfg, "REF_ADS_MEDIAN_SAMPLES", getattr(cfg, "REF_INA219_MEDIAN_SAMPLES", 1))))
        if n == 1:
            v = ads1115_read_single_ended(_ref_smbus, addr, ch, fsr)
            return v * 1000.0 * scale
        samples = [
            ads1115_read_single_ended(_ref_smbus, addr, ch, fsr) * 1000.0 * scale
            for _ in range(n)
        ]
        return float(statistics.median(samples))
    except Exception as e:
        print(f"[reference] ADS1115 read failed: {e}")
        return 0.0


def _read_raw_mv_sim(duties: dict[int, float], statuses: dict[int, str]) -> float:
    native = getattr(cfg, "SIM_NATIVE_ZINC_MV", 200.0)
    shift = 0.0
    for i in range(cfg.NUM_CHANNELS):
        st = statuses.get(i)
        d = duties.get(i, 0.0)
        norm = d / max(cfg.PWM_MAX_DUTY, 1)
        if st == "PROTECTING":
            shift += 25.0 * norm
        elif st == "REGULATE":
            shift += 18.0 * norm
        elif st == "OPEN":
            shift += 0.0
    # Under CP the mV-like scalar falls vs native; model raw = native − effect so
    # shift_mv = native − raw stays positive when protected (matches hardware).
    return round(native - shift + random.gauss(0, 1.5), 2)


class ReferenceElectrode:
    """Reference electrode reader and shift tracker."""

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
        ts = (
            time.strftime("%Y-%m-%dT%H:%M:%S")
            if time.time() > 1_000_000_000
            else "CLOCK_UNSYNCED"
        )
        _update_comm_file({"native_mv": mv, "native_measured_at": ts})

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
        """Polarization vs native: native_mv − raw (positive when reading drops under CP)."""
        if self.native_mv is None:
            return None
        raw = self.read(duties, statuses)
        return round(self.native_mv - raw, 2)

    def read_raw_and_shift(
        self,
        duties: dict[int, float] | None = None,
        statuses: dict[int, str] | None = None,
    ) -> tuple[float, float | None]:
        try:
            raw = self.read(duties, statuses)
        except RuntimeError as e:
            print(e)
            return 0.0, None
        if self.native_mv is None:
            return raw, None
        return raw, round(self.native_mv - raw, 2)

    def protection_status(self, shift_mv: float | None = None) -> str:
        """Band vs TARGET_SHIFT_MV / MAX_SHIFT_MV for shift = native − raw (not a CP survey criterion)."""
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
