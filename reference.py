"""
CoilShield — reference electrode (zinc or similar).

Reads the reference node via a **dedicated INA219** on I2C (`REF_INA219_ADDRESS`),
same driver stack as anode channels in `sensors.py`. Tracks native baseline and
computes protection shift (mV) for the outer loop.

SIM_MODE: COILSHIELD_SIM=1 uses simulated readings (no hardware).
"""

from __future__ import annotations

import json
import os
import random
import statistics
import time

import config.settings as cfg

SIM_MODE = os.environ.get("COILSHIELD_SIM", "0") == "1"

_COMM_FILE = cfg.PROJECT_ROOT / "commissioning.json"

_ref_ina: object | None = None
_REF_INIT_ERROR: str | None = None
_REF_I2C_BUS: int = int(getattr(cfg, "REF_I2C_BUS", cfg.I2C_BUS))

# When False, no ref INA219 is constructed (no I2C traffic on REF_I2C_BUS).
_REF_ENABLED: bool = bool(getattr(cfg, "REF_ENABLED", True))

if not SIM_MODE and _REF_ENABLED:
    try:
        from ina219 import INA219

        _ref_ina = INA219(
            cfg.REF_INA219_SHUNT_OHMS,
            address=cfg.REF_INA219_ADDRESS,
            busnum=_REF_I2C_BUS,
        )
        _ref_ina.configure(
            voltage_range=INA219.RANGE_16V,
            gain=INA219.GAIN_AUTO,
            bus_adc=INA219.ADC_128SAMP,
            shunt_adc=INA219.ADC_128SAMP,
        )
        print(
            f"[reference] INA219 ref init OK at {hex(cfg.REF_INA219_ADDRESS)} "
            f"on i2c-{_REF_I2C_BUS} (source={cfg.REF_INA219_SOURCE!r})"
        )
    except Exception as _hw_err:
        _REF_INIT_ERROR = str(_hw_err)
        print(f"[reference] INA219 ref init failed: {_hw_err}")
        _ref_ina = None


def ref_hw_ok() -> bool:
    """True if simulator, disabled, or reference INA219 initialized."""
    if SIM_MODE or not _REF_ENABLED:
        return True
    return _ref_ina is not None


def ref_hw_message() -> str:
    """One-line status for console / dashboard."""
    if not _REF_ENABLED:
        return "disabled"
    if SIM_MODE:
        return "sim (no ref INA219)"
    if _ref_ina is not None:
        return f"INA219 OK {hex(cfg.REF_INA219_ADDRESS)} i2c-{_REF_I2C_BUS}"
    err = (_REF_INIT_ERROR or "unknown").replace("\n", " ")
    if len(err) > 72:
        err = err[:69] + "..."
    return f"INA219 fault: {err}"


def ref_ux_hint(*, baseline_set: bool, hw_ok: bool, skip_commission: bool) -> str:
    """Short banner text for dashboard / one-shot console tip."""
    if not _REF_ENABLED:
        return ""
    if not hw_ok and not SIM_MODE:
        return "Reference INA219 not reachable — check I2C address and wiring."
    if baseline_set:
        return ""
    if skip_commission:
        return "Run without --skip-commission to record native_mv and enable polarization shift."
    return ""


def _ina219_scalar_mv(sensor: object, source: str) -> float:
    """Map INA219 readings to the mV-like scalar used for native_mv / shift_mv."""
    if source == "shunt_mv":
        return float(sensor.shunt_voltage())
    return float(sensor.voltage()) * 1000.0


def _read_raw_mv_hw() -> float:
    if _ref_ina is None:
        raise RuntimeError("[reference] INA219 unavailable — check I2C wiring and address")
    try:
        src = getattr(cfg, "REF_INA219_SOURCE", "bus_v")
        n = max(1, int(getattr(cfg, "REF_INA219_MEDIAN_SAMPLES", 1)))
        if n == 1:
            return _ina219_scalar_mv(_ref_ina, src)
        samples = [_ina219_scalar_mv(_ref_ina, src) for _ in range(n)]
        return float(statistics.median(samples))
    except RuntimeError:
        raise
    except Exception as e:
        print(f"[reference] INA219 read failed: {e}")
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
    return round(native + shift + random.gauss(0, 1.5), 2)


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
        if self.native_mv is None:
            return None
        raw = self.read(duties, statuses)
        return round(raw - self.native_mv, 2)

    def read_raw_and_shift(
        self,
        duties: dict[int, float] | None = None,
        statuses: dict[int, str] | None = None,
    ) -> tuple[float, float | None]:
        """Single INA219 sample; shift vs native when baseline exists."""
        try:
            raw = self.read(duties, statuses)
        except RuntimeError as e:
            print(e)
            return 0.0, None
        if self.native_mv is None:
            return raw, None
        return raw, round(raw - self.native_mv, 2)

    def protection_status(self, shift_mv: float | None = None) -> str:
        """Band vs TARGET_SHIFT_MV / MAX_SHIFT_MV (not an industry CP criterion)."""
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
