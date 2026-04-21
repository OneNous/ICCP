#!/usr/bin/env python3
"""
CoilShield ICCP controller — main loop.

Operator CLI (after `pip install -e .` in venv): `iccp -start`, `iccp probe`, `iccp clear-fault`.

Use `--sim` or `COILSHIELD_SIM=1` for the bench simulator; default is hardware (`COILSHIELD_SIM=0`).
On a Raspberry Pi, `COILSHIELD_SIM=1` in the environment alone is ignored (hardware is used) unless you pass `--sim`.
Clear fault latch: `touch <PROJECT_ROOT>/clear_fault` or `iccp clear-fault`
Commissioning reset: `python3 -c "import commissioning; commissioning.reset()"`
Telemetry directory: match the dashboard — ``--log-dir /abs/path/logs`` or ``COILSHIELD_LOG_DIR`` before import (see ``config/argv_log_dir.py``).
Sim speed: SIM_TIME_SCALE=10 or `python3 main.py --sim --sim-time-scale 60`
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _running_on_raspberry_pi() -> bool:
    """True when /proc/device-tree/model looks like a Raspberry Pi board."""
    try:
        model = Path("/proc/device-tree/model").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return False
    return "Raspberry Pi" in model


def _apply_argv_log_dir(argv: list[str]) -> None:
    from config.argv_log_dir import apply_coilshield_log_dir_from_argv

    apply_coilshield_log_dir_from_argv(argv)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CoilShield ICCP monitor/controller")
    p.add_argument(
        "--sim",
        action="store_true",
        help="simulated sensors + no GPIO (sets COILSHIELD_SIM=1)",
    )
    p.add_argument(
        "--real",
        action="store_true",
        help="INA219 + GPIO on Pi (sets COILSHIELD_SIM=0)",
    )
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
    p.add_argument(
        "--set-native",
        type=float,
        default=None,
        metavar="MV",
        help="write native_mv to commissioning.json and exit (e.g. --set-native -232.0)",
    )
    p.add_argument(
        "--log-dir",
        metavar="PATH",
        default=None,
        help="telemetry directory (absolute path); same as COILSHIELD_LOG_DIR — must match dashboard",
    )
    return p.parse_args()


def main() -> int:
    _apply_argv_log_dir(sys.argv[1:])
    args = _parse_args()
    if args.sim:
        os.environ["COILSHIELD_SIM"] = "1"
    if args.real:
        os.environ["COILSHIELD_SIM"] = "0"
    # Leftover COILSHIELD_SIM=1 from a dev machine (systemd, profile, etc.) forces sim
    # on import; on real Pi hardware we default to INA219 unless --sim was passed.
    if not args.sim and _running_on_raspberry_pi():
        if os.environ.get("COILSHIELD_SIM", "0").strip() == "1":
            print(
                "[main] Raspberry Pi: ignoring COILSHIELD_SIM=1 from the environment "
                "(using hardware). Pass --sim to run simulated sensors."
            )
            os.environ["COILSHIELD_SIM"] = "0"
    if args.sim_time_scale is not None:
        os.environ["SIM_TIME_SCALE"] = str(args.sim_time_scale)

    import config.settings as cfg

    if args.set_native is not None:
        from reference import ReferenceElectrode
        ref = ReferenceElectrode()
        ref.save_native(args.set_native)
        print(
            f"[main] native_mv set to {args.set_native:.2f} mV "
            f"→ {cfg.PROJECT_ROOT / 'commissioning.json'}"
        )
        return 0

    import sensors

    sim = sensors.SIM_MODE
    use_hw_gpio = not sim

    if use_hw_gpio:
        try:
            import RPi.GPIO as GPIO  # noqa: N814

            GPIO.setmode(GPIO.BCM)
        except ImportError:
            print(
                "RPi.GPIO not available on this machine — run with "
                "`python3 main.py --sim` (or set COILSHIELD_SIM=1) for bench mode.",
                file=sys.stderr,
            )
            return 1

    from iccp_runtime import run_iccp_forever

    return run_iccp_forever(args)


if __name__ == "__main__":
    raise SystemExit(main())
