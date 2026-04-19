#!/usr/bin/env python3
"""
CoilShield `iccp` CLI — entry point for console_scripts `iccp`.

  iccp -start [args ...]   Run ICCP (defaults: --real --verbose --skip-commission)
  iccp probe [args ...]    Full hardware probe (see hw_probe.py)
  iccp clear-fault         Touch clear_fault (uses config CLEAR_FAULT_FILE)
  iccp version             Package / install version
  iccp --help              Usage
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _print_help() -> None:
    print(
        """CoilShield ICCP

  iccp -start [args ...]     Run controller. Sets COILSHIELD_SIM=0 unless you pass --sim.
                             Default argv: --real --verbose --skip-commission

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

    if cmd in ("clear-fault", "clear_fault", "clear-faults"):
        return _cmd_clear_fault()

    if cmd in ("version", "-V", "--version"):
        return _cmd_version()

    print(f"Unknown command: {cmd!r}. Try: iccp --help", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
