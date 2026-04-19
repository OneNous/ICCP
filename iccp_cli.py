#!/usr/bin/env python3
"""
CoilShield `iccp` CLI — entry point for console_scripts `iccp`.

  iccp -start [args ...]   Run ICCP (defaults: --real --verbose --skip-commission)
  iccp commission [--sim]  Reference + current commissioning (writes commissioning.json)
  iccp probe [args ...]    Full hardware probe (see hw_probe.py)
  iccp clear-fault         Touch clear_fault (uses config CLEAR_FAULT_FILE)
  iccp version             Package / install version
  iccp --help              Usage
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _running_on_raspberry_pi() -> bool:
    try:
        model = Path("/proc/device-tree/model").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return False
    return "Raspberry Pi" in model


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _print_help() -> None:
    print(
        """CoilShield ICCP

  iccp -start [args ...]     Run controller. Sets COILSHIELD_SIM=0 unless you pass --sim.
                             Default argv: --real --verbose --skip-commission

  iccp commission [--sim]    Self-commission reference (native_mv) + ramp to target shift;
                             writes commissioning.json. On Pi uses hardware unless --sim.
                             Off-Pi defaults to simulator unless you run on a Pi.

  iccp probe [args ...]      Hardware probe (I2C, INA219 smbus2, ADS1115, DS18B20, PWM).
                             Same options as hw_probe.py (--continuous, --skip-pwm, …).

  iccp clear-fault           Create/truncate clear_fault (see config.settings CLEAR_FAULT_FILE).

  iccp version               Show coilshield-iccp version (from pip metadata).

  iccp --help                This message.

Install:  pip install -e .   (from repo root, in your venv)
"""
    )


def _cmd_clear_fault() -> int:
    import config.settings as cfg

    path = cfg.CLEAR_FAULT_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        print(f"OK: touched {path}")
        return 0
    except OSError as e:
        print(f"ERROR: could not write {path}: {e}", file=sys.stderr)
        return 1


def _cmd_version() -> int:
    try:
        import importlib.metadata as md

        v = md.version("coilshield-iccp")
    except Exception:
        v = "unknown (run: pip install -e . from repo root)"
    print(f"coilshield-iccp {v}")
    return 0


def _cmd_commission(rest: list[str]) -> int:
    """Run commissioning.run() — same sequence as first boot of main.py (no --skip-commission)."""
    use_sim = "--sim" in rest
    if use_sim:
        os.environ["COILSHIELD_SIM"] = "1"
    elif _running_on_raspberry_pi():
        if os.environ.get("COILSHIELD_SIM", "0").strip() == "1":
            print(
                "[iccp commission] Raspberry Pi: ignoring COILSHIELD_SIM=1 from environment."
            )
            os.environ["COILSHIELD_SIM"] = "0"
        else:
            os.environ.setdefault("COILSHIELD_SIM", "0")
    else:
        os.environ["COILSHIELD_SIM"] = "1"
        print(
            "[iccp commission] Not on a Raspberry Pi — using simulator. "
            "For real hardware, run on the Pi without --sim."
        )

    import commissioning
    import sensors
    from control import Controller
    from reference import ReferenceElectrode, ref_hw_message

    sim = sensors.SIM_MODE
    use_hw_gpio = not sim
    if use_hw_gpio:
        try:
            import RPi.GPIO as GPIO  # noqa: N814

            GPIO.setmode(GPIO.BCM)
        except ImportError:
            print(
                "ERROR: RPi.GPIO not available — use `iccp commission --sim` on this machine.",
                file=sys.stderr,
            )
            return 1

    sim_state = sensors.SimSensorState() if sim else None
    ctrl = Controller()
    ref = ReferenceElectrode()
    print(f"[iccp commission] Reference path: {ref_hw_message()}")
    try:
        commissioned = commissioning.run(
            ref, ctrl, sim_state=sim_state, verbose=True
        )
        print(
            f"[iccp commission] Done. commissioned_target_ma={commissioned:.3f} "
            f"(see commissioning.json)"
        )
    finally:
        ctrl.cleanup()
        if use_hw_gpio:
            try:
                import RPi.GPIO as GPIO  # noqa: N814

                GPIO.cleanup()
            except Exception:
                pass
    return 0


def main() -> int:
    argv = sys.argv[1:]
    root = _project_root()

    if not argv or argv[0] in ("-h", "--help", "help"):
        _print_help()
        return 0

    cmd = argv[0]
    rest = argv[1:]

    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    if cmd in ("-start", "--start", "start"):
        os.environ.setdefault("COILSHIELD_SIM", "0")
        sys.argv = ["main.py", "--real", "--verbose", "--skip-commission"] + rest
        import main as app

        return int(app.main())

    if cmd == "probe":
        sys.argv = ["hw_probe.py"] + rest
        import hw_probe

        return int(hw_probe.main())

    if cmd in ("commission", "--commission", "-commission"):
        return _cmd_commission(rest)

    if cmd in ("clear-fault", "clear_fault", "clear-faults"):
        return _cmd_clear_fault()

    if cmd in ("version", "-V", "--version"):
        return _cmd_version()

    print(f"Unknown command: {cmd!r}. Try: iccp --help", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
