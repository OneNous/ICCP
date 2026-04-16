#!/usr/bin/env python3
"""
CoilShield ICCP controller — main loop.

Set COILSHIELD_SIM=1 for simulator (default on macOS if unset).
Clear fault latch: `touch ~/coilshield/clear_fault`
Sim speed: SIM_TIME_SCALE=10 (real seconds per simulated hour, default 10), or
  `python3 main.py --sim --sim-time-scale 60`
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
        help="sim only: real seconds per simulated hour (sets SIM_TIME_SCALE before sensors load)",
    )
    return p.parse_args()


def _print_sim_schedule(sensor_module: object) -> None:
    """Print the 24-hour cycle schedule and per-anode wet profiles at startup."""
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
    sim_line: str = "",
) -> None:
    try:
        if sim_line:
            print(sim_line)
        print("─" * 90)
        print(
            f"{'CH':<4} {'State':<12} {'BusV':<8} {'mA':<12} {'Duty%':<8} "
            f"{'AnyWet':<8} {'Latch':<6} {'Faults'}"
        )
        print("─" * 90)
        for i in sorted(readings.keys()):
            r = readings[i]
            st = ch_status.get(i, "?")
            if r.get("ok"):
                line = (
                    f"{i + 1:<4} {st:<12} {r['bus_v']:<8.3f} "
                    f"{r['current']:<12.4f} {duties.get(i, 0):<8.1f}"
                )
            else:
                line = f"{i + 1:<4} {st:<12} {'--':<8} {'ERR':<12} {'0':<8}"
            if i == 0:
                line += f" {int(any_wet):<8} {int(latched):<6} {';'.join(faults) or '-'}"
            print(line)
        print("─" * 90)
    except BrokenPipeError:
        raise SystemExit(0) from None


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

    import sensors
    from control import Controller
    from logger import DataLogger

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
    leds = StatusLEDs(use_hw_gpio)
    log = DataLogger()

    leds.setup()

    print(
        f"CoilShield starting (sim={sim}, TARGET_MA={cfg.TARGET_MA}, "
        f"clear fault: touch {cfg.CLEAR_FAULT_FILE})"
    )

    if sim:
        _print_sim_schedule(sensors)

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

            sim_time_kw: str | None = None
            if sim and sim_state is not None:
                sim_time_kw = sim_state.sim_hhmm()
            log.record(
                readings,
                any_wet,
                faults,
                duties,
                fault_latched,
                ch_status,
                sim_time=sim_time_kw,
            )
            log.maybe_flush()

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
                    sim_line,
                )

            elif faults or fault_latched:
                print(
                    time.strftime("%H:%M:%S"),
                    "FAULTS:",
                    "; ".join(faults),
                    f"latched={fault_latched}",
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
