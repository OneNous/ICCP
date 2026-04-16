#!/usr/bin/env python3
"""
CoilShield ICCP controller — main loop.

Set COILSHIELD_SIM=1 for simulator (default on macOS if unset).
Clear fault latch: `touch ~/coilshield/clear_fault` (see config/settings.CLEAR_FAULT_FILE).
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
    p.add_argument("--sim", action="store_true", help="force simulator (COILSHIELD_SIM=1)")
    p.add_argument(
        "--real",
        action="store_true",
        help="force real hardware path (COILSHIELD_SIM=0)",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="print status table each tick",
    )
    return p.parse_args()


def _print_table(
    readings: dict,
    faults: list,
    duties: dict,
    latched: bool,
    ch_status: dict[int, str],
    any_wet: bool,
) -> None:
    print("─" * 88)
    print(
        f"{'CH':<4} {'State':<12} {'BusV':<8} {'mA':<12} {'Duty%':<8} "
        f"{'AnyWet':<8} {'Latch':<6} {'Faults'}"
    )
    print("─" * 88)
    for i in sorted(readings.keys()):
        r = readings[i]
        st = ch_status.get(i, "?")
        if r.get("ok"):
            line = (
                f"{i + 1:<4} {st:<12} {r['bus_v']:<8.3f} {r['current']:<12.4f} "
                f"{duties.get(i, 0):<8.1f}"
            )
        else:
            line = f"{i + 1:<4} {st:<12} {'--':<8} {'ERR':<12} {'0':<8}"
        if i == 0:
            line += f" {int(any_wet):<8} {int(latched):<6} {';'.join(faults) or '-'}"
        print(line)
    print("─" * 88)


def main() -> int:
    _default_sim_on_mac()
    args = _parse_args()
    if args.sim:
        os.environ["COILSHIELD_SIM"] = "1"
    if args.real:
        os.environ["COILSHIELD_SIM"] = "0"

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
            print("RPi.GPIO not available; use COILSHIELD_SIM=1 or --sim", file=sys.stderr)
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

            log.record(readings, any_wet, faults, duties, fault_latched, ch_status)
            log.maybe_flush()

            leds.set_running_ok(not fault_latched and len(faults) == 0)

            if args.verbose:
                _print_table(readings, faults, duties, fault_latched, ch_status, any_wet)
            elif faults or fault_latched:
                print(time.strftime("%H:%M:%S"), "FAULTS:", "; ".join(faults), "latched=", fault_latched)

            time.sleep(cfg.SAMPLE_INTERVAL_S)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        log.close()
        leds.shutdown()
        ctrl.cleanup()
        if use_hw_gpio:
            import RPi.GPIO as GPIO  # noqa: N814

            try:
                GPIO.cleanup()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
