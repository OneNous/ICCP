"""
CoilShield — reference electrode (polarization shift input).

Hardware backends (see config.settings):
  • **ads1115** (default): ADS1115 @ `ADS1115_ADDRESS` on `ADS1115_BUS`, single-ended
    channel `ADS1115_CHANNEL`; raw scalar = volts × 1000 × effective scale (mV-like).
    Scale is `REF_ADS_SCALE` (and env `COILSHIELD_REF_ADS_SCALE`), optionally overridden by
    numeric ``ref_ads_scale`` in `commissioning.json` after `load_native` / commissioning updates.
  • **ina219**: legacy dedicated INA219 on `REF_I2C_BUS` / `REF_INA219_ADDRESS`.

Optional pan-temperature trim (°F only): ``native_temp_f`` in `commissioning.json` with
`REF_TEMP_COMP_MV_PER_F` adjusts raw mV vs that anchor; if ``native_temp_f`` is missing,
`REF_TEMP_COMP_BASE_F` (default 77 °F ≈ 25 °C) is the anchor.

SIM_MODE: COILSHIELD_SIM=1 uses simulated readings (no hardware).
"""

from __future__ import annotations

import json
import math
import os
import platform
import random
import statistics
import sys
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any

import config.settings as cfg

SIM_MODE = os.environ.get("COILSHIELD_SIM", "0") == "1"

_COMM_FILE = cfg.PROJECT_ROOT / "commissioning.json"


def _atomic_write_json_same_dir(path: Path, data: dict) -> None:
    """Write JSON atomically (temp + replace) so crashes never leave a half file."""
    text = json.dumps(data, indent=2)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)

_REF_ENABLED: bool = bool(getattr(cfg, "REF_ENABLED", True))
_REF_BACKEND: str = str(getattr(cfg, "REF_ADC_BACKEND", "ads1115")).lower().strip()

_ref_ina: object | None = None
_ref_smbus: Any | None = None
_REF_INIT_ERROR: str | None = None
_REF_I2C_BUS: int = int(getattr(cfg, "REF_I2C_BUS", cfg.I2C_BUS))
_ADS_ALRT_GPIO_SETUP: bool = False
# Set True after first GPIO.wait_for_edge RuntimeError; stays True for process lifetime
# so we do not re-arm wait_for_edge on every OC burst (avoids log spam on Bookworm).
_ADS_ALRT_WAIT_EDGE_BROKEN: bool = False
_ALRT_DIAG_LOGGED_RUNTIME: bool = False
_ALRT_DIAG_LOGGED_TIMEOUT: bool = False
_OS_WAIT_FAIL_LOGGED: bool = False

# None → use cfg.REF_ADS_SCALE only; float → commissioning.json ``ref_ads_scale`` override.
_COMM_REF_ADS_SCALE: float | None = None

# True if the last hardware read returned 0.0 because of a trapped OSError — surfaces
# up through ReferenceElectrode.ref_valid() so control can raise REFERENCE_INVALID
# (see docs/iccp-requirements.md §6.1). Module-level because the hw helpers are module-level.
_REF_LAST_READ_FAILED: bool = False
# After import failed to open ADS, first read() runs _init_ref_ads1115() once (extra chance).
_REF_ADS_LAZY_REINIT_RAN: bool = False


def _reload_comm_ref_ads_scale() -> None:
    """Reload ``ref_ads_scale`` from commissioning.json (called after load_native / _update_comm_file)."""
    global _COMM_REF_ADS_SCALE
    _COMM_REF_ADS_SCALE = None
    if not _COMM_FILE.exists():
        return
    try:
        data = json.loads(_COMM_FILE.read_text(encoding="utf-8"))
        if "ref_ads_scale" in data and data["ref_ads_scale"] is not None:
            _COMM_REF_ADS_SCALE = float(data["ref_ads_scale"])
    except json.JSONDecodeError as e:
        print(
            f"[reference] commissioning.json invalid JSON (ref_ads_scale reload): {e}",
            file=sys.stderr,
        )
    except (OSError, TypeError, ValueError) as e:
        print(
            f"[reference] commissioning.json read error (ref_ads_scale reload): {e}",
            file=sys.stderr,
        )


def _effective_ref_ads_scale() -> float:
    if _COMM_REF_ADS_SCALE is not None:
        return float(_COMM_REF_ADS_SCALE)
    return float(getattr(cfg, "REF_ADS_SCALE", 1.0))


def ads_alrt_edge_wait_broken() -> bool:
    """True after first GPIO.wait_for_edge RuntimeError on ADS1115 ALRT (process lifetime)."""
    return _ADS_ALRT_WAIT_EDGE_BROKEN


def _ads_gpio_module_hint() -> str:
    try:
        import RPi.GPIO as GPIO  # noqa: N814

        return str(getattr(GPIO, "__file__", repr(GPIO)))
    except Exception as e:
        return f"(no RPi.GPIO: {e})"


def _ads1115_config_comp_que_bits(bus: Any, addr: int) -> int | None:
    try:
        from i2c_bench import ads1115_read_config_word

        w = ads1115_read_config_word(bus, addr)
        return int(w & 3)
    except Exception:
        return None


