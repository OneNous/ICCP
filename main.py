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


def _print_table(readings: dict, wet: bool, faults: list, duties: dict, latched: bool) -> None:
    print("─" * 72)
    print(
        f"{'CH':<4} {'BusV':<8} {'mA':<12} {'Duty%':<8} {'Wet':<6} {'Latch':<6} {'Faults'}"
    )
    print("─" * 72)
    for i in sorted(readings.keys()):
        r = readings[i]
        if r.get("ok"):
            line = (
                f"{i + 1:<4} {r['bus_v']:<8.3f} {r['current']:<12.4f} "
                f"{duties.get(i, 0):<8.1f}"
            )
        else:
            line = f"{i + 1:<4} {'--':<8} {'ERR':<12} {'0':<8}"
        if i == 0:
            line += f" {int(wet):<6} {int(latched):<6} {';'.join(faults) or '-'}"
        print(line)
    print("─" * 72)


def main() -> int:
    _default_sim_on_mac()
    args = _parse_args()
    if args.sim:
        os.environ["COILSHIELD_SIM"] = "1"
    if args.real:
        os.environ["COILSHIELD_SIM"] = "0"

    import config.settings as cfg

    # Import sensors only after COILSHIELD_SIM is finalized
    import sensors
    import safety
    from control import PWMController
    from logger import DataLogger
    from wet_switch import WetSwitch

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
    wet = WetSwitch(use_hw_gpio, cfg.SIM_ASSUME_WET)
    ctrl = PWMController(use_hw_gpio)
    leds = StatusLEDs(use_hw_gpio)
    log = DataLogger()

    wet.setup()
    ctrl.setup()
    leds.setup()

    fault_latched = False
    print(
        f"CoilShield starting (sim={sim}, TARGET_MA={cfg.TARGET_MA}, "
        f"clear fault: touch {cfg.CLEAR_FAULT_FILE})"
    )

    try:
        while True:
            if cfg.CLEAR_FAULT_FILE.exists():
                fault_latched = False
                try:
                    cfg.CLEAR_FAULT_FILE.unlink()
                except OSError:
                    pass

            if sim:
                readings = sensors.read_all_sim(sim_state)  # type: ignore[arg-type]
            else:
                readings = sensors.read_all_real()

            wet_now = wet.read()
            faults = safety.evaluate(readings, wet_now)
            if not fault_latched and safety.should_latch(faults):
                fault_latched = True

            ctrl.update(readings, wet_now, fault_latched)
            duties = ctrl.duties()

            log.record(readings, wet_now, faults, duties, fault_latched)
            log.maybe_flush()

            leds.set_running_ok(not fault_latched and len(faults) == 0)

            if args.verbose:
                _print_table(readings, wet_now, faults, duties, fault_latched)
            elif faults or fault_latched:
                print(time.strftime("%H:%M:%S"), "FAULTS:", "; ".join(faults), "latched=", fault_latched)

            time.sleep(cfg.SAMPLE_INTERVAL_S)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        log.close()
        leds.shutdown()
        ctrl.shutdown()
        wet.shutdown()
        if use_hw_gpio:
            import RPi.GPIO as GPIO  # noqa: N814

            try:
                GPIO.cleanup()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
