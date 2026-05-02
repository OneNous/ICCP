#!/usr/bin/env python3
"""
CoilShield Pi BLE provisioning service (Linux + BlueZ).

Exposes GATT: device_info (read), wifi_credentials (write), status (notify).
On first central connection, pushes an initial status notify. WiFi apply uses
nmcli when available, else appends a network block to WPA_SUPPLICANT_CONF.
UUIDs and payloads are documented in packages/api-contract/ble-protocol.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

# UUIDs — keep in sync with packages/api-contract/ble-protocol.md
SERVICE_UUID = "e3310000-62ec-4c38-baf1-ae1bd4f6788d"
CHAR_DEVICE_INFO = "e3310001-62ec-4c38-baf1-ae1bd4f6788d"
CHAR_WIFI = "e3310002-62ec-4c38-baf1-ae1bd4f6788d"
CHAR_STATUS = "e3310003-62ec-4c38-baf1-ae1bd4f6788d"
CHAR_COMMAND = "e3310004-62ec-4c38-baf1-ae1bd4f6788d"
CHAR_WIFI_SCAN = "e3310006-62ec-4c38-baf1-ae1bd4f6788d"
OP_WIFI_SCAN = 0x07

STATUS_IDLE = 0
STATUS_WIFI_RECEIVED = 1
STATUS_CONNECTING = 2
STATUS_ONLINE = 3
STATUS_ERROR = 255


def _norm_uuid(u: str) -> str:
    return str(__import__("uuid").UUID(u))


def _device_serial() -> str:
    """Stable serial. Same precedence as src/device_identity.derive_device_serial()."""
    env = (os.environ.get("COILSHIELD_SERIAL") or "").strip()
    if env:
        return env
    try:
        from device_identity import derive_device_serial  # type: ignore[import-not-found]

        return derive_device_serial()
    except ImportError:
        return "CS-00-00-00-00"


def _apply_wifi_nmcli(ssid: str, password: str, log: logging.Logger) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            [
                "nmcli",
                "device",
                "wifi",
                "connect",
                ssid,
                "password",
                password,
                "name",
                "coilshield-ble",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if r.returncode == 0:
            return True, out or "connected"
        log.warning("nmcli failed: %s", err or out)
        return False, err or out or "nmcli failed"
    except FileNotFoundError:
        return False, "nmcli not found"
    except subprocess.TimeoutExpired:
        return False, "nmcli timeout"


def _wpa_dq(s: str) -> str:
    """Double-quoted string for wpa_supplicant.conf ssid/psk."""
    esc = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{esc}"'


def _apply_wifi_wpa_supplicant(
    ssid: str, password: str, conf_path: str, log: logging.Logger
) -> tuple[bool, str]:
    block = (
        "\n# coilshield-ble-provision\n"
        "network={\n"
        f"\tssid={_wpa_dq(ssid)}\n"
        f"\tpsk={_wpa_dq(password)}\n"
        "\tkey_mgmt=WPA-PSK\n"
        "}\n"
    )
    try:
        with open(conf_path, "a", encoding="utf-8") as f:
            f.write(block)
    except OSError as e:
        log.exception("wpa_supplicant write failed")
        return False, str(e)
    try:
        subprocess.run(
            ["wpa_cli", "-i", os.environ.get("WPA_IFACE", "wlan0"), "reconfigure"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        log.info("wpa_cli not found; reboot or reconfigure manually")
    return True, f"appended network block to {conf_path}"


async def apply_wifi(
    ssid: str, password: str, conf_path: str, log: logging.Logger
) -> tuple[bool, str]:
    ok, msg = _apply_wifi_nmcli(ssid, password, log)
    if ok:
        return True, msg
    ok2, msg2 = _apply_wifi_wpa_supplicant(ssid, password, conf_path, log)
    return ok2, msg2 if ok2 else f"{msg}; {msg2}"


class ProvisionState:
    def __init__(self, server: Any, serial: str, model: str, fw: str):
        self.server = server
        self.serial = serial
        self.model = model
        self.fw = fw

    def pack_status(self, state: int, code: int = 0) -> bytearray:
        return bytearray([state & 0xFF, code & 0xFF])

    def device_info_payload(self) -> bytearray:
        payload = {"serial": self.serial, "model": self.model, "fw": self.fw}
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return bytearray(raw[:240])

    def wifi_scan_payload(self) -> bytearray:
        """Bench SSIDs — mirrors Pi `wifi_scan_results` for mobile UI tests."""
        payload = {"ssids": ["CoilBench-Net", "CoilLab-5G", "CoilDev-Guest"]}
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return bytearray(raw[:512])

    async def push_wifi_scan_after_command(self, log: logging.Logger) -> None:
        await asyncio.sleep(0.25)
        ch = self.server.get_characteristic(_norm_uuid(CHAR_WIFI_SCAN))
        if ch is None:
            return
        ch.value = self.wifi_scan_payload()
        self.server.update_value(_norm_uuid(SERVICE_UUID), _norm_uuid(CHAR_WIFI_SCAN))
        log.info("bench wifi_scan_results refreshed (mock SSIDs)")

    async def push_status(self, state: int, code: int = 0) -> None:
        char = self.server.get_characteristic(_norm_uuid(CHAR_STATUS))
        if char is None:
            return
        char.value = self.pack_status(state, code)
        self.server.update_value(_norm_uuid(SERVICE_UUID), _norm_uuid(CHAR_STATUS))

    async def on_wifi_write(self, raw: bytes, conf_path: str, log: logging.Logger) -> None:
        await self.push_status(STATUS_WIFI_RECEIVED, 0)
        try:
            obj = json.loads(raw.decode("utf-8"))
            ssid = str(obj.get("ssid", "")).strip()
            psk = str(obj.get("psk", obj.get("password", ""))).strip()
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as e:
            await self.push_status(STATUS_ERROR, 1)
            log.warning("wifi json parse error: %s", e)
            return
        if not ssid or len(ssid) > 32 or len(psk) > 128:
            await self.push_status(STATUS_ERROR, 2)
            log.warning("wifi validation failed")
            return
        await self.push_status(STATUS_CONNECTING, 0)
        ok, msg = await apply_wifi(ssid, psk, conf_path, log)
        log.info("wifi apply: ok=%s %s", ok, msg)
        if ok:
            await self.push_status(STATUS_ONLINE, 0)
        else:
            await self.push_status(STATUS_ERROR, 3)


def _make_read(state: ProvisionState) -> Callable[[Any], bytearray]:
    def _read(char: Any) -> bytearray:
        u = str(char.uuid).lower()
        if u == _norm_uuid(CHAR_DEVICE_INFO).lower():
            return state.device_info_payload()
        if u == _norm_uuid(CHAR_STATUS).lower():
            return state.pack_status(STATUS_IDLE, 0)
        if u == _norm_uuid(CHAR_WIFI_SCAN).lower():
            return state.wifi_scan_payload()
        return bytearray()

    return _read


def _make_write(
    state: ProvisionState, loop: asyncio.AbstractEventLoop, conf_path: str, log: logging.Logger
) -> Callable[[Any, Any], None]:
    def _write(char: Any, value: Any) -> None:
        u = str(char.uuid).lower()
        b = bytearray(value) if not isinstance(value, bytearray) else value
        if u == _norm_uuid(CHAR_WIFI).lower():

            async def _run() -> None:
                await state.on_wifi_write(bytes(b), conf_path, log)

            fut = asyncio.run_coroutine_threadsafe(_run(), loop)
            fut.add_done_callback(
                lambda f: f.exception() and log.error("wifi task: %s", f.exception())
            )
            return
        if u == _norm_uuid(CHAR_COMMAND).lower():
            if len(b) >= 1 and b[0] == OP_WIFI_SCAN:

                async def _scan() -> None:
                    await state.push_wifi_scan_after_command(log)

                fut = asyncio.run_coroutine_threadsafe(_scan(), loop)
                fut.add_done_callback(
                    lambda f: f.exception() and log.error("wifi_scan cmd: %s", f.exception())
                )
            return

    return _write


async def _connection_watcher(server: Any, state: ProvisionState, log: logging.Logger) -> None:
    seen = False
    while True:
        await asyncio.sleep(0.35)
        try:
            connected = await server.is_connected()
        except Exception:
            connected = False
        if connected and not seen:
            seen = True
            log.info("central connected; status notify idle")
            await state.push_status(STATUS_IDLE, 0)
        if not connected:
            seen = False


async def run_ble(args: argparse.Namespace, log: logging.Logger) -> None:
    if sys.platform != "linux":
        log.error("BLE peripheral requires Linux + BlueZ (Raspberry Pi OS).")
        sys.exit(2)
    try:
        from pi_edge.ensure_bluetooth import ensure_bluetooth_enabled

        ensure_bluetooth_enabled(verbose=args.v)
    except ImportError:
        for exe in ("/usr/sbin/rfkill", "/sbin/rfkill"):
            if Path(exe).is_file():
                subprocess.run(
                    [exe, "unblock", "bluetooth"],
                    check=False,
                    timeout=10,
                    capture_output=True,
                )
                break
        else:
            subprocess.run(
                ["rfkill", "unblock", "bluetooth"],
                check=False,
                timeout=10,
                capture_output=True,
            )
    from bless import BlessServer, GATTCharacteristicProperties, GATTAttributePermissions

    loop = asyncio.get_running_loop()
    serial = args.serial or _device_serial()
    adv_name = args.advertise_name or f"CoilShield-{serial[-5:].replace('-', '')}"

    server: Any = BlessServer(name=adv_name, loop=loop, adapter=args.adapter)
    state = ProvisionState(server, serial=serial, model=args.model, fw=args.fw)

    server.read_request_func = _make_read(state)
    server.write_request_func = _make_write(state, loop, args.wpa_conf, log)

    su = _norm_uuid(SERVICE_UUID)
    await server.add_new_service(su)
    await server.add_new_characteristic(
        su,
        _norm_uuid(CHAR_DEVICE_INFO),
        GATTCharacteristicProperties.read,
        state.device_info_payload(),
        GATTAttributePermissions.readable,
    )
    await server.add_new_characteristic(
        su,
        _norm_uuid(CHAR_WIFI),
        GATTCharacteristicProperties.write
        | GATTCharacteristicProperties.write_without_response,
        bytearray(64),
        GATTAttributePermissions.writeable,
    )
    await server.add_new_characteristic(
        su,
        _norm_uuid(CHAR_STATUS),
        GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
        state.pack_status(STATUS_IDLE, 0),
        GATTAttributePermissions.readable,
    )
    await server.add_new_characteristic(
        su,
        _norm_uuid(CHAR_COMMAND),
        GATTCharacteristicProperties.write
        | GATTCharacteristicProperties.write_without_response,
        bytearray([0]),
        GATTAttributePermissions.writeable,
    )
    await server.add_new_characteristic(
        su,
        _norm_uuid(CHAR_WIFI_SCAN),
        GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
        state.wifi_scan_payload(),
        GATTAttributePermissions.readable,
    )

    await server.start()
    log.info("Advertising as %r service %s", adv_name, su)
    asyncio.create_task(_connection_watcher(server, state, log))
    await asyncio.Event().wait()


def main() -> None:
    p = argparse.ArgumentParser(description="CoilShield BLE WiFi provisioning (Pi)")
    p.add_argument("--serial", default=None, help="Controller serial (default COILSHIELD_SERIAL)")
    p.add_argument("--advertise-name", default=None, help="BLE advertised local name")
    p.add_argument("--model", default="CoilShield-ICCP", help="device_info model string")
    p.add_argument("--fw", default="0.0.0", help="device_info firmware string")
    p.add_argument(
        "--wpa-conf",
        default=os.environ.get("WPA_SUPPLICANT_CONF", "/etc/wpa_supplicant/wpa_supplicant.conf"),
        help="wpa_supplicant.conf path for fallback writes",
    )
    p.add_argument("--adapter", default=None, help="hci adapter name for BlueZ (optional)")
    p.add_argument("--dry-run", action="store_true", help="Print config and exit (no BLE)")
    p.add_argument("-v", action="store_true", help="verbose logging")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.v else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("ble_provision")
    if args.dry_run:
        log.info("dry-run serial=%s wpa_conf=%s", args.serial or _device_serial(), args.wpa_conf)
        return
    try:
        asyncio.run(run_ble(args, log))
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