def _ads1115_alrt_diag_lines(
    *,
    pin: int,
    addr: int,
    ch: int,
    dr: int,
    timeout_ms: int,
    bus: Any,
    gpio_input_before: int | None = None,
    gpio_input_after: int | None = None,
    os_ready_before_edge: bool | None = None,
    exc: BaseException | None = None,
    timeout: bool = False,
) -> list[str]:
    lines = [
        "[reference] DIAG: ADS1115 ALRT / wait_for_edge",
        f"  sys.executable={sys.executable}",
        f"  kernel={platform.release()}  RPi.GPIO={_ads_gpio_module_hint()}",
        f"  ALRT_BCM={pin}  ADS1115_addr={hex(addr)}  channel=AIN{ch}  DR={dr}  timeout_ms={timeout_ms}",
    ]
    if gpio_input_before is not None:
        lines.append(
            f"  GPIO.input before wait={gpio_input_before} (1=idle high with pull-up)"
        )
    if os_ready_before_edge is not None:
        lines.append(
            f"  OS-ready before wait={os_ready_before_edge} (False expected to arm edge path)"
        )
    cq = _ads1115_config_comp_que_bits(bus, addr)
    if cq is not None:
        lines.append(
            f"  ADS1115 config low bits COMP_QUE={cq} (0b00 = conversion-ready style per TI)"
        )
    if timeout:
        lines.append(
            "  wait_for_edge returned None (timeout) — no falling edge in window."
        )
    if gpio_input_after is not None:
        lines.append(f"  GPIO.input after wait={gpio_input_after}")
    if exc is not None:
        lines.append(f"  exception={exc!r}")
    lines.append(
        "  hints: Bookworm/6.x often needs `rpi-lgpio` in the same venv as iccp; "
        "set ADS1115_ALRT_USE_WAIT_FOR_EDGE=False to skip edges; verify ALRT wiring "
        "and that threshold init printed OK (not skipped)."
    )
    return lines


def _print_alrt_diag_bundle(lines: list[str], *, kind: str) -> None:
    global _ALRT_DIAG_LOGGED_RUNTIME, _ALRT_DIAG_LOGGED_TIMEOUT
    if kind == "runtime" and _ALRT_DIAG_LOGGED_RUNTIME:
        return
    if kind == "timeout" and _ALRT_DIAG_LOGGED_TIMEOUT:
        return
    for ln in lines:
        print(ln)
    if kind == "runtime":
        _ALRT_DIAG_LOGGED_RUNTIME = True
    elif kind == "timeout":
        _ALRT_DIAG_LOGGED_TIMEOUT = True


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
        addrs = ", ".join(hex(a) for a in getattr(cfg, "INA219_ADDRESSES", ()))
        print(
            f"[reference] INA219 ref init failed: {_hw_err} "
            f"(ref={hex(cfg.REF_INA219_ADDRESS)} i2c-{_REF_I2C_BUS}; "
            f"anode INA219 addresses on same bus must differ: [{addrs}])"
        )
        _ref_ina = None


def _i2c_transient_errno(e: BaseException) -> bool:
    """True if ``e`` is an OSError/TimeoutError with a errno we retry (mux/ADS/INA)."""
    if not isinstance(e, OSError):
        return False
    en = getattr(e, "errno", None)
    if en is None:
        return False
    t = tuple(int(x) for x in getattr(cfg, "I2C_TRANSIENT_ERRNOS", (5, 121, 110)))
    return int(en) in t


def _init_ref_ads1115() -> None:
    """Open SMBus, mux to ADS1115, test read; multiple attempts on EIO (busy I²C at import)."""
    global _ref_smbus, _REF_INIT_ERROR
    max_a = max(1, int(getattr(cfg, "REF_ADS1115_INIT_MAX_ATTEMPTS", 12)))
    delay0 = max(0.0, float(getattr(cfg, "REF_ADS1115_INIT_RETRY_DELAY_S", 0.12)))
    last_err: BaseException | None = None
    for attempt in range(1, max_a + 1):
        sm: Any = None
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
            fsr = float(getattr(cfg, "ADS1115_FSR_V", 2.048))
            ads1115_read_single_ended(sm, addr, ch, fsr)
            try:
                sm.write_i2c_block_data(addr, 0x02, [0x7F, 0xFF])  # Lo_thresh
                sm.write_i2c_block_data(addr, 0x03, [0x80, 0x00])  # Hi_thresh
                print(
                    "[reference] ADS1115 ALERT/RDY threshold registers OK "
                    "(Lo/Hi for TI conversion-ready ALERT pulsing; COMP_QUE≠11 in config word)"
                )
            except Exception as _alrt_err:
                print(
                    f"[reference] ADS1115 ALERT/RDY threshold init skipped: {_alrt_err} "
                    "(OC capture will fall back to polled conversion timing)"
                )
            _ref_smbus = sm
            sm = None
            _REF_INIT_ERROR = None
            kind = getattr(cfg, "REF_ELECTRODE_KIND", "unknown")
            tag = f" (attempt {attempt} of {max_a})" if attempt > 1 else ""
            print(
                f"[reference] ADS1115 OK ch AIN{ch} @ {hex(addr)} i2c-{busnum} "
                f"(±{fsr} V, electrode={kind!r}){tag}"
            )
            return
        except KeyboardInterrupt:
            if sm is not None:
                try:
                    sm.close()
                except Exception:
                    pass
            print(
                "\n[reference] ADS1115 init interrupted (Ctrl+C).",
                file=sys.stderr,
            )
            raise
        except Exception as e:
            last_err = e
            _REF_INIT_ERROR = str(e)
            if sm is not None:
                try:
                    sm.close()
                except Exception:
                    pass
            if attempt < max_a:
                w = min(2.0, delay0 * attempt)
                en = getattr(e, "errno", None)
                es = f" errno {en}" if en is not None else ""
                print(
                    f"[reference] ADS1115 init attempt {attempt}/{max_a} failed:{es} {e!s} — "
                    f"retrying in {w:.2f}s"
                )
                try:
                    time.sleep(w)
                except KeyboardInterrupt:
                    print(
                        "\n[reference] ADS1115 init interrupted (Ctrl+C).",
                        file=sys.stderr,
                    )
                    raise
    _ref_smbus = None
    _REF_INIT_ERROR = str(last_err) if last_err else "init failed"
    print(
        f"[reference] ADS1115 init failed after {max_a} attempt(s). Last: {_REF_INIT_ERROR}"
    )


if not SIM_MODE and _REF_ENABLED:
    if _REF_BACKEND == "ina219":
        _init_ref_ina219()
    else:
        _init_ref_ads1115()

