"""
Set COILSHIELD_LOG_DIR from argv before ``import config.settings`` (LOG_DIR is fixed at import).

Used by main.py, iccp_cli.py, dashboard.py, and tui.py so telemetry paths match without relying on
shell environment alone.

Dashboard / TUI: if neither ``--log-dir`` nor COILSHIELD_LOG_DIR/ICCP_LOG_DIR is set, Linux builds
can copy the telemetry directory from a running ``iccp start`` (or ``main.py``) process so the UI
tracks the live controller without hand-matching systemd Environment= lines.
"""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

_LATEST_JSON_NAME = "latest.json"


def apply_coilshield_log_dir_from_argv(argv: list[str]) -> None:
    """If argv contains ``--log-dir <path>`` or ``--log-dir=<path>``, set ``COILSHIELD_LOG_DIR``."""
    for i, a in enumerate(argv):
        if a == "--log-dir" and i + 1 < len(argv):
            os.environ["COILSHIELD_LOG_DIR"] = argv[i + 1].strip().strip('"').strip("'")
            return
        if a.startswith("--log-dir="):
            os.environ["COILSHIELD_LOG_DIR"] = a.split("=", 1)[1].strip().strip('"').strip(
                "'"
            )
            return


def _log_dir_set_in_environ() -> bool:
    return bool(
        (os.environ.get("COILSHIELD_LOG_DIR") or "").strip()
        or (os.environ.get("ICCP_LOG_DIR") or "").strip()
    )


def _parse_proc_environ(blob: bytes) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in blob.split(b"\x00"):
        if not item or b"=" not in item:
            continue
        key_b, _, val_b = item.partition(b"=")
        try:
            env[key_b.decode()] = val_b.decode()
        except UnicodeDecodeError:
            continue
    return env


def _resolve_log_dir_for_project(project_root: Path, environ: dict[str, str]) -> Path:
    """Mirror ``config.settings._resolve_log_dir`` without importing settings."""
    raw = (environ.get("COILSHIELD_LOG_DIR") or environ.get("ICCP_LOG_DIR") or "").strip()
    if not raw:
        return (project_root / "logs").resolve()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = project_root / p
    return p.resolve()


def _is_controller_cmdline(parts: list[str]) -> bool:
    """True for ``iccp start`` / ``main.py`` loop, false for commission/probe/dashboard/tui/etc."""
    if not parts:
        return False
    banned = frozenset(
        {
            "commission",
            "probe",
            "dashboard",
            "tui",
            "live",
            "diag",
            "version",
            "clear-fault",
        }
    )
    tail0 = os.path.basename(parts[0]).lower()
    if tail0 == "iccp":
        if len(parts) < 2:
            return False
        sub = parts[1].lower()
        if sub in banned:
            return False
        return sub == "start"
    if any(os.path.basename(p).lower() == "main.py" for p in parts):
        return True
    return False


def _gather_controller_log_dirs_linux() -> list[tuple[Path, float]]:
    """Each entry is ``(resolved LOG_DIR, mtime of LOG_DIR/latest.json or 0)``."""
    out: list[tuple[Path, float]] = []
    for proc_path_s in glob.glob("/proc/[0-9]*"):
        proc_path = Path(proc_path_s)
        try:
            cmdline = proc_path.joinpath("cmdline").read_bytes()
        except OSError:
            continue
        parts = [p.decode(errors="replace") for p in cmdline.split(b"\x00") if p]
        if not _is_controller_cmdline(parts):
            continue
        try:
            env = _parse_proc_environ(proc_path.joinpath("environ").read_bytes())
        except OSError:
            continue
        try:
            cwd = proc_path.joinpath("cwd").resolve()
        except OSError:
            continue
        logd = _resolve_log_dir_for_project(cwd, env)
        latest = logd / _LATEST_JSON_NAME
        try:
            mt = float(latest.stat().st_mtime)
        except OSError:
            mt = 0.0
        out.append((logd, mt))
    return out


def _pick_log_dir_freshest_latest(candidates: list[tuple[Path, float]]) -> Path | None:
    if not candidates:
        return None
    by_dir: dict[str, float] = {}
    for logd, mt in candidates:
        key = str(logd.resolve())
        by_dir[key] = max(by_dir.get(key, 0.0), mt)
    merged = [(Path(k), v) for k, v in by_dir.items()]
    merged.sort(key=lambda kv: kv[1], reverse=True)
    return merged[0][0]


def apply_coilshield_log_dir_from_running_controller_if_unset() -> None:
    """
    If COILSHIELD_LOG_DIR / ICCP_LOG_DIR are unset, set COILSHIELD_LOG_DIR from a running controller.

    Intended for ``iccp dashboard`` / ``iccp tui`` on the Pi when the systemd unit for the
    controller exports ``COILSHIELD_LOG_DIR`` but the dashboard unit does not: we read the same
    value from ``/proc/<pid>/environ`` so ``latest.json`` stays live without extra configuration.
    """
    if _log_dir_set_in_environ():
        return
    if not sys.platform.startswith("linux"):
        return
    cand = _gather_controller_log_dirs_linux()
    best = _pick_log_dir_freshest_latest(cand)
    if best is None:
        return
    os.environ["COILSHIELD_LOG_DIR"] = str(best)
