#!/usr/bin/env python3
"""
CoilShield ``iccp`` CLI — the single supported entry point for this project.

Every subcommand has exactly one canonical spelling. No aliases.

  iccp start [args ...]     Run the controller (defaults: --real --verbose --skip-commission)
  iccp commission [--sim] [--force] [--native-only] [--no-anode-prompts]
                            Self-commissioning (writes commissioning.json).
                            --native-only runs Phase 1 only (native baseline re-capture).
                            Pauses for anode in/out unless --no-anode-prompts (or non-TTY / sim).
  iccp probe [args ...]     Hardware probe (see `iccp probe --help` / hw_probe.py)
  iccp tui [--poll-interval SEC] [--log-dir PATH]
                            Terminal UI (Textual)
  iccp dashboard [--host H] [--port P] [--log-dir PATH]
                            Web dashboard (Flask)
  iccp live                 Print logs/latest.json (pretty JSON)
  iccp diag [--request]     Print logs/diagnostic_snapshot.json (or touch request_diag)
  iccp clear-fault [--channel N]
                            Clear all channels (no --channel) or only channel N (0-based).
  iccp version              Print coilshield-iccp package version
  iccp --help / -h / help  Usage (all commands + quick guide)

On a Raspberry Pi, recognized subcommands run ``sudo systemctl daemon-reload`` unless
``ICCP_SYSTEMD_SYNC=0``. ``tui`` / ``dashboard`` / ``live`` / ``diag`` stop there.
``commission`` / ``probe`` run ``stop <unit>``. ``start`` only ``daemon-reload``
(never ``restart`` — avoids two controllers). Everything else runs ``restart <unit>``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
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

  Typical order on a Pi (after wiring):
    1)  sudo systemctl stop iccp   — free I²C / PWM (probe/commission also stop the unit)
    2)  iccp probe                  — I²C sweep + INA/ADS checks; add --live --interval 0.5
         to stream readings and confirm the mux and sensors (see: iccp probe --help)
    3)  iccp commission             — native ref + target current (needs stable hardware)
    4)  iccp start  or  systemctl   — run the controller; match --log-dir with tui/dashboard

  Per-command flags:  iccp <command> --help
    e.g.  iccp probe --help   iccp tui --help   iccp start --help

  iccp start [args ...]      Run controller. Sets COILSHIELD_SIM=0 unless you pass --sim.
                             Default argv: --real --verbose --skip-commission
                             On Pi: refuses if systemd iccp is already active (use --force).
                             Optional: --log-dir /abs/path/logs (same as COILSHIELD_LOG_DIR;
                             must match dashboard / tui).
                             Anode subset: --channels 0,2 or --channel 0 (0-based), or
                             --anodes 1,3 or --anode 1 (1-based; singular = one anode only).
                             Requires SHARED_RETURN_PWM = False. Same flags on tui, dashboard,
                             commission, probe. Or env COILSHIELD_ACTIVE_CHANNELS=0,2.
                             Field tunables: COILSHIELD_TARGET_MA, COILSHIELD_REF_ADS_SCALE,
                             COILSHIELD_ADS1115_FSR_V, optional COILSHIELD_MUX_ADDRESS for TCA rigs, …

  iccp commission [--sim] [--force] [--native-only] [--no-anode-prompts]
                             Self-commission (writes commissioning.json).
                             On Pi uses hardware unless --sim. Aborts if latest.json is fresh
                             unless --force (stop the iccp service first).
                             --native-only runs Phase 1 only (native baseline re-capture).
                             Without --no-anode-prompts, waits for Enter: anodes out (Phase 1),
                             then anodes in (before Phase 2). Disabled for sim, pipes, or
                             COMMISSIONING_ANODE_PLACEMENT_PROMPTS=0 / ICCP_COMMISSION_NO_ANODE_PROMPTS=1.
                             If Enter is ignored, pass --no-anode-prompts (reads are from /dev/tty first).

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
  iccp clear-fault [--channel N]
                             Without --channel: touch the all-channel fault-clear file.
                             With --channel N (0-based): write an atomic JSON side file
                             that clears only channel N on the next controller tick.
  iccp version               Show coilshield-iccp version.
  iccp --help / -h / help   This list (and guide above).

On a Raspberry Pi, recognized subcommands run ``sudo systemctl daemon-reload`` automatically.
``tui`` / ``dashboard`` / ``live`` / ``diag`` stop there (read-only). ``commission`` /
``probe`` run ``stop iccp`` first. Foreground ``start`` runs ``daemon-reload`` only (never
``restart``) — if the iccp unit is already ``active``, ``start`` exits unless you pass
``--force``. Other subcommands run ``restart iccp``. Disable with ICCP_SYSTEMD_SYNC=0;
override unit name with ICCP_SYSTEMD_UNIT=myunit.

Install:  pip install -e .   (from repo root, in your venv)
"""
    )


