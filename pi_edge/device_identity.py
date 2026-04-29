"""
Stable device serial for Pi edge services (register, MQTT client id, topics).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def _read_cpuinfo_serial() -> str | None:
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"^Serial\s*:\s*0*(\w+)\s*$", text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return None
    s = m.group(1).strip()
    return s or None


def _read_machine_id() -> str | None:
    for p in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            raw = p.read_text(encoding="ascii").strip()
        except OSError:
            continue
        if len(raw) >= 8:
            return raw
    return None


def _read_vcgencmd_serial() -> str | None:
    try:
        r = subprocess.run(
            ["vcgencmd", "otp_dump"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    for line in r.stdout.splitlines():
        line = line.strip()
        low = line.lower()
        for prefix in ("28:", "29:", "30:"):
            if low.startswith(prefix):
                hexpart = line.split(":", 1)[1].strip()
                if len(hexpart) >= 8 and re.fullmatch(r"[0-9a-fA-F]+", hexpart):
                    return hexpart
    return None


def _read_rpi_serial_tool() -> str | None:
    try:
        r = subprocess.run(
            ["rpi-eeprom-config"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    m = re.search(r"^BOARD_SERIAL=(\S+)", r.stdout, re.MULTILINE)
    return m.group(1).strip() if m else None


def device_serial() -> str:
    """
    Prefer Raspberry Pi ``/proc/cpuinfo`` Serial; fall back to ``machine-id``.
    Normalized lowercase hex string suitable for MQTT topics and HTTP payloads.
    """
    for fn in (
        _read_cpuinfo_serial,
        _read_vcgencmd_serial,
        _read_rpi_serial_tool,
        _read_machine_id,
    ):
        s = fn()
        if s:
            return s.lower()
    return "unknown-device"