_reload_comm_ref_ads_scale()


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
    global _REF_LAST_READ_FAILED, _REF_ADS_LAZY_REINIT_RAN
    from i2c_bench import (
        ads1115_read_single_ended,
        i2c_bus_lock,
        ina219_diag_snapshot,
        mux_select_on_bus,
    )

    if _REF_BACKEND == "ina219":
        if _ref_ina is None:
            raise RuntimeError("[reference] INA219 unavailable — check I2C wiring and address")
        bus_lock = int(_REF_I2C_BUS)
        try:
            for attempt in range(2):
                try:
                    with i2c_bus_lock(bus_lock):
                        src = getattr(cfg, "REF_INA219_SOURCE", "bus_v")
                        n = max(1, int(getattr(cfg, "REF_INA219_MEDIAN_SAMPLES", 1)))
                        if n == 1:
                            val = _ina219_scalar_mv(_ref_ina, src)
                            _REF_LAST_READ_FAILED = False
                            return val
                        samples = [_ina219_scalar_mv(_ref_ina, src) for _ in range(n)]
                        _REF_LAST_READ_FAILED = False
                        return float(statistics.median(samples))
                except OSError as e:
                    if _i2c_transient_errno(e) and attempt == 0:
                        time.sleep(0.003)
                        continue
                    raise
        except Exception as e:
            _REF_LAST_READ_FAILED = True
            print(f"[reference] INA219 read failed: {e}")
            try:
                import smbus2

                with i2c_bus_lock(bus_lock):
                    sm = smbus2.SMBus(bus_lock)
                    try:
                        snap = ina219_diag_snapshot(
                            sm,
                            int(cfg.REF_INA219_ADDRESS),
                            shunt_ohm=float(cfg.REF_INA219_SHUNT_OHMS),
                        )
                        print(f"[reference] DIAG INA219 ref snapshot: {snap!r}")
                    finally:
                        sm.close()
            except Exception as snap_e:
                print(f"[reference] DIAG INA219 ref snapshot skipped: {snap_e}")
            return 0.0

    if _ref_smbus is None:
        if not _REF_ADS_LAZY_REINIT_RAN:
            _REF_ADS_LAZY_REINIT_RAN = True
            print(
                "[reference] ADS1115 bus not open at read — re-running init (one lazy retry)…"
            )
            _init_ref_ads1115()
    if _ref_smbus is None:
        raise RuntimeError("[reference] ADS1115 unavailable — check I2C wiring and address")
    busnum = int(getattr(cfg, "ADS1115_BUS", cfg.I2C_BUS))
    mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None)
    mux_ch = getattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None)
    addr = int(getattr(cfg, "ADS1115_ADDRESS", 0x48))
    ch = int(getattr(cfg, "ADS1115_CHANNEL", 0))
    fsr = float(getattr(cfg, "ADS1115_FSR_V", 2.048))
    scale = _effective_ref_ads_scale()
    n = max(
        1,
        int(
            getattr(
                cfg,
                "REF_ADS_MEDIAN_SAMPLES",
                getattr(cfg, "REF_INA219_MEDIAN_SAMPLES", 1),
            )
        ),
    )
    dr = int(getattr(cfg, "REF_ADS1115_DR", 5))
    for attempt in range(2):
        try:
            with i2c_bus_lock(busnum):
                mux_select_on_bus(_ref_smbus, mux_addr, mux_ch)
                if n == 1:
                    v = ads1115_read_single_ended(_ref_smbus, addr, ch, fsr, dr=dr)
                    _REF_LAST_READ_FAILED = False
                    return v * 1000.0 * scale
                samples = [
                    ads1115_read_single_ended(_ref_smbus, addr, ch, fsr, dr=dr)
                    * 1000.0
                    * scale
                    for _ in range(n)
                ]
                _REF_LAST_READ_FAILED = False
                return float(statistics.median(samples))
        except OSError as e:
            if _i2c_transient_errno(e) and attempt == 0:
                time.sleep(0.003)
                continue
            _REF_LAST_READ_FAILED = True
            print(f"[reference] ADS1115 read failed: {e}")
            return 0.0
        except Exception as e:
            _REF_LAST_READ_FAILED = True
            print(f"[reference] ADS1115 read failed: {e}")
            return 0.0
    _REF_LAST_READ_FAILED = True
    print("[reference] ADS1115 read failed: repeated I/O error (transient I²C errnos)")
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
    # shift_mv = baseline_mv_for_shift − raw stays positive when protected (matches hardware).
    return round(native - shift + random.gauss(0, 1.5), 2)


def _linear_regression_slope_mv_s(points: list[tuple[float, float]]) -> float:
    """Signed dV/dt (mV/s) for V(t); t may be absolute, shifted internally."""
    if len(points) < 2:
        return 0.0
    t0 = float(points[0][0])
    t = [float(p[0]) - t0 for p in points]
    v = [float(p[1]) for p in points]
    n = len(points)
    sum_t = sum(t)
    sum_v = sum(v)
    sum_tt = sum(x * x for x in t)
    sum_tv = sum(t[i] * v[i] for i in range(n))
    den = n * sum_tt - sum_t * sum_t
    if abs(den) < 1e-18:
        return 0.0
    return (n * sum_tv - sum_t * sum_v) / den


