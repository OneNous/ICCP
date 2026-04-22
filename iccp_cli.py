#!/usr/bin/env python3
"""
CoilShield ``iccp`` CLI — the single supported entry point for this project.

Every subcommand has exactly one canonical spelling. No aliases.

  iccp start [args ...]     Run the controller (defaults: --real --verbose --skip-commission)
  iccp commission [--sim] [--force]
                            Self-commissioning (writes commissioning.json)
  iccp probe [args ...]     Hardware probe (see `iccp probe --help` / hw_probe.py)
  iccp tui [--poll-interval SEC] [--log-dir PATH]
                            Terminal UI (Textual)
  iccp dashboard [--host H] [--port P] [--log-dir PATH]
                            Web dashboard (Flask)
  iccp live                 Print logs/latest.json (pretty JSON)
  iccp diag [--request]     Print logs/diagnostic_snapshot.json (or touch request_diag)
  iccp clear-fault          Touch the clear-fault file (config.settings.CLEAR_FAULT_FILE)
  iccp version              Print coilshield-iccp package version
  iccp --help / -h          Usage

On a Raspberry Pi, recognized subcommands run ``sudo systemctl daemon-reload`` unless
``ICCP_SYSTEMD_SYNC=0``. ``tui`` / ``dashboard`` / ``live`` / ``diag`` stop there.
``commission`` / ``probe`` run ``stop <unit>``. ``start`` only ``daemon-reload``
(never ``restart`` — avoids two controllers). Everything else runs ``restart <unit>``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from platform_util import running_on_raspberry_pi


def _project_root() -> Path:
    return Path(__file__).resolve().parent


_ICCP_CLI_COMMANDS: frozenset[str] = frozenset(
    {
        "start",
        "commission",
        "probe",
        "tui",
        "dashboard",
        "live",
        "diag",
        "clear-fault",
        "version",
    }
)

# Subcommands that only read telemetry / show UI — never restart the controller.
_ICCP_CLI_READ_ONLY_SYSTEMD_KEYS: frozenset[str] = frozenset(
    {"tui", "dashboard", "live", "diag"}
)


def _sync_systemd_for_iccp_cli(cmd: str) -> None:
    """
    On a Raspberry Pi, refresh systemd and reconcile the ``iccp`` service so you do not
    need to run ``daemon-reload`` / ``restart`` by hand after editing units or code.

    - ``ICCP_SYSTEMD_SYNC=0`` — disable entirely (CI, laptops, or no sudo).
    - ``ICCP_SYSTEMD_UNIT`` — unit name (default ``iccp``).

    Foreground ``iccp start``: only ``daemon-reload`` (never ``restart`` — avoids two
    controllers). ``commission`` / ``probe``: ``daemon-reload`` then ``stop`` the unit
    (``restart`` would leave PWM running and break commission). Read-only ``tui`` /
    ``dashboard`` / ``live`` / ``diag``: ``daemon-reload`` only. Other commands:
    ``daemon-reload`` then ``restart``.
    """
    if not running_on_raspberry_pi():
        return
    flag = os.environ.get("ICCP_SYSTEMD_SYNC", "1").strip().lower()
    if flag in ("0", "off", "false", "no", "disable", "disabled"):
        return

    unit = (os.environ.get("ICCP_SYSTEMD_UNIT") or "iccp").strip() or "iccp"

    def _run(args: list[str]) -> int:
        try:
            r = subprocess.run(
                ["sudo", "systemctl", *args],
                timeout=180,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0 and (r.stderr or "").strip():
                sys.stderr.write(r.stderr)
                if not r.stderr.endswith("\n"):
                    sys.stderr.write("\n")
            return int(r.returncode)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            print(f"[iccp] systemd: {e}", file=sys.stderr)
            return 1

    if _run(["daemon-reload"]) != 0:
        print(
            "[iccp] `sudo systemctl daemon-reload` failed — check sudo; disable auto-sync "
            "with ICCP_SYSTEMD_SYNC=0",
            file=sys.stderr,
        )
        return

    if cmd == "start":
        print(
            "[iccp] systemctl daemon-reload OK (no restart before foreground start — "
            "would duplicate a systemd-run controller)."
        )
        return
    if cmd == "commission":
        _run(["stop", unit])
        print(
            f"[iccp] systemctl stop {unit} (before commission; `restart` would leave the "
            "service driving PWM)."
        )
        return
    if cmd == "probe":
        _run(["stop", unit])
        print(f"[iccp] systemctl stop {unit} (before probe — frees PWM / I2C).")
        return

    if cmd in _ICCP_CLI_READ_ONLY_SYSTEMD_KEYS:
        print(
            "[iccp] systemctl daemon-reload OK "
            "(tui / dashboard / live / diag do not restart the service)."
        )
        return

    if _run(["restart", unit]) == 0:
        print(f"[iccp] systemctl daemon-reload && systemctl restart {unit}")
    else:
        print(
            f"[iccp] systemctl restart {unit} failed (no unit? set ICCP_SYSTEMD_UNIT). "
            "Disable with ICCP_SYSTEMD_SYNC=0.",
            file=sys.stderr,
        )


def _print_help() -> None:
    print(
        """CoilShield ICCP — single CLI surface.

  iccp start [args ...]      Run controller. Sets COILSHIELD_SIM=0 unless you pass --sim.
                             Default argv: --real --verbose --skip-commission
                             On Pi: refuses if systemd iccp is already active (use --force).
                             Optional: --log-dir /abs/path/logs (same as COILSHIELD_LOG_DIR;
                             must match dashboard / tui).

  iccp commission [--sim] [--force]
                             Self-commission (writes commissioning.json).
                             On Pi uses hardware unless --sim. Aborts if latest.json is fresh
                             unless --force (stop the iccp service first).

  iccp probe [args ...]      Hardware probe (I2C, INA219 smbus2, ADS1115, DS18B20, PWM).
                             See `iccp probe --help` for options.

  iccp tui [--poll-interval SEC] [--log-dir PATH]
                             Live terminal dashboard (Textual). Same data as the web UI.
                             Keys: d/D diag, f clear fault, t paths, p probe.

  iccp dashboard [--host H] [--port P] [--log-dir PATH]
                             Web dashboard (Flask). Reads the same latest.json / SQLite the
                             controller writes; use matching --log-dir / COILSHIELD_LOG_DIR.

  iccp live                  Pretty-print logs/latest.json once.
  iccp diag [--request]      Print diagnostic_snapshot.json (or touch request_diag).
  iccp clear-fault           Touch the fault-clear file (config.settings.CLEAR_FAULT_FILE).
  iccp version               Show coilshield-iccp version.
  iccp --help / -h           This message.

