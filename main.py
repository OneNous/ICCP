#!/usr/bin/env python3
"""
CoilShield ICCP controller — main loop.

Set COILSHIELD_SIM=1 for simulator (default on macOS if unset).
Clear fault latch: `touch <PROJECT_ROOT>/clear_fault`
Commissioning reset: `python3 -c "import commissioning; commissioning.reset()"`
Sim speed: SIM_TIME_SCALE=10 or `python3 main.py --sim --sim-time-scale 60`
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def _default_sim_on_mac() -> None:
    if sys.platform == "darwin" and "COILSHIELD_SIM" not in os.environ:
        os.environ["COILSHIELD_SIM"] = "1"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CoilShield ICCP monitor/controller")
    p.add_argument("--sim", action="store_true", help="force simulator mode")
    p.add_argument("--real", action="store_true", help="force real hardware mode")
    p.add_argument(
        "--skip-commission",
        action="store_true",
        help="skip commissioning even if commissioning.json is absent",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="print status table each tick",
    )
    p.add_argument(
        "--sim-time-scale",
        type=int,
        default=None,
        metavar="N",
        help="sim only: real seconds per simulated hour",
    )
    return p.parse_args()


def _print_sim_schedule(sensor_module: object) -> None:
    scale = getattr(sensor_module, "SIM_REAL_S_PER_SIM_HOUR", 10.0)
    real_minutes = (86400.0 / (3600.0 / float(scale))) / 60.0
    print(
        f"[sim] 24-hour window → {real_minutes:.0f} real minutes "
        f"(SIM_TIME_SCALE={int(scale)})"
    )
    print("[sim] 10 cooling cycles:")
    cycles = getattr(sensor_module, "COOLING_CYCLES", ())
    for i, (s, e) in enumerate(cycles):
        duration = (e - s) // 60
        print(
            f"      {i + 1:2d}. {s // 3600:02d}:{(s % 3600) // 60:02d}"
            f"–{e // 3600:02d}:{(e % 3600) // 60:02d}  ({duration} min)"
        )
    print("[sim] Per-anode wet profiles:")
    params = getattr(sensor_module, "ANODE_WET_PARAMS", ())
    for ch, (wd, dd) in enumerate(params):
        print(
            f"      CH{ch + 1}: wets {wd // 60} min after cycle start, "
            f"dries {dd // 60} min after cycle stop"
        )
    print()


def _print_table(
    readings: dict,
    faults: list,
    duties: dict,
    latched: bool,
    ch_status: dict[int, str],
    any_wet: bool,
    ref_raw_mv: float,
    ref_shift: float | None,
    ref_band: str,
    ref_hw_line: str,
    temp_f: float | None,
    sim_line: str = "",
) -> None:
    try:
        if sim_line:
            print(sim_line)
        shift_str = (
            f"{ref_shift:+.1f} mV"
            if ref_shift is not None
            else "— (commissioning needed for shift)"
        )
        band_disp = ref_band if ref_shift is not None else "—"
        temp_str = f"{temp_f:.1f}°F" if temp_f is not None else "—"
        print(f"  Ref sensor: {ref_hw_line}")
        print(
            f"    raw={ref_raw_mv:.1f} mV  |  polarization shift={shift_str}  "
            f"|  shift_band={band_disp}    Temp: {temp_str}"
        )
        print("─" * 90)
        print(
            f"{'CH':<4} {'State':<12} {'BusV':<8} {'mA':>8}  {'Duty%':<8} "
            f"{'Ω imp':<10} {'Vc':<8} {'Wet':<5}"
        )
        print("─" * 90)
        for i in sorted(readings.keys()):
            r = readings[i]
            st = ch_status.get(i, "?")
            if r.get("ok"):
                ma = float(r.get("current", 0))
                bus_v = float(r.get("bus_v", 0))
                duty = float(duties.get(i, 0))
                imp = round(bus_v / max(ma / 1000, 0.00001)) if ma > 0 else 0
                vc = round(bus_v * (duty / 100.0), 3)
                print(
                    f"{i + 1:<4} {st:<12} {bus_v:<8.3f} {ma:>8.2f}  {duty:<8.1f} "
                    f"{imp:<10,.0f} {vc:<8.3f} {int(st == 'PROTECTING'):<5}"
                )
            else:
                print(f"{i + 1:<4} {st:<12} {'--':<8} {'ERR':<10} {'0':<8} {'—':<10} {'—':<8}")
        print("─" * 90)
        print(
            f"  AnyWet={int(any_wet)}  Latch={int(latched)}  "
            f"Faults: {'; '.join(faults) or '—'}"
        )
    except BrokenPipeError:
        raise SystemExit(0) from None


def _print_ref_compact(
    ref_hw_line: str,
    ref_raw_mv: float,
    ref_shift: float | None,
    ref_band: str,
    ref_hint: str,
) -> None:
    """Single-line ref summary for non-verbose mode (same cadence as LOG_INTERVAL_S)."""
    shift_str = (
        f"{ref_shift:+.1f} mV"
        if ref_shift is not None
        else "— (commissioning needed for shift)"
    )
    band_disp = ref_band if ref_shift is not None else "—"
    hint = f"  |  {ref_hint}" if ref_hint else ""
    print(
        f"[ref] {ref_hw_line}  |  raw={ref_raw_mv:.1f} mV  |  shift={shift_str}  "
        f"|  band={band_disp}{hint}"
    )


def main() -> int:
    _default_sim_on_mac()
    args = _parse_args()
    if args.sim:
        os.environ["COILSHIELD_SIM"] = "1"
    if args.real:
        os.environ["COILSHIELD_SIM"] = "0"
    if args.sim_time_scale is not None:
        os.environ["SIM_TIME_SCALE"] = str(args.sim_time_scale)

    import config.settings as cfg

    import commissioning
    import sensors
    import temp as temp_mod
    from control import Controller
    from logger import DataLogger
    from reference import ReferenceElectrode, ref_hw_message, ref_hw_ok, ref_ux_hint

    from leds import StatusLEDs

    sim = sensors.SIM_MODE
    use_hw_gpio = not sim

    if use_hw_gpio:
        try:
            import RPi.GPIO as GPIO  # noqa: N814

            GPIO.setmode(GPIO.BCM)
        except ImportError:
            print(
                "RPi.GPIO not available — use --sim or COILSHIELD_SIM=1",
                file=sys.stderr,
            )
            return 1

    sim_state = sensors.SimSensorState() if sim else None
    ctrl = Controller()
    ref = ReferenceElectrode()
    leds = StatusLEDs(use_hw_gpio)
    log = DataLogger()

    leds.setup()

    print(
        f"CoilShield starting (sim={sim}, TARGET_MA={cfg.TARGET_MA}, "
        f"clear fault: touch {cfg.CLEAR_FAULT_FILE})"
    )
    print(f"[main] Reference path: {ref_hw_message()}")

    if sim:
        _print_sim_schedule(sensors)

    if not args.skip_commission:
        if commissioning.needs_commissioning():
            print("[main] No commissioning data. Starting commissioning sequence...")
            print("[main] (use --skip-commission to bypass for bench testing)")
            commissioned_target = commissioning.run(
                ref, ctrl, sim_state=sim_state, verbose=True
            )
            cfg.TARGET_MA = commissioned_target
        else:
            ref.load_native()
            cfg.TARGET_MA = commissioning.load_commissioned_target()
            print(
                f"[main] Commissioning loaded — native={ref.native_mv:.1f} mV  "
                f"target={cfg.TARGET_MA:.3f} mA"
            )
    else:
        ref.load_native()
        if not commissioning.needs_commissioning():
            cfg.TARGET_MA = commissioning.load_commissioned_target()
        print(
            f"[main] Commissioning skipped. native_mv="
            f"{'set' if ref.native_mv is not None else 'not set'}"
        )

    outer_loop_counter = 0
    outer_loop_interval = max(
        1, int(cfg.LOG_INTERVAL_S / cfg.SAMPLE_INTERVAL_S)
    )
    _ux_tip_shown = False

    try:
        while True:
            if sim:
                readings = sensors.read_all_sim(sim_state)  # type: ignore[arg-type]
            else:
                readings = sensors.read_all_real()

            faults, fault_latched = ctrl.update(readings)
            duties = ctrl.duties()
            ch_status = ctrl.channel_statuses()
            any_wet = ctrl.any_wet()

            if sim and sim_state is not None:
                sim_state.duties = dict(duties)

            ref_raw_mv, ref_shift = ref.read_raw_and_shift(
                duties=duties, statuses=ch_status
            )
            ref_band = (
                ref.protection_status(ref_shift)
                if ref_shift is not None
                else "—"
            )
            temp_f = temp_mod.read_fahrenheit()

            if not _ux_tip_shown:
                tip = ref_ux_hint(
                    baseline_set=ref.native_mv is not None,
                    hw_ok=ref_hw_ok(),
                    skip_commission=args.skip_commission,
                )
                if tip:
                    print(f"[main] {tip}")
                _ux_tip_shown = True

            outer_loop_counter += 1
            ref_log_tick = False
            if outer_loop_counter >= outer_loop_interval:
                ctrl.update_potential_target(ref_shift)
                outer_loop_counter = 0
                ref_log_tick = True

            sim_time_kw: str | None = None
            if sim and sim_state is not None:
                sim_time_kw = sim_state.sim_hhmm()
            ref_hint = ref_ux_hint(
                baseline_set=ref.native_mv is not None,
                hw_ok=ref_hw_ok(),
                skip_commission=args.skip_commission,
            )
            ref_hw_line = ref_hw_message()
            ref_baseline_set = ref.native_mv is not None
            log.record(
                readings,
                any_wet,
                faults,
                duties,
                fault_latched,
                ch_status,
                sim_time=sim_time_kw,
                ref_shift_mv=ref_shift,
                ref_status=ref_band if ref_shift is not None else "N/A",
                temp_f=temp_f,
                ref_raw_mv=ref_raw_mv,
                ref_hw_ok=ref_hw_ok(),
                ref_hint=ref_hint or None,
                ref_hw_message=ref_hw_line,
                ref_baseline_set=ref_baseline_set,
            )
            log.maybe_flush()

            if ref_log_tick and not args.verbose:
                _print_ref_compact(
                    ref_hw_line,
                    ref_raw_mv,
                    ref_shift,
                    ref_band,
                    ref_hint,
                )

            leds.set_running_ok(not fault_latched and len(faults) == 0)

            if args.verbose:
                sim_line = ""
                if sim and sim_state is not None:
                    sim_s = sim_state.sim_seconds()
                    cycle = sim_state.active_cycle(sim_s)
                    cycle_str = (
                        f"cycle {cycle}/10 ACTIVE" if cycle else "between cycles"
                    )
                    wet_map = sim_state.wet_map(sim_s)
                    sim_line = (
                        f"[sim {sim_state.sim_hhmm()}] {cycle_str:<24} "
                        f"anodes: {wet_map}  (W=wet  .=dry)"
                    )
                _print_table(
                    readings,
                    faults,
                    duties,
                    fault_latched,
                    ch_status,
                    any_wet,
                    ref_raw_mv,
                    ref_shift,
                    ref_band,
                    ref_hw_message(),
                    temp_f,
                    sim_line,
                )

            elif faults or fault_latched:
                shift_str = (
                    f"{ref_shift:+.1f} mV"
                    if ref_shift is not None
                    else "— (no shift baseline)"
                )
                band_disp = ref_band if ref_shift is not None else "—"
                print(
                    time.strftime("%H:%M:%S"),
                    "FAULTS:",
                    "; ".join(faults),
                    f"latched={fault_latched}",
                    f"| ref raw={ref_raw_mv:.1f} mV shift={shift_str} band={band_disp}",
                )

            time.sleep(cfg.SAMPLE_INTERVAL_S)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        log.close()
        leds.shutdown()
        ctrl.cleanup()
        if use_hw_gpio:
            try:
                import RPi.GPIO as GPIO  # noqa: N814

                GPIO.cleanup()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