def find_oc_curve_metrics(
    samples: list[tuple[float, float]],
    *,
    skip_rates: int | None = None,
    tail_exclude: float | None = None,
) -> tuple[float, float]:
    """
    Open-circuit decay curve: inflection mV (knee) and signed depolarization slope (mV/s).

    Knee: smallest |dV/dt| in the guarded window, preferring segments where dV < 0 (decay)
    so a post-cut rising transient does not dominate. Depolarization slope is a linear
    regression on samples **from the knee onward** through the same pre-tail time bound
    (not the whole pre-knee band), so the reported mV/s reflects post-knee decay.
    """
    if not samples:
        return (0.0, 0.0)
    if len(samples) < 4:
        return (float(samples[-1][1]), 0.0)

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

    signed_rates: list[float] = []
    for i in range(1, len(samples)):
        dt = float(samples[i][0] - samples[i - 1][0])
        dv = float(samples[i][1]) - float(samples[i - 1][1])
        signed_rates.append(dv / max(dt, 1e-9))

    n_rates = len(signed_rates)
    k_lo = max(0, min(skip, n_rates - 2))
    tail_n = max(0, int(n_rates * tail))
    k_hi = max(k_lo + 1, n_rates - tail_n)
    if k_hi <= k_lo:
        mid = float(samples[len(samples) // 2][1])
        seg = samples[max(0, len(samples) // 4) :]
        return (mid, _linear_regression_slope_mv_s(seg))

    window = list(range(k_lo, min(k_hi, n_rates)))
    decay_idx = [j for j in window if signed_rates[j] < 0.0]
    pool = decay_idx if decay_idx else window
    # Round |dV/dt| so float noise does not push the knee to the far end of a constant-slope tail.
    k = min(pool, key=lambda j: (round(abs(signed_rates[j]), 6), j))
    idx = min(k + 1, len(samples) - 1)
    inf_mv = float(samples[idx][1])

    hi_s = min(k_hi, len(samples) - 1)
    start_s = min(idx, hi_s)
    if start_s >= hi_s:
        seg = samples[max(0, hi_s - 3) : hi_s + 1]
    else:
        seg = samples[start_s : hi_s + 1]
    depol = _linear_regression_slope_mv_s(seg)
    return (inf_mv, round(depol, 6))


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
    return find_oc_curve_metrics(
        samples, skip_rates=skip_rates, tail_exclude=tail_exclude
    )[0]


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
        ads1115_wait_os_ready,
        i2c_bus_lock,
        mux_select_on_bus,
    )

    if _ref_smbus is None:
        raise RuntimeError("[reference] ADS1115 bus not open")

    busnum = int(getattr(cfg, "ADS1115_BUS", cfg.I2C_BUS))
    mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None)
    mux_ch = getattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None)
    addr = int(getattr(cfg, "ADS1115_ADDRESS", 0x48))
    ch = int(getattr(cfg, "ADS1115_CHANNEL", 0))
    fsr = float(getattr(cfg, "ADS1115_FSR_V", 2.048))
    scale = _effective_ref_ads_scale()
    pin = _ensure_ads_alrt_gpio() if use_alrt else None

    def _one_volt() -> float:
        if pin is not None and use_alrt:
            import RPi.GPIO as GPIO  # noqa: N814

            global _ADS_ALRT_WAIT_EDGE_BROKEN, _OS_WAIT_FAIL_LOGGED

            ads1115_start_single_shot(_ref_smbus, addr, ch, fsr, dr=dr)
            t_wait = _ads1115_dr_conversion_s(dr) * 2.0 + 0.005
            # RPi.GPIO wait_for_edge timeout is integer milliseconds, not seconds.
            timeout_ms = max(1, int(math.ceil(t_wait * 1000.0)))
            use_edge = bool(getattr(cfg, "ADS1115_ALRT_USE_WAIT_FOR_EDGE", False))
            os_before = ads1115_config_os_ready(_ref_smbus, addr)
            if use_edge and os_before:
                if not getattr(cfg, "ADS1115_ALRT_SUPPRESS_EDGE_SKIP_LOG", True):
                    print(
                        "[reference] DEBUG: ALRT edge wait skipped (OS already set before "
                        "wait_for_edge — conversion finished very early)"
                    )
            if (
                use_edge
                and not _ADS_ALRT_WAIT_EDGE_BROKEN
                and not os_before
            ):
                gpio_in_before: int | None = None
                try:
                    gpio_in_before = int(GPIO.input(pin))
                except Exception:
                    gpio_in_before = None
                try:
                    ret = GPIO.wait_for_edge(pin, GPIO.FALLING, timeout=timeout_ms)
                except RuntimeError as exc:
                    _ADS_ALRT_WAIT_EDGE_BROKEN = True
                    print(
                        "[reference] WARNING: GPIO.wait_for_edge on ADS1115 ALRT failed "
                        f"({exc!s}); using polled OS-bit timing for the rest of this process "
                        "(edge wait disabled). Set ADS1115_ALRT_USE_WAIT_FOR_EDGE=False or "
                        "ADS1115_ALRT_GPIO=None to skip; verify ALRT wiring / TI conversion-ready "
                        "ALERT/RDY mode."
                    )
                    _print_alrt_diag_bundle(
                        _ads1115_alrt_diag_lines(
                            pin=pin,
                            addr=addr,
                            ch=ch,
                            dr=dr,
                            timeout_ms=timeout_ms,
                            bus=_ref_smbus,
                            gpio_input_before=gpio_in_before,
                            os_ready_before_edge=os_before,
                            exc=exc,
                        ),
                        kind="runtime",
                    )
                else:
                    if ret is None:
                        gpio_in_after: int | None = None
                        try:
                            gpio_in_after = int(GPIO.input(pin))
                        except Exception:
                            gpio_in_after = None
                        print(
                            "[reference] WARNING: GPIO.wait_for_edge on ADS1115 ALRT timed out "
                            "(no falling edge); continuing with OS polling."
                        )
                        _print_alrt_diag_bundle(
                            _ads1115_alrt_diag_lines(
                                pin=pin,
                                addr=addr,
                                ch=ch,
                                dr=dr,
                                timeout_ms=timeout_ms,
                                bus=_ref_smbus,
                                gpio_input_before=gpio_in_before,
                                gpio_input_after=gpio_in_after,
                                os_ready_before_edge=os_before,
                                timeout=True,
                            ),
                            kind="timeout",
                        )
            t_conv = _ads1115_dr_conversion_s(dr)
            deadline_s = t_conv * 1.25 + 0.001
            poll_iv = float(getattr(cfg, "ADS1115_OS_POLL_INTERVAL_S", 0.0003))
            if not ads1115_wait_os_ready(
                _ref_smbus, addr, deadline_s=deadline_s, poll_interval_s=poll_iv
            ):
                if not _OS_WAIT_FAIL_LOGGED:
                    _OS_WAIT_FAIL_LOGGED = True
                    cq = _ads1115_config_comp_que_bits(_ref_smbus, addr)
                    print(
                        "[reference] WARNING: ads1115_wait_os_ready timed out "
                        f"(addr={hex(addr)} ch={ch} dr={dr} deadline_s={deadline_s:.4f} "
                        f"COMP_QUE_read={cq!s}) — brief sleep fallback"
                    )
                time.sleep(max(0.0, t_conv * 1.25 + 5e-4))
            return float(ads1115_read_conversion_volts(_ref_smbus, addr, fsr))
        return float(ads1115_read_single_ended(_ref_smbus, addr, ch, fsr, dr=dr))

    n_sub = max(1, int(median_subsamples))
    # Same bus as anode INA219 reads: serialize with i2c_bus_lock + transient I²C retry
    # (matches _read_raw_mv_hw). OC burst used to interleave without the lock → EIO.
    for attempt in range(2):
        try:
            with i2c_bus_lock(busnum):
                mux_select_on_bus(_ref_smbus, mux_addr, mux_ch)
                if n_sub == 1:
                    return round(_one_volt() * 1000.0 * scale, 4)
                sub = [_one_volt() * 1000.0 * scale for _ in range(n_sub)]
                return round(float(statistics.median(sub)), 4)
        except OSError as e:
            if _i2c_transient_errno(e) and attempt == 0:
                time.sleep(0.003)
                continue
            print(f"[reference] ADS1115 read failed: {e}")
            return 0.0
        except Exception as e:
            print(f"[reference] ADS1115 read failed: {e}")
            return 0.0
    print("[reference] ADS1115 read failed: repeated I/O error (transient I²C errnos)")
    return 0.0


class ReferenceElectrode:
    """Reference electrode reader and shift tracker."""

    def __init__(self) -> None:
        self.native_mv: float | None = None
        # Phase 1b: OCP with anodes in electrolyte, MOSFETs off (same T_RELAX as 1a).
        self.native_oc_anodes_in_mv: float | None = None
        self.native_oc_anodes_measured_at: str | None = None
        # native_mv (1a) − native_oc_anodes_in_mv (1b); positive = "depression" vs true metal.
        self.galvanic_offset_mv: float | None = None
        # First-install offset for health trending (not overwritten on later commissions).
        self.galvanic_offset_baseline_mv: float | None = None
        self.galvanic_offset_service_recommended: bool = False
        self.native_temp_f: float | None = None
        self.native_measured_at: str | None = None
        self.native_measured_unix: float | None = None
        self._last_raw_mv: float = 0.0
        # Rolling window for ref_valid stability check (W_REF seconds).
        self._ref_history: list[tuple[float, float]] = []
        self._consecutive_failures: int = 0
        self._last_valid_reason: str = ""
        be = str(getattr(cfg, "REF_ADC_BACKEND", "ads1115")).lower().strip()
        if be != "ads1115":
            print(
                f"[reference] REF_ADC_BACKEND={be!r} is legacy and not spec-supported (docs/iccp-requirements.md §7.1). "
                "Use ads1115 for production rigs.",
                file=sys.stderr,
            )

    def load_native(self) -> bool:
        if not _COMM_FILE.exists():
            return False
        try:
            data = json.loads(_COMM_FILE.read_text(encoding="utf-8"))
            self.native_mv = float(data["native_mv"])
            nai = data.get("native_oc_anodes_in_mv")
            try:
                self.native_oc_anodes_in_mv = float(nai) if nai is not None else None
            except (TypeError, ValueError):
                self.native_oc_anodes_in_mv = None
            self.native_oc_anodes_measured_at = data.get("native_oc_anodes_measured_at")
            go = data.get("galvanic_offset_mv")
            try:
                self.galvanic_offset_mv = float(go) if go is not None else None
            except (TypeError, ValueError):
                self.galvanic_offset_mv = None
            gob = data.get("galvanic_offset_baseline_mv")
            try:
                self.galvanic_offset_baseline_mv = float(gob) if gob is not None else None
            except (TypeError, ValueError):
                self.galvanic_offset_baseline_mv = None
            self.galvanic_offset_service_recommended = bool(
                data.get("galvanic_offset_service_recommended", False)
            )
            nt = data.get("native_temp_f")
            if nt is not None and str(nt).strip() != "":
                try:
                    self.native_temp_f = float(nt)
                except (TypeError, ValueError):
                    self.native_temp_f = None
            else:
                self.native_temp_f = None
            self.native_measured_at = data.get("native_measured_at")
            nu = data.get("native_measured_unix")
            try:
                self.native_measured_unix = float(nu) if nu is not None else None
            except (TypeError, ValueError):
                self.native_measured_unix = None
            _reload_comm_ref_ads_scale()
            return True
        except json.JSONDecodeError as e:
            print(
                f"[reference] load_native: invalid commissioning.json: {e}",
                file=sys.stderr,
            )
            return False
        except (KeyError, TypeError, ValueError) as e:
            print(
                f"[reference] load_native: missing or invalid native_mv / fields: {e}",
                file=sys.stderr,
            )
            return False
        except OSError as e:
            print(f"[reference] load_native: cannot read commissioning.json: {e}", file=sys.stderr)
            return False

    def native_baseline_file_payload(self) -> dict[str, Any]:
        """All native Ecorr keys for a full :func:`_update_comm_file` replace (after Phase 1+)."""
        if self.native_mv is None:
            raise ValueError("native baseline not set")
        if self.native_measured_at is None or str(self.native_measured_at).strip() == "":
            raise ValueError("native_measured_at not set (call save_native first)")
        ts = str(self.native_measured_at)
        payload: dict[str, Any] = {
            "native_mv": float(self.native_mv),
            "native_measured_at": ts,
        }
        if self.native_measured_unix is not None:
            payload["native_measured_unix"] = round(float(self.native_measured_unix), 3)
        recap = float(getattr(cfg, "NATIVE_RECAPTURE_S", 24 * 3600.0))
        if self.native_measured_unix is not None and recap > 0:
            payload["native_recapture_due_unix"] = round(self.native_measured_unix + recap, 3)
        if self.native_temp_f is not None:
            payload["native_temp_f"] = round(float(self.native_temp_f), 2)
        if self.native_oc_anodes_in_mv is not None:
            payload["native_oc_anodes_in_mv"] = round(float(self.native_oc_anodes_in_mv), 2)
        if self.native_oc_anodes_measured_at is not None:
            payload["native_oc_anodes_measured_at"] = str(self.native_oc_anodes_measured_at)
        if self.galvanic_offset_mv is not None:
            payload["galvanic_offset_mv"] = round(float(self.galvanic_offset_mv), 2)
        if self.galvanic_offset_baseline_mv is not None:
            payload["galvanic_offset_baseline_mv"] = round(
                float(self.galvanic_offset_baseline_mv), 2
            )
        if self.galvanic_offset_service_recommended:
            payload["galvanic_offset_service_recommended"] = True
        return payload

    def save_native(
        self, mv: float, *, native_temp_f: float | None = None
    ) -> None:
        self.native_mv = mv
        # New 1a invalidates previous Phase 1b — clear until 1b is re-run.
        self.native_oc_anodes_in_mv = None
        self.native_oc_anodes_measured_at = None
        self.galvanic_offset_mv = None
        self.galvanic_offset_service_recommended = False
        now = time.time()
        ts = (
            time.strftime("%Y-%m-%dT%H:%M:%S")
            if now > 1_000_000_000
            else "CLOCK_UNSYNCED"
        )
        self.native_measured_at = ts
        self.native_measured_unix = now if now > 1_000_000_000 else None
        payload: dict[str, Any] = {
            "native_mv": mv,
            "native_measured_at": ts,
            "native_oc_anodes_in_mv": None,
            "native_oc_anodes_measured_at": None,
            "galvanic_offset_mv": None,
            "galvanic_offset_service_recommended": False,
        }
        if self.native_measured_unix is not None:
            payload["native_measured_unix"] = round(self.native_measured_unix, 3)
        recap = float(getattr(cfg, "NATIVE_RECAPTURE_S", 24 * 3600.0))
        if self.native_measured_unix is not None and recap > 0:
            payload["native_recapture_due_unix"] = round(self.native_measured_unix + recap, 3)
        if native_temp_f is not None:
            self.native_temp_f = round(float(native_temp_f), 2)
            payload["native_temp_f"] = self.native_temp_f
        _update_comm_file(payload)
        # Do not clear galvanic_offset_baseline_mv — it is the first-install reference for health.

    def save_native_oc_anodes_in(
        self,
        mv: float,
        *,
        true_native_mv: float,
    ) -> None:
        """
        Phase 1b: OCP with anodes in bath, MOSFETs off. Persists offset vs Phase 1a
        (``true_native_mv``) and sets service flag if offset vs first-install baseline
        falls below GALVANIC_OFFSET_SERVICE_FRACTION.
        """
        now = time.time()
        ts = (
            time.strftime("%Y-%m-%dT%H:%M:%S")
            if now > 1_000_000_000
            else "CLOCK_UNSYNCED"
        )
        self.native_oc_anodes_in_mv = float(mv)
        self.native_oc_anodes_measured_at = ts
        off = round(float(true_native_mv) - float(mv), 2)
        self.galvanic_offset_mv = off
        self.galvanic_offset_service_recommended = False

        existing: dict = {}
        if _COMM_FILE.exists():
            try:
                existing = json.loads(_COMM_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
        if not isinstance(existing, dict):
            existing = {}
        if existing.get("galvanic_offset_baseline_mv") is None and off > 0:
            # First time we have a full 1a+1b pair — anchor health trending.
            self.galvanic_offset_baseline_mv = off
        else:
            try:
                g0 = existing.get("galvanic_offset_baseline_mv")
                self.galvanic_offset_baseline_mv = (
                    float(g0) if g0 is not None else self.galvanic_offset_baseline_mv
                )
            except (TypeError, ValueError):
                self.galvanic_offset_baseline_mv = self.galvanic_offset_baseline_mv

        frac = float(getattr(cfg, "GALVANIC_OFFSET_SERVICE_FRACTION", 0.2))
        bl = self.galvanic_offset_baseline_mv
        if bl is not None and bl > 0 and off < frac * bl:
            self.galvanic_offset_service_recommended = True
            print(
                f"[reference] Galvanic offset {off:.1f} mV < {frac:.0%} of install baseline "
                f"({bl:.1f} mV) — anode service / replacement may be needed (see "
                f"docs/galvanic-offset-calibration.md).",
                file=sys.stderr,
            )

        upd: dict[str, Any] = {
            "native_oc_anodes_in_mv": round(float(mv), 2),
            "native_oc_anodes_measured_at": ts,
            "galvanic_offset_mv": off,
            "galvanic_offset_service_recommended": self.galvanic_offset_service_recommended,
        }
        if self.galvanic_offset_baseline_mv is not None:
            upd["galvanic_offset_baseline_mv"] = round(
                float(self.galvanic_offset_baseline_mv), 2
            )
        _update_comm_file(upd)

    def baseline_mv_for_shift(self) -> float | None:
        """
        Open-circuit baseline for shift = this − raw (when 1b was run, use in-situ OCP
        with anodes installed; else Phase 1a true native only).
        """
        if self.native_oc_anodes_in_mv is not None:
            return float(self.native_oc_anodes_in_mv)
        return self.native_mv

    def effective_shift_target_mv(self) -> float:
        """
        Additional mV of shift (from :meth:`baseline_mv_for_shift`) needed so that
        **total** polarization from Phase 1a (true native) reaches ``TARGET_SHIFT_MV``.

        If galvanic OCP (1a−1b) already moved the reading by ``galvanic_offset_mv``,
        only ``TARGET_SHIFT_MV − offset`` mV more is required from the 1b baseline.
        When 1b was not commissioned, offset is unknown → use full ``TARGET_SHIFT_MV``.
        """
        t = float(getattr(cfg, "TARGET_SHIFT_MV", 100.0))
        if self.galvanic_offset_mv is None:
            return t
        return max(0.0, t - float(self.galvanic_offset_mv))

    def effective_max_shift_mv(self) -> float:
        """
        Max **additional** shift from the 1b baseline (when present) that keeps
        total polarization from 1a at or below ``MAX_SHIFT_MV``:
        ``max(0, MAX_SHIFT_MV - galvanic_offset_mv)``.
        """
        m = float(getattr(cfg, "MAX_SHIFT_MV", 200.0))
        if self.galvanic_offset_mv is None:
            return m
        return max(0.0, m - float(self.galvanic_offset_mv))

    def read(
        self,
        duties: dict[int, float] | None = None,
        statuses: dict[int, str] | None = None,
        *,
        temp_f: float | None = None,
    ) -> float:
        if SIM_MODE:
            mv = _read_raw_mv_sim(duties or {}, statuses or {})
            read_ok = True
        else:
            mv = _read_raw_mv_hw()
            read_ok = not _REF_LAST_READ_FAILED
        mv = self.ref_temp_adjust_mv(mv, temp_f)
        self._last_raw_mv = mv
        # Track rolling window and failure count for ref_valid() / REFERENCE_INVALID.
        now = time.monotonic()
        if read_ok:
            self._consecutive_failures = 0
            self._ref_history.append((now, mv))
            w = max(1.0, float(getattr(cfg, "W_REF", 20.0)))
            cutoff = now - w
            self._ref_history = [(t, v) for t, v in self._ref_history if t >= cutoff]
        else:
            self._consecutive_failures += 1
        return mv

    def ref_valid(self) -> tuple[bool, str]:
        """Return (valid, reason). Invalid → control should assert REFERENCE_INVALID (§6.1)."""
        if self._consecutive_failures >= 3:
            self._last_valid_reason = f"read_failed_x{self._consecutive_failures}"
            return False, self._last_valid_reason
        if len(self._ref_history) < 3:
            self._last_valid_reason = "warmup"
            return True, self._last_valid_reason
        vals = [v for _, v in self._ref_history]
        pp = max(vals) - min(vals)
        stab = float(getattr(cfg, "NATIVE_STABILITY_MV", 5.0)) * 4.0
        if pp > stab:
            self._last_valid_reason = f"noisy_p2p_{pp:.1f}>{stab:.1f}"
            return False, self._last_valid_reason
        self._last_valid_reason = "ok"
        return True, self._last_valid_reason

    def capture_native(
        self,
        *,
        temp_f: float | None = None,
        rest_current_ok: callable | None = None,  # type: ignore[valid-type]
        static_gate_low: callable | None = None,  # type: ignore[valid-type]
        gate_restore: callable | None = None,  # type: ignore[valid-type]
        on_relax_progress: Callable[[float, int, float | None], None] | None = None,
    ) -> tuple[float | None, str]:
        """
        Capture a fresh native baseline per docs/iccp-requirements.md §8.1 Phase 1.
        Returns (native_mv, reason). On failure returns (None, reason) — caller should
        raise REFERENCE_INVALID if no existing baseline is usable.

        Callers supply optional helpers to enforce gates:
          - rest_current_ok(): bool — True when |I| < I_REST_MA on every channel.
          - static_gate_low(): force all anode gates low (Phase 1 "true off").
          - gate_restore(): restore normal PWM control after capture.
        """
        retries = max(0, int(getattr(cfg, "NATIVE_CAPTURE_RETRIES", 2)))
        t_relax = max(1.0, float(getattr(cfg, "T_RELAX", 30.0)))
        interval = max(0.05, float(getattr(cfg, "NATIVE_SAMPLE_INTERVAL_S", 2.0)))
        stab = max(0.1, float(getattr(cfg, "NATIVE_STABILITY_MV", 5.0)))
        slope_limit = max(0.0, float(getattr(cfg, "NATIVE_SLOPE_MV_PER_MIN", 2.0)))
        rest_confirm_s = max(0.0, float(getattr(cfg, "T_REST_CONFIRM", 3.0)))
        if static_gate_low is not None:
            try:
                static_gate_low()
            except Exception as e:  # pragma: no cover — best effort
                print(f"[reference] capture_native: static_gate_low raised: {e}", file=sys.stderr)
        last_reason = "unknown"
        _relax_log_every_s = 2.0
        try:
            for attempt in range(retries + 1):
                if rest_current_ok is not None:
                    rest_t0 = time.monotonic()
                    rested = False
                    while time.monotonic() - rest_t0 < rest_confirm_s:
                        try:
                            if rest_current_ok():
                                rested = True
                                break
                        except Exception:
                            pass
                        time.sleep(0.25)
                    if not rested:
                        last_reason = "rest_current_not_below_I_REST_MA"
                        continue
                samples: list[tuple[float, float]] = []
                t0 = time.monotonic()
                if on_relax_progress is not None:
                    on_relax_progress(t_relax, 0, None)
                _last_relax_log = t0
                while time.monotonic() - t0 < t_relax:
                    mv = self.read(temp_f=temp_f)
                    if not SIM_MODE and _REF_LAST_READ_FAILED:
                        last_reason = "read_failed_during_capture"
                        break
                    samples.append((time.monotonic() - t0, float(mv)))
                    if on_relax_progress is not None:
                        _now = time.monotonic()
                        if _now - _last_relax_log >= _relax_log_every_s:
                            _rem = max(0.0, t_relax - (_now - t0))
                            on_relax_progress(_rem, len(samples), float(mv))
                            _last_relax_log = _now
                    time.sleep(interval)
                else:
                    if len(samples) < 3:
                        last_reason = "too_few_samples"
                        continue
                    vals = [v for _, v in samples]
                    pp = max(vals) - min(vals)
                    if pp > stab:
                        last_reason = f"unstable_p2p_{pp:.1f}>{stab:.1f}"
                        continue
                    first = statistics.fmean(vals[: max(2, len(vals) // 3)])
                    last = statistics.fmean(vals[-max(2, len(vals) // 3):])
                    span_min = max(samples[-1][0] - samples[0][0], 1e-6) / 60.0
                    slope = (last - first) / span_min
                    if slope_limit > 0 and abs(slope) > slope_limit:
                        last_reason = f"slope_{slope:.2f}>{slope_limit:.2f}mv_per_min"
                        continue
                    median_mv = float(statistics.median(vals))
                    return median_mv, "ok"
                if last_reason.startswith("read_failed"):
                    continue
            return None, last_reason
        finally:
            if gate_restore is not None:
                try:
                    gate_restore()
                except Exception as e:  # pragma: no cover
                    print(f"[reference] capture_native: gate_restore raised: {e}", file=sys.stderr)

    def native_age_s(self) -> float | None:
        if self.native_measured_unix is None:
            return None
        return max(0.0, time.time() - float(self.native_measured_unix))

    def next_native_recapture_s(self) -> float | None:
        if self.native_measured_unix is None:
            return None
        recap = float(getattr(cfg, "NATIVE_RECAPTURE_S", 24 * 3600.0))
        if recap <= 0:
            return None
        remaining = (float(self.native_measured_unix) + recap) - time.time()
        return remaining

    def ref_temp_adjust_mv(self, mv: float, temp_f: float | None) -> float:
        """Optional linear mV trim vs pan °F (see REF_TEMP_COMP_*; native_temp_f in JSON)."""
        coef = float(getattr(cfg, "REF_TEMP_COMP_MV_PER_F", 0.0))
        if coef == 0.0 or temp_f is None:
            return mv
        anchor = self.native_temp_f
        if anchor is None:
            anchor = float(getattr(cfg, "REF_TEMP_COMP_BASE_F", 77.0))
        return mv + (float(temp_f) - float(anchor)) * coef

    def shift_mv(
        self,
        duties: dict[int, float] | None = None,
        statuses: dict[int, str] | None = None,
        *,
        temp_f: float | None = None,
    ) -> float | None:
        """Polarization vs open-circuit baseline: baseline_mv_for_shift − raw."""
        bl = self.baseline_mv_for_shift()
        if bl is None:
            return None
        raw = self.read(duties, statuses, temp_f=temp_f)
        return round(bl - raw, 2)

    def read_raw_and_shift(
        self,
        duties: dict[int, float] | None = None,
        statuses: dict[int, str] | None = None,
        *,
        temp_f: float | None = None,
    ) -> tuple[float, float | None]:
        try:
            raw = self.read(duties, statuses, temp_f=temp_f)
        except RuntimeError as e:
            print(e)
            return 0.0, None
        bl = self.baseline_mv_for_shift()
        if bl is None:
            return raw, None
        return raw, round(bl - raw, 2)

    def collect_oc_decay_samples(self) -> list[tuple[float, float]]:
        """
        After PWM cut: (elapsed_s, mV-like) points for OC inflection pick.
        INA219 ref backend → single sample (or time series in duration mode);
        SIM → synthetic decay; ADS1115 → burst or duration-window sampling.
        """
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
        """Band vs effective target/max for shift = baseline − raw (not a CP survey criterion)."""
        if shift_mv is None:
            return "UNKNOWN"
        t = self.effective_shift_target_mv()
        hi = self.effective_max_shift_mv()
        if shift_mv < t * 0.8:
            return "UNDER"
        if shift_mv > hi:
            return "OVER"
        return "OK"

    @property
    def last_raw_mv(self) -> float:
        return self._last_raw_mv


def _update_comm_file(data: dict, *, replace: bool = False) -> None:
    """
    Update commissioning.json. Default **merge** (read + ``dict.update``) so partial
    writes (e.g. :meth:`ReferenceElectrode.save_native`) do not drop unrelated keys.
    If ``replace`` is True, **write** ``data`` as the full file (no merge) — use when
    saving a complete snapshot after a successful full ``commissioning.run``.
    """
    if replace:
        if not isinstance(data, dict):
            print(
                "[reference] _update_comm_file(replace) expected dict; not writing",
                file=sys.stderr,
            )
            return
        _atomic_write_json_same_dir(_COMM_FILE, data)
        _reload_comm_ref_ads_scale()
        return

    existing: dict = {}
    if _COMM_FILE.exists():
        try:
            raw = _COMM_FILE.read_text(encoding="utf-8")
            existing = json.loads(raw)
            if not isinstance(existing, dict):
                print(
                    "[reference] commissioning.json root is not an object; replacing with update payload keys only",
                    file=sys.stderr,
                )
                existing = {}
        except json.JSONDecodeError as e:
            print(
                f"[reference] commissioning.json corrupt; overwriting merge base: {e}",
                file=sys.stderr,
            )
            existing = {}
        except OSError as e:
            print(
                f"[reference] cannot read commissioning.json before update: {e}",
                file=sys.stderr,
            )
            existing = {}
    existing.update(data)
    _atomic_write_json_same_dir(_COMM_FILE, existing)
    _reload_comm_ref_ads_scale()
