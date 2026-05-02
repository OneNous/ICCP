"""
Ensure the Linux Bluetooth controller is usable (Raspberry Pi + BlueZ).

Soft rfkill blocks provisioning even though BlueZ can be running. We unblock
via ``rfkill(8)`` when available, then sysfs fallback, before DBus ``Powered``.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path


def _unblock_bluetooth_sysfs() -> None:
    root = Path("/sys/class/rfkill")
    if not root.is_dir():
        return
    for child in root.iterdir():
        if not child.is_dir() or not child.name.startswith("rfkill"):
            continue
        typ_f = child / "type"
        if not typ_f.is_file():
            continue
        try:
            if typ_f.read_text(encoding="utf-8").strip().lower() != "bluetooth":
                continue
        except OSError:
            continue
        soft = child / "soft"
        if not soft.is_file():
            continue
        try:
            soft.write_bytes(b"0\n")
        except OSError:
            pass


def _rfkill_unblock_cli() -> None:
    for exe in ("/usr/sbin/rfkill", "/sbin/rfkill"):
        if Path(exe).is_file():
            try:
                subprocess.run(
                    [exe, "unblock", "bluetooth"],
                    check=False,
                    timeout=10,
                    capture_output=True,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            return
    try:
        subprocess.run(
            ["rfkill", "unblock", "bluetooth"],
            check=False,
            timeout=10,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def ensure_bluetooth_enabled(*, verbose: bool = False) -> None:
    """
    Unblock Bluetooth rfkill so the adapter appears on DBus.

    Idempotent; safe to call before every BLE session. Requires root for
    ``rfkill`` / sysfs writes on typical Pi images.
    """
    if verbose:
        print("ble_provision: ensuring Bluetooth rfkill is unblocked")
    _rfkill_unblock_cli()
    _unblock_bluetooth_sysfs()


def wait_for_ble_adapter_path(
    find_adapter, *, attempts: int = 25, delay_s: float = 0.2
) -> str | None:
    """
    Poll ``find_adapter()`` until it returns a path or attempts exhausted.

    ``find_adapter`` is typically ``lambda: find_adapter_path(bus)``.
    """
    for i in range(attempts):
        path = find_adapter()
        if path:
            return path
        if i < attempts - 1:
            ensure_bluetooth_enabled()
            time.sleep(delay_s)
    return None