On a Raspberry Pi, recognized subcommands run ``sudo systemctl daemon-reload`` automatically.
``tui`` / ``dashboard`` / ``live`` / ``diag`` stop there (read-only). ``commission`` /
``probe`` run ``stop iccp`` first. Foreground ``start`` runs ``daemon-reload`` only (never
``restart``) — if the iccp unit is already ``active``, ``start`` exits unless you pass
``--force``. Other subcommands run ``restart iccp``. Disable with ICCP_SYSTEMD_SYNC=0;
override unit name with ICCP_SYSTEMD_UNIT=myunit.

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


def _cmd_live() -> int:
    import json

    import config.settings as cfg

    p = cfg.LOG_DIR / cfg.LATEST_JSON_NAME
    tp = cfg.resolved_telemetry_paths()
    print(f"# Reading: {tp['latest_json']} (log_dir_source={tp['log_dir_source']})")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR reading {p}: {e}", file=sys.stderr)
        return 1
    print(json.dumps(data, indent=2))
    return 0


def _cmd_diag(rest: list[str]) -> int:
    import config.settings as cfg

    snap = cfg.LOG_DIR / getattr(cfg, "DIAGNOSTIC_SNAPSHOT_JSON", "diagnostic_snapshot.json")
    if "--request" in rest:
        req = cfg.LOG_DIR / getattr(cfg, "DIAGNOSTIC_REQUEST_FILE", "request_diag")
        try:
            cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
            req.touch()
        except OSError as e:
            print(f"ERROR: could not touch {req}: {e}", file=sys.stderr)
            return 1
        print(
            f"OK: {req} — keep the controller running; snapshot → "
            f"{snap.name} (rate-limited)."
        )
        return 0
    try:
        print(snap.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"No snapshot at {snap} yet.", file=sys.stderr)
        return 1
    return 0


def _cmd_version() -> int:
    try:
        import importlib.metadata as md

        v = md.version("coilshield-iccp")
    except Exception:
        v = "unknown (run: pip install -e . from repo root)"
    print(f"coilshield-iccp {v}")
    return 0


def _split_force_flag(rest: list[str]) -> tuple[list[str], bool]:
    """Strip ``--force`` from argv; return (remaining_argv, force)."""
    force = False
    out: list[str] = []
    for a in rest:
        if a == "--force":
            force = True
            continue
        out.append(a)
    return out, force


def _systemd_unit_is_active_non_sudo(unit: str) -> bool | None:
    """
    True if ``systemctl is-active`` reports ``active``.

    Returns None if ``systemctl`` is unavailable or the state cannot be read
    (do not block foreground start).
    """
    try:
        r = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    state = (r.stdout or "").strip()
    if r.returncode == 0 and state == "active":
        return True
    if state in ("inactive", "failed", "activating", "deactivating"):
        return False
    return None


