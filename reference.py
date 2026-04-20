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
import math
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
_ADS_ALRT_GPIO_SETUP: bool = False
# Set True after first GPIO.wait_for_edge RuntimeError so we poll instead of retrying.
_ADS_ALRT_WAIT_EDGE_BROKEN: bool = False


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
        # TI ADS1115: Lo_thresh MSB=0, Hi_thresh MSB=1 + COMP_QUE≠11 in config → ALERT/RDY pulses conversion-ready.
        try:
            sm.write_i2c_block_data(addr, 0x02, [0x7F, 0xFF])  # Lo_thresh
            sm.write_i2c_block_data(addr, 0x03, [0x80, 0x00])  # Hi_thresh
        except Exception as _alrt_err:
            print(
                f"[reference] ADS1115 ALERT/RDY threshold init skipped: {_alrt_err} "
                "(OC capture will fall back to polled conversion timing)"
            )
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
        dr = int(getattr(cfg, "REF_ADS1115_DR", 5))
        if n == 1:
            v = ads1115_read_single_ended(_ref_smbus, addr, ch, fsr, dr=dr)
            return v * 1000.0 * scale
        samples = [
            ads1115_read_single_ended(_ref_smbus, addr, ch, fsr, dr=dr) * 1000.0 * scale
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


def find_oc_inflection_mv(
    samples: list[tuple[float, float]],
    *,
    skip_rates: int | None = None,
    tail_exclude: float | None = None,
) -> float:
    """
    Pick mV at the decay-curve knee: minimum |dV/dt| over a guarded window of
    finite-difference rates (avoids IR-collapse tail and final flat tail).
    """
    if not samples:
        return 0.0
    if len(samples) < 4:
        return float(samples[-1][1])

    skip = int(
        skip_rates
        if skip_rates is not None
        else getattr(cfg, "COMMISSIONING_OC_INFLECTION_SKIP_RATES", 3)
    )
    tail = float(
        tail_exclude
        if tail_exclude is not None
        else getattr(cfg, "COMMISSIONING_OC_INFLECTION_TAIL_EXCLUDE", 0.2)
    )

    rates: list[float] = []
    for i in range(1, len(samples)):
        dt = float(samples[i][0] - samples[i - 1][0])
        dv = abs(float(samples[i][1]) - float(samples[i - 1][1]))
        rates.append(dv / max(dt, 1e-9))

    n_rates = len(rates)
    k_lo = max(0, min(skip, n_rates - 2))
    tail_n = max(0, int(n_rates * tail))
    k_hi = max(k_lo + 1, n_rates - tail_n)
    if k_hi <= k_lo:
        return float(samples[len(samples) // 2][1])

    sub = rates[k_lo:k_hi]
    # Round so near-identical |dV/dt| from float noise does not pick a late
    # segment; then prefer the earliest index on ties (first knee in window).
    best = min(range(len(sub)), key=lambda j: (round(sub[j], 6), j))
    k = k_lo + best
    idx = min(k + 1, len(samples) - 1)
    return float(samples[idx][1])


def _ensure_ads_alrt_gpio() -> int | None:
    """One-time BCM setup for ADS1115 ALERT/RDY (open-drain, active low)."""
    global _ADS_ALRT_GPIO_SETUP
    if SIM_MODE:
        return None
    pin = getattr(cfg, "ADS1115_ALRT_GPIO", None)
    if pin is None:
        return None
    if not _ADS_ALRT_GPIO_SETUP:
        import RPi.GPIO as GPIO  # noqa: N814

        GPIO.setwarnings(False)
        GPIO.setup(int(pin), GPIO.IN, pull_up_down=GPIO.PUD_UP)
        _ADS_ALRT_GPIO_SETUP = True
    return int(pin)


def _read_ads_mv_scaled_once(
    *,
    dr: int,
    use_alrt: bool,
    median_subsamples: int,
) -> float:
    """Single ADS1115 sample → mV-like scalar (mux, scale)."""
    from i2c_bench import (
        _ads1115_dr_conversion_s,
        ads1115_config_os_ready,
        ads1115_read_conversion_volts,
        ads1115_read_single_ended,
        ads1115_start_single_shot,
        mux_select_on_bus,
    )

    if _ref_smbus is None:
        raise RuntimeError("[reference] ADS1115 bus not open")

    mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None)
    mux_ch = getattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None)
    mux_select_on_bus(_ref_smbus, mux_addr, mux_ch)
    addr = int(getattr(cfg, "ADS1115_ADDRESS", 0x48))
    ch = int(getattr(cfg, "ADS1115_CHANNEL", 0))
    fsr = float(getattr(cfg, "ADS1115_FSR_V", 4.096))
    scale = float(getattr(cfg, "REF_ADS_SCALE", 1.0))
    pin = _ensure_ads_alrt_gpio() if use_alrt else None

    def _one_volt() -> float:
        if pin is not None and use_alrt:
            import RPi.GPIO as GPIO  # noqa: N814

            global _ADS_ALRT_WAIT_EDGE_BROKEN

            ads1115_start_single_shot(_ref_smbus, addr, ch, fsr, dr=dr)
            t_wait = _ads1115_dr_conversion_s(dr) * 2.0 + 0.005
            # RPi.GPIO wait_for_edge timeout is integer milliseconds, not seconds.
            timeout_ms = max(1, int(math.ceil(t_wait * 1000.0)))
            use_edge = bool(getattr(cfg, "ADS1115_ALRT_USE_WAIT_FOR_EDGE", True))
            if (
                use_edge
                and not _ADS_ALRT_WAIT_EDGE_BROKEN
                and not ads1115_config_os_ready(_ref_smbus, addr)
            ):
                try:
                    GPIO.wait_for_edge(pin, GPIO.FALLING, timeout=timeout_ms)
                except RuntimeError as exc:
                    _ADS_ALRT_WAIT_EDGE_BROKEN = True
                    print(
                        "[reference] WARNING: GPIO.wait_for_edge on ADS1115 ALRT failed "
                        f"({exc!s}); using polled conversion timing for the rest of this "
                        "OC capture. Each new `collect_oc_decay_samples()` run re-tries edge "
                        "wait. Set ADS1115_ALRT_USE_WAIT_FOR_EDGE=False or ADS1115_ALRT_GPIO=None "
                        "to skip; verify ALRT wiring / TI conversion-ready ALERT/RDY mode."
                    )
            if not ads1115_config_os_ready(_ref_smbus, addr):
                time.sleep(_ads1115_dr_conversion_s(dr) * 1.25 + 0.001)
            return float(ads1115_read_conversion_volts(_ref_smbus, addr, fsr))
        return float(ads1115_read_single_ended(_ref_smbus, addr, ch, fsr, dr=dr))

    n_sub = max(1, int(median_subsamples))
    if n_sub == 1:
        return round(_one_volt() * 1000.0 * scale, 4)
    sub = [_one_volt() * 1000.0 * scale for _ in range(n_sub)]
    return round(float(statistics.median(sub)), 4)


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

    def collect_oc_decay_samples(self) -> list[tuple[float, float]]:
        """
        After PWM cut: (elapsed_s, mV-like) points for OC inflection pick.
        INA219 ref backend → single sample (or time series in duration mode);
        SIM → synthetic decay; ADS1115 → burst or duration-window sampling.
        """
        global _ADS_ALRT_WAIT_EDGE_BROKEN

        _ADS_ALRT_WAIT_EDGE_BROKEN = False

        dr = int(getattr(cfg, "COMMISSIONING_ADS1115_DR", 7))
        med_sub = max(1, int(getattr(cfg, "COMMISSIONING_OC_ADS_MEDIAN_SAMPLES", 1)))
        use_alrt = bool(
            not SIM_MODE
            and getattr(cfg, "ADS1115_ALRT_GPIO", None) is not None
            and str(getattr(cfg, "REF_ADC_BACKEND", "ads1115")).lower() == "ads1115"
        )
        duration_mode = bool(getattr(cfg, "COMMISSIONING_OC_DURATION_MODE", False))
        n = max(1, int(getattr(cfg, "COMMISSIONING_OC_BURST_SAMPLES", 20)))
        interval = float(getattr(cfg, "COMMISSIONING_OC_BURST_INTERVAL_S", 0.01))
        duration_s = max(0.05, float(getattr(cfg, "COMMISSIONING_OC_CURVE_DURATION_S", 3.0)))
        poll_s = max(0.0, float(getattr(cfg, "COMMISSIONING_OC_CURVE_POLL_S", 0.002)))

        def _sim_point(elapsed: float) -> tuple[float, float]:
            base = float(getattr(cfg, "SIM_NATIVE_ZINC_MV", 200.0))
            mv = base - 45.0 * (1.0 - math.exp(-elapsed / 0.08)) + random.gauss(0, 0.8)
            return elapsed, round(mv, 2)

        if SIM_MODE:
            t0 = time.monotonic()
            out: list[tuple[float, float]] = []
            if duration_mode:
                while time.monotonic() - t0 < duration_s:
                    elapsed = time.monotonic() - t0
                    out.append(_sim_point(elapsed))
                    time.sleep(poll_s if poll_s > 0 else interval)
            else:
                for _ in range(n):
                    elapsed = time.monotonic() - t0
                    out.append(_sim_point(elapsed))
                    time.sleep(max(0.0, interval))
            return out

        backend = str(getattr(cfg, "REF_ADC_BACKEND", "ads1115")).lower()
        if backend == "ina219":
            if duration_mode and poll_s > 0.0:
                t0 = time.monotonic()
                series: list[tuple[float, float]] = []
                while time.monotonic() - t0 < duration_s:
                    elapsed = time.monotonic() - t0
                    try:
                        v = float(self.read())
                    except Exception:
                        v = 0.0
                    series.append((elapsed, v))
                    time.sleep(poll_s)
                if series:
                    return series
            try:
                v = float(self.read())
            except Exception:
                v = 0.0
            return [(0.0, v)]

        from i2c_bench import _ads1115_dr_conversion_s

        # Approximate I2C + conversion wall time so sleep does not add full poll/interval on top.
        conv_block = float(_ads1115_dr_conversion_s(dr)) * max(1, med_sub) * 1.15 + 0.002

        def _sleep_after_ads_sample(period_s: float) -> None:
            nap = max(0.0, float(period_s))
            if use_alrt and nap > 0:
                nap = max(0.0, nap - conv_block)
            time.sleep(nap)

        samples: list[tuple[float, float]] = []
        t0 = time.monotonic()
        if duration_mode:
            while time.monotonic() - t0 < duration_s:
                elapsed = time.monotonic() - t0
                mv = _read_ads_mv_scaled_once(
                    dr=dr,
                    use_alrt=use_alrt,
                    median_subsamples=med_sub,
                )
                samples.append((elapsed, float(mv)))
                _sleep_after_ads_sample(poll_s if poll_s > 0 else interval)
        else:
            for _ in range(n):
                elapsed = time.monotonic() - t0
                mv = _read_ads_mv_scaled_once(
                    dr=dr,
                    use_alrt=use_alrt,
                    median_subsamples=med_sub,
                )
                samples.append((elapsed, float(mv)))
                _sleep_after_ads_sample(interval)
        return samples

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
