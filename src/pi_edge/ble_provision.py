"""
BLE GATT Wi‑Fi provisioning entrypoint (Raspberry Pi + BlueZ).

Requires optional ``[ble]`` dependencies (``dbus-python``, PyGObject). Gated by
``ICCP_BLE_PROVISIONING=1`` and/or a flag file (see ``provisioning_requested``).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _register_after_wifi(_ip: str | None) -> None:
    if os.environ.get("ICCP_REGISTER_AFTER_WIFI", "").strip() != "1":
        return
    exe = shutil.which("iccp-cloud-register")
    if not exe:
        print(
            "iccp-ble-provision: ICCP_REGISTER_AFTER_WIFI=1 but iccp-cloud-register "
            "not found on PATH",
            file=sys.stderr,
        )
        return
    try:
        subprocess.Popen(
            [exe],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        print(f"iccp-ble-provision: could not spawn cloud register: {e}", file=sys.stderr)


def provisioning_requested() -> bool:
    if os.environ.get("ICCP_BLE_PROVISIONING", "").strip() == "1":
        return True
    flag = (
        os.environ.get("ICCP_BLE_PROVISION_FLAG", "").strip()
        or "/etc/iccp/ble_provision.enable"
    )
    return Path(flag).is_file()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="ICCP BLE Wi‑Fi provisioning (BlueZ GATT)")
    p.add_argument(
        "--iface",
        default=os.environ.get("ICCP_WIFI_IFACE", "wlan0"),
        help="Wi‑Fi interface (wpa_cli / nmcli; default wlan0). Backend: COILSHIELD_WIFI_BACKEND.",
    )
    p.add_argument(
        "--name",
        default=os.environ.get("ICCP_BLE_LOCAL_NAME", "CoilShield-ICCP"),
        help="BLE advertised local name",
    )
    p.add_argument(
        "--window-s",
        type=float,
        default=float(os.environ.get("ICCP_BLE_WINDOW_S", "600")),
        help="Stop advertising after this many seconds (0 = until killed)",
    )
    p.add_argument(
        "--no-register-after-wifi",
        action="store_true",
        help="Ignore ICCP_REGISTER_AFTER_WIFI even if set",
    )
    args = p.parse_args(argv)

    if sys.platform != "linux":
        print("iccp-ble-provision: Linux required (BlueZ).", file=sys.stderr)
        return 2

    if not provisioning_requested():
        print(
            "iccp-ble-provision: not enabled — set ICCP_BLE_PROVISIONING=1 "
            "or create the flag file (see ICCP_BLE_PROVISION_FLAG).",
            file=sys.stderr,
        )
        return 0

    try:
        from pi_edge.ble_gatt_bluez import run_ble_provision_server
    except ImportError as e:
        print(
            "iccp-ble-provision: missing BLE dependencies — "
            "install with: pip install 'coilshield-iccp[ble]' "
            f"({e})",
            file=sys.stderr,
        )
        return 2

    hook = None if args.no_register_after_wifi else _register_after_wifi
    try:
        run_ble_provision_server(
            iface=args.iface,
            local_name=args.name,
            window_s=args.window_s,
            on_wifi_ok=hook,
        )
    except RuntimeError as e:
        print(f"iccp-ble-provision: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