def _abort_if_systemd_iccp_active_for_foreground_start(force: bool) -> int | None:
    """
    If the packaged systemd unit is already running, refuse ``iccp start`` so two
    controllers do not share PWM / I2C. Override with ``--force`` (unsafe if unsure).
    """
    if force:
        return None
    if not running_on_raspberry_pi():
        return None
    flag = os.environ.get("ICCP_SYSTEMD_SYNC", "1").strip().lower()
    if flag in ("0", "off", "false", "no", "disable", "disabled"):
        return None
    unit = (os.environ.get("ICCP_SYSTEMD_UNIT") or "iccp").strip() or "iccp"
    if _systemd_unit_is_active_non_sudo(unit) is not True:
        return None
    print(
        "[iccp start] ERROR: "
        f"systemd unit {unit!r} is already active — another controller owns PWM/GPIO.\n"
        f"  Stop it first:  sudo systemctl stop {unit}\n"
        "  Or use only the service (recommended), not foreground start in parallel.\n"
        "  Override (unsafe):  iccp start --force",
        file=sys.stderr,
    )
    return 1


def _abort_if_concurrent_controller_active(*, force: bool, on_pi_hw: bool) -> int | None:
    """
    If latest.json is very fresh, another controller likely still owns PWM —
    abort unless --force.
    """
    if force or not on_pi_hw:
        return None
    try:
        import time

        import config.settings as cfg

        p = cfg.LOG_DIR / getattr(cfg, "LATEST_JSON_NAME", "latest.json")
        if not p.is_file():
            return None
        age = time.time() - p.stat().st_mtime
        thr = max(5.0, 4.0 * float(getattr(cfg, "SAMPLE_INTERVAL_S", 1.0)))
        if age >= thr:
            return None
        print(
            "[iccp commission] ERROR: "
            f"{p} was updated {age:.1f}s ago (threshold {thr:.0f}s) — a controller is probably "
            "still running.\n"
            "  Stop it first, e.g.:  sudo systemctl stop iccp\n"
            "  (or stop any manual `iccp start`). Two processes share the same PWM "
            "GPIO; this CLI's all_off() cannot turn off the other process's duty — shunts "
            "stay high and native baseline aborts.\n"
            "  Override (unsafe):  iccp commission --force",
            file=sys.stderr,
        )
        return 1
    except OSError:
        return None


def _cmd_commission(rest: list[str]) -> int:
    """Run commissioning.run() — same sequence as first boot of the controller."""
    rest, force_comm = _split_force_flag(rest)
    use_sim = "--sim" in rest
    if use_sim:
        os.environ["COILSHIELD_SIM"] = "1"
    elif running_on_raspberry_pi():
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
    on_pi_hw = not sim and running_on_raspberry_pi()
    abort = _abort_if_concurrent_controller_active(force=force_comm, on_pi_hw=on_pi_hw)
    if abort is not None:
        return abort

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

    if not argv or argv[0] in ("-h", "--help"):
        _print_help()
        return 0

    cmd = argv[0]
    rest = argv[1:]

    if cmd not in _ICCP_CLI_COMMANDS:
        print(
            f"Unknown command: {cmd!r}. Run `iccp --help` for the full list.",
            file=sys.stderr,
        )
        return 2

    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from config.argv_log_dir import apply_coilshield_log_dir_from_argv

    apply_coilshield_log_dir_from_argv(argv)

    _sync_systemd_for_iccp_cli(cmd)

    if cmd == "start":
        rest, force_start = _split_force_flag(rest)
        blocked = _abort_if_systemd_iccp_active_for_foreground_start(force_start)
        if blocked is not None:
            return blocked
        os.environ.setdefault("COILSHIELD_SIM", "0")
        sys.argv = ["main.py", "--real", "--verbose", "--skip-commission"] + rest
        import main as app

        return int(app.main())

    if cmd == "commission":
        return _cmd_commission(rest)

    if cmd == "probe":
        sys.argv = ["hw_probe.py"] + rest
        import hw_probe

        return int(hw_probe.main())

    if cmd == "tui":
        import tui as tui_mod

        return int(tui_mod.main(rest))

    if cmd == "dashboard":
        sys.argv = ["dashboard.py"] + rest
        import dashboard as dash_mod

        dash_mod.main()
        return 0

    if cmd == "live":
        return _cmd_live()

    if cmd == "diag":
        return _cmd_diag(rest)

    if cmd == "clear-fault":
        return _cmd_clear_fault()

    if cmd == "version":
        return _cmd_version()

    print(f"Internal error: command {cmd!r} missing handler.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