def _cmd_clear_fault(rest: list[str] | None = None) -> int:
    """Clear one or all latched faults.

    ``iccp clear-fault`` (no args) touches the all-channel clear file — the controller
    drains it on the next tick and clears every channel in FAULT.

    ``iccp clear-fault --channel N`` writes an atomic JSON side file (0-based index to
    match the rest of the code, see docs/iccp-requirements.md §6.2 Decision Q5). Only
    that channel is cleared; others keep their state.
    """
    import config.settings as cfg

    rest = rest or []
    channel: int | None = None
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--channel":
            if i + 1 >= len(rest):
                print("ERROR: --channel requires an integer argument (0-based)", file=sys.stderr)
                return 2
            try:
                channel = int(rest[i + 1])
            except ValueError:
                print(f"ERROR: --channel expects an integer, got {rest[i + 1]!r}", file=sys.stderr)
                return 2
            i += 2
            continue
        if a.startswith("--channel="):
            try:
                channel = int(a.split("=", 1)[1])
            except ValueError:
                print(f"ERROR: could not parse {a!r}", file=sys.stderr)
                return 2
            i += 1
            continue
        print(f"ERROR: unknown argument {a!r}", file=sys.stderr)
        return 2

    if channel is None:
        path = cfg.CLEAR_FAULT_FILE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
            print(f"OK: touched {path}")
            return 0
        except OSError as e:
            print(f"ERROR: could not write {path}: {e}", file=sys.stderr)
            return 1

    num_ch = int(getattr(cfg, "NUM_CHANNELS", 4))
    if channel < 0 or channel >= num_ch:
        print(
            f"ERROR: --channel {channel} out of range 0..{num_ch - 1}",
            file=sys.stderr,
        )
        return 2
    path = getattr(cfg, "CLEAR_FAULT_CHANNEL_FILE", None)
    if path is None:
        print("ERROR: CLEAR_FAULT_CHANNEL_FILE is not configured", file=sys.stderr)
        return 1
    try:
        import json
        import os

        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"channel": channel, "ts": time.time()})
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
        print(f"OK: wrote {path} (channel {channel})")
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
    """Run commissioning.run() — same sequence as first boot of the controller.

    With ``--native-only`` the CLI runs Phase 1 only via
    :func:`commissioning.run_native_only`, useful for scheduled native re-capture
    or a maintenance pass after a reference-electrode swap.
    """
    rest, force_comm = _split_force_flag(rest)
    native_only = "--native-only" in rest
    if native_only:
        rest = [a for a in rest if a != "--native-only"]
    no_anode_prompts = "--no-anode-prompts" in rest
    if no_anode_prompts:
        rest = [a for a in rest if a != "--no-anode-prompts"]
    anode_prompt_kw = {"anode_placement_prompts": False} if no_anode_prompts else {}
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

    import config.settings as cfg
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
    from console_ui import print_commission_header

    ctrl = Controller()
    ref = ReferenceElectrode()
    print_commission_header()
    print(f"[main] Reference path: {ref_hw_message()}")
    try:
        if native_only:
            native_mv, reason = commissioning.run_native_only(
                ref, ctrl, sim_state=sim_state, verbose=True, **anode_prompt_kw
            )
            if native_mv is None:
                print(
                    f"[main] Native capture failed: {reason}",
                    file=sys.stderr,
                )
                return 1
            print(
                f"[main] Native re-captured: {native_mv:.2f} mV ({reason})"
            )
        else:
            commissioned = commissioning.run(
                ref, ctrl, sim_state=sim_state, verbose=True, **anode_prompt_kw
            )
            print(
                f"[main] Done — commissioned_target_ma={commissioned:.3f} mA "
                f"→ {cfg.PROJECT_ROOT / 'commissioning.json'}"
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
    from config.argv_channels import apply_coilshield_active_channels_from_argv

    apply_coilshield_log_dir_from_argv(argv)
    if apply_coilshield_active_channels_from_argv(argv) == 2:
        return 2

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
        return _cmd_clear_fault(rest)

    if cmd == "version":
        return _cmd_version()

    print(f"Internal error: command {cmd!r} missing handler.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
